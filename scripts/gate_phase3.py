"""Phase-3 verification gate: lazy model registry, thresholds cache, indexes,
the SimulationControl plane, prune_readings, run_sim, and the view regressions
these changes touch.

Everything runs inside one transaction that is rolled back at the end, so the
development database is left exactly as it was found.

Usage:
    python scripts/gate_phase3.py
"""
from __future__ import annotations

import io
import logging
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import (  # noqa: E402
    CaptureQueriesContext,  # noqa: E402
    setup_test_environment,
    teardown_test_environment,
)
from django.utils import timezone  # noqa: E402

from apps.monitoring import views as mon_views  # noqa: E402
from apps.monitoring.models import EventLog, Reading, SimulationControl, Thresholds  # noqa: E402
from apps.monitoring.services import faults as faults_mod  # noqa: E402
from apps.monitoring.services.faults import (  # noqa: E402
    get_thresholds,
    invalidate_thresholds,
)
from core.model_registry import ModelNotAvailable, registry  # noqa: E402

# Adds "testserver" to ALLOWED_HOSTS (the test client's default host) and
# swaps in the locmem email backend so the fault-alert send_mail in
# predict_view cannot reach a real SMTP server. DEBUG is left untouched.
setup_test_environment()

# The gate deliberately provokes a 400 (invalid simulation type); hide
# django.request's WARNING for it so it does not print after the GATE line.
# ERROR (5xx) is kept, and the test client re-raises view exceptions anyway.
logging.getLogger("django.request").setLevel(logging.ERROR)

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, bool(ok), detail))


# --------------------------------------------------------------- model registry
def test_model_registry() -> None:
    # The whole point of the change: importing the module that USES the model
    # must not deserialise it.
    import apps.prediction.views  # noqa: F401

    check("registry: importing apps.prediction.views does not load artifacts",
          registry._state == registry.EMPTY, f"state={registry._state}")
    check("registry: nothing loaded at import time", registry._state != registry.LOADED,
          f"state={registry._state}")

    registry.reload()
    check("registry: reload() resets to empty", registry._state == registry.EMPTY)

    check("registry: available() sees the real artifacts", registry.available(),
          str(registry.model_path))

    model, scaler = registry.get()
    check("registry: get() returns both artifacts",
          model is not None and scaler is not None)
    check("registry: state is loaded", registry._state == registry.LOADED)

    model2, _ = registry.get()
    check("registry: second get() is cached (same object)",
          model2 is model, f"id {id(model)} == {id(model2)}")

    # Thread safety: eight concurrent callers must all receive the one object
    # the single guarded load produced. If the lock were missing, two threads
    # could deserialize in parallel and hand out different instances.
    registry.reload()
    seen: set[int] = set()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            m, _ = registry.get()
            seen.add(id(m))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    check("registry: 8 concurrent callers, zero errors",
          not errors, f"{len(errors)} errors")
    check("registry: all callers share one loaded instance",
          len(seen) == 1, f"{len(seen)} distinct instances")
    check("registry: no thread left hanging",
          not any(t.is_alive() for t in threads))

    # Missing artifacts -> ModelNotAvailable, remembered rather than retried.
    # The registry logs this at ERROR level by design; suppress it here so an
    # *expected* failure does not read like a gate failure on stderr.
    import logging

    registry_logger = logging.getLogger("core.model_registry")
    previous_level = registry_logger.level
    registry_logger.setLevel(logging.CRITICAL)
    try:
        with override_settings(MODEL_DIR=str(ROOT / "ml" / "__no_such_dir__")):
            registry.reload()
            raised = False
            try:
                registry.get()
            except ModelNotAvailable:
                raised = True
            check("registry: missing artifacts raise ModelNotAvailable", raised)
            check("registry: failure is cached (state=failed)",
                  registry._state == registry.FAILED, f"state={registry._state}")

            again = False
            try:
                registry.get()
            except ModelNotAvailable:
                again = True
            check("registry: repeat call raises without retrying", again)
    finally:
        registry_logger.setLevel(previous_level)
    registry.reload()


# ------------------------------------------------------------- thresholds cache
def test_threshold_cache() -> None:
    invalidate_thresholds()

    with CaptureQueriesContext(connection) as ctx:
        first = get_thresholds()
    check("cache: cold read hits the database", len(ctx) >= 1, f"{len(ctx)} queries")

    with CaptureQueriesContext(connection) as ctx:
        second = get_thresholds()
    check("cache: warm read is served from memory", len(ctx) == 0, f"{len(ctx)} queries")
    check("cache: warm read returns the same row", first.pk == second.pk)

    # post_save must invalidate immediately (same process).
    original_max = first.max_current
    first.max_current = 33.5
    first.save()  # -> post_save -> invalidate_thresholds
    with CaptureQueriesContext(connection) as ctx:
        after = get_thresholds()
    check("cache: post_save invalidates", after.max_current == 33.5,
          f"max_current={after.max_current}")
    check("cache: invalidated read re-queries", len(ctx) >= 1, f"{len(ctx)} queries")

    first.max_current = original_max
    first.save()
    check("cache: restored original threshold",
          get_thresholds().max_current == original_max)

    # TTL: ageing the timestamp out forces a re-read even without a signal.
    invalidate_thresholds()
    get_thresholds()
    faults_mod._cached_at = time.monotonic() - (faults_mod.CACHE_TTL_SECONDS + 5)
    with CaptureQueriesContext(connection) as ctx:
        get_thresholds()
    check("cache: TTL expiry forces a re-read", len(ctx) >= 1, f"{len(ctx)} queries")

    # The row is created lazily inside the lock; post_save fires on that same
    # thread. A non-reentrant Lock would deadlock here instead of returning.
    invalidate_thresholds()
    Thresholds.objects.all().delete()  # -> post_delete -> invalidate
    outcome: dict[str, object] = {}

    def create_row() -> None:
        try:
            outcome["row"] = get_thresholds()
            outcome["ok"] = True
        except BaseException as exc:  # noqa: BLE001
            outcome["err"] = exc

    t = threading.Thread(target=create_row, daemon=True)
    t.start()
    t.join(timeout=5)
    check("cache: auto-create under lock does not deadlock", not t.is_alive())
    check("cache: auto-create returns a row", outcome.get("ok") is True,
          str(outcome.get("err", "")))

    # post_delete must invalidate too.
    invalidate_thresholds()
    check("cache: row re-created after delete",
          isinstance(get_thresholds(), Thresholds))


# ---------------------------------------------------------------------- indexes
def test_indexes() -> None:
    expected = {
        "monitoring_reading": {"created_at"},
        "monitoring_eventlog": {"created_at", "event_type"},
    }
    with connection.cursor() as cursor:
        for table, columns in expected.items():
            constraints = connection.introspection.get_constraints(cursor, table)
            covered: set[str] = set()
            for spec in constraints.values():
                if spec.get("primary_key") or spec.get("unique"):
                    continue
                covered.update(spec.get("columns") or [])
            for column in columns:
                check(f"index: {table}.{column}", column in covered,
                      f"indexed columns={sorted(covered)}")


# -------------------------------------------------------- SimulationControl model
def test_simulation_control() -> None:
    control = SimulationControl.get_solo()
    check("control: get_solo() returns the singleton",
          control.pk == 1 and SimulationControl.get_solo().pk == control.pk)

    # Reset to a known state.
    control.requested_type = ""
    control.active_type = ""
    control.heartbeat_at = None
    control.save()

    check("control: no heartbeat -> not alive", control.daemon_alive is False)
    check("control: no heartbeat -> not running", control.is_running is False)
    check("control: no request -> not stale", control.stale is False)

    control.heartbeat_at = timezone.now()
    check("control: fresh heartbeat -> alive", control.daemon_alive is True)
    check("control: alive but idle -> not running", control.is_running is False)

    control.active_type = SimulationControl.SIM_NORMAL
    check("control: alive + active -> running", control.is_running is True)
    check("control: running -> not stale", control.stale is False)

    control.active_type = ""
    control.requested_type = SimulationControl.SIM_FAULT
    check("control: requested but no live daemon -> stale", control.stale is True)

    control.heartbeat_at = timezone.now() - timedelta(seconds=60)
    check("control: stale heartbeat -> not alive", control.daemon_alive is False)

    control.requested_type = ""
    control.active_type = ""
    control.heartbeat_at = None
    control.save()


# ---------------------------------------------------------------- prune_readings
def test_prune_readings() -> None:
    Reading.objects.all().delete()
    now = timezone.now()

    recent = [Reading.objects.create(current=20, voltage=430, temperature=40)
              for _ in range(5)]
    stale = [Reading.objects.create(current=21, voltage=431, temperature=41)
             for _ in range(4)]
    for r in stale:
        # created_at is auto_now_add, so backdate with a direct UPDATE.
        Reading.objects.filter(pk=r.pk).update(created_at=now - timedelta(days=90))
    # Give the survivors distinct, strictly increasing timestamps. Rapid
    # inserts can share a timestamp at Windows' ~1ms clock resolution, which
    # would make order_by("-created_at") tie and the "kept the newest" check
    # meaningless. recent[i] ends up i minutes older than `now`.
    for i, r in enumerate(recent):
        Reading.objects.filter(pk=r.pk).update(
            created_at=now - timedelta(minutes=len(recent) - i)
        )

    check("prune: fixture in place", Reading.objects.count() == 9,
          f"{Reading.objects.count()} rows")

    out = io.StringIO()
    call_command("prune_readings", days=30, keep=0, dry_run=True, stdout=out)
    check("prune: --dry-run deletes nothing", Reading.objects.count() == 9,
          f"{Reading.objects.count()} rows")
    check("prune: --dry-run reports the count", "Would delete 4" in out.getvalue(),
          out.getvalue().strip().splitlines()[-1] if out.getvalue() else "")

    out = io.StringIO()
    call_command("prune_readings", days=30, keep=0, stdout=out)
    check("prune: age bound removes only stale rows", Reading.objects.count() == 5,
          f"{Reading.objects.count()} rows")
    remaining_ids = set(Reading.objects.values_list("id", flat=True))
    check("prune: age bound kept the recent rows",
          remaining_ids == {r.pk for r in recent})

    # Capture the expectation BEFORE pruning: the three newest must survive.
    expected_top3 = {recent[-1].pk, recent[-2].pk, recent[-3].pk}
    check("prune: newest three are the three largest by created_at",
          set(Reading.objects.order_by("-created_at").values_list("id", flat=True)[:3])
          == expected_top3)

    out = io.StringIO()
    call_command("prune_readings", days=0, keep=3, stdout=out)
    survivors = set(Reading.objects.values_list("id", flat=True))
    check("prune: count bound caps to --keep", len(survivors) == 3,
          f"{len(survivors)} rows")
    check("prune: count bound kept the NEWEST rows", survivors == expected_top3,
          f"kept {sorted(survivors)}, expected {sorted(expected_top3)}")

    out = io.StringIO()
    call_command("prune_readings", days=0, keep=0, stdout=out)
    check("prune: both bounds off is a no-op", Reading.objects.count() == 3,
          "both --days and --keep are 0")

    Reading.objects.all().delete()


# ----------------------------------------------------------------------- run_sim
def test_run_sim_one_shot() -> None:
    Reading.objects.all().delete()
    before = Reading.objects.count()

    out = io.StringIO()
    call_command(
        "run_sim", "--type", "normal", "--iterations", "3", "--period", "0.01",
        stdout=out,
    )
    produced = Reading.objects.count() - before
    check("run_sim: one-shot writes the requested number of samples",
          produced == 3, f"{produced} readings")
    check("run_sim: one-shot reports completion",
          "Simulation completed" in out.getvalue())
    check("run_sim: one-shot does not claim the shared control plane",
          SimulationControl.get_solo().active_type == "",
          SimulationControl.get_solo().active_type)

    Reading.objects.all().delete()


def test_run_sim_daemon() -> None:
    control = SimulationControl.get_solo()
    control.requested_type = ""
    control.active_type = ""
    control.heartbeat_at = None
    control.save()

    observations: dict[int, str] = {}
    ticks = {"n": 0}
    real_sleep = time.sleep

    def fake_sleep(seconds: float) -> None:
        ticks["n"] += 1
        n = ticks["n"]
        observations[n] = control.__class__.objects.get(pk=control.pk).active_type
        if n == 1:
            # Stop whatever is running.
            c = SimulationControl.objects.get(pk=control.pk)
            c.requested_type = ""
            c.save(update_fields=["requested_type", "updated_at"])
        elif n == 2:
            # Ask for the fault scenario instead.
            c = SimulationControl.objects.get(pk=control.pk)
            c.requested_type = SimulationControl.SIM_FAULT
            c.save(update_fields=["requested_type", "updated_at"])
        elif n >= 4:
            raise KeyboardInterrupt
        real_sleep(0.002)

    out = io.StringIO()
    import unittest.mock as mock

    with mock.patch("time.sleep", side_effect=fake_sleep):
        call_command(
            "run_sim", "--daemon", "--type", "normal", "--period", "0.01",
            stdout=out,
        )

    check("run_sim daemon: honoured the seeded --type",
          observations.get(1) == SimulationControl.SIM_NORMAL, str(observations))
    check("run_sim daemon: stopped when the UI cleared the request",
          observations.get(2) == "", str(observations))
    check("run_sim daemon: started the newly requested scenario",
          observations.get(3) == SimulationControl.SIM_FAULT, str(observations))
    check("run_sim daemon: ran at least four ticks", ticks["n"] >= 4,
          f"{ticks['n']} ticks")

    control.refresh_from_db()
    check("run_sim daemon: released active_type on exit",
          control.active_type == "", control.active_type)
    check("run_sim daemon: cleared heartbeat on exit",
          control.heartbeat_at is None, str(control.heartbeat_at))
    check("run_sim daemon: reports itself stopped to the UI",
          control.is_running is False)

    control.requested_type = ""
    control.save()


# --------------------------------------------------------------- view regressions
def test_views() -> None:
    check("views: module-level _simulation_threads removed",
          not hasattr(mon_views, "_simulation_threads"))
    check("views: module-level _simulation_instances removed",
          not hasattr(mon_views, "_simulation_instances"))

    client = Client()
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user, _ = User.objects.get_or_create(username="Admin")
    client.force_login(user)
    json_headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    # status payload now advertises the simulator's state
    resp = client.get("/api/monitoring/status/")
    check("views: /status/ returns 200", resp.status_code == 200, str(resp.status_code))
    payload = resp.json() if resp.status_code == 200 else {}
    check("views: /status/ always returns thresholds (auto-created)",
          payload.get("thresholds") is not None)
    check("views: /status/ exposes simulation state",
          "simulation" in payload, ",".join(sorted(payload)))
    sim = payload.get("simulation") or {}
    check("views: /status/ reports the simulator offline (no daemon in this gate)",
          sim.get("simulator_alive") is False, str(sim))

    # start -> records the request, and tells the truth when no daemon runs
    resp = client.post("/api/monitoring/simulate/start/", {"type": "normal"},
                       **json_headers)
    body = resp.json() if resp.status_code == 200 else {}
    check("views: start returns 200", resp.status_code == 200, str(resp.status_code))
    check("views: start records requested_type",
          SimulationControl.get_solo().requested_type == "normal",
          SimulationControl.get_solo().requested_type)
    check("views: start reports simulator offline without a daemon",
          body.get("simulator") == "offline", str(body))
    check("views: start message tells the operator how to run it",
          "run_sim" in (body.get("message") or ""), body.get("message", ""))

    # stop -> clears the pending request. A Start immediately preceded this, so
    # requested_type was still set: the honest answer is "stopped".
    resp = client.post("/api/monitoring/simulate/stop/", {"type": "normal"},
                       **json_headers)
    body = resp.json() if resp.status_code == 200 else {}
    check("views: stop returns 200", resp.status_code == 200, str(resp.status_code))
    check("views: stop clears requested_type",
          SimulationControl.get_solo().requested_type == "",
          SimulationControl.get_solo().requested_type)
    check("views: stop reports 'stopped' when a request was pending",
          body.get("status") == "stopped", str(body))

    # Second stop with genuinely nothing requested -> the idle branch.
    resp = client.post("/api/monitoring/simulate/stop/", {"type": "normal"},
                       **json_headers)
    body = resp.json() if resp.status_code == 200 else {}
    check("views: stop reports 'idle' when nothing is running",
          body.get("status") == "idle", str(body))

    # an invalid type must still be rejected
    resp = client.post("/api/monitoring/simulate/start/", {"type": "bogus"},
                       **json_headers)
    check("views: invalid simulation type rejected",
          resp.status_code == 400, str(resp.status_code))

    # thresholds POST invalidates the cache and is visible to check_fault
    invalidate_thresholds()
    resp = client.post("/api/monitoring/thresholds/", {"max_current": "31.5"},
                       **json_headers)
    check("views: thresholds POST returns 200", resp.status_code == 200,
          str(resp.status_code))
    check("views: thresholds POST is visible through the cache",
          get_thresholds().max_current == 31.5, str(get_thresholds().max_current))
    resp = client.get("/api/monitoring/thresholds/")
    check("views: thresholds GET returns the saved value",
          resp.json().get("max_current") == 31.5, str(resp.json().get("max_current")))


def test_predict_zero_branch() -> None:
    """soc=0 and battery_temp=0 used to raise NameError on log_reading()."""
    client = Client()
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user, _ = User.objects.get_or_create(username="Admin")
    client.force_login(user)

    Reading.objects.all().delete()
    before_readings = Reading.objects.count()

    before_errors = EventLog.objects.filter(event_type="PREDICTION_ERROR").count()

    resp = client.post("/prediction/", {
        "form0-battery_temp": "0",
        "form0-soc": "0",
        "form0-duration": "1",
        "form0-timestamp": "2026-01-01T10:00",
    })
    check("predict: zero-branch returns 200", resp.status_code == 200,
          str(resp.status_code))
    check("predict: zero-branch logs no reading (0/0/0 would read as FAULT)",
          Reading.objects.count() == before_readings,
          f"{Reading.objects.count()} vs {before_readings}")
    after_errors = EventLog.objects.filter(event_type="PREDICTION_ERROR").count()
    check("predict: zero-branch raises no PREDICTION_ERROR",
          after_errors == before_errors,
          f"{after_errors} vs {before_errors} prediction errors")

    predictions = resp.context["predictions"] if resp.context else []
    check("predict: zero-branch produces a prediction entry (NameError fixed)",
          len(predictions) == 1, f"{len(predictions)} predictions")


# ----------------------------------------------------------------------- runner
def main() -> int:
    # Redirect DB writes to a savepoint we can roll back so the dev database
    # is untouched by the gate.
    with transaction.atomic():
        test_model_registry()
        test_threshold_cache()
        test_indexes()
        test_simulation_control()
        test_prune_readings()
        test_run_sim_one_shot()
        test_run_sim_daemon()
        test_views()
        test_predict_zero_branch()
        transaction.set_rollback(True)

    teardown_test_environment()

    print()
    failed = 0
    for label, ok, detail in results:
        if not ok:
            failed += 1
        suffix = f"   [{detail}]" if detail else ""
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")

    print()
    print(f"{len(results) - failed}/{len(results)} assertions passed")
    print("GATE: PASS" if not failed else f"GATE: FAIL ({failed} failed)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

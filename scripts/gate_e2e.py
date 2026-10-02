"""Live end-to-end gate: the `run_sim` daemon and the web control plane.

Exercises the contract Phase 3 introduced, over real HTTP against a running
dev server, with a real `run_sim --daemon` process:

    web  --POST /simulate/start/-->  SimulationControl.requested_type
    daemon --polls--> starts, heartbeats, writes readings
    web  --GET  /status/-->  simulation.running == True, latest_reading grows
    web  --POST /simulate/stop/--> clears requested_type
    daemon --polls--> stops, clears active_type, heartbeat drops

The daemon must be started separately first:

    python manage.py run_sim --daemon --period 0.5

Usage:
    python scripts/gate_e2e.py http://127.0.0.1:8025
"""
from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8025"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

results: list[tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, "PASS" if ok else "FAIL", detail))


def _read(resp) -> tuple[int, str]:
    return resp.status, resp.read().decode("utf-8", "replace")


def get(path: str) -> tuple[int, str] | None:
    try:
        with opener.open(BASE + path, timeout=10) as r:
            return _read(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        results.append((path, "ERR", str(e)))
        return None


def csrf_token() -> str:
    """Value of the `csrftoken` cookie Django set when we rendered a form.

    Sent both as the hidden `csrfmiddlewaretoken` field and as the
    `X-CSRFToken` header, which is how the real page's JS authenticates
    non-form requests.
    """
    for cookie in jar:
        if cookie.name == "csrftoken":
            return cookie.value
    return ""


def post(path: str, payload: dict) -> tuple[int, dict] | None:
    token = csrf_token()
    payload = {**payload, "csrfmiddlewaretoken": token}
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={
            "Referer": BASE + "/",
            # Mirrors visualization/index.html postForm(); the endpoints branch
            # on this to decide between JSON and a redirect.
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": token,
        },
    )
    try:
        with opener.open(req, timeout=10) as r:
            status, body = _read(r)
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        results.append((path, "ERR", str(e)))
        return None
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        results.append((path, str(status), f"non-JSON body: {body[:80]!r}"))
        return status, {}


def login() -> bool:
    got = get("/accounts/login/")
    if not got:
        return False
    token = None
    for line in got[1].splitlines():
        if "csrfmiddlewaretoken" in line:
            token = line.split('value="')[1].split('"')[0]
            break
    if not token:
        results.append(("login", "FAIL", "no csrf token"))
        return False

    data = urllib.parse.urlencode(
        {
            "csrfmiddlewaretoken": token,
            "username": "Admin",
            "password": "Admin@123",
            "next": "/home/",
        }
    ).encode()
    req = urllib.request.Request(
        BASE + "/accounts/login/",
        data=data,
        headers={"Referer": BASE + "/accounts/login/"},
    )
    try:
        with opener.open(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    check("login", code in (200, 302), f"HTTP {code}")
    return True


def status() -> dict | None:
    got = get("/api/monitoring/status/")
    if not got:
        return None
    try:
        return json.loads(got[1])
    except json.JSONDecodeError:
        results.append(("/status/", "FAIL", "non-JSON"))
        return None


def latest_id(s: dict | None) -> int:
    lr = (s or {}).get("latest_reading") or {}
    return int(lr.get("id") or 0)


def wait_until(predicate, timeout: float, interval: float = 0.5):
    """Poll `/status/` until predicate(status) holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = status()
        if predicate(last):
            return last
        time.sleep(interval)
    return last


def main() -> int:
    if not login():
        print("GATE: FAIL (could not log in)")
        return 1

    # ------------------------------------------------------------- baseline
    base = status()
    check("baseline: status reachable", base is not None)
    check("baseline: simulation block present",
          base is not None and "simulation" in base,
          ",".join(sorted(base)) if base else "")
    sim0 = (base or {}).get("simulation") or {}
    # This gate REQUIRES a running daemon (see module docstring), so the
    # offline path is covered by gate_phase3 instead.
    check("baseline: daemon reported online (it is running)",
          sim0.get("simulator_alive") is True, str(sim0))

    # --------------------------------------------------------------- start
    resp = post("/api/monitoring/simulate/start/", {"type": "normal"})
    check("start: HTTP 200", resp is not None and resp[0] == 200,
          str(resp[0]) if resp else "no response")
    body = (resp or (0, {}))[1]
    check("start: reports the simulator online (daemon is running)",
          body.get("simulator") == "online", str(body))
    check("start: message says requested, not started",
          "requested" in (body.get("message") or ""), body.get("message", ""))
    check("start: records requested_type",
          (status() or {}).get("simulation", {}).get("requested") == "normal",
          str((status() or {}).get("simulation")))

    # -------------------------------------------------- daemon picks it up
    running = wait_until(
        lambda s: bool((s or {}).get("simulation", {}).get("running")), timeout=20
    )
    sim_run = (running or {}).get("simulation", {})
    check("daemon: simulation reported running",
          sim_run.get("running") is True, str(sim_run))
    check("daemon: active_type is normal", sim_run.get("active") == "normal",
          str(sim_run))
    check("daemon: heartbeat reported alive", sim_run.get("simulator_alive") is True,
          str(sim_run))

    # ------------------------------------------------------ it writes data
    time.sleep(3)
    after_start = status()
    grew = latest_id(after_start) > latest_id(base)
    check("daemon: readings are being written",
          grew, f"{latest_id(base)} -> {latest_id(after_start)}")

    # ---------------------------------------------------------------- stop
    resp = post("/api/monitoring/simulate/stop/", {"type": "normal"})
    check("stop: HTTP 200", resp is not None and resp[0] == 200,
          str(resp[0]) if resp else "no response")
    body = (resp or (0, {}))[1]
    check("stop: reports stopped (a request was pending)",
          body.get("status") == "stopped", str(body))

    # ------------------------------------------------------ daemon lets go
    idle = wait_until(
        lambda s: (s or {}).get("simulation", {}).get("active") == "", timeout=20
    )
    sim_idle = (idle or {}).get("simulation", {})
    check("daemon: active_type cleared after stop",
          sim_idle.get("active") == "", str(sim_idle))
    check("daemon: no longer reported running",
          sim_idle.get("running") is False, str(sim_idle))

    # ------------------------------- writes must actually have ceased
    # Sample id when the stop took effect, then confirm it stops advancing.
    frozen = latest_id(idle)
    time.sleep(4)
    final = status()
    delta = latest_id(final) - frozen
    check("daemon: readings stopped after stop (delta <= 1 in 4s)",
          delta <= 1, f"delta={delta} ({frozen} -> {latest_id(final)})")

    # ------------------------------------------- second stop is a no-op
    resp = post("/api/monitoring/simulate/stop/", {"type": "normal"})
    body = (resp or (0, {}))[1]
    check("stop: reports idle when nothing is running",
          body.get("status") == "idle", str(body))

    # ---------------------------------------------------------- reporting
    print()
    for label, mark, detail in results:
        suffix = f"   [{detail}]" if detail else ""
        print(f"  {mark}  {label}{suffix}")
    failed = sum(1 for _, m, _ in results if m == "FAIL")
    print()
    print(f"{len(results) - failed}/{len(results)} assertions passed")
    print("GATE: PASS" if not failed else f"GATE: FAIL ({failed} failed)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

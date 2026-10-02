"""Seed a demo dataset: a backdated reading series plus matching event log.

The application creates nothing on its own -- a freshly migrated database has
no readings and no events, so every dashboard renders empty until a simulation
is started. That is correct behaviour for production, but it makes demos,
screenshots and manual UI checks needlessly awkward. This script fills the gap.

What it writes:

* :class:`~apps.monitoring.models.Thresholds` -- created with the documented
  defaults if absent. An existing (operator-customised) row is left alone.
* :class:`~apps.monitoring.models.Reading` -- a deterministic normal -> fault ->
  recovery waveform spread backwards over ``--hours``, so time-axis charts have
  a real width and ``prune_readings --days`` has something to prune.
* :class:`~apps.monitoring.models.EventLog` -- one ``FAULT_DETECTED`` entry per
  fault window, produced by the real :func:`check_fault` so the wording matches
  what a live run would log.
* :class:`~apps.monitoring.models.SimulationControl` -- the singleton row is
  created only. Its ``heartbeat_at`` is deliberately **not** touched: faking a
  heartbeat would make the UI report "simulator running" when no ``run_sim``
  process exists.

The sequence is generated from a fixed ``random.Random`` seed, so two runs
produce byte-identical samples for the same arguments.

Usage::

    python scripts/seed_db.py                     # 600 readings over 6 hours
    python scripts/seed_db.py --readings 2000 --hours 48
    python scripts/seed_db.py --reset             # wipe first, then seed
    python scripts/seed_db.py --dry-run           # report counts, change nothing
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django

django.setup()

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.monitoring.models import (  # noqa: E402
    EventLog,
    Reading,
    SimulationControl,
    Thresholds,
)
from apps.monitoring.services.faults import check_fault, get_thresholds  # noqa: E402
from apps.monitoring.services.values import PredictedValues  # noqa: E402

#: Fixed seed: the demo waveform must be reproducible across runs.
RNG_SEED = 1337

#: How the window is split. (label, fraction of the window, sampler)
SEGMENTS: tuple[tuple[str, float, str], ...] = (
    ("normal", 0.40, "normal"),
    ("over-current", 0.15, "over_current"),
    ("normal", 0.25, "normal"),
    ("over-temperature", 0.15, "over_temperature"),
    ("normal", 0.05, "normal"),
)


def _sample(kind: str, rng: random.Random) -> PredictedValues:
    """Draw one sample inside the plausible sensor range for `kind`.

    Every value stays within the source's physical range -- the seed data is
    meant to trip the *safety* thresholds, never to look like a broken sensor.
    """
    if kind == "over_current":
        current = rng.uniform(31.0, 44.0)  # above the 30 A limit
        temperature = rng.uniform(30.0, 75.0)
    elif kind == "over_temperature":
        current = rng.uniform(18.0, 26.0)
        temperature = rng.uniform(82.0, 96.0)  # above the 80 C limit
    else:
        current = rng.uniform(16.0, 28.0)  # inside the 10-30 A band
        temperature = rng.uniform(18.0, 72.0)
    voltage = rng.uniform(405.0, 455.0)  # inside the 400-460 V band
    return PredictedValues(current=current, voltage=voltage, temperature=temperature)


def build_timeline(readings: int, hours: float, rng: random.Random):
    """Return ``[(timestamp, sample, segment_label), ...]`` oldest first."""
    now = timezone.now()
    window = timedelta(hours=hours)

    # Weight the sample index by segment fraction so each segment gets its
    # share of rows rather than an equal slice.
    total_weight = sum(fraction for _label, fraction, _kind in SEGMENTS)
    spans: list[tuple[str, str, int]] = []
    for label, fraction, kind in SEGMENTS:
        spans.append((label, kind, int(round(readings * fraction / total_weight))))
    # Absorb rounding drift into the leading normal segment.
    drift = readings - sum(span for _l, _k, span in spans)
    if drift:
        label, kind, span = spans[0]
        spans[0] = (label, kind, span + drift)

    timeline: list[tuple[object, PredictedValues, str]] = []
    index = 0
    for label, kind, span in spans:
        for _ in range(span):
            # Linear ramp across the whole window, so the chart's x-axis is
            # evenly spaced regardless of how the rows were grouped.
            offset = window * (index / max(readings - 1, 1))
            timeline.append((now - window + offset, _sample(kind, rng), label))
            index += 1
    return timeline


@transaction.atomic
def seed(readings: int, hours: float, reset: bool, dry_run: bool) -> int:
    """Write the demo rows. Returns the number of readings created."""
    rng = random.Random(RNG_SEED)

    thresholds = Thresholds.objects.order_by("-updated_at").first()
    thresholds_existed = thresholds is not None
    if thresholds is None:
        thresholds = Thresholds()  # field defaults are the documented limits
        if not dry_run:
            thresholds.save()
    active = get_thresholds(refresh=True)

    timeline = build_timeline(readings, hours, rng)

    if reset and not dry_run:
        Reading.objects.all().delete()
        EventLog.objects.all().delete()

    if dry_run:
        faults = sum(
            1 for _ts, sample, _label in timeline if check_fault(sample)[0]
        )
        print(
            f"[dry-run] would create {len(timeline)} readings over {hours:g}h "
            f"({faults} of them outside the limits) and reset={'yes' if reset else 'no'}"
        )
        print(f"[dry-run] thresholds row {'already present' if thresholds_existed else 'would be created'}")
        return 0

    # `created_at` is auto_now_add, so every row would otherwise be stamped
    # "now" and the series would collapse to a single point. The field is
    # relaxed for the duration of the insert and restored immediately after --
    # flipping it on the model class is safe here because the script is a
    # single-threaded, single-purpose process. With the flag off the assigned
    # value must actually be present, hence created_at=ts below.
    created_field = Reading._meta.get_field("created_at")
    rows = [
        Reading(
            current=sample.current,
            voltage=sample.voltage,
            temperature=sample.temperature,
            created_at=ts,
        )
        for ts, sample, _label in timeline
    ]
    previous_auto_now_add = created_field.auto_now_add
    created_field.auto_now_add = False
    try:
        Reading.objects.bulk_create(rows)
    finally:
        created_field.auto_now_add = previous_auto_now_add

    # One event per contiguous fault window, matching how a live run logs them.
    events_written = 0
    previous_faulty = False
    fault_rows: list[EventLog] = []
    for ts, sample, label in timeline:
        is_fault, message = check_fault(sample)
        if is_fault and not previous_faulty:
            fault_rows.append(
                EventLog(
                    event_type="FAULT_DETECTED",
                    details=f"Seeded demo fault in the '{label}' window: {message}",
                    response=message,
                    created_at=ts,
                )
            )
        previous_faulty = is_fault

    info_row = EventLog(
        event_type="INFO",
        details=(
            f"Seeded {len(rows)} readings across {hours:g}h "
            f"({len(fault_rows)} fault windows) via scripts/seed_db.py"
        ),
        response="",
        created_at=timezone.now(),
    )

    # Same relaxation as Reading.created_at: both columns are auto_now_add, so
    # the assigned timestamps above would otherwise be discarded on insert.
    event_field = EventLog._meta.get_field("created_at")
    previous_event_auto_now_add = event_field.auto_now_add
    event_field.auto_now_add = False
    try:
        EventLog.objects.bulk_create([*fault_rows, info_row])
    finally:
        event_field.auto_now_add = previous_event_auto_now_add
    events_written = len(fault_rows) + 1

    # The control row must exist for the start/stop endpoints, but its
    # heartbeat is left NULL so the UI correctly reports "no simulator running".
    SimulationControl.get_solo()

    print(
        f"Seeded {len(rows)} readings over {hours:g}h "
        f"({events_written} events). Thresholds: {active}"
        + ("" if thresholds_existed else " (created with defaults)")
    )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a reproducible demo dataset (readings + events)."
    )
    parser.add_argument("--readings", type=int, default=600, help="How many readings to create")
    parser.add_argument("--hours", type=float, default=6.0, help="Spread the series over this many hours")
    parser.add_argument("--reset", action="store_true", help="Delete all existing readings and events first")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without writing")
    args = parser.parse_args(argv)

    if args.readings < 2:
        parser.error("--readings must be at least 2")
    if args.hours <= 0:
        parser.error("--hours must be positive")

    seed(args.readings, args.hours, args.reset, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Threshold evaluation: decide whether a sample constitutes a fault.

Thresholds are read on **every** sample — up to ~1/sec while a simulation runs,
plus once per prediction form. Reading the singleton row each time is a wasted
query, so it is cached in-process and invalidated by the ``post_save`` /
``post_delete`` receivers wired in ``apps.monitoring.apps``.

Cache invalidation model:

* **same process** — an edit through the API or the admin fires ``post_save``
  and the cache is dropped immediately, so the next sample sees the new limits.
* **other processes** — a signal only reaches the process that fired it, so a
  multi-worker deployment could otherwise keep stale limits until restart. A
  short TTL bounds that staleness independently of the signals.

The TTL is deliberately small: a threshold write is rare, and a 5 s window is
far below anything safety-relevant while still collapsing the 1 Hz query storm
into ~0.2 queries/sec.
"""
from __future__ import annotations

import threading
import time

from ..models import Thresholds
from .values import PredictedValues

#: Maximum age of a cached thresholds row, in seconds. Signals make same-process
#: edits instant; this bounds staleness in every *other* worker.
CACHE_TTL_SECONDS = 5.0

# Reentrant on purpose: get_thresholds() holds this while calling
# Thresholds.objects.create(), which fires post_save -> invalidate_thresholds()
# on the *same thread*. A non-reentrant Lock would self-deadlock there.
_lock = threading.RLock()
_cached: Thresholds | None = None
_cached_at: float = 0.0


def invalidate_thresholds(**_kwargs) -> None:
    """Drop the cached row.

    Wired to ``post_save``/``post_delete`` for :class:`Thresholds`, so it must
    accept (and ignore) the signal's keyword arguments.
    """
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0


def get_thresholds(*, refresh: bool = False) -> Thresholds:
    """Return the active thresholds row, creating the default row if absent.

    Args:
        refresh: bypass the cache and re-read from the database.
    """
    global _cached, _cached_at

    with _lock:
        if not refresh and _cached is not None:
            if (time.monotonic() - _cached_at) <= CACHE_TTL_SECONDS:
                return _cached
            _cached = None

        thresholds = Thresholds.objects.order_by("-updated_at").first()
        if not thresholds:
            thresholds = Thresholds.objects.create()

        _cached = thresholds
        _cached_at = time.monotonic()
        return thresholds


def check_fault(predicted_values: PredictedValues) -> tuple[bool, str]:
    """Check if predicted values violate safety thresholds.

    Returns:
        ``(is_fault, message)`` — ``message`` is ``"SAFE"`` when no threshold
        is violated, otherwise a human-readable description of every violation.
    """
    thresholds = get_thresholds()
    faults = []

    if (
        predicted_values.current > thresholds.max_current
        or predicted_values.current < thresholds.min_current
    ):
        faults.append(
            f"Current: {predicted_values.current:.2f}A "
            f"(limit: {thresholds.min_current}-{thresholds.max_current}A)"
        )

    if (
        predicted_values.voltage > thresholds.max_voltage
        or predicted_values.voltage < thresholds.min_voltage
    ):
        faults.append(
            f"Voltage: {predicted_values.voltage:.2f}V "
            f"(limit: {thresholds.min_voltage}-{thresholds.max_voltage}V)"
        )

    if (
        predicted_values.temperature > thresholds.max_temperature
        or predicted_values.temperature < thresholds.min_temperature
    ):
        faults.append(
            f"Temperature: {predicted_values.temperature:.2f}\u00b0C "
            f"(limit: {thresholds.min_temperature}-{thresholds.max_temperature}\u00b0C)"
        )

    if faults:
        return True, "FAULT DETECTED - " + "; ".join(faults)
    return False, "SAFE"

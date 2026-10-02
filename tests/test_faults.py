"""Threshold evaluation and the in-process cache around it.

``check_fault`` runs on every simulated sample (~1 Hz) and every prediction, so
its cache is exercised here at the same level of detail as the Phase 3 gate:
cold/warm query counts, signal invalidation, TTL expiry, and the reentrant-lock
path that would self-deadlock with a plain ``Lock``.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.monitoring.models import Thresholds
from apps.monitoring.services import faults
from apps.monitoring.services.faults import (
    CACHE_TTL_SECONDS,
    check_fault,
    get_thresholds,
    invalidate_thresholds,
)
from apps.monitoring.services.values import PredictedValues

# Mid-range on all three axes: inside every default threshold.
SAFE = PredictedValues(current=20.0, voltage=430.0, temperature=40.0)


class _StubClock:
    """Stand-in for the ``time`` module exposing only ``monotonic``.

    Patched onto ``services.faults`` (which does ``import time``) so the TTL can
    be controlled without touching the real clock the rest of the suite uses.
    """

    def __init__(self, now: float) -> None:
        self._now = now

    def monotonic(self) -> float:
        return self._now


@pytest.mark.django_db
class TestCheckFault:
    def test_in_range_reads_safe(self):
        is_fault, message = check_fault(SAFE)
        assert is_fault is False
        assert message == "SAFE"

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            # Above the maximum. Positional order is (current, voltage,
            # temperature), so the value under test is placed explicitly.
            (PredictedValues(current=31.0, voltage=430.0, temperature=40.0), "Current"),
            (PredictedValues(current=20.0, voltage=461.0, temperature=40.0), "Voltage"),
            (PredictedValues(current=20.0, voltage=430.0, temperature=81.0), "Temperature"),
            # Below the minimum -- the symmetric branch, easy to leave out.
            (PredictedValues(current=9.0, voltage=430.0, temperature=40.0), "Current"),
            (PredictedValues(current=20.0, voltage=399.0, temperature=40.0), "Voltage"),
            # Regression: consumers.get_status_data used to omit the min
            # temperature comparison entirely.
            (PredictedValues(current=20.0, voltage=430.0, temperature=-1.0), "Temperature"),
        ],
    )
    def test_each_parameter_detects_both_bounds(self, values, expected):
        is_fault, message = check_fault(values)
        assert is_fault is True
        assert expected in message
        assert "FAULT DETECTED" in message

    def test_every_violation_is_reported(self):
        is_fault, message = check_fault(PredictedValues(5.0, 100.0, 999.0))
        assert is_fault is True
        for parameter in ("Current", "Voltage", "Temperature"):
            assert parameter in message

    def test_temperature_message_is_not_mojibake(self):
        _is_fault, message = check_fault(PredictedValues(20.0, 430.0, 500.0))
        assert "Â" not in message
        assert "°C" in message

    def test_fault_uses_the_current_thresholds(self, db):
        th = get_thresholds()
        th.max_current = 15.0
        th.save()

        # 20 A was safe under the 30 A default and is a fault now.
        assert check_fault(SAFE)[0] is True


@pytest.mark.django_db
class TestThresholdCache:
    @staticmethod
    def _seed() -> None:
        """Ensure a row exists so a cold read is exactly one SELECT.

        With an empty table ``get_thresholds()`` would SELECT then INSERT, and
        the query count would be 2 for a reason unrelated to caching.
        """
        if not Thresholds.objects.exists():
            Thresholds.objects.create()
        invalidate_thresholds()

    def test_cold_read_queries_the_database(self):
        self._seed()
        with CaptureQueriesContext(connection) as ctx:
            get_thresholds()
        assert len(ctx.captured_queries) == 1, [q["sql"] for q in ctx.captured_queries]

    def test_warm_read_is_served_from_memory(self):
        get_thresholds(refresh=True)  # also guarantees a row exists
        with CaptureQueriesContext(connection) as ctx:
            get_thresholds()
        assert len(ctx.captured_queries) == 0, [q["sql"] for q in ctx.captured_queries]

    def test_warm_read_returns_the_same_object(self):
        assert get_thresholds() is get_thresholds()

    def test_refresh_bypasses_the_cache(self):
        first = get_thresholds()
        assert get_thresholds(refresh=True) is not first

    def test_save_invalidates(self):
        self._seed()
        get_thresholds()
        th = Thresholds.objects.order_by("-updated_at").first()
        th.max_current = 33.5
        th.save()  # -> post_save -> invalidate_thresholds()

        assert get_thresholds().max_current == 33.5

    def test_delete_invalidates(self):
        self._seed()
        get_thresholds()
        Thresholds.objects.all().delete()

        # Recreated on demand rather than raising or returning a dead row.
        recreated = get_thresholds()
        assert recreated.pk is not None
        assert recreated.max_current == 30.0

    def test_ttl_expiry_forces_a_re_read(self, monkeypatch):
        """Signals only reach the process that fired them; the TTL bounds the
        staleness every *other* worker can observe."""
        self._seed()
        get_thresholds()
        assert faults._cached_at > 0.0

        # Move the clock past the cache's own timestamp by more than the TTL.
        monkeypatch.setattr(
            faults, "time", _StubClock(faults._cached_at + CACHE_TTL_SECONDS + 1)
        )

        with CaptureQueriesContext(connection) as ctx:
            get_thresholds()
        assert len(ctx.captured_queries) == 1, [q["sql"] for q in ctx.captured_queries]

    def test_a_cache_hit_within_the_ttl_does_not_re_read(self, monkeypatch):
        self._seed()
        get_thresholds()

        # One millisecond short of expiry, measured from the cache's own stamp
        # so real elapsed time cannot make this flaky.
        monkeypatch.setattr(
            faults, "time", _StubClock(faults._cached_at + CACHE_TTL_SECONDS - 0.001)
        )

        with CaptureQueriesContext(connection) as ctx:
            get_thresholds()
        assert len(ctx.captured_queries) == 0, [q["sql"] for q in ctx.captured_queries]

    def test_lock_is_reentrant(self):
        """`get_thresholds()` holds the lock while calling `create()`, and
        `create()` fires `post_save` -> `invalidate_thresholds()` on the *same
        thread*. A non-reentrant Lock would deadlock there forever.

        The first half asserts the property directly; the second drives the
        real auto-create path that would hang.
        """
        assert faults._lock.acquire(blocking=False)
        try:
            # Re-acquiring on the same thread must not block.
            assert faults._lock.acquire(blocking=False)
            faults._lock.release()
        finally:
            faults._lock.release()

        invalidate_thresholds()
        Thresholds.objects.all().delete()
        created = get_thresholds()  # auto-create fires post_save under the lock
        assert created.pk is not None

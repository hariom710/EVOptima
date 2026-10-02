"""Management commands: retention, threshold reset, and one-shot simulation.

``run_sim --daemon`` is exercised end-to-end against a live process by
``scripts/gate_e2e.py`` (it is an infinite control loop, so it does not belong
in a unit suite); everything around it -- one-shot mode, duration resolution,
and the exit path that must never leave a stale "running" flag -- is here.
"""
from __future__ import annotations

import io
import re
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.monitoring.management.commands.run_sim import (
    FAULT_CYCLE_SECONDS,
)
from apps.monitoring.management.commands.run_sim import (
    Command as RunSimCommand,
)
from apps.monitoring.models import EventLog, Reading, SimulationControl, Thresholds
from apps.monitoring.services import get_thresholds, invalidate_thresholds

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(name: str, *args: str) -> str:
    """Invoke a command capturing plain (ANSI-stripped) output."""
    out = io.StringIO()
    call_command(name, *args, stdout=out, stderr=io.StringIO())
    return ANSI.sub("", out.getvalue())


def _reading(age_days: float) -> Reading:
    reading = Reading.objects.create(current=20.0, voltage=430.0, temperature=40.0)
    # created_at is auto_now_add, so backdate with a direct UPDATE.
    Reading.objects.filter(pk=reading.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return reading


@pytest.mark.django_db
class TestPruneReadings:
    def test_age_bound_removes_only_stale_rows(self):
        stale = [_reading(90) for _ in range(3)]
        recent = [_reading(1) for _ in range(4)]

        output = run("prune_readings", "--days", "30", "--keep", "0")

        remaining = set(Reading.objects.values_list("id", flat=True))
        assert remaining == {r.pk for r in recent}
        assert not remaining & {r.pk for r in stale}
        assert "Deleted 3 of 7" in output

    def test_count_bound_keeps_the_newest_rows(self):
        survivors = [_reading(5 - i) for i in range(5)]  # survivors[4] is newest
        expected = {survivors[-1].pk, survivors[-2].pk, survivors[-3].pk}

        output = run("prune_readings", "--days", "0", "--keep", "3")

        assert set(Reading.objects.values_list("id", flat=True)) == expected
        assert "keeping newest 3, pruning 2" in output

    def test_dry_run_deletes_nothing(self):
        _reading(90)
        _reading(90)
        _reading(1)

        output = run("prune_readings", "--days", "30", "--keep", "0", "--dry-run")

        assert "Would delete 2" in output
        assert Reading.objects.count() == 3

    def test_dry_run_reports_the_size_bound_too(self):
        for _ in range(5):
            _reading(1)
        output = run("prune_readings", "--days", "0", "--keep", "2", "--dry-run")
        assert "Would delete 3" in output
        assert Reading.objects.count() == 5

    def test_zero_for_both_bounds_is_a_no_op(self):
        _reading(90)
        output = run("prune_readings", "--days", "0", "--keep", "0")
        assert "Nothing to do" in output
        assert Reading.objects.count() == 1

    def test_nothing_to_prune_is_reported(self):
        _reading(1)
        output = run("prune_readings", "--days", "30", "--keep", "10")
        assert "Nothing to prune" in output
        assert Reading.objects.count() == 1

    def test_age_bound_within_the_window_keeps_everything(self):
        _reading(1)
        _reading(2)
        run("prune_readings", "--days", "30", "--keep", "0")
        assert Reading.objects.count() == 2

    def test_empty_table_is_handled(self):
        output = run("prune_readings")
        assert "Nothing to prune" in output


@pytest.mark.django_db
class TestResetThresholds:
    def test_restores_the_documented_defaults(self):
        Thresholds.objects.create(max_current=1.0, min_temperature=-100.0)

        run("reset_thresholds")

        # The command deletes rather than updates, so this is a new row.
        th = Thresholds.objects.get()
        assert (th.min_current, th.max_current) == (10.0, 30.0)
        assert (th.min_voltage, th.max_voltage) == (400.0, 460.0)
        assert (th.min_temperature, th.max_temperature) == (0.0, 80.0)

    def test_replaces_multiple_rows_with_one(self):
        Thresholds.objects.create()
        Thresholds.objects.create()

        run("reset_thresholds")

        assert Thresholds.objects.count() == 1

    def test_writes_through_the_cache(self):
        """`delete` + `create` both fire signals, so reset_thresholds needs no
        explicit invalidation -- this asserts that stays true."""
        invalidate_thresholds()
        thresholds = get_thresholds()
        thresholds.max_current = 1.0
        thresholds.save()  # -> post_save -> invalidate
        assert get_thresholds().max_current == 1.0

        run("reset_thresholds")

        assert get_thresholds().max_current == 30.0

    def test_records_an_audit_event(self):
        run("reset_thresholds")
        entry = EventLog.objects.filter(event_type="THRESHOLDS_CHANGED").first()
        assert entry is not None
        assert "reset" in entry.details.lower()


@pytest.mark.django_db
class TestRunSimOneShot:
    def _sim(self, *args: str) -> str:
        return run("run_sim", "--period", "0.01", *args)

    def test_writes_the_requested_number_of_samples(self):
        before = Reading.objects.count()
        output = self._sim("--type", "normal", "--iterations", "3")
        assert Reading.objects.count() - before == 3
        assert "Simulation completed" in output

    def test_defaults_to_the_normal_scenario(self):
        before = Reading.objects.count()
        output = self._sim("--iterations", "2")
        assert "Starting normal simulation" in output
        assert Reading.objects.count() - before == 2

    def test_fault_scenario_runs_too(self):
        before = Reading.objects.count()
        output = self._sim("--type", "fault", "--iterations", "2")
        assert "Starting fault simulation" in output
        assert Reading.objects.count() - before == 2

    def test_one_shot_does_not_claim_the_control_plane(self):
        """One-shot is a local CLI run; claiming SimulationControl would fight
        a daemon that is following the web UI."""
        self._sim("--type", "normal", "--iterations", "2")
        control = SimulationControl.get_solo()
        assert control.active_type == ""
        assert control.requested_type == ""
        assert control.heartbeat_at is None

    def test_reports_the_sample_period(self):
        output = self._sim("--type", "normal", "--iterations", "1")
        assert "Sample period: 0.01s" in output


@pytest.mark.django_db
class TestRunSimDaemonSupport:
    """The pieces of --daemon that can be tested without an infinite loop."""

    def test_cycles_resolve_to_a_duration(self):
        assert RunSimCommand()._resolve_duration(
            {"duration": None, "cycles": 2}
        ) == 2 * FAULT_CYCLE_SECONDS

    def test_cycles_without_duration_is_none_when_not_given(self):
        assert RunSimCommand()._resolve_duration(
            {"duration": None, "cycles": None}
        ) is None

    def test_explicit_duration_wins_over_cycles(self):
        assert RunSimCommand()._resolve_duration(
            {"duration": 12.0, "cycles": 3}
        ) == 12.0

    @pytest.mark.parametrize("cycles,expected", [(1, 50.0), (3, 150.0)])
    def test_fault_cycle_length_is_fixed(self, cycles, expected):
        assert RunSimCommand()._resolve_duration(
            {"duration": None, "cycles": cycles}
        ) == expected

    def test_release_clears_everything_the_ui_reads(self):
        """On any exit the daemon must clear the flags, or the status endpoint
        keeps claiming a simulation is running."""
        control = SimulationControl.get_solo()
        control.active_type = SimulationControl.SIM_NORMAL
        control.heartbeat_at = timezone.now()
        control.save()

        RunSimCommand._release(control)

        control.refresh_from_db()
        assert control.active_type == ""
        assert control.heartbeat_at is None
        assert control.is_running is False

    def test_release_is_safe_when_nothing_was_running(self):
        control = SimulationControl.get_solo()
        RunSimCommand._release(control)
        control.refresh_from_db()
        assert control.active_type == ""
        assert control.heartbeat_at is None

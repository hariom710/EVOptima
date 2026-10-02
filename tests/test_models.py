"""Model-level invariants: safety defaults, indexes, and the control plane."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.monitoring.models import EventLog, Reading, SimulationControl, Thresholds


@pytest.mark.django_db
class TestThresholdsDefaults:
    """The defaults are the documented safety envelope, not arbitrary numbers."""

    def test_defaults_match_the_documented_limits(self):
        th = Thresholds()
        assert (th.min_current, th.max_current) == (10.0, 30.0)
        assert (th.min_voltage, th.max_voltage) == (400.0, 460.0)
        assert (th.min_temperature, th.max_temperature) == (0.0, 80.0)

    def test_str_is_human_readable(self):
        assert "10.0-30.0A" in str(Thresholds())


class TestIndexes:
    """`/status/` orders by created_at on every poll and readings arrive ~1/sec.

    Without these, each poll is a full table scan. Asserted from the model so a
    migration that drops an index fails the suite, not production.
    """

    @pytest.mark.parametrize(
        ("model", "field"),
        [
            (Reading, "created_at"),
            (EventLog, "created_at"),
            (EventLog, "event_type"),
        ],
    )
    def test_field_is_indexed(self, model, field):
        assert model._meta.get_field(field).db_index is True

    def test_unindexed_fields_are_not_accidentally_indexed(self):
        # Guard the other side too, so the migration stays intentional.
        assert Reading._meta.get_field("current").db_index is False


class TestEventLogTypes:
    """Every literal written by application code must exist in EVENT_TYPES."""

    @pytest.mark.parametrize(
        "event_type",
        [
            "FAULT_DETECTED",
            "CHARGING_STOPPED",
            "THRESHOLDS_CHANGED",
            "INFO",
            "PREDICTION_ERROR",
            # prediction/views.py logs a failed fault email with this value; it
            # was missing from the choices, so it was invisible in the admin
            # dropdown and failed full_clean().
            "EMAIL_ERROR",
        ],
    )
    def test_choice_exists(self, event_type):
        assert event_type in dict(EventLog.EVENT_TYPES)

    @pytest.mark.django_db
    def test_every_choice_passes_full_clean(self):
        for value, _label in EventLog.EVENT_TYPES:
            EventLog(event_type=value).full_clean()


@pytest.mark.django_db
class TestSimulationControl:
    def test_get_solo_is_stable_and_creates_once(self):
        first = SimulationControl.get_solo()
        second = SimulationControl.get_solo()
        assert first.pk == 1
        assert first.pk == second.pk
        assert SimulationControl.objects.count() == 1

    def test_defaults_mean_no_simulation(self):
        control = SimulationControl.get_solo()
        assert control.requested_type == SimulationControl.SIM_NONE
        assert control.active_type == SimulationControl.SIM_NONE
        assert control.daemon_alive is False
        assert control.is_running is False
        assert control.stale is False

    def test_no_heartbeat_means_dead_daemon(self):
        control = SimulationControl(
            active_type=SimulationControl.SIM_NORMAL,
            heartbeat_at=None,
        )
        assert control.daemon_alive is False
        # An active simulation with no live daemon must NOT read as running:
        # this is the state left behind by a hard-killed daemon.
        assert control.is_running is False

    def test_fresh_heartbeat_means_alive(self):
        control = SimulationControl(heartbeat_at=timezone.now())
        assert control.daemon_alive is True

    def test_heartbeat_older_than_timeout_means_dead(self):
        control = SimulationControl(
            heartbeat_at=timezone.now()
            - timedelta(seconds=SimulationControl.HEARTBEAT_TIMEOUT + 1),
        )
        assert control.daemon_alive is False

    def test_alive_but_idle_is_not_running(self):
        control = SimulationControl(
            active_type="", heartbeat_at=timezone.now()
        )
        assert control.daemon_alive is True
        assert control.is_running is False

    def test_alive_and_active_is_running(self):
        control = SimulationControl(
            active_type=SimulationControl.SIM_NORMAL,
            heartbeat_at=timezone.now(),
        )
        assert control.is_running is True
        assert control.stale is False

    def test_requested_without_pickup_is_stale(self):
        control = SimulationControl(
            requested_type=SimulationControl.SIM_FAULT,
            active_type="",
            heartbeat_at=None,
        )
        assert control.is_running is False
        assert control.stale is True

    def test_str_shows_both_sides(self):
        control = SimulationControl(
            requested_type=SimulationControl.SIM_NORMAL,
            active_type=SimulationControl.SIM_FAULT,
        )
        text = str(control)
        assert "requested=normal" in text
        assert "active=fault" in text

    def test_empty_sides_render_as_dash(self):
        text = str(SimulationControl())
        assert "requested=-" in text and "active=-" in text

"""`apps.prediction` — the lazy registry, and the zero-input regression.

The bug this pins: when ``soc == 0 and battery_temp == 0`` the view left
``predicted_values`` unassigned but still called ``log_reading(predicted_values)``,
so every such submit raised ``NameError`` inside the blanket ``except`` and
showed a misleading "Error making prediction". That branch also must not persist
an I=0/V=0/T=0 reading, because `/status/` would read it as an out-of-range
FAULT.
"""
from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.monitoring.models import EventLog, Reading
from core.model_registry import registry

JSON = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

TIMESTAMP = "2026-01-01T12:00"


def _form_fields(**overrides) -> dict:
    """One valid `form0` submission; the other two prefixes stay absent."""
    values = {
        "form0-battery_temp": "25",
        "form0-soc": "50",
        "form0-duration": "2",
        "form0-timestamp": TIMESTAMP,
    }
    values.update(overrides)
    return values


@pytest.mark.django_db
class TestAuth:
    @pytest.mark.parametrize(
        "path", ["/prediction/", "/home/", "/prediction/welcome/"]
    )
    def test_pages_redirect_anonymous_users(self, client, path):
        assert client.get(path).status_code == 302

    @pytest.mark.parametrize(
        "path", ["/prediction/", "/home/", "/prediction/welcome/"]
    )
    def test_pages_render_for_a_logged_in_user(self, auth_client, path):
        assert auth_client.get(path).status_code == 200


@pytest.mark.django_db
class TestPredictView:
    def test_get_renders_the_form(self, auth_client):
        response = auth_client.get(reverse("prediction:predict"))
        assert response.status_code == 200
        assert len(response.context["forms"]) == 3

    def test_zero_input_branch_does_not_raise(self, auth_client):
        """Regression: this used to raise NameError, swallowed into a generic
        'Error making prediction' message."""
        response = auth_client.post(reverse("prediction:predict"), _form_fields(
            **{"form0-battery_temp": "0", "form0-soc": "0"}
        ))
        assert response.status_code == 200
        # The form was processed and produced an entry -- i.e. execution
        # reached the end of the loop body rather than dying in it.
        assert len(response.context["predictions"]) == 1

    def test_zero_input_branch_writes_no_reading(self, auth_client):
        """An I=0/V=0/T=0 row would be read as an out-of-range FAULT."""
        auth_client.post(reverse("prediction:predict"), _form_fields(
            **{"form0-battery_temp": "0", "form0-soc": "0"}
        ))
        assert Reading.objects.count() == 0

    def test_zero_input_branch_logs_no_prediction_error(self, auth_client):
        auth_client.post(reverse("prediction:predict"), _form_fields(
            **{"form0-battery_temp": "0", "form0-soc": "0"}
        ))
        assert not EventLog.objects.filter(event_type="PREDICTION_ERROR").exists()

    def test_zero_input_entry_is_zeroed_out(self, auth_client):
        response = auth_client.post(reverse("prediction:predict"), _form_fields(
            **{"form0-battery_temp": "0", "form0-soc": "0"}
        ))
        entry = response.context["predictions"][0]
        assert entry["soc"] == 0
        assert entry["battery_temp"] == 0
        assert entry["predicted_value"] == 0
        assert entry["fault_detected"] is False

    def test_real_input_persists_a_reading(self, auth_client):
        response = auth_client.post(reverse("prediction:predict"), _form_fields())
        assert response.status_code == 200
        assert Reading.objects.count() == 1

    def test_real_input_produces_a_prediction_entry(self, auth_client):
        response = auth_client.post(reverse("prediction:predict"), _form_fields())
        assert len(response.context["predictions"]) == 1
        assert response.context["predictions"][0]["soc"] == 50

    def test_only_valid_forms_are_processed(self, auth_client):
        """Two of the three prefixes are absent; exactly one entry comes back."""
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form1-soc": "50"}),  # incomplete -> invalid
        )
        assert len(response.context["predictions"]) == 1

    def test_invalid_values_are_rejected_without_crashing(self, auth_client):
        # soc > 100 violates the form's max_value.
        response = auth_client.post(
            reverse("prediction:predict"), _form_fields(**{"form0-soc": "500"})
        )
        assert response.status_code == 200
        assert response.context["predictions"] == []


@pytest.mark.django_db
class TestDegradedWhenArtifactsMissing:
    def test_missing_artifacts_degrade_to_a_message(self, auth_client, tmp_path):
        """A deployment problem must surface as a usable page plus an event,
        never as a startup traceback or a 500."""
        with override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            response = auth_client.get(reverse("prediction:predict"))
            assert response.status_code == 200
            assert response.context["predictions"] == []

        entry = EventLog.objects.filter(event_type="PREDICTION_ERROR").first()
        assert entry is not None
        assert "artifacts" in entry.details.lower()

    def test_missing_artifacts_do_not_break_posting(self, auth_client, tmp_path):
        with override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            response = auth_client.post(
                reverse("prediction:predict"), _form_fields()
            )
        assert response.status_code == 200
        assert response.context["predictions"] == []
        assert Reading.objects.count() == 0

    def test_every_degraded_request_records_an_event(self, auth_client, tmp_path):
        with override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            for _ in range(3):
                auth_client.get(reverse("prediction:predict"))
            # The registry remembers the *named* load failure too (see
            # test_registry), so the second and third requests never re-hit the
            # disk -- but each request still degrades explicitly with an
            # auditable event of its own. ("power" is loaded first, so it is
            # the failure that gets recorded; the view never reaches "energy".)
            assert registry.failed("power") is not None

        assert EventLog.objects.filter(
            event_type="PREDICTION_ERROR"
        ).count() == 3


@pytest.mark.django_db
class TestHomeView:
    def test_home_reports_safe(self, auth_client):
        Reading.objects.create(current=20.0, voltage=430.0, temperature=40.0)
        response = auth_client.get(reverse("home"))
        assert response.status_code == 200
        assert response.context["state"] == "SAFE"

    def test_home_reports_fault(self, auth_client):
        Reading.objects.create(current=999.0, voltage=430.0, temperature=40.0)
        response = auth_client.get(reverse("home"))
        assert response.context["state"] == "FAULT"

    def test_home_reports_safe_with_no_readings(self, auth_client):
        response = auth_client.get(reverse("home"))
        assert response.context["state"] == "SAFE"
        assert response.context["latest_reading"] is None

    def test_home_shows_recent_events(self, auth_client):
        EventLog.objects.create(event_type="INFO", details="hello")
        response = auth_client.get(reverse("home"))
        assert len(response.context["recent_events"]) == 1

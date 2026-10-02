"""Monitoring endpoints: authentication, status classification, and the
simulation control plane that replaced `_simulation_threads`.

Every endpoint here is ``@login_required`` + ``@api_view``; both decorators
matter, because returning a DRF ``Response`` from a *plain* Django view (the
old start/stop implementation) raised ``.accepted_renderer not set`` and turned
every AJAX click into an HTTP 500.
"""
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.monitoring.models import EventLog, Reading, SimulationControl, Thresholds
from apps.monitoring.services import get_thresholds, invalidate_thresholds

JSON = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

STATUS = "monitoring:status"
THRESHOLDS = "monitoring:thresholds"
EVENTS = "monitoring:events"
START = "monitoring:start_simulation"
STOP = "monitoring:stop_simulation"


@pytest.mark.django_db
class TestAuthenticationRequired:
    """A monitoring surface must not be readable anonymously."""

    @pytest.mark.parametrize(
        ("name", "method"),
        [(STATUS, "get"), (THRESHOLDS, "get"), (EVENTS, "get"),
         (START, "post"), (STOP, "post")],
    )
    def test_endpoint_redirects_to_login(self, client, name, method):
        response = getattr(client, method)(reverse(name))
        assert response.status_code == 302
        assert response.url.startswith("/accounts/login/")

    @pytest.mark.parametrize(
        "path",
        ["/api/monitoring/status/", "/api/status/", "/api/monitoring/events/"],
    )
    def test_alias_routes_are_also_protected(self, client, path):
        assert client.get(path).status_code == 302


@pytest.mark.django_db
class TestStatus:
    def test_reports_safe_when_there_is_no_reading(self, auth_client):
        response = auth_client.get(reverse(STATUS))
        assert response.status_code == 200
        payload = response.json()
        assert payload["state"] == "SAFE"
        assert payload["latest_reading"] is None
        assert payload["fault_type"] is None

    def test_payload_exposes_every_documented_key(self, auth_client):
        payload = auth_client.get(reverse(STATUS)).json()
        for key in (
            "now", "latest_reading", "thresholds", "state", "fault_type",
            "is_current_anomaly", "is_voltage_anomaly", "is_temperature_anomaly",
            "simulation",
        ):
            assert key in payload, f"missing {key!r}"

    def test_reading_in_range_reads_safe(self, auth_client):
        Reading.objects.create(current=20.0, voltage=430.0, temperature=40.0)
        payload = auth_client.get(reverse(STATUS)).json()
        assert payload["state"] == "SAFE"
        assert payload["fault_type"] is None

    @pytest.mark.parametrize(
        ("current", "voltage", "temperature", "fault_type", "flag"),
        [
            (31.0, 430.0, 40.0, "CURRENT_OUT_OF_RANGE", "is_current_anomaly"),
            (20.0, 461.0, 40.0, "VOLTAGE_OUT_OF_RANGE", "is_voltage_anomaly"),
            (20.0, 430.0, 81.0, "TEMPERATURE_OUT_OF_RANGE", "is_temperature_anomaly"),
            # Regression: the WebSocket consumer used to skip this branch.
            (20.0, 430.0, -1.0, "TEMPERATURE_OUT_OF_RANGE", "is_temperature_anomaly"),
            # Below the current minimum.
            (9.0, 430.0, 40.0, "CURRENT_OUT_OF_RANGE", "is_current_anomaly"),
            # Below the voltage minimum.
            (20.0, 399.0, 40.0, "VOLTAGE_OUT_OF_RANGE", "is_voltage_anomaly"),
        ],
    )
    def test_fault_classification(
        self, auth_client, current, voltage, temperature, fault_type, flag
    ):
        Reading.objects.create(
            current=current, voltage=voltage, temperature=temperature
        )
        payload = auth_client.get(reverse(STATUS)).json()
        assert payload["state"] == "FAULT"
        assert payload["fault_type"] == fault_type
        assert payload[flag] is True

    def test_only_the_first_violation_is_reported(self, auth_client):
        """The view is an if/elif chain; pin the precedence so a refactor
        cannot silently change which flag the dashboard lights up."""
        Reading.objects.create(current=999.0, voltage=999.0, temperature=999.0)
        payload = auth_client.get(reverse(STATUS)).json()
        assert payload["fault_type"] == "CURRENT_OUT_OF_RANGE"

    def test_simulation_block_is_always_present(self, auth_client):
        payload = auth_client.get(reverse(STATUS)).json()
        assert payload["simulation"] == {
            "requested": "",
            "active": "",
            "simulator_alive": False,
            "running": False,
            "stale": False,
        }

    def test_simulation_block_reflects_a_live_daemon(self, auth_client):
        SimulationControl.get_solo()
        SimulationControl.objects.filter(pk=1).update(
            requested_type="normal",
            active_type="normal",
            heartbeat_at=timezone.now(),
        )
        payload = auth_client.get(reverse(STATUS)).json()
        assert payload["simulation"] == {
            "requested": "normal",
            "active": "normal",
            "simulator_alive": True,
            "running": True,
            "stale": False,
        }

    def test_alias_route_returns_the_same_payload(self, auth_client):
        Reading.objects.create(current=999.0, voltage=430.0, temperature=40.0)
        canonical = auth_client.get(reverse(STATUS)).json()
        alias = auth_client.get("/api/status/").json()
        # `now` is generated per request and would differ by microseconds.
        canonical.pop("now")
        alias.pop("now")
        assert alias == canonical


@pytest.mark.django_db
class TestThresholds:
    def test_get_returns_the_documented_defaults(self, auth_client):
        payload = auth_client.get(reverse(THRESHOLDS)).json()
        assert payload["min_current"] == 10.0
        assert payload["max_current"] == 30.0
        assert payload["min_voltage"] == 400.0
        assert payload["max_voltage"] == 460.0
        assert payload["min_temperature"] == 0.0
        assert payload["max_temperature"] == 80.0

    def test_post_updates_and_persists(self, auth_client):
        response = auth_client.post(
            reverse(THRESHOLDS), {"max_current": "31.5"}, **JSON
        )
        assert response.status_code == 200
        assert response.json()["max_current"] == 31.5
        assert Thresholds.objects.get().max_current == 31.5

    def test_post_invalidates_the_cache_immediately(self, auth_client):
        invalidate_thresholds()
        assert get_thresholds().max_current == 30.0

        auth_client.post(reverse(THRESHOLDS), {"max_current": "31.5"}, **JSON)

        # No refresh= needed: post_save dropped the cached row.
        assert get_thresholds().max_current == 31.5

    def test_post_with_partial_payload_keeps_the_other_limits(self, auth_client):
        response = auth_client.post(
            reverse(THRESHOLDS), {"min_temperature": "-20"}, **JSON
        )
        payload = response.json()
        assert payload["min_temperature"] == -20.0
        assert payload["max_current"] == 30.0
        assert payload["max_voltage"] == 460.0

    def test_post_records_an_audit_event(self, auth_client):
        auth_client.post(reverse(THRESHOLDS), {"max_current": "31.5"}, **JSON)
        assert EventLog.objects.filter(event_type="THRESHOLDS_CHANGED").exists()

    def test_post_is_rejected_on_a_get_only_endpoint(self, auth_client):
        assert auth_client.post(reverse(EVENTS), {}, **JSON).status_code == 405


@pytest.mark.django_db
class TestEvents:
    def test_empty_log_returns_an_empty_list(self, auth_client):
        assert auth_client.get(reverse(EVENTS)).json() == []

    def test_returns_newest_first(self, auth_client):
        EventLog.objects.create(event_type="INFO", details="first")
        EventLog.objects.create(event_type="INFO", details="second")
        payload = auth_client.get(reverse(EVENTS)).json()
        assert [e["details"] for e in payload] == ["second", "first"]

    def test_is_capped_at_two_entries(self, auth_client):
        for i in range(205):
            EventLog.objects.create(event_type="INFO", details=f"e{i}")
        assert len(auth_client.get(reverse(EVENTS)).json()) == 200

    def test_payload_shape(self, auth_client):
        EventLog.objects.create(event_type="INFO", details="d", response="r")
        entry = auth_client.get(reverse(EVENTS)).json()[0]
        assert set(entry) == {
            "id", "event_type", "details", "response", "created_at",
        }


@pytest.mark.django_db
class TestStartSimulation:
    def test_rejects_an_unknown_type(self, auth_client):
        response = auth_client.post(
            reverse(START), {"type": "bogus"}, **JSON
        )
        assert response.status_code == 400
        assert response.json()["status"] == "error"

    def test_records_intent_and_reports_the_daemon_offline(self, auth_client):
        """No daemon is running in the test suite, so the response must not
        claim a simulation started -- it must tell the operator how to start
        one."""
        response = auth_client.post(
            reverse(START), {"type": "normal"}, **JSON
        )
        payload = response.json()
        assert response.status_code == 200
        assert payload["status"] == "requested"
        assert payload["simulator"] == "offline"
        assert "run_sim --daemon" in payload["message"]
        assert SimulationControl.get_solo().requested_type == "normal"

    def test_reports_the_daemon_online_when_one_is_alive(self, auth_client):
        control = SimulationControl.get_solo()
        control.requested_type = ""
        control.active_type = ""
        control.heartbeat_at = timezone.now()
        control.save()

        payload = auth_client.post(reverse(START), {"type": "fault"}, **JSON).json()
        assert payload["simulator"] == "online"
        assert "requested" in payload["message"]
        assert "run_sim --daemon" not in payload["message"]
        assert SimulationControl.get_solo().requested_type == "fault"

    def test_already_running_same_type_short_circuits(self, auth_client):
        SimulationControl.get_solo()
        SimulationControl.objects.filter(pk=1).update(
            active_type="normal", heartbeat_at=timezone.now()
        )
        payload = auth_client.post(reverse(START), {"type": "normal"}, **JSON).json()
        assert payload["status"] == "already_running"

    def test_switching_simulation_type_is_allowed(self, auth_client):
        SimulationControl.get_solo()
        SimulationControl.objects.filter(pk=1).update(
            active_type="normal", heartbeat_at=timezone.now()
        )
        payload = auth_client.post(reverse(START), {"type": "fault"}, **JSON).json()
        assert payload["status"] == "requested"
        assert SimulationControl.get_solo().requested_type == "fault"

    def test_start_logs_an_info_event(self, auth_client):
        auth_client.post(reverse(START), {"type": "normal"}, **JSON)
        entry = EventLog.objects.filter(
            event_type="INFO", details__contains="Simulation requested"
        ).first()
        assert entry is not None

    def test_optional_knobs_are_echoed_back(self, auth_client):
        payload = auth_client.post(
            reverse(START), {"type": "normal", "duration": "5"}, **JSON
        ).json()
        assert "duration=5" in payload["message"]

    def test_without_the_ajax_header_it_redirects(self, auth_client):
        response = auth_client.post(reverse(START), {"type": "normal"})
        assert response.status_code == 302
        assert response.url == "/visualization/"


@pytest.mark.django_db
class TestStopSimulation:
    def test_stop_clears_a_pending_request(self, auth_client):
        auth_client.post(reverse(START), {"type": "normal"}, **JSON)
        assert SimulationControl.get_solo().requested_type == "normal"

        payload = auth_client.post(
            reverse(STOP), {"type": "normal"}, **JSON
        ).json()
        assert payload["status"] == "stopped"
        assert SimulationControl.get_solo().requested_type == ""

    def test_stop_is_idle_when_nothing_is_pending(self, auth_client):
        payload = auth_client.post(
            reverse(STOP), {"type": "normal"}, **JSON
        ).json()
        assert payload["status"] == "idle"

    def test_stop_logs_an_event(self, auth_client):
        auth_client.post(reverse(STOP), {"type": "normal"}, **JSON)
        assert EventLog.objects.filter(
            event_type="INFO", details__contains="Simulation stop"
        ).exists()

    def test_stop_without_the_ajax_header_redirects(self, auth_client):
        response = auth_client.post(reverse(STOP), {"type": "normal"})
        assert response.status_code == 302
        assert response.url == "/visualization/"


@pytest.mark.django_db
class TestUrlNamespaces:
    def test_canonical_routes_reverse(self):
        for name in (STATUS, THRESHOLDS, EVENTS, START, STOP):
            assert reverse(name).startswith("/api/monitoring/")

    def test_short_aliases_resolve_to_the_same_views(self):
        from django.urls import resolve

        assert resolve("/api/status/").func is resolve(
            "/api/monitoring/status/"
        ).func
        assert resolve("/api/thresholds/").func is resolve(
            "/api/monitoring/thresholds/"
        ).func

    def test_dashboard_routes_exist(self):
        # dashboard_urls.py is included without an app_name, so this route is
        # global rather than part of the `monitoring` namespace.
        assert reverse("monitoring_dashboard") == "/monitoring/dashboard/"
        assert reverse("monitoring_dashboard_alias") == "/api/dashboard/"

"""WebSocket payload parity with the REST status endpoint, plus ASGI routing.

The regression this pins: ``MonitoringConsumer.get_status_data`` checked only
``temperature > max_temperature``, so a *low*-temperature fault reported FAULT
on ``/api/monitoring/status/`` while the WebSocket dashboard still said SAFE.
The two now derive ``state`` from the same rules, asserted side by side below.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from apps.monitoring.consumers import MonitoringConsumer
from apps.monitoring.models import EventLog, Reading

# Django strips the leading slash before handing a path to a URL pattern, so
# `pattern.match()` sees this without the "/". The WebSocket routes also live
# *only* in config.asgi's URLRouter, not in ROOT_URLCONF, so
# django.urls.resolve() cannot see them -- hence testing the patterns directly.
WS_ROUTE = "ws/monitoring/"
#: Same route as Django's resolver sees it, with the leading slash.
WS_PATH_PREFIXED = "/" + WS_ROUTE


def status_data() -> dict:
    """Run the consumer's payload builder on the calling thread.

    ``get_status_data`` is wrapped in ``@database_sync_to_async``, which would
    dispatch it to a worker thread that cannot see this test's uncommitted
    transaction. ``__wrapped__`` is the original function (``functools.wraps``
    sets it), so calling it directly keeps the query on this connection while
    still exercising the real implementation.
    """
    return MonitoringConsumer.get_status_data.__wrapped__(MonitoringConsumer())


@pytest.mark.django_db
class TestRestWebSocketParity:
    @pytest.mark.parametrize(
        ("current", "voltage", "temperature"),
        [
            (20.0, 430.0, 40.0),   # all in range
            (999.0, 430.0, 40.0),  # current high
            (9.0, 430.0, 40.0),    # current low
            (20.0, 461.0, 40.0),   # voltage high
            (20.0, 399.0, 40.0),   # voltage low
            (20.0, 430.0, 81.0),   # temperature high
            # The exact case the consumer used to miss entirely.
            (20.0, 430.0, -1.0),   # temperature low
        ],
    )
    def test_state_matches_the_rest_endpoint(
        self, auth_client, current, voltage, temperature
    ):
        Reading.objects.create(
            current=current, voltage=voltage, temperature=temperature
        )
        rest = auth_client.get(reverse("monitoring:status")).json()["state"]
        assert status_data()["state"] == rest

    @pytest.mark.parametrize(
        ("current", "voltage", "temperature"),
        [(31.0, 430.0, 40.0), (20.0, 461.0, 40.0), (20.0, 430.0, 81.0)],
    )
    def test_low_temperature_is_not_the_only_difference(
        self, auth_client, current, voltage, temperature
    ):
        Reading.objects.create(
            current=current, voltage=voltage, temperature=temperature
        )
        assert status_data()["state"] == "FAULT"

    def test_rest_detects_low_temperature(self, auth_client):
        """Assert the fix on the REST side too, so the parity above cannot pass
        because both sides are wrong in the same way."""
        Reading.objects.create(current=20.0, voltage=430.0, temperature=-1.0)
        payload = auth_client.get(reverse("monitoring:status")).json()
        assert payload["state"] == "FAULT"
        assert payload["fault_type"] == "TEMPERATURE_OUT_OF_RANGE"

    def test_websocket_detects_low_temperature(self):
        Reading.objects.create(current=20.0, voltage=430.0, temperature=-1.0)
        assert status_data()["state"] == "FAULT"


@pytest.mark.django_db
class TestConsumerPayload:
    def test_payload_keys(self):
        data = status_data()
        assert set(data) == {
            "latest_reading", "thresholds", "state", "events",
        }

    def test_no_reading_yields_null_latest(self):
        data = status_data()
        assert data["latest_reading"] is None
        assert data["state"] == "SAFE"

    def test_latest_reading_is_serialised(self):
        reading = Reading.objects.create(
            current=20.0, voltage=430.0, temperature=40.0
        )
        latest = status_data()["latest_reading"]
        assert latest["id"] == reading.id
        assert latest["current"] == 20.0

    def test_thresholds_are_included(self):
        assert status_data()["thresholds"]["max_current"] == 30.0

    def test_events_are_capped_at_ten(self):
        for i in range(25):
            EventLog.objects.create(event_type="INFO", details=f"e{i}")
        assert len(status_data()["events"]) == 10

    def test_events_are_newest_first(self):
        EventLog.objects.create(event_type="INFO", details="oldest")
        EventLog.objects.create(event_type="INFO", details="newest")
        events = status_data()["events"]
        assert [e["details"] for e in events][:2] == ["newest", "oldest"]

    def test_event_shape_matches_the_rest_events_endpoint(self, auth_client):
        EventLog.objects.create(event_type="INFO", details="d", response="r")
        ws_event = status_data()["events"][0]
        rest_event = auth_client.get(reverse("monitoring:events")).json()[0]
        assert set(ws_event) == set(rest_event)
        assert ws_event["id"] == rest_event["id"]


class TestAsgiRouting:
    def test_websocket_route_is_registered(self):
        from apps.monitoring.routing import websocket_urlpatterns

        matched = [
            route for route in websocket_urlpatterns
            if route.pattern.match(WS_ROUTE)
        ]
        assert len(matched) == 1

    def test_unknown_websocket_paths_do_not_match(self):
        from apps.monitoring.routing import websocket_urlpatterns

        assert all(
            route.pattern.match("ws/nope/") is None
            for route in websocket_urlpatterns
        )

    def test_the_route_is_not_matched_with_a_leading_slash(self):
        """Pin the resolver convention the assertions above depend on."""
        from apps.monitoring.routing import websocket_urlpatterns

        assert websocket_urlpatterns[0].pattern.match(WS_PATH_PREFIXED) is None

    def test_route_targets_the_monitoring_consumer(self):
        from apps.monitoring.routing import websocket_urlpatterns

        target = websocket_urlpatterns[0].callback
        assert target.__name__ == "MonitoringConsumer"

    def test_asgi_application_routes_websockets(self):
        from channels.routing import ProtocolTypeRouter

        from config.asgi import application

        assert isinstance(application, ProtocolTypeRouter)
        assert "websocket" in application.application_mapping
        assert "http" in application.application_mapping

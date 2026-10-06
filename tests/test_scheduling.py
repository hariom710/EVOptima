"""`/scheduling/` -- the page that has to make the optimiser's value legible.

The risk this file pins: a comparison page is only worth shipping if both
columns are honest. So the tests check that the *greedy* column is the rule
the site actually runs, that its missed deadline is shown rather than
hidden, and that the optimiser's own binding constraints are printed too --
a page that only ever shows a saving would be marketing, not instrumentation.
"""
from __future__ import annotations

import pytest

from apps.scheduling.fleet import FLEET_SPEC, fleet
from core.site import TOTAL_POWER_KW
from optimization import hourly_tariff

URL = "/scheduling/"


@pytest.mark.django_db
class TestAccess:
    def test_requires_login(self, client):
        response = client.get(URL)
        assert response.status_code == 302
        assert response.url.startswith("/accounts/login/")

    def test_renders_for_a_signed_in_user(self, auth_client):
        response = auth_client.get(URL)
        assert response.status_code == 200
        assert "Smart Charging Scheduler" in response.content.decode()

    def test_route_is_namespaced(self):
        from django.urls import reverse

        assert reverse("scheduling:schedule") == URL

    def test_nav_carries_the_link(self, auth_client):
        """The feature is unreachable from the UI otherwise."""
        page = auth_client.get(URL).content.decode()
        assert 'href="/scheduling/"' in page


@pytest.mark.django_db
class TestComparison:
    def test_both_policies_appear(self, auth_client):
        page = auth_client.get(URL).content.decode()
        assert "Greedy" in page and "Optimised" in page
        # The demo fleet, not a fabricated one.
        for session_id, *_ in FLEET_SPEC:
            assert session_id in page

    def test_the_missed_deadline_is_shown_not_hidden(self, auth_client):
        """Greedy starves EV-06 at the real site budget; the page must say so."""
        response = auth_client.get(URL)
        assert response.context["summary"]["greedy_feasible"] is False
        assert response.context["summary"]["optimal_feasible"] is True
        page = response.content.decode()
        assert "falls 54 kWh short of its deadline" in page
        assert response.context["verdict"]["kind"] == "warning"

    def test_the_optimiser_prints_its_own_binding_constraints(self, auth_client):
        page = auth_client.get(URL).content.decode()
        assert "at its 50 kW limit" in page
        assert "saturated at slot(s) 12-17" in page

    def test_the_optimised_plan_is_the_cheaper_one(self, auth_client):
        summary = auth_client.get(URL).context["summary"]
        assert summary["optimal_cost"] < summary["greedy_cost"]
        assert summary["saved"] > 0
        # ...and buys the same energy while doing it.
        assert summary["optimal_delivered"] == pytest.approx(summary["total_kwh"])

    def test_the_optimiser_buys_less_energy_in_the_expensive_band(self, auth_client):
        summary = auth_client.get(URL).context["summary"]
        assert summary["optimal_peak_kwh"] < summary["greedy_peak_kwh"]


@pytest.mark.django_db
class TestControls:
    @pytest.mark.parametrize(
        ("query", "capacity", "peak_weight"),
        [
            ("", TOTAL_POWER_KW, 0.0),
            ("?capacity=60", 60.0, 0.0),
            ("?capacity=abc", TOTAL_POWER_KW, 0.0),
            ("?capacity=-5", 1.0, 0.0),
            ("?peak_weight=5", TOTAL_POWER_KW, 5.0),
            ("?peak_weight=oops", TOTAL_POWER_KW, 0.0),
            ("?peak_weight=-2", TOTAL_POWER_KW, 0.0),
        ],
    )
    def test_query_parameters_are_clamped(self, auth_client, query, capacity, peak_weight):
        response = auth_client.get(URL + query)
        assert response.context["capacity"] == capacity
        assert response.context["peak_weight"] == peak_weight

    def test_raising_capacity_turns_the_plan_green(self, auth_client):
        squeezed = auth_client.get(URL + "?capacity=60").context["summary"]
        assert squeezed["optimal_feasible"] is False
        page = auth_client.get(URL + "?capacity=60").content.decode()
        assert "could not meet every deadline" in page

    def test_pricing_the_peak_flattens_the_load_but_costs_more(self, auth_client):
        """Lambda is a trade, not a free lunch -- the page must show both sides."""
        cheap = auth_client.get(URL).context["summary"]
        flat = auth_client.get(URL + "?peak_weight=5").context["summary"]

        assert flat["optimal_peak"] < cheap["optimal_peak"]
        assert flat["optimal_cost"] > cheap["optimal_cost"]
        assert flat["optimal_feasible"]


@pytest.mark.django_db
class TestDemoFleet:
    def test_every_session_is_individually_satisfiable(self):
        """A fleet nobody could serve would make the comparison meaningless."""
        for session in fleet():
            assert session.deliverable_kwh >= session.energy_needed_kwh, session.session_id

    def test_simultaneous_demand_exceeds_the_site_budget(self):
        """Four ports share the 09:00-11:00 window; their combined limit is
        172 kW against a 100 kW bus, so greedy *can* collide with a deadline."""
        overlapping = [row for row in FLEET_SPEC if row[1] < 12 and row[2] > 9]
        assert len(overlapping) >= 4
        assert sum(row[4] for row in overlapping) > TOTAL_POWER_KW

    def test_fleet_returns_fresh_objects(self):
        first, second = fleet(), fleet()
        assert first is not second
        assert first[0] is not second[0]

    def test_horizon_is_covered_by_the_tariff(self):
        latest = max(row[2] for row in FLEET_SPEC)
        assert len(hourly_tariff()) >= latest

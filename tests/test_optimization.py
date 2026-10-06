"""Smart Charging Scheduler -- the optimisation slice, pinned.

These tests are about behaviour an operator can be told, not about matrix
indices:

* the plan moves energy to cheap hours and only to cheap hours;
* the site budget is never exceeded, in any solve;
* a deadline that cannot be met says *whose* and *why*, and never passes
  silently -- which is the whole reason the result object carries ``binding``;
* ``peak_weight`` actually flattens the load;
* the LP differs from the rule the site runs today, and the difference is
  in the direction the sales pitch claims (cheaper);
* identical inputs give identical output.

Framework-free on purpose: none of this needs a request, a user or a row, so
the optimisation is tested without the four-second Django suite.
"""
from __future__ import annotations

import pytest

from core.site import TOTAL_POWER_KW
from optimization import (
    FLAT_RATE,
    TOU_HOURLY,
    Session,
    greedy_schedule,
    hourly_tariff,
    solve_schedule,
)
from optimization.solver import _deadline_problems, _overlap_hours, _slot_ranges

# Distinct prices per slot, so no ordering can be an artefact of a tie: the LP
# is free to pick any of several equal optima and a test must not depend on
# which one HiGHS happens to return first.
STEEP = [0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15]


def _cheap_at(slot: int) -> list[float]:
    prices = STEEP.copy()
    prices[slot] = 0.01
    return prices


def _energy(result, slot: int) -> float:
    """kWh scheduled in one slot across every session."""
    return sum(plan.power_kw[slot] for plan in result.sessions) * result.slot_hours


def _arrival_window_session(session_id: str, arrival: float, departure: float, **kw) -> Session:
    defaults = {"energy_needed_kwh": 40.0, "max_power_kw": 10.0}
    defaults.update(kw)
    return Session(
        session_id=session_id,
        arrival_hour=arrival,
        departure_hour=departure,
        **defaults,
    )


# --------------------------------------------------------------- price signal
class TestTariffResponse:
    def test_energy_moves_to_the_cheap_slot(self):
        """Making one hour cheap must pull charge into *that* hour, not a neighbour."""
        session = _arrival_window_session("A", 0.0, 8.0, energy_needed_kwh=20.0)

        at_three = solve_schedule([session], _cheap_at(3), capacity_kw=100.0)
        at_six = solve_schedule([session], _cheap_at(6), capacity_kw=100.0)

        # Each run fills its own cheap slot to the port limit, then buys the
        # second 10 kWh from the cheapest remaining hour -- which is 0.15 in
        # both tariffs, i.e. slot 7.
        assert _energy(at_three, 3) == pytest.approx(10.0)
        assert _energy(at_three, 7) == pytest.approx(10.0)
        assert _energy(at_three, 6) == pytest.approx(0.0)

        assert _energy(at_six, 6) == pytest.approx(10.0)
        assert _energy(at_six, 7) == pytest.approx(10.0)
        assert _energy(at_six, 3) == pytest.approx(0.0)

    def test_cheaper_hours_cost_less_than_expensive_ones(self):
        """The bill must fall when the tariff falls, with everything else fixed."""
        session = _arrival_window_session("A", 0.0, 8.0, energy_needed_kwh=20.0)

        expensive = solve_schedule([session], [1.00] * 8, capacity_kw=100.0)
        cheap = solve_schedule([session], [0.10] * 8, capacity_kw=100.0)

        assert cheap.energy_cost == pytest.approx(20 * 0.10)
        assert expensive.energy_cost == pytest.approx(20 * 1.00)
        assert cheap.energy_cost < expensive.energy_cost

    def test_flat_tariff_delivers_the_same_energy(self):
        """Price only moves *when* energy is delivered, never how much."""
        session = _arrival_window_session("A", 0.0, 8.0, energy_needed_kwh=20.0)
        result = solve_schedule([session], [FLAT_RATE] * 8, capacity_kw=100.0)
        assert result.scheduled_kwh == pytest.approx(20.0)
        assert result.feasible


# ------------------------------------------------------------------- capacity
class TestCapacity:
    def test_site_budget_is_never_exceeded(self):
        sessions = [
            _arrival_window_session("A", 0.0, 8.0, energy_needed_kwh=60.0),
            _arrival_window_session("B", 0.0, 8.0, energy_needed_kwh=60.0),
            _arrival_window_session("C", 4.0, 12.0, energy_needed_kwh=60.0),
        ]
        capacity = 45.0
        result = solve_schedule(sessions, hourly_tariff(), capacity_kw=capacity)

        assert max(result.site_load_kw) <= capacity + 1e-9
        assert result.feasible

    def test_each_port_is_capped_at_its_own_limit(self):
        session = _arrival_window_session("A", 0.0, 4.0, max_power_kw=11.0)
        result = solve_schedule([session], hourly_tariff(), capacity_kw=100.0)

        for _slot, kw in enumerate(result.plan("A").power_kw):
            assert kw <= 11.0 + 1e-9

    def test_shared_site_budget_is_the_default(self):
        """The scheduler and the prediction page must read one number."""
        from apps.prediction import views

        assert views.TOTAL_POWER_KW is TOTAL_POWER_KW
        assert TOTAL_POWER_KW == 100.0
        result = solve_schedule(
            [_arrival_window_session("A", 0.0, 8.0, energy_needed_kwh=60.0)],
            hourly_tariff(),
        )
        assert result.capacity_kw == TOTAL_POWER_KW
        assert max(result.site_load_kw) <= TOTAL_POWER_KW + 1e-9


# ------------------------------------------------------------------ deadlines
class TestDeadlines:
    def test_impossible_deadline_is_named_not_swallowed(self):
        """100 kWh through an 11 kW port in a 2 h window cannot happen.

        The result must say whose window failed and by how much -- a bare
        ``feasible=False`` would send the operator looking at the site when
        the fix is a longer stay.
        """
        session = Session("S1", 14.0, 16.0, energy_needed_kwh=100.0, max_power_kw=10.0)
        result = solve_schedule([session], hourly_tariff(), capacity_kw=100.0)

        assert not result.feasible
        assert any("S1" in reason and "deadline" in reason for reason in result.binding)
        # It still takes everything the window allows -- 10 kW x 2 h -- and
        # reports only the remainder as owed.
        assert result.plan("S1").scheduled_kwh == pytest.approx(20.0)
        assert result.plan("S1").shortfall_kwh == pytest.approx(80.0)
        assert not result.plan("S1").feasible

    def test_capacity_shortfall_is_attributed_to_capacity(self):
        """Two on-time requests the site physically cannot fit at once."""
        sessions = [
            _arrival_window_session("A", 0.0, 4.0, energy_needed_kwh=40.0),
            _arrival_window_session("B", 0.0, 4.0, energy_needed_kwh=40.0),
        ]
        result = solve_schedule(sessions, hourly_tariff(), capacity_kw=10.0)

        assert not result.feasible
        assert any("capacity" in reason for reason in result.binding)
        # Neither window is impossible alone, so neither is blamed for it.
        assert not any("deadline" in reason for reason in result.binding)
        assert result.shortfall_kwh == pytest.approx(40.0)
        assert max(result.site_load_kw) <= 10.0 + 1e-9
        assert result.capacity_bound

    def test_every_short_session_appears_in_binding(self):
        """The invariant: no shortfall without a stated reason."""
        sessions = [
            Session("over", 0.0, 3.0, energy_needed_kwh=90.0, max_power_kw=10.0),
            _arrival_window_session("tight", 0.0, 3.0, energy_needed_kwh=30.0),
        ]
        result = solve_schedule(sessions, hourly_tariff(), capacity_kw=20.0)

        assert not result.feasible
        named = " ".join(result.binding)
        for plan in result.sessions:
            if plan.shortfall_kwh > 1e-6:
                assert plan.session_id in named

    def test_a_feasible_schedule_has_no_shortfall_anywhere(self):
        sessions = [
            _arrival_window_session("A", 0.0, 8.0, energy_needed_kwh=40.0),
            _arrival_window_session("B", 6.0, 20.0, energy_needed_kwh=55.0),
        ]
        result = solve_schedule(sessions, hourly_tariff(), capacity_kw=100.0)

        assert result.feasible
        assert result.shortfall_kwh == pytest.approx(0.0)
        for plan in result.sessions:
            assert plan.scheduled_kwh == pytest.approx(plan.required_kwh)
            assert plan.feasible

    def test_zero_energy_request_is_trivially_met(self):
        result = solve_schedule(
            [_arrival_window_session("idle", 0.0, 4.0, energy_needed_kwh=0.0)],
            hourly_tariff(),
        )
        assert result.feasible
        assert result.plan("idle").scheduled_kwh == pytest.approx(0.0)


# --------------------------------------------------------------------- window
class TestWindow:
    def test_no_power_outside_the_stay(self):
        """8:30 to 17:30: nothing before 8, nothing from 18 on.

        A second session extends the horizon past 17:30 so the trailing
        assertion is checking real slots rather than slicing an empty tail.
        """
        sessions = [
            Session("W", 8.5, 17.5, energy_needed_kwh=30.0, max_power_kw=10.0),
            Session("late", 20.0, 23.0, energy_needed_kwh=0.0, max_power_kw=10.0),
        ]
        result = solve_schedule(sessions, hourly_tariff(), capacity_kw=100.0)
        power = result.plan("W").power_kw

        assert all(value == 0.0 for value in power[:8])
        assert all(value == 0.0 for value in power[18:])
        assert result.plan("W").first_slot == 8
        assert result.plan("W").stop_slot == 18
        assert sum(power) * result.slot_hours == pytest.approx(30.0)

    def test_partial_slots_are_proportionally_capped(self):
        """The half hour at each end is usable, but only for half a port."""
        session = Session("W", 8.5, 17.5, energy_needed_kwh=90.0, max_power_kw=10.0)
        result = solve_schedule([session], hourly_tariff(), capacity_kw=100.0)
        power = result.plan("W").power_kw

        assert power[8] <= 10.0 * 0.5 + 1e-9
        assert power[17] <= 10.0 * 0.5 + 1e-9
        # 10 kW x 9 h of stay: all of it is deliverable, so nothing is owed.
        assert result.feasible
        assert result.scheduled_kwh == pytest.approx(90.0)

    def test_overlap_sums_to_the_window(self):
        """Guards the exactness of the deadline check at any granularity."""
        session = Session("W", 8.5, 17.5, energy_needed_kwh=1.0, max_power_kw=10.0)
        total = sum(_overlap_hours(session, t, 1.0) for t in range(8, 18))
        assert total == pytest.approx(session.window_hours)

    def test_sub_slot_window_reports_honestly(self):
        """A 6-minute stay cannot fill a 60-minute slot -- say so, do not fake it."""
        session = Session("flash", 10.0, 10.1, energy_needed_kwh=5.0, max_power_kw=10.0)
        result = solve_schedule([session], hourly_tariff(), capacity_kw=100.0)

        assert not result.feasible
        assert any("flash" in reason for reason in result.binding)
        assert result.plan("flash").shortfall_kwh == pytest.approx(5.0 - 10.0 * 0.1)


# --------------------------------------------------------------- peak shaping
class TestPeakWeight:
    TARIFF = [0.01, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]

    @staticmethod
    def _fleet() -> list[Session]:
        return [
            Session(f"F{i}", 0.0, 8.0, energy_needed_kwh=20.0, max_power_kw=10.0)
            for i in range(3)
        ]

    def test_unpriced_peak_packs_the_cheap_hour(self):
        result = solve_schedule(self._fleet(), self.TARIFF, capacity_kw=100.0)
        assert result.peak_kw == pytest.approx(30.0)
        assert _energy(result, 0) == pytest.approx(30.0)
        assert result.peak_charge == 0.0

    def test_pricing_the_peak_flattens_the_load(self):
        flat = solve_schedule(self._fleet(), self.TARIFF, capacity_kw=100.0)
        shaped = solve_schedule(
            self._fleet(), self.TARIFF, capacity_kw=100.0, peak_weight=10.0
        )

        assert shaped.peak_kw < flat.peak_kw
        assert shaped.peak_kw == pytest.approx(7.5)
        assert shaped.peak_charge == pytest.approx(10.0 * 7.5)
        # Flattening is not free: the same 60 kWh, bought at the dear hour.
        assert shaped.energy_cost > flat.energy_cost

    def test_everyone_is_still_fully_charged(self):
        shaped = solve_schedule(
            self._fleet(), self.TARIFF, capacity_kw=100.0, peak_weight=10.0
        )
        assert shaped.feasible
        assert shaped.scheduled_kwh == pytest.approx(60.0)

    def test_zero_weight_never_adds_a_demand_charge(self):
        result = solve_schedule(self._fleet(), self.TARIFF, capacity_kw=100.0)
        assert result.cost == pytest.approx(result.energy_cost)


# ----------------------------------------------------------- greedy vs the LP
class TestAgainstGreedy:
    def test_lp_waits_for_cheap_power_and_greedy_does_not(self):
        """Both deliver; only one of them looks at the clock."""
        sessions = [
            _arrival_window_session("A", 8.0, 20.0, energy_needed_kwh=40.0),
            _arrival_window_session("B", 8.0, 20.0, energy_needed_kwh=40.0),
        ]
        tariff = hourly_tariff()
        greedy = greedy_schedule(sessions, tariff, capacity_kw=100.0)
        optimal = solve_schedule(sessions, tariff, capacity_kw=100.0)

        # Greedy front-loads into the 08:00-11:00 peak at 0.35.
        assert greedy.energy_cost == pytest.approx(80 * 0.35)
        assert _energy(greedy, 8) + _energy(greedy, 9) + _energy(greedy, 10) + _energy(greedy, 11) == pytest.approx(80.0)

        # The LP waits for the 12:00-17:00 shoulder at 0.20 and pays half as much.
        assert optimal.energy_cost == pytest.approx(80 * 0.20)
        assert sum(_energy(optimal, t) for t in (8, 9, 10, 11)) == pytest.approx(0.0)

        assert optimal.energy_cost < greedy.energy_cost
        assert greedy.feasible and optimal.feasible

    def test_greedy_misses_a_deadline_the_lp_meets(self):
        """First-come-first-served spends the budget before the deadline needs it.

        ``late`` must have 44 kWh through an 11 kW port in its last four
        hours -- that is exactly its window, so there is no slack. Greedy
        gives the earlier arrival the capacity first and leaves it 2 kWh
        short; the LP reserves what the deadline needs and charges the other
        session around it.
        """
        sessions = [
            _arrival_window_session("hog", 0.0, 12.0, energy_needed_kwh=100.0),
            Session("late", 8.0, 12.0, energy_needed_kwh=44.0, max_power_kw=11.0),
        ]
        tariff = hourly_tariff()
        greedy = greedy_schedule(sessions, tariff, capacity_kw=20.0)
        optimal = solve_schedule(sessions, tariff, capacity_kw=20.0)

        assert optimal.feasible
        assert not greedy.feasible
        assert optimal.plan("late").shortfall_kwh == pytest.approx(0.0)
        assert greedy.plan("late").shortfall_kwh > 0.0
        assert any("late" in reason for reason in greedy.binding)

    def test_both_return_the_same_shape_and_order(self):
        sessions = [
            _arrival_window_session("Z", 0.0, 8.0),
            _arrival_window_session("A", 2.0, 10.0),
        ]
        tariff = hourly_tariff()
        for result in (
            greedy_schedule(sessions, tariff),
            solve_schedule(sessions, tariff),
        ):
            assert [p.session_id for p in result.sessions] == ["Z", "A"]
            # The horizon runs to the *last* departure, not the first.
            assert len(result.site_load_kw) == 10
            assert result.tariff == tariff[:10]

    def test_greedy_respects_the_site_budget(self):
        sessions = [
            _arrival_window_session("A", 0.0, 6.0, energy_needed_kwh=50.0),
            _arrival_window_session("B", 0.0, 6.0, energy_needed_kwh=50.0),
        ]
        result = greedy_schedule(sessions, hourly_tariff(), capacity_kw=25.0)
        assert max(result.site_load_kw) <= 25.0 + 1e-9


# ---------------------------------------------------------------- determinism
class TestDeterminism:
    def test_identical_inputs_give_identical_plans(self):
        sessions = [
            _arrival_window_session("A", 0.0, 9.0, energy_needed_kwh=52.0),
            _arrival_window_session("B", 3.0, 14.0, energy_needed_kwh=47.0),
            _arrival_window_session("C", 7.0, 23.0, energy_needed_kwh=61.0),
        ]
        tariff = hourly_tariff()
        kwargs = {"capacity_kw": 40.0, "peak_weight": 2.0}

        first = solve_schedule(sessions, tariff, **kwargs)
        second = solve_schedule(sessions, tariff, **kwargs)

        assert first.site_load_kw == second.site_load_kw
        assert first.energy_cost == second.energy_cost
        assert first.binding == second.binding
        assert [p.power_kw for p in first.sessions] == [p.power_kw for p in second.sessions]

    def test_greedy_is_deterministic_too(self):
        sessions = [
            _arrival_window_session("B", 0.0, 6.0),
            _arrival_window_session("A", 0.0, 6.0),
        ]
        tariff = hourly_tariff()
        assert greedy_schedule(sessions, tariff).binding == greedy_schedule(
            sessions, tariff
        ).binding

    def test_session_order_is_preserved_not_sorted(self):
        sessions = [
            _arrival_window_session("Z", 5.0, 9.0),
            _arrival_window_session("A", 0.0, 4.0),
        ]
        result = solve_schedule(sessions, hourly_tariff())
        assert [p.session_id for p in result.sessions] == ["Z", "A"]


# -------------------------------------------------------------------- inputs
class TestInputValidation:
    @pytest.mark.parametrize(
        ("energy", "power_kw", "match"),
        [
            (10.0, 0.0, "max_power_kw"),
            (10.0, -5.0, "max_power_kw"),
            (-1.0, 10.0, "energy_needed_kwh"),
        ],
    )
    def test_malformed_session_is_rejected(self, energy, power_kw, match):
        session = Session("bad", 0.0, 4.0, energy, power_kw)
        with pytest.raises(ValueError, match=match):
            solve_schedule([session], hourly_tariff())

    def test_departure_before_arrival_is_rejected(self):
        with pytest.raises(ValueError, match="before arrival"):
            solve_schedule([Session("bad", 8.0, 4.0, 10.0, 10.0)], hourly_tariff())

    def test_empty_session_id_is_rejected(self):
        with pytest.raises(ValueError, match="session_id"):
            solve_schedule([Session("", 0.0, 4.0, 10.0, 10.0)], hourly_tariff())

    def test_short_tariff_is_rejected_rather_than_padded(self):
        """Padding with 0 would invent free power in the hours nobody priced."""
        with pytest.raises(ValueError, match="tariff covers"):
            solve_schedule(
                [_arrival_window_session("A", 0.0, 24.0)], hourly_tariff(hours=8)
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"slot_hours": 0.0},
            {"slot_hours": -1.0},
            {"capacity_kw": 0.0},
            {"peak_weight": -1.0},
        ],
    )
    def test_bad_solver_settings_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            solve_schedule([_arrival_window_session("A", 0.0, 4.0)], hourly_tariff(), **kwargs)

    def test_no_sessions_is_well_formed(self):
        result = solve_schedule([], hourly_tariff())
        assert result.feasible
        assert result.peak_kw == 0.0
        assert result.binding == []


# -------------------------------------------------------------------- tariff
class TestTariffTable:
    def test_day_table_matches_the_published_bands(self):
        assert len(TOU_HOURLY) == 24
        assert TOU_HOURLY[3] == 0.10   # overnight off-peak
        assert TOU_HOURLY[9] == 0.35   # morning peak
        assert TOU_HOURLY[14] == 0.20  # daytime shoulder
        assert TOU_HOURLY[20] == 0.35  # evening peak

    def test_multi_day_horizon_wraps_instead_of_running_off_the_end(self):
        two_days = hourly_tariff(48)
        assert len(two_days) == 48
        assert two_days[24] == TOU_HOURLY[0]
        assert two_days[47] == TOU_HOURLY[23]

    def test_flat_rate_is_the_mean_of_the_table(self):
        assert FLAT_RATE == pytest.approx(sum(TOU_HOURLY) / 24)
        assert hourly_tariff(6, flat=True) == [FLAT_RATE] * 6

    def test_empty_horizon_is_rejected(self):
        with pytest.raises(ValueError, match="horizon"):
            hourly_tariff(0)


# ------------------------------------------------------------------ internals
class TestInternals:
    def test_slot_ranges_collapses_runs(self):
        assert _slot_ranges([9, 3, 5, 4]) == "3-5, 9"
        assert _slot_ranges([7]) == "7"
        assert _slot_ranges([]) == ""

    def test_deadline_check_is_analytic(self):
        """The named reason must be derivable without running the solver."""
        impossible = Session("x", 0.0, 2.0, energy_needed_kwh=50.0, max_power_kw=10.0)
        possible = Session("y", 0.0, 8.0, energy_needed_kwh=50.0, max_power_kw=10.0)
        problems = _deadline_problems([impossible, possible])
        assert [i for i, _ in problems] == [0]
        assert "x" in problems[0][1]

"""The baseline: charge as soon as you can, as hard as you can.

This is not a straw man. It is the rule the site runs today in
``apps.prediction`` -- ``min(charging_rate, remaining_power)``, first come
first served -- lifted out so it can be executed over a horizon and priced
against the LP. Keeping it here, rather than re-deriving it inside the tests,
means the comparison on the results page is between two implementations of
two real policies, not between a policy and whatever the test author felt
like typing.

What it cannot do, and the LP can
---------------------------------
* It never waits. Every session charges at its port limit the moment it
  arrives, so a 08:00 arrival pays the morning peak even if it does not leave
  until 20:00.
* It cannot see the future. Capacity is handed out slot by slot in
  ``arrival`` order, so an early session can consume the budget an imminent
  deadline needed -- and greedy will miss that deadline without ever saying
  so. This module *does* say so: any session it under-delivers appears in
  ``binding``, exactly as in the LP result, so the two are comparable.
"""
from __future__ import annotations

from core.site import TOTAL_POWER_KW

from .session import Session
from .solver import (
    TOL,
    ScheduleResult,
    SessionPlan,
    _deadline_problems,
    _overlap_hours,
    _slot_ranges,
    _slots_needed,
    _window,
)


def greedy_schedule(
    sessions: list[Session],
    tariff: list[float],
    *,
    slot_hours: float = 1.0,
    capacity_kw: float | None = TOTAL_POWER_KW,
) -> ScheduleResult:
    """Run the site's current rule over the horizon and price the result.

    Returns the same ``ScheduleResult`` shape as ``solve_schedule`` so the two
    can be diffed field by field. ``peak_weight`` is deliberately absent:
    greedy has no notion of a peak, which is part of what it is being
    measured against.
    """
    for session in sessions:
        session.validate()
    if slot_hours <= 0:
        raise ValueError(f"slot_hours must be > 0, got {slot_hours}")
    if capacity_kw is not None and capacity_kw <= 0:
        raise ValueError(f"capacity_kw must be > 0 or None, got {capacity_kw}")

    horizon = _slots_needed(sessions, slot_hours)
    tariff = list(tariff)
    if len(tariff) < horizon:
        raise ValueError(
            f"tariff covers {len(tariff)} slots but the horizon needs {horizon}"
        )
    windows = [_window(s, slot_hours, horizon) for s in sessions]

    # Plug-in order, with the id as tie-break: two vehicles arriving on the
    # same hour must have a defined, reproducible order or the baseline is
    # not reproducible and the comparison means nothing.
    order = sorted(
        range(len(sessions)),
        key=lambda i: (sessions[i].arrival_hour, sessions[i].session_id),
    )
    owed = [s.energy_needed_kwh for s in sessions]
    power = [[0.0] * horizon for _ in sessions]
    saturated: set[int] = set()

    for t in range(horizon):
        free = capacity_kw if capacity_kw is not None else float("inf")
        for i in order:
            first, stop = windows[i]
            if not first <= t < stop:
                continue
            limit = sessions[i].max_power_kw * _overlap_hours(sessions[i], t, slot_hours) / slot_hours
            take = min(limit, free, owed[i] / slot_hours)
            if take <= TOL:
                continue
            power[i][t] = take
            free -= take
            owed[i] -= take * slot_hours
        if capacity_kw is not None and free <= TOL:
            saturated.add(t)

    site = [sum(power[i][t] for i in range(len(sessions))) for t in range(horizon)]
    peak = max(site, default=0.0)
    energy_cost = sum(
        tariff[t] * power[i][t] * slot_hours
        for i in range(len(sessions))
        for t in range(horizon)
        if power[i][t] > TOL
    )

    plans = []
    unexplained = 0.0
    for i, session in enumerate(sessions):
        scheduled = sum(power[i]) * slot_hours
        raw = session.energy_needed_kwh - scheduled
        shortfall = raw if raw > TOL else 0.0
        unexplained += shortfall
        first, stop = windows[i]
        plans.append(
            SessionPlan(
                session_id=session.session_id,
                arrival_hour=session.arrival_hour,
                departure_hour=session.departure_hour,
                required_kwh=session.energy_needed_kwh,
                scheduled_kwh=scheduled,
                shortfall_kwh=shortfall,
                power_kw=power[i],
                first_slot=first,
                stop_slot=stop,
                feasible=shortfall <= TOL,
            )
        )

    deadline_problems = _deadline_problems(sessions)
    feasible = unexplained <= TOL and not deadline_problems
    binding: list[str] = []
    for plan in plans:
        if plan.shortfall_kwh > TOL:
            binding.append(
                f"session {plan.session_id!r} falls {plan.shortfall_kwh:g} kWh short of "
                "its deadline under first-come, first-served"
            )
    binding.extend(text for _, text in deadline_problems)
    if saturated:
        binding.append(
            f"site capacity {(capacity_kw if capacity_kw is not None else peak):g} kW "
            f"saturated at slot(s) {_slot_ranges(saturated)}"
        )

    return ScheduleResult(
        feasible=feasible,
        binding=binding,
        peak_kw=peak,
        energy_cost=energy_cost,
        peak_charge=0.0,
        sessions=plans,
        site_load_kw=site,
        slot_hours=slot_hours,
        tariff=tariff[:horizon],
        capacity_kw=capacity_kw,
        peak_weight=0.0,
        status=0 if feasible else 2,
        message="first-come, first-served at each port limit",
    )

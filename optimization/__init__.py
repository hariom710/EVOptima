"""Smart Charging Scheduler -- roadmap item #1, first slice.

Framework-free by design: ``Session``, ``solve_schedule`` and
``greedy_schedule`` know nothing about Django, the database or the model
registry, so the optimisation can be tested, timed and reasoned about on its
own, then called from a view, a management command or the simulation tick.

Typical use::

    from optimization import Session, hourly_tariff, solve_schedule

    result = solve_schedule(
        [Session("A", 8.0, 20.0, energy_needed_kwh=40, max_power_kw=11)],
        hourly_tariff(),
    )
    if not result.feasible:
        print(result.binding)  # names the constraint that failed

The three pieces:

* :mod:`optimization.session`   -- one vehicle's request, in hours.
* :mod:`optimization.tariff`    -- what a kWh costs in each slot.
* :mod:`optimization.solver`    -- the LP, and the named-binding report.
* :mod:`optimization.baseline`  -- the site's current rule, for comparison.
"""
from __future__ import annotations

from .baseline import greedy_schedule
from .session import Session
from .solver import ScheduleResult, SessionPlan, solve_schedule
from .tariff import FLAT_RATE, TOU_HOURLY, hourly_tariff

__all__ = [
    "FLAT_RATE",
    "TOU_HOURLY",
    "ScheduleResult",
    "Session",
    "SessionPlan",
    "greedy_schedule",
    "hourly_tariff",
    "solve_schedule",
]

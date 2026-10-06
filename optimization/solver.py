"""The scheduler: turn a set of charging requests into a per-slot power plan.

A linear program. ``scipy.optimize.linprog`` ships with SciPy, which this
project already depends on for the ML pipeline, so the scheduler adds no new
dependency and no new service -- the same wheel that fits the model fits the
plan.

Why an LP rather than a smarter heuristic
-----------------------------------------
The greedy rule in ``apps.prediction`` (take the highest rate you can, right
now) is the correct behaviour for an operator watching one port. It is the
wrong behaviour for a site: it answers "who charges first", never "when is
power cheap", and it cannot see that two sessions arriving at 08:00 and 14:00
can share one capacity-limited window. The LP answers both at once because
price, capacity, per-port limits and deadlines are all just rows of one
matrix. Nothing here is heuristic, so ``solve_schedule`` is deterministic:
identical inputs give identical schedules, which is what makes the
greedy-vs-optimised comparison worth reading.

Two ways to fail, named separately
----------------------------------
A schedule can miss a deadline because the *session* asked for more than its
port and window can deliver, or because the *site* was too busy. Collapsing
those into one "infeasible" is the failure this code exists to avoid: the
first is a request to renegotiate, the second is a request to defer. So
``binding`` names which one it was, and every session with a shortfall
appears in it -- a deadline is never missed silently.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import OptimizeResult, linprog

from core.site import TOTAL_POWER_KW

from .session import Session

#: Tolerance for "is this constraint active?" and "is this shortfall real?".
#: HiGHS returns solutions accurate to ~1e-9 relative; anything below this is
#: solver noise and must not surface as a reported 4e-13 kWh shortfall.
TOL = 1e-6


@dataclass(frozen=True)
class SessionPlan:
    """What one session was given, and what it still owes."""

    session_id: str
    arrival_hour: float
    departure_hour: float
    required_kwh: float
    scheduled_kwh: float
    shortfall_kwh: float
    #: Mean kW drawn in each slot of the horizon, zero outside the window.
    power_kw: list[float]
    first_slot: int
    stop_slot: int  # exclusive
    feasible: bool


@dataclass(frozen=True)
class ScheduleResult:
    """A plan, plus the evidence for why it is shaped the way it is.

    ``binding`` is the field that makes this auditable. When the LP cannot
    meet every deadline it names the row responsible -- a session's own
    window, or the site capacity -- instead of returning a shrug. When it
    succeeds, it lists the constraints active at the optimum, which is how an
    operator learns that the next session will not fit.
    """

    feasible: bool
    binding: list[str]
    peak_kw: float
    #: Tariff cost of the energy actually scheduled, currency.
    energy_cost: float
    #: ``peak_weight x peak_kw`` -- zero unless the caller priced the peak.
    peak_charge: float
    sessions: list[SessionPlan]
    site_load_kw: list[float]
    slot_hours: float
    tariff: list[float]
    capacity_kw: float | None
    peak_weight: float
    status: int
    message: str

    @property
    def cost(self) -> float:
        """Exactly the objective the solver minimised."""
        return self.energy_cost + self.peak_charge

    @property
    def required_kwh(self) -> float:
        return sum(s.required_kwh for s in self.sessions)

    @property
    def scheduled_kwh(self) -> float:
        return sum(s.scheduled_kwh for s in self.sessions)

    @property
    def shortfall_kwh(self) -> float:
        return sum(s.shortfall_kwh for s in self.sessions)

    @property
    def capacity_bound(self) -> bool:
        """True when some slot reached the site capacity exactly."""
        if self.capacity_kw is None:
            return False
        return any(load >= self.capacity_kw - TOL for load in self.site_load_kw)

    def plan(self, session_id: str) -> SessionPlan:
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        raise KeyError(session_id)


# --------------------------------------------------------------------- layout
def _slots_needed(sessions: list[Session], slot_hours: float) -> int:
    """Slots required to cover every session's window, partial ones included.

    A window ending at 17:30 still needs slot 17 (it covers 17:00-18:00) --
    truncating to whole slots would throw away the last half hour of charge
    and then report the session as infeasible for a reason that does not
    exist.
    """
    eps = 1e-9
    horizon = 0
    for session in sessions:
        horizon = max(horizon, math.ceil(session.departure_hour / slot_hours - eps))
        horizon = max(horizon, math.ceil(session.arrival_hour / slot_hours - eps))
    return max(horizon, 0)


def _window(session: Session, slot_hours: float, horizon: int) -> tuple[int, int]:
    """``(first_slot, stop_slot)``: the slots that overlap ``session`` at all.

    Half-open, so ``range(first, stop)`` is the session's editable region and
    everything outside it is hard-zero.
    """
    eps = 1e-9
    first = math.floor(session.arrival_hour / slot_hours + eps)
    stop = math.ceil(session.departure_hour / slot_hours - eps)
    first = max(first, 0)
    stop = min(max(stop, first), horizon)
    return first, stop


def _overlap_hours(session: Session, slot: int, slot_hours: float) -> float:
    """Hours of ``session``'s stay that fall inside ``slot``.

    This is what makes a 17:30 departure usable: the last slot is only
    chargeable for the half hour the vehicle is still plugged in, so its
    power bound is halved rather than the slot being dropped or taken whole.
    The overlaps sum to exactly ``session.window_hours``, so a session's
    deadline check stays exact at any slot granularity.
    """
    start = max(session.arrival_hour, slot * slot_hours)
    stop = min(session.departure_hour, (slot + 1) * slot_hours)
    return max(stop - start, 0.0)


def _slot_ranges(slots) -> str:
    """``[3, 4, 5, 9]`` -> ``"3-5, 9"`` -- readable in a template and a test."""
    ordered = sorted(slots)
    out: list[str] = []
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1] == ordered[end] + 1:
            end += 1
        out.append(str(ordered[index]) if end == index else f"{ordered[index]}-{ordered[end]}")
        index = end + 1
    return ", ".join(out)


def _build(
    sessions: list[Session],
    windows: list[tuple[int, int]],
    horizon: int,
    slot_hours: float,
    tariff: list[float],
    capacity_kw: float | None,
    peak_weight: float,
    elastic: bool,
) -> tuple:
    """Assemble the LP matrices. Shared by the real solve and its diagnosis.

    ``elastic=True`` adds one non-negative slack per session to the deadline
    row, turning "impossible" into "short by this much". It is only ever run
    after the tight solve failed, so the honest answer -- a hard deadline --
    is what gets reported first.
    """
    n_sessions = len(sessions)
    col_of: dict[tuple[int, int], int] = {}
    ordered: list[tuple[int, int]] = []
    caps: list[float] = []
    for i, (first, stop) in enumerate(windows):
        for t in range(first, stop):
            overlap = _overlap_hours(sessions[i], t, slot_hours)
            if overlap <= TOL:
                continue  # no usable time in this slot; drop the variable
            col_of[(i, t)] = len(ordered)
            ordered.append((i, t))
            caps.append(sessions[i].max_power_kw * overlap / slot_hours)

    n_p = len(ordered)
    z_col = n_p if peak_weight > 0 else None
    if not elastic:
        slack_col = None
    elif z_col is not None:
        slack_col = n_p + 1
    else:
        slack_col = n_p
    n_vars = n_p + (1 if z_col is not None else 0) + (n_sessions if elastic else 0)

    cols_at: list[list[int]] = [[] for _ in range(horizon)]
    for (_i, t), col in col_of.items():
        cols_at[t].append(col)

    # ---------------------------------------------------------- objective
    c = np.zeros(n_vars)
    for (_i, t), col in col_of.items():
        c[col] = tariff[t] * slot_hours
    if z_col is not None:
        c[z_col] = peak_weight
    if slack_col is not None:
        # Heavily outweigh any plausible energy cost, so the elastic solve
        # minimises unmet energy *first* and only then the bill. This is a
        # diagnosis, not a pricing decision: it reports who falls shortest,
        # it does not choose to starve anyone cheaply.
        total = sum(s.energy_needed_kwh for s in sessions)
        biggest_bill = max(tariff[:horizon], default=0.0) * total + 1.0
        for i in range(n_sessions):
            c[slack_col + i] = 1000.0 * biggest_bill

    # ------------------------------------------------------------- bounds
    bounds = [(0.0, cap) for cap in caps]
    if z_col is not None:
        bounds.append((0.0, None))
    if slack_col is not None:
        bounds.extend([(0.0, None)] * n_sessions)

    # -------------------------------------------------------- inequalities
    rows: list[dict[int, float]] = []
    rhs: list[float] = []
    if capacity_kw is not None:
        for t in range(horizon):
            if cols_at[t]:
                rows.append({col: 1.0 for col in cols_at[t]})
                rhs.append(capacity_kw)
    for i, (first, stop) in enumerate(windows):
        row: dict[int, float] = {}
        for t in range(first, stop):
            col = col_of.get((i, t))
            if col is not None:
                row[col] = -slot_hours
        if slack_col is not None:
            row[slack_col + i] = -1.0
        rows.append(row)
        rhs.append(-sessions[i].energy_needed_kwh)
    if z_col is not None:
        for t in range(horizon):
            if cols_at[t]:
                row = {col: 1.0 for col in cols_at[t]}
                row[z_col] = -1.0
                rows.append(row)
                rhs.append(0.0)

    a_ub = np.zeros((len(rows), n_vars))
    for r, row in enumerate(rows):
        for col, value in row.items():
            a_ub[r, col] = value
    return (
        c,
        a_ub,
        np.asarray(rhs, dtype=float),
        bounds,
        col_of,
        ordered,
        z_col,
        slack_col,
    )


def _run(c, a_ub, b_ub, bounds) -> OptimizeResult | None:
    """Solve, or return ``None`` when there is nothing to decide.

    ``None`` is not success: zero variables means no session has any usable
    time, and the caller must still explain the resulting shortfall.
    """
    if a_ub.shape[1] == 0:
        return None
    return linprog(c, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")


def _deadline_problems(
    sessions: list[Session],
) -> list[tuple[int, str]]:
    """Sessions whose own window cannot hold their request.

    Checked analytically rather than by reading the solver's status, so the
    message can quote the number that fails. ``session.deliverable_kwh`` is
    the ceiling for a session alone -- ``max_power x window`` -- and because
    the per-slot bounds sum to exactly that, no discretisation can make a
    session miss a deadline it could have met.
    """
    problems = []
    for i, session in enumerate(sessions):
        if session.energy_needed_kwh <= TOL:
            continue
        if session.deliverable_kwh + TOL < session.energy_needed_kwh:
            problems.append(
                (
                    i,
                    f"session {session.session_id!r} deadline: needs "
                    f"{session.energy_needed_kwh:g} kWh but {session.window_hours:g} h at "
                    f"{session.max_power_kw:g} kW delivers at most "
                    f"{session.deliverable_kwh:g} kWh",
                )
            )
    return problems


def _read(
    x: np.ndarray,
    sessions: list[Session],
    horizon: int,
    slot_hours: float,
    tariff: list[float],
    ordered: list[tuple[int, int]],
    col_of: dict[tuple[int, int], int],
) -> tuple[list[list[float]], list[float], float, float]:
    """Turn a solver vector into per-session power, site load, peak and bill."""
    power = [[0.0] * horizon for _ in sessions]
    for (i, t), col in col_of.items():
        value = float(x[col])
        power[i][t] = 0.0 if abs(value) < TOL else max(value, 0.0)
    site = [sum(power[i][t] for i in range(len(sessions))) for t in range(horizon)]
    peak = max(site, default=0.0)
    energy_cost = sum(tariff[t] * power[i][t] * slot_hours for (i, t) in ordered)
    return power, site, peak, energy_cost


# ------------------------------------------------------------------- solver
def solve_schedule(
    sessions: list[Session],
    tariff: list[float],
    *,
    slot_hours: float = 1.0,
    capacity_kw: float | None = TOTAL_POWER_KW,
    peak_weight: float = 0.0,
) -> ScheduleResult:
    """Find the cheapest per-slot power plan that meets every deadline.

    Parameters
    ----------
    sessions:
        The requests. Order is preserved in the output, so
        ``result.sessions[i]`` always describes ``sessions[i]``.
    tariff:
        Cost per kWh for each slot of the horizon. See
        ``optimization.tariff.hourly_tariff``.
    slot_hours:
        Granularity of the plan. 1.0 is the default because the published
        tariff is hourly; smaller values cost more variables, not more
        accuracy on data that is hourly to begin with.
    capacity_kw:
        Site budget shared by every port at once. ``None`` removes the
        constraint -- never a way to sneak past a real limit, but it keeps the
        matrix builder honest about what it can switch off.
    peak_weight:
        Lambda: extra cost charged per kW of site peak. Raising it trades bill
        for flatness, which is how a demand charge is expressed. Set it to 0
        to minimise the energy bill alone.

    Returns
    -------
    ScheduleResult
        Always populated -- an infeasible request still yields a best-effort
        plan plus the named reason it falls short, because a UI that shows
        "no solution" has thrown away the one thing the operator needed.
    """
    sessions = list(sessions)
    for session in sessions:
        session.validate()
    if slot_hours <= 0:
        raise ValueError(f"slot_hours must be > 0, got {slot_hours}")
    if capacity_kw is not None and capacity_kw <= 0:
        raise ValueError(f"capacity_kw must be > 0 or None, got {capacity_kw}")
    if peak_weight < 0:
        raise ValueError(f"peak_weight must be >= 0, got {peak_weight}")

    horizon = _slots_needed(sessions, slot_hours)
    tariff = list(tariff)
    if len(tariff) < horizon:
        raise ValueError(
            f"tariff covers {len(tariff)} slots but the horizon needs {horizon}"
        )
    windows = [_window(s, slot_hours, horizon) for s in sessions]
    deadline_problems = _deadline_problems(sessions)

    packed = _build(
        sessions, windows, horizon, slot_hours, tariff, capacity_kw, peak_weight, elastic=False
    )
    c, a_ub, b_ub, bounds, col_of, ordered, z_col, slack_col = packed
    result = _run(c, a_ub, b_ub, bounds)
    if result is None:
        # Nothing to schedule: either no sessions, or none with usable time.
        status = 2 if deadline_problems else 0
        message = "no chargeable slots in the horizon"
    else:
        status = int(result.status)
        message = str(result.message)

    elastic = False
    if status != 0:
        elastic = True
        packed = _build(
            sessions,
            windows,
            horizon,
            slot_hours,
            tariff,
            capacity_kw,
            peak_weight,
            elastic=True,
        )
        c, a_ub, b_ub, bounds, col_of, ordered, z_col, slack_col = packed
        relaxed = _run(c, a_ub, b_ub, bounds)
        if relaxed is None or int(relaxed.status) != 0:
            return _hopeless(
                sessions,
                windows,
                horizon,
                slot_hours,
                tariff,
                capacity_kw,
                peak_weight,
                deadline_problems,
                status,
                f"{message} (no relaxation could schedule anything)",
            )
        result = relaxed
        message = f"{message} (relaxed: deadlines reported as shortfalls)"

    x = result.x if result is not None else np.zeros(len(c))
    power, site, peak, energy_cost = _read(x, sessions, horizon, slot_hours, tariff, ordered, col_of)

    feasible = status == 0 and not deadline_problems
    binding = _binding(
        sessions,
        windows,
        power,
        site,
        horizon,
        slot_hours,
        capacity_kw,
        deadline_problems,
        feasible,
    )

    plans = []
    for i, session in enumerate(sessions):
        scheduled = sum(power[i]) * slot_hours
        # Only trust a shortfall when the LP was actually relaxed: a tight
        # solve already enforced the deadline as a hard row, so any residual
        # is rounding and reporting it would be a lie.
        raw = session.energy_needed_kwh - scheduled
        shortfall = raw if elastic and raw > TOL else 0.0
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

    return ScheduleResult(
        feasible=feasible,
        binding=binding,
        peak_kw=peak,
        energy_cost=energy_cost,
        peak_charge=peak_weight * peak,
        sessions=plans,
        site_load_kw=site,
        slot_hours=slot_hours,
        tariff=tariff[:horizon],
        capacity_kw=capacity_kw,
        peak_weight=peak_weight,
        status=status,
        message=message,
    )


def _hopeless(
    sessions: list[Session],
    windows: list[tuple[int, int]],
    horizon: int,
    slot_hours: float,
    tariff: list[float],
    capacity_kw: float | None,
    peak_weight: float,
    deadline_problems: list[tuple[int, str]],
    status: int,
    message: str,
) -> ScheduleResult:
    """A plan for a request even the relaxed LP could not place.

    Every session keeps its window and owes its full requirement; the reason
    it is owed is what the caller actually needs to see.
    """
    reasons = [text for _, text in deadline_problems] or ["no feasible schedule exists"]
    plans = [
        SessionPlan(
            session_id=s.session_id,
            arrival_hour=s.arrival_hour,
            departure_hour=s.departure_hour,
            required_kwh=s.energy_needed_kwh,
            scheduled_kwh=0.0,
            shortfall_kwh=s.energy_needed_kwh,
            power_kw=[0.0] * horizon,
            first_slot=first,
            stop_slot=stop,
            feasible=s.energy_needed_kwh <= TOL,
        )
        for s, (first, stop) in zip(sessions, windows, strict=True)
    ]
    return ScheduleResult(
        feasible=False,
        binding=reasons,
        peak_kw=0.0,
        energy_cost=0.0,
        peak_charge=0.0,
        sessions=plans,
        site_load_kw=[0.0] * horizon,
        slot_hours=slot_hours,
        tariff=tariff[:horizon],
        capacity_kw=capacity_kw,
        peak_weight=peak_weight,
        status=status,
        message=message,
    )


def _binding(
    sessions: list[Session],
    windows: list[tuple[int, int]],
    power: list[list[float]],
    site: list[float],
    horizon: int,
    slot_hours: float,
    capacity_kw: float | None,
    deadline_problems: list[tuple[int, str]],
    feasible: bool,
) -> list[str]:
    """Name the rows that constrain this solution.

    Infeasible: the reason it failed -- the session's own window, the site
    capacity, or both when both are true. Capacity is only blamed for the
    sessions whose own window *could* have held them, so a session that asked
    for the impossible is never hidden behind a site-wide excuse.

    Feasible: which constraints are already active, so the caller can see that
    the *next* session will not fit rather than discovering it on arrival.
    """
    if not feasible:
        reasons = [text for _, text in deadline_problems]
        missed = [i for i, _ in deadline_problems]
        total = sum(s.energy_needed_kwh for s in sessions)
        unexplained = 0.0
        for i, session in enumerate(sessions):
            if i in missed:
                continue
            owed = session.energy_needed_kwh - sum(power[i]) * slot_hours
            if owed > TOL:
                unexplained += owed
        if unexplained > TOL:
            if capacity_kw is not None:
                reasons.append(
                    f"site capacity {capacity_kw:g} kW binds: {unexplained:g} kWh of the "
                    f"{total:g} kWh requested cannot be delivered before departure"
                )
            else:
                reasons.append(
                    f"{unexplained:g} kWh of the {total:g} kWh requested "
                    "cannot be delivered before departure"
                )
        return reasons

    reasons = []
    if capacity_kw is not None:
        saturated = [t for t in range(horizon) if site[t] >= capacity_kw - TOL]
        if saturated:
            reasons.append(
                f"site capacity {capacity_kw:g} kW saturated at slot(s) "
                f"{_slot_ranges(saturated)}"
            )
    for i, session in enumerate(sessions):
        first, stop = windows[i]
        editable = [t for t in range(first, stop) if _overlap_hours(session, t, slot_hours) > TOL]
        if not editable:
            continue
        pegged = all(
            power[i][t]
            >= session.max_power_kw * _overlap_hours(session, t, slot_hours) / slot_hours - TOL
            for t in editable
        )
        if pegged:
            reasons.append(
                f"session {session.session_id!r} at its {session.max_power_kw:g} kW limit"
            )
    return reasons

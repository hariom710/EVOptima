"""The unit the scheduler reasons about: one vehicle at one charger.

Deliberately framework-free -- no Django import, no model, no database. A
``Session`` is a plain value object built from whatever the caller already
knows (a form submission, a queue row, a simulation tick), which keeps the
solver testable without a request, a user, or a migration.

Hours, not slots. Callers think in ``"arrives 08:30, leaves 17:00"``; the
solver converts to slot indices once, so a change of ``slot_hours`` cannot
silently shift a session's window.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    """One vehicle's charging request over a wall-clock window.

    ``arrival_hour``/``departure_hour`` are hours since the start of the
    planning horizon (midnight for a daily plan), so a session arriving at
    08:30 and leaving at 17:00 is ``arrival_hour=8.5, departure_hour=17``.

    ``energy_needed_kwh`` is the *deficit*, not the battery size: what has to
    cross the port before ``departure_hour``. ``max_power_kw`` is the port's
    limit (the vehicle's on-board charger or the session's requested rate),
    not the site's -- the site limit is a constraint on the *sum*, and lives
    with the solver.
    """

    session_id: str
    arrival_hour: float
    departure_hour: float
    energy_needed_kwh: float
    max_power_kw: float

    @property
    def window_hours(self) -> float:
        """Hours between plug-in and plug-out. Zero or negative = no window."""
        return self.departure_hour - self.arrival_hour

    @property
    def deliverable_kwh(self) -> float:
        """Upper bound on what this session could take *alone*.

        ``max_power_kw x window_hours``. Compared against
        ``energy_needed_kwh`` to separate the two ways a schedule can fail:
        a deadline the session could never meet even with the whole site to
        itself, versus a deadline that only fails because other sessions are
        competing for the same capacity. The distinction is what lets the
        solver name the binding constraint instead of shrugging.
        """
        return self.max_power_kw * max(self.window_hours, 0.0)

    def validate(self) -> None:
        """Reject malformed input; scheduling failures are reported, not raised.

        A negative rate or a non-positive energy request is the caller's bug,
        not a condition the LP can resolve -- ``linprog`` would happily return
        an empty schedule for it and the site would silently charge nobody.
        """
        if not self.session_id:
            raise ValueError("session_id must be a non-empty string")
        if self.max_power_kw <= 0:
            raise ValueError(
                f"session {self.session_id!r}: max_power_kw must be > 0, got {self.max_power_kw}"
            )
        if self.energy_needed_kwh < 0:
            raise ValueError(
                f"session {self.session_id!r}: energy_needed_kwh must be >= 0, "
                f"got {self.energy_needed_kwh}"
            )
        if self.departure_hour < self.arrival_hour:
            raise ValueError(
                f"session {self.session_id!r}: departure {self.departure_hour} is before "
                f"arrival {self.arrival_hour}"
            )

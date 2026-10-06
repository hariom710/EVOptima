"""The scenario `/scheduling/` runs.

A hand-built fleet, not a query. EVOptima records measurements, not
bookings: there is no arrival, departure or requested-energy column anywhere
in the supplied CSVs, and inventing one would put fiction in the model. So
the page says plainly that it is a demonstration scenario, and the numbers it
prints are real -- every one of them comes out of ``solve_schedule`` and
``greedy_schedule`` run over this list at the live site capacity.

The fleet is shaped to make both failure modes visible at once:

* ``EV-06`` asks for exactly what its window allows (50 kW x 3 h = 150 kWh),
  so it has no slack at all and competes for capacity it does not control;
* ``EV-02`` has a four-hour window and a lot to buy, so it must be served
  during the expensive morning band;
* the rest are flexible, which is where the saving comes from -- an
  inflexible session cannot be made cheaper, only moved around.
"""
from __future__ import annotations

from optimization import Session

#: ``(id, arrival, departure, kWh, max kW)`` -- see module docstring for why
#: each window is what it is.
FLEET_SPEC: tuple[tuple[str, float, float, float, float], ...] = (
    ("EV-01", 7.0, 17.0, 120.0, 50.0),
    ("EV-02", 8.0, 12.0, 140.0, 50.0),
    ("EV-03", 8.5, 18.0, 90.0, 22.0),
    ("EV-07", 9.0, 20.0, 200.0, 50.0),
    ("EV-04", 12.0, 22.0, 150.0, 50.0),
    ("EV-06", 13.0, 16.0, 150.0, 50.0),
    ("EV-05", 17.0, 23.0, 120.0, 50.0),
)


def fleet() -> list[Session]:
    """Fresh ``Session`` objects, so a caller can mutate them safely."""
    return [
        Session(
            session_id=sid,
            arrival_hour=arrival,
            departure_hour=departure,
            energy_needed_kwh=energy,
            max_power_kw=max_kw,
        )
        for sid, arrival, departure, energy, max_kw in FLEET_SPEC
    ]

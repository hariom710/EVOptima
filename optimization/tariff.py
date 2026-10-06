"""Time-of-use pricing: what a kilowatt-hour costs in each slot.

A static table, deliberately. The roadmap item this scheduler implements
(#1 Smart Charging Scheduler) is priced from a published tariff; *forecasting*
the price is a separate, later item, and folding a forecast in here would
make every schedule non-reproducible the moment the forecast moved.

Rates are currency per kWh and are the only thing the objective needs: the
solver charges ``tariff[slot] x kW x slot_hours`` for every unit it schedules.
"""
from __future__ import annotations

#: Published residential time-of-use bands, by hour of day (0-23).
#:
#: Three bands, because that is what the shape of a real day looks like and
#: what a schedule has to actually exploit: overnight is cheap (demand is low
#: and the grid is not price-constrained), the morning and evening ramps are
#: expensive (the utility pays more for every marginal kW), and the middle of
#: the day sits between them.
TOU_HOURLY = (
    # 00-06 overnight: off-peak
    0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10,
    # 07 morning ramp: shoulder
    0.20,
    # 08-11 morning peak
    0.35, 0.35, 0.35, 0.35,
    # 12-17 daytime: shoulder
    0.20, 0.20, 0.20, 0.20, 0.20, 0.20,
    # 18-22 evening peak
    0.35, 0.35, 0.35, 0.35, 0.35,
    # 23 late night: off-peak
    0.10,
)

#: Mean of the table above, used when a caller wants a schedule that ignores
#: price but still pays a well-defined cost (comparisons, regressions).
FLAT_RATE = sum(TOU_HOURLY) / len(TOU_HOURLY)


def hourly_tariff(hours: int = 24, flat: bool = False) -> list[float]:
    """Cost per kWh for each hour of a ``hours``-long horizon.

    Hours past midnight wrap through the table, so a 48-hour plan sees the
    same day twice rather than running off the end of the tuple.
    """
    if hours <= 0:
        raise ValueError(f"horizon must be > 0 hours, got {hours}")
    if flat:
        return [FLAT_RATE] * hours
    return [TOU_HOURLY[h % len(TOU_HOURLY)] for h in range(hours)]

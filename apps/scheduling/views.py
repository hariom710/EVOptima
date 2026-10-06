"""Smart Charging Scheduler (roadmap #1): greedy versus optimised, side by side.

The page exists to answer one question honestly: *what does the optimiser
actually buy?* So it runs both policies over the same fleet at the same
capacity and prints both bills -- never a saving computed against a straw man
the site does not run.

Everything is derived in the view from ``optimization``; the template only
formats. No database writes, no model registry: the scheduler is pure
arithmetic, and a GET that changes no state cannot corrupt anything.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from core.site import TOTAL_POWER_KW
from optimization import greedy_schedule, hourly_tariff, solve_schedule

from .fleet import fleet

#: Hours priced in the expensive band. Shown as its own column because
#: "cheaper" is only meaningful once the reader can see *when* the energy was
#: bought, not just what it cost.
PEAK_RATE = 0.35


def _form(value, low, high, default):
    """Clamp a query-string number into range; anything unparsable is default."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, low), high)


def _hourly_rows(greedy, optimal) -> list[dict]:
    """One row per slot: tariff and both plans' site load."""
    rows = []
    for slot, rate in enumerate(optimal.tariff):
        greedy_kw = greedy.site_load_kw[slot] if slot < len(greedy.site_load_kw) else 0.0
        optimal_kw = optimal.site_load_kw[slot]
        rows.append(
            {
                "slot": slot,
                "hour": f"{slot:02d}:00",
                "rate": rate,
                "is_peak": rate >= PEAK_RATE,
                "greedy_kw": round(greedy_kw, 2),
                "optimal_kw": round(optimal_kw, 2),
                "delta_kw": round(greedy_kw - optimal_kw, 2),
            }
        )
    return rows


def _session_rows(sessions, greedy, optimal) -> list[dict]:
    """One row per vehicle: what each policy gave it, and what it still owes."""
    peak_slots = [i for i, rate in enumerate(optimal.tariff) if rate >= PEAK_RATE]
    rows = []
    for session, before, after in zip(sessions, greedy.sessions, optimal.sessions, strict=True):
        rows.append(
            {
                "session_id": session.session_id,
                "window": f"{session.arrival_hour:g}-{session.departure_hour:g}",
                "required_kwh": round(session.energy_needed_kwh, 1),
                "port_kw": session.max_power_kw,
                "greedy_kwh": round(before.scheduled_kwh, 1),
                "greedy_short": round(before.shortfall_kwh, 1),
                "optimal_kwh": round(after.scheduled_kwh, 1),
                "optimal_short": round(after.shortfall_kwh, 1),
                "greedy_peak_kwh": round(
                    sum(before.power_kw[s] for s in peak_slots) * optimal.slot_hours, 1
                ),
                "optimal_peak_kwh": round(
                    sum(after.power_kw[s] for s in peak_slots) * optimal.slot_hours, 1
                ),
            }
        )
    return rows


def _peak_band_kwh(result) -> float:
    """kWh this plan buys in the expensive band."""
    return sum(
        plan.power_kw[slot] * result.slot_hours
        for plan in result.sessions
        for slot, rate in enumerate(result.tariff)
        if rate >= PEAK_RATE
    )


def _summary(greedy, optimal) -> dict:
    """The four numbers the page leads with, each computed from both plans."""
    saved = greedy.energy_cost - optimal.energy_cost
    greedy_peak_kwh = _peak_band_kwh(greedy)
    optimal_peak_kwh = _peak_band_kwh(optimal)
    return {
        "greedy_cost": greedy.energy_cost,
        "optimal_cost": optimal.energy_cost,
        "saved": saved,
        # Only ever read when ``saved <= 0``; keeping the sign out of the
        # sentence avoids "costs -26.57 more", which reads as a saving.
        "extra": -saved,
        "saved_pct": (saved / greedy.energy_cost * 100.0) if greedy.energy_cost else 0.0,
        "greedy_peak": greedy.peak_kw,
        "optimal_peak": optimal.peak_kw,
        "peak_delta": greedy.peak_kw - optimal.peak_kw,
        "greedy_feasible": greedy.feasible,
        "optimal_feasible": optimal.feasible,
        "total_kwh": sum(s.required_kwh for s in greedy.sessions),
        "greedy_delivered": greedy.scheduled_kwh,
        "optimal_delivered": optimal.scheduled_kwh,
        "greedy_peak_kwh": greedy_peak_kwh,
        "optimal_peak_kwh": optimal_peak_kwh,
        # kWh taken out of the expensive band by waiting. Signed like
        # ``saved``: positive means the optimiser did the better thing.
        "peak_kwh_shifted": greedy_peak_kwh - optimal_peak_kwh,
    }


def _verdict(greedy, optimal, summary) -> dict:
    """One alert, chosen in the view so the template does not do arithmetic.

    Three cases, in this order: the optimiser failed (worst, and the one the
    operator must act on), the greedy rule failed while the optimiser did not
    (the whole point of the feature), or both worked (then the only honest
    claim left is the difference in price).
    """
    if not optimal.feasible:
        return {"kind": "danger", "reasons": optimal.binding}
    if not greedy.feasible:
        return {"kind": "warning", "reasons": greedy.binding}
    if summary["saved"] > 0:
        headline = (
            f"Both policies met every deadline. The optimised plan buys the same "
            f"{summary['total_kwh']:.0f} kWh for {summary['saved']:.2f} less "
            f"({summary['saved_pct']:.1f}%)."
        )
    else:
        headline = "Both policies met every deadline, at the same price."
    return {"kind": "success", "reasons": [], "headline": headline}


@login_required
def schedule_view(request):
    """Render the greedy-vs-optimised comparison for the demo fleet."""
    sessions = fleet()
    capacity = _form(request.GET.get("capacity"), 1.0, 10_000.0, TOTAL_POWER_KW)
    peak_weight = _form(request.GET.get("peak_weight"), 0.0, 1_000.0, 0.0)

    tariff = hourly_tariff()
    optimal = solve_schedule(
        sessions, tariff, capacity_kw=capacity, peak_weight=peak_weight
    )
    greedy = greedy_schedule(sessions, tariff, capacity_kw=capacity)

    summary = _summary(greedy, optimal)
    context = {
        "summary": summary,
        "verdict": _verdict(greedy, optimal, summary),
        "sessions": _session_rows(sessions, greedy, optimal),
        "hours": _hourly_rows(greedy, optimal),
        "binding": {"greedy": greedy.binding, "optimal": optimal.binding},
        "notes": {"greedy": greedy.message, "optimal": optimal.message},
        "capacity": capacity,
        "peak_weight": peak_weight,
        "total_power": TOTAL_POWER_KW,
        "horizon_hours": len(optimal.tariff),
    }
    return render(request, "scheduling/schedule.html", context)

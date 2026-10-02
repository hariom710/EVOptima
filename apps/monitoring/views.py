"""Monitoring HTTP endpoints.

Combines both former implementations:

- project **A** contributed the richer status payload (``fault_type`` plus the
  three per-parameter anomaly flags) and the simulation start/stop endpoints;
- project **fault2** contributed the standalone WebSocket dashboard view.

All endpoints require an authenticated session — project A already did, and
fault2's anonymous endpoints are not acceptable for a monitoring surface.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms.models import model_to_dict
from django.shortcuts import redirect, render
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import EventLog, Reading, SimulationControl, Thresholds
from .services import get_thresholds

# NOTE: this module used to keep `_simulation_threads` / `_simulation_instances`
# dicts here so start/stop could toggle a background `threading.Thread`. That
# only ever worked inside the one worker that handled the request, leaked the
# thread on restart, and gave a second worker nothing to stop. Simulations are
# now driven by `manage.py run_sim --daemon`, which polls the shared
# SimulationControl row written by the endpoints below — any worker can write
# it and any number of daemon processes can read it.


# --------------------------------------------------------------------- status
@login_required
@api_view(["GET"])
def status(request):
    latest = Reading.objects.order_by("-created_at").first()
    # Cached: /status/ is polled every 1.5 s by the visualization page.
    thresholds = get_thresholds()
    control = SimulationControl.get_solo()

    state = "SAFE"
    fault_type = None
    is_current_anomaly = is_voltage_anomaly = is_temperature_anomaly = False

    if latest:
        if latest.current > thresholds.max_current or latest.current < thresholds.min_current:
            state, fault_type, is_current_anomaly = "FAULT", "CURRENT_OUT_OF_RANGE", True
        elif latest.voltage > thresholds.max_voltage or latest.voltage < thresholds.min_voltage:
            state, fault_type, is_voltage_anomaly = "FAULT", "VOLTAGE_OUT_OF_RANGE", True
        elif (
            latest.temperature > thresholds.max_temperature
            or latest.temperature < thresholds.min_temperature
        ):
            state, fault_type, is_temperature_anomaly = "FAULT", "TEMPERATURE_OUT_OF_RANGE", True

    return Response(
        {
            "now": timezone.now().isoformat(),
            "latest_reading": model_to_dict(latest) if latest else None,
            "thresholds": model_to_dict(thresholds),
            "state": state,
            "fault_type": fault_type,
            "is_current_anomaly": is_current_anomaly,
            "is_voltage_anomaly": is_voltage_anomaly,
            "is_temperature_anomaly": is_temperature_anomaly,
            "simulation": {
                "requested": control.requested_type,
                "active": control.active_type,
                "simulator_alive": control.daemon_alive,
                "running": control.is_running,
                "stale": control.stale,
            },
        }
    )


# ---------------------------------------------------------------- thresholds
@login_required
@api_view(["GET", "POST"])
def thresholds_view(request):
    if request.method == "GET":
        # Cached and auto-created; also invalidates via post_save when set below.
        return Response(model_to_dict(get_thresholds()))

    th = Thresholds.objects.order_by("-updated_at").first() or Thresholds()
    th.min_current = float(request.data.get("min_current", th.min_current or 10))
    th.max_current = float(request.data.get("max_current", th.max_current or 30))
    th.min_voltage = float(request.data.get("min_voltage", th.min_voltage or 400))
    th.max_voltage = float(request.data.get("max_voltage", th.max_voltage or 460))
    th.min_temperature = float(request.data.get("min_temperature", th.min_temperature or 0))
    th.max_temperature = float(request.data.get("max_temperature", th.max_temperature or 80))
    th.save()  # -> post_save -> services.faults.invalidate_thresholds()
    EventLog.objects.create(
        event_type="THRESHOLDS_CHANGED",
        details="Thresholds updated",
        response="Using new thresholds",
    )
    return Response(model_to_dict(th))


# -------------------------------------------------------------------- events
@login_required
@api_view(["GET"])
def events(request):
    items = EventLog.objects.order_by("-created_at")[:200]
    return Response(
        [
            {
                "id": e.id,
                "event_type": e.event_type,
                "details": e.details,
                "response": e.response,
                "created_at": e.created_at.isoformat(),
            }
            for e in items
        ]
    )


# ----------------------------------------------------------------- dashboard
@login_required
def dashboard(request):
    """Standalone real-time fault dashboard (WebSocket driven)."""
    return render(request, "monitoring/dashboard.html")


# ------------------------------------------------------- simulation controls
def _wants_json(request) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or (
        "application/json" in request.headers.get("Accept", "")
    )


@login_required
@api_view(["POST"])
def start_simulation(request):
    """Ask the `run_sim` daemon to start a normal or fault simulation.

    Writes intent only. The previous implementation spawned a
    ``threading.Thread`` here and stashed it in ``_simulation_threads`` — a
    module-level dict, so a second gunicorn worker saw an empty dict and a web
    restart orphaned the loop entirely.
    """
    sim_type = request.POST.get("type", SimulationControl.SIM_NORMAL)
    duration = request.POST.get("duration")
    cycles = request.POST.get("cycles")
    max_iterations = request.POST.get("iterations")

    if sim_type not in (SimulationControl.SIM_NORMAL, SimulationControl.SIM_FAULT):
        messages.error(request, "Invalid simulation type")
        if _wants_json(request):
            return Response({"status": "error", "message": "invalid type"}, status=400)
        return redirect("/visualization/")

    control = SimulationControl.get_solo()

    if control.is_running and control.active_type == sim_type:
        messages.warning(request, f"{sim_type} simulation is already running")
        if _wants_json(request):
            return Response({
                "status": "already_running",
                "type": sim_type,
                "message": f"{sim_type} simulation is already running",
            })
        return redirect("/visualization/")

    if control.is_running and control.active_type != sim_type:
        messages.warning(
            request, f"Switching simulation: {control.active_type} -> {sim_type}"
        )

    # Optional knobs the daemon does not share through the control row: echo
    # them back so callers know they were received, and log them for the
    # operator. They are applied by `run_sim --duration/--iterations/--cycles`
    # when driving the daemon directly.
    notes = []
    if duration:
        notes.append(f"duration={duration}")
    if cycles:
        notes.append(f"cycles={cycles}")
    if max_iterations:
        notes.append(f"iterations={max_iterations}")

    control.requested_type = sim_type
    control.save(update_fields=["requested_type", "updated_at"])

    simulator = "online" if control.daemon_alive else "offline"
    # Echo the knobs on both branches: they were received either way, and the
    # EventLog entry below stores this same message. They used to be appended
    # only when a daemon was online, silently dropping them from the far more
    # common "no simulator running" reply.
    suffix = f" ({', '.join(notes)})" if notes else ""
    if simulator == "offline":
        message = (
            f"Requested {sim_type} simulation, but no simulator is running. "
            "Start one with: python manage.py run_sim --daemon"
        ) + suffix
    else:
        message = f"{sim_type} simulation requested" + suffix

    EventLog.objects.create(
        event_type="INFO",
        details=f"Simulation requested: {sim_type}",
        response=message,
    )

    if _wants_json(request):
        return Response({"status": "requested", "type": sim_type, "simulator": simulator, "message": message})

    messages.info(request, message)
    return redirect("/visualization/")


@login_required
@api_view(["POST"])
def stop_simulation(request):
    """Ask the `run_sim` daemon to stop whatever it is running."""
    sim_type = request.POST.get("type", SimulationControl.SIM_NORMAL)

    control = SimulationControl.get_solo()
    was_running = bool(control.active_type) or bool(control.requested_type)

    control.requested_type = SimulationControl.SIM_NONE
    control.save(update_fields=["requested_type", "updated_at"])

    if was_running:
        message = f"{sim_type} simulation stop requested"
        messages.success(request, message)
        status_code = "stopped"
    else:
        message = f"No {sim_type} simulation running"
        messages.warning(request, message)
        status_code = "idle"

    EventLog.objects.create(event_type="INFO", details=f"Simulation stop: {sim_type}", response=message)

    if _wants_json(request):
        return Response({"status": status_code, "type": sim_type, "message": message})
    return redirect("/visualization/")

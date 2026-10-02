from django.contrib import admin

from .models import EventLog, Reading, Thresholds


@admin.register(Thresholds)
class ThresholdsAdmin(admin.ModelAdmin):
    list_display = (
        "min_current",
        "max_current",
        "min_voltage",
        "max_voltage",
        "min_temperature",
        "max_temperature",
        "updated_at",
    )


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("current", "voltage", "temperature", "created_at")
    ordering = ("-created_at",)


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ("event_type", "created_at", "details", "response")
    ordering = ("-created_at",)
    list_filter = ("event_type",)


# SimulationControl is deliberately NOT registered. It is the control plane
# between the web tier and the `run_sim` daemon, and hand-editing it through
# the admin would fight a live daemon: a stale `active_type` makes /status/
# claim a simulation is running, and a stray `requested_type` is picked up by
# whatever daemon starts next. Monitor it via `GET /api/monitoring/status/`,
# which reports `simulation.{requested,active,simulator_alive,running,stale}`.

from django.db import models
from django.utils import timezone


class Thresholds(models.Model):
    # EV Charger Standard Limits - Updated per user requirements
    min_current = models.FloatField(default=10.0)   # Minimum 10A for safe charging
    max_current = models.FloatField(default=30.0)   # Maximum 30A for safe charging
    min_voltage = models.FloatField(default=400.0)  # Minimum 400V for DC fast charging
    max_voltage = models.FloatField(default=460.0)  # Maximum 460V for DC fast charging
    min_temperature = models.FloatField(default=0.0)   # Minimum 0°C operating temperature
    max_temperature = models.FloatField(default=80.0)  # Maximum 80°C operating temperature
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Thresholds({self.min_current}-{self.max_current}A, {self.min_voltage}-{self.max_voltage}V, {self.min_temperature}-{self.max_temperature}°C)"


class Reading(models.Model):
    current = models.FloatField()
    voltage = models.FloatField()
    temperature = models.FloatField()
    # Indexed: /status/ and /home/ both do `order_by("-created_at").first()`,
    # and prune_readings deletes by age. Readings arrive ~1/sec while a
    # simulation runs, so without an index both become full scans.
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"Reading I={self.current}A V={self.voltage}V T={self.temperature}C @ {self.created_at}"


class EventLog(models.Model):
    EVENT_TYPES = (
        ("FAULT_DETECTED", "Fault Detected"),
        ("CHARGING_STOPPED", "Charging Stopped"),
        ("THRESHOLDS_CHANGED", "Thresholds Changed"),
        ("INFO", "Info"),
        ("PREDICTION_ERROR", "Prediction Error"),
        # prediction/views.py logs a failed fault email; without this entry the
        # value is invisible in the admin dropdown and fails full_clean().
        ("EMAIL_ERROR", "Email Error"),
    )

    # Indexed: the events endpoint filters by type and orders by recency.
    event_type = models.CharField(max_length=32, choices=EVENT_TYPES, db_index=True)
    details = models.TextField(blank=True)
    response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.event_type} @ {self.created_at}: {self.details[:50]}"


class SimulationControl(models.Model):
    """Shared control plane driving the `run_sim` daemon.

    Exactly one row exists (``pk=1``, see :meth:`get_solo`).

    Why a database row rather than a module-level dict: the previous
    implementation kept ``threading.Thread`` objects in
    ``views._simulation_threads``, which meant the start/stop endpoints only
    worked inside the worker that handled the request — with more than one
    gunicorn/daphne worker the stop button stopped nothing, and every restart
    leaked or orphaned the running loop. Persisting the *intent* instead lets
    any worker write it, any number of ``run_sim`` processes read it, and the
    state survives a restart.

    Lifecycle:
        web  -> writes :attr:`requested_type`
        daemon -> polls it, sets :attr:`active_type` and refreshes
                 :attr:`heartbeat_at` while alive
        web  -> reads both to report whether a simulator is actually running
    """

    SIM_NORMAL = "normal"
    SIM_FAULT = "fault"
    SIM_CHOICES = (
        (SIM_NORMAL, "Normal charging"),
        (SIM_FAULT, "Fault detection"),
    )
    #: "" means "no simulation requested".
    SIM_NONE = ""
    #: A daemon whose heartbeat is older than this is considered dead.
    HEARTBEAT_TIMEOUT = 10.0

    id = models.AutoField(primary_key=True)
    requested_type = models.CharField(max_length=16, choices=SIM_CHOICES, blank=True, default=SIM_NONE)
    active_type = models.CharField(max_length=16, choices=SIM_CHOICES, blank=True, default=SIM_NONE)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "simulation control"

    def __str__(self) -> str:
        return f"SimulationControl(requested={self.requested_type or '-'}, active={self.active_type or '-'})"

    @classmethod
    def get_solo(cls) -> "SimulationControl":
        """Return the singleton row, creating it on first use."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def daemon_alive(self) -> bool:
        """True when a `run_sim` process heartbeat arrived recently."""
        if self.heartbeat_at is None:
            return False
        age = (timezone.now() - self.heartbeat_at).total_seconds()
        return age <= self.HEARTBEAT_TIMEOUT

    @property
    def is_running(self) -> bool:
        """True only when a live daemon reports an active simulation."""
        return self.daemon_alive and bool(self.active_type)

    @property
    def stale(self) -> bool:
        """A simulation was requested but no live daemon picked it up."""
        return bool(self.requested_type) and not self.is_running

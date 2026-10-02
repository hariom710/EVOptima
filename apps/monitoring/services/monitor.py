"""Long-running real-time monitoring loop with WebSocket push.

Runs as its own process (``manage.py run_monitor``), so the web workers stay
stateless and multiple gunicorn/daphne workers can serve requests safely.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from channels.layers import get_channel_layer
from django.db import transaction
from django.forms.models import model_to_dict

from ..models import EventLog, Reading, Thresholds
from .values import PredictedValues

CHANNEL_GROUP = "monitoring"


class MonitoringService:
    """Sample a prediction source, persist readings, detect persistent faults.

    A fault must persist for ``fault_seconds_threshold`` consecutive samples
    before the charging system is treated as shut down, which avoids flapping
    on single-sample noise.
    """

    def __init__(
        self,
        get_prediction: Callable[[], PredictedValues],
        alert_func: Callable[[str, str], None] | None = None,
        sample_period_sec: float = 1.0,
        fault_seconds_threshold: int = 5,
        stop_on_fault: bool = True,
        update_func: Callable[[dict], None] | None = None,
    ):
        self.get_prediction = get_prediction
        self.alert_func = alert_func or self._default_alert
        self.sample_period_sec = sample_period_sec
        self.fault_seconds_threshold = fault_seconds_threshold
        self.stop_on_fault = stop_on_fault
        self.update_func = update_func or self._send_update
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._counters = {"current": 0, "voltage": 0, "temperature": 0}
        self._was_faulty = {"current": False, "voltage": False, "temperature": False}
        self._charging_enabled = True

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="MonitoringService", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------- side effects
    def _default_alert(self, subject: str, message: str) -> None:
        EventLog.objects.create(event_type="INFO", details=f"ALERT: {subject}", response=message)

    def _shutdown_charging(self, reason: str) -> None:
        self._charging_enabled = False
        EventLog.objects.create(
            event_type="CHARGING_STOPPED",
            details=reason,
            response="Charging stop signal issued",
        )

    def _send_update(self, data: dict) -> None:
        """Push a ``monitoring_update`` to the WebSocket ``monitoring`` group."""
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        try:
            asyncio.run(channel_layer.group_send(CHANNEL_GROUP, {"type": "monitoring_update", "data": data}))
        except RuntimeError:
            # An event loop is already running in this thread; run on a fresh
            # loop in a worker thread instead of failing silently.
            threading.Thread(
                target=lambda: asyncio.run(
                    channel_layer.group_send(CHANNEL_GROUP, {"type": "monitoring_update", "data": data})
                ),
                daemon=True,
            ).start()

    # ----------------------------------------------------------------- loop
    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                pred = self.get_prediction()
            except StopIteration:
                EventLog.objects.create(
                    event_type="INFO", details="CSV exhausted", response="Monitoring stopped"
                )
                self.update_func({"status": "stopped", "message": "CSV exhausted"})
                break
            except Exception as exc:  # noqa: BLE001 - one bad sample must not kill the loop
                EventLog.objects.create(
                    event_type="INFO",
                    details=f"Prediction error: {exc}",
                    response="Skipping cycle",
                )
                self.update_func({"status": "error", "message": str(exc)})
                time.sleep(self.sample_period_sec)
                continue

            self._cycle(pred)
            time.sleep(self.sample_period_sec)

    def _cycle(self, pred: PredictedValues) -> None:
        with transaction.atomic():
            reading = Reading.objects.create(
                current=pred.current, voltage=pred.voltage, temperature=pred.temperature
            )
            thresholds = (
                Thresholds.objects.order_by("-updated_at").select_for_update().first()
                or Thresholds.objects.create()
            )

            violations = {
                "current": pred.current > thresholds.max_current or pred.current < thresholds.min_current,
                "voltage": pred.voltage > thresholds.max_voltage or pred.voltage < thresholds.min_voltage,
                "temperature": (
                    pred.temperature > thresholds.max_temperature
                    or pred.temperature < thresholds.min_temperature
                ),
            }

            state = "SAFE"
            persistent = False
            for key, faulty in violations.items():
                self._counters[key] = self._counters[key] + 1 if faulty else 0

                if faulty:
                    self._log_abnormal(key, pred, thresholds)
                    self._was_faulty[key] = True
                elif self._was_faulty[key]:
                    self._log_normalized(key, pred, thresholds)
                    self._was_faulty[key] = False

                if self._counters[key] >= self.fault_seconds_threshold:
                    persistent = True

            if persistent:
                state = "FAULT"
                if self._charging_enabled:
                    self._trip(pred, thresholds)

            self.update_func({"latest_reading": model_to_dict(reading), "state": state})

    # ----------------------------------------------------------------- logging
    def _limits(self, key: str, thresholds: Thresholds) -> str:
        if key == "current":
            return f"{thresholds.min_current}-{thresholds.max_current}A"
        if key == "voltage":
            return f"{thresholds.min_voltage}-{thresholds.max_voltage}V"
        return f"{thresholds.min_temperature}-{thresholds.max_temperature}\u00b0C"

    def _log_abnormal(self, key: str, pred: PredictedValues, thresholds: Thresholds) -> None:
        value = getattr(pred, key)
        unit = {"current": "A", "voltage": "V", "temperature": "\u00b0C"}[key]
        EventLog.objects.create(
            event_type="INFO",
            details=f"{key.capitalize()} abnormal: {value}{unit} (limits: {self._limits(key, thresholds)})",
            response=f"Fault counter: {self._counters[key]}/{self.fault_seconds_threshold}",
        )

    def _log_normalized(self, key: str, pred: PredictedValues, thresholds: Thresholds) -> None:
        value = getattr(pred, key)
        unit = {"current": "A", "voltage": "V", "temperature": "\u00b0C"}[key]
        EventLog.objects.create(
            event_type="INFO",
            details=f"{key.capitalize()} normalized: {value}{unit} (within limits: {self._limits(key, thresholds)})",
            response=f"{key.capitalize()} readings back to safe range",
        )

    def _trip(self, pred: PredictedValues, thresholds: Thresholds) -> None:
        """Persistent fault reached: log it and shut charging down."""
        faults = []
        for key, faulty in violations_items(self._counters, self.fault_seconds_threshold):
            if not faulty:
                continue
            value = getattr(pred, key)
            unit = {"current": "A", "voltage": "V", "temperature": "\u00b0C"}[key]
            faults.append(f"{key.capitalize()}: {value}{unit} (limit: {self._limits(key, thresholds)})")

        message = "PERSISTENT FAULT - " + "; ".join(faults)
        EventLog.objects.create(
            event_type="FAULT_DETECTED",
            details=message,
            response="Charging system protection activated",
        )
        self._shutdown_charging(reason=message)
        self.alert_func("Persistent fault detected", message)
        if self.stop_on_fault:
            self._stop_event.set()


def violations_items(counters: dict, threshold: int):
    """Yield ``(key, persistent)`` pairs for the counters mapping."""
    for key, count in counters.items():
        yield key, count >= threshold

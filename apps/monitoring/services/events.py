"""Persistence helpers for readings and events."""
from ..models import EventLog, Reading
from .values import PredictedValues


def log_reading(predicted_values: PredictedValues) -> Reading:
    """Persist a single reading sample."""
    return Reading.objects.create(
        current=predicted_values.current,
        voltage=predicted_values.voltage,
        temperature=predicted_values.temperature,
    )


def log_event(event_type: str, details: str, response: str = "") -> EventLog:
    """Persist a single event-log entry."""
    return EventLog.objects.create(
        event_type=event_type,
        details=details,
        response=response,
    )

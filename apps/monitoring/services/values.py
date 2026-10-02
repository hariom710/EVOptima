"""Shared value objects for the monitoring service layer."""
from dataclasses import dataclass


@dataclass
class PredictedValues:
    """A single predicted/simulated sample of the three watched parameters."""

    current: float
    voltage: float
    temperature: float

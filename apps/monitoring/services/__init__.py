"""EV charging monitoring service layer.

Split into focused modules so that fault logic, persistence, data sources and
simulation can be tested and reused independently:

- ``values``       shared value objects (``PredictedValues``)
- ``faults``       threshold evaluation (``check_fault``)
- ``events``       persistence helpers for readings and events
- ``sources``      prediction sources (CSV replay, mock source)
- ``simulations``  synthetic normal/fault charging scenarios
- ``monitor``      long-running real-time monitoring loop with WebSocket push
"""
from .events import log_event, log_reading
from .faults import check_fault, get_thresholds, invalidate_thresholds
from .monitor import MonitoringService
from .simulations import (
    ChargingSimulation,
    FaultDetectionSimulation,
    NormalChargingSimulation,
    run_sample,
    run_simulation,
)
from .sources import CsvPredictionSource, mock_prediction
from .values import PredictedValues

__all__ = [
    "PredictedValues",
    "check_fault",
    "get_thresholds",
    "invalidate_thresholds",
    "log_event",
    "log_reading",
    "CsvPredictionSource",
    "mock_prediction",
    "MonitoringService",
    "ChargingSimulation",
    "NormalChargingSimulation",
    "FaultDetectionSimulation",
    "run_sample",
    "run_simulation",
]

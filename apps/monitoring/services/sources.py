"""Prediction sources that feed the monitoring loop.

Two implementations are provided:

- :class:`CsvPredictionSource` replays recorded charging data from a CSV file
  (used for demos and tests).
- :func:`mock_prediction` generates synthetic samples with realistic phase
  transitions (normal -> fault -> recovery).
"""
from __future__ import annotations

import random
import time
from collections.abc import Iterator
from pathlib import Path

from django.conf import settings

from .values import PredictedValues

#: Header substrings that identify each column. Resolution is by **name**,
#: not by position, because the recorded datasets do not share a column order:
#: ``ev_charging_data2.csv`` carries an extra ``Time-lap`` column at index 1
#: which shifts every following column by one and would silently replay
#: power as voltage, voltage as current and current as temperature.
VOLTAGE_KEYS = ("charging voltage", "voltage")
CURRENT_KEYS = ("charging current", "current")
TEMPERATURE_KEYS = ("battery temperature", "temperature")

#: Legacy positional fallback, used only when the file has no recognisable
#: header (the original order of ``ev_charging_data1.csv``).
VOLTAGE_IDX = 3
CURRENT_IDX = 4
TEMPERATURE_IDX = 5

#: Plausible sensor ranges. These reject only physically impossible readings;
#: whether a value is *unsafe* is decided by the thresholds, not by the source.
#: (The previous code clamped voltage to 200-300 V, which contradicted the
#: 400-460 V safety band and therefore reported a permanent FAULT.)
CURRENT_RANGE = (0.0, 200.0)
VOLTAGE_RANGE = (0.0, 1000.0)
TEMPERATURE_RANGE = (-40.0, 150.0)

#: Values substituted when a line cannot be parsed (mid-range, safe).
FALLBACK = PredictedValues(current=20.0, voltage=430.0, temperature=40.0)


def _match_column(header: list[str], keys: tuple[str, ...]) -> int | None:
    """Return the index of the first header cell containing any of ``keys``.

    Matching is case-insensitive and prefers the longest (most specific) key
    so ``Charging Voltage_V`` wins over a bare ``Voltage`` column, and
    ``Battery Temperature_C`` wins over any other temperature column.
    """
    lowered = [c.strip().lower() for c in header]
    best_idx, best_len = None, 0
    for key in keys:
        for idx, cell in enumerate(lowered):
            if key in cell and len(key) > best_len:
                best_idx, best_len = idx, len(key)
    return best_idx


def resolve_columns(header: list[str]) -> tuple[int, int, int]:
    """Map a CSV header onto ``(voltage_idx, current_idx, temperature_idx)``.

    Falls back to the legacy positional order when a column cannot be found,
    so headerless or unexpected files still replay instead of raising.
    """
    voltage = _match_column(header, VOLTAGE_KEYS)
    current = _match_column(header, CURRENT_KEYS)
    temperature = _match_column(header, TEMPERATURE_KEYS)
    return (
        voltage if voltage is not None else VOLTAGE_IDX,
        current if current is not None else CURRENT_IDX,
        temperature if temperature is not None else TEMPERATURE_IDX,
    )


#: Dataset filenames tried in order by :func:`default_csv_path`.
CSV_CANDIDATES = (
    "ev_charging_data.csv",
    "ev_charging_data1.csv",
    "ev_charging_data2.csv",
)


def default_csv_path() -> Path:
    """Return the first existing replay dataset under ``settings.DATA_DIR``.

    ``DATA_DIR`` is gitignored, so the exact filename depends on which local
    dataset was dropped in; resolving through :data:`CSV_CANDIDATES` keeps the
    monitor working without a hard-coded single name.
    """
    data_dir = Path(getattr(settings, "DATA_DIR", settings.BASE_DIR / "data" / "raw"))
    for name in CSV_CANDIDATES:
        candidate = data_dir / name
        if candidate.exists():
            return candidate
    # Nothing present yet — report the canonical name so the error message
    # points somewhere sensible.
    return data_dir / CSV_CANDIDATES[0]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


class CsvPredictionSource:
    """Replay ``current``/``voltage``/``temperature`` samples from a CSV file.

    The file is opened lazily and **reopened from the start on EOF**, so the
    source loops indefinitely instead of raising ``StopIteration`` and killing
    the monitoring loop.
    """

    def __init__(
        self,
        csv_path: str | Path | None = None,
        has_header: bool = True,
        current_idx: int | None = None,
        voltage_idx: int | None = None,
        temperature_idx: int | None = None,
        delimiter: str = ",",
    ):
        # NOTE: the previous implementation ignored ``csv_path`` entirely and
        # hardcoded a relative path, which broke whenever the working directory
        # was not the project root.
        self.csv_path = Path(csv_path) if csv_path else default_csv_path()
        self.has_header = has_header
        # ``None`` means "derive from the header"; an explicit value pins the
        # position (kept for headerless files and backwards compatibility).
        self._explicit = {
            "current": current_idx,
            "voltage": voltage_idx,
            "temperature": temperature_idx,
        }
        self.current_idx = current_idx if current_idx is not None else CURRENT_IDX
        self.voltage_idx = voltage_idx if voltage_idx is not None else VOLTAGE_IDX
        self.temperature_idx = (
            temperature_idx if temperature_idx is not None else TEMPERATURE_IDX
        )
        self.delimiter = delimiter
        self._file = None
        self._iter: Iterator[str] | None = None

    def _resolve(self, header_line: str) -> None:
        """Set the column indices from the header row."""
        if any(v is not None for v in self._explicit.values()):
            return  # caller pinned at least one column; do not second-guess
        header = header_line.rstrip("\r\n").split(self.delimiter)
        voltage, current, temperature = resolve_columns(header)
        self.voltage_idx = voltage
        self.current_idx = current
        self.temperature_idx = temperature

    def _open(self) -> None:
        """(Re)open the file and position after the header, if any."""
        self._close()
        try:
            self._file = open(self.csv_path, encoding="utf-8", newline="")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"CSV replay dataset not found: {self.csv_path}. "
                "Place a dataset in settings.DATA_DIR or pass --csv <path>."
            ) from exc
        self._iter = iter(self._file)
        if self.has_header:
            try:
                self._resolve(next(self._iter))
            except StopIteration:
                pass

    def _close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
                self._iter = None

    def close(self) -> None:
        self._close()

    def _parse(self, line: str) -> PredictedValues:
        parts = line.strip().split(self.delimiter)
        try:
            current = _clamp(float(parts[self.current_idx]), *CURRENT_RANGE)
            voltage = _clamp(float(parts[self.voltage_idx]), *VOLTAGE_RANGE)
            temperature = _clamp(float(parts[self.temperature_idx]), *TEMPERATURE_RANGE)
            return PredictedValues(current=current, voltage=voltage, temperature=temperature)
        except (ValueError, IndexError):
            return FALLBACK

    def next(self) -> PredictedValues:
        """Return the next sample, looping back to the top of the file at EOF."""
        if self._iter is None:
            self._open()
        try:
            line = next(self._iter)
        except StopIteration:
            # EOF: restart once. If the file is empty/header-only, bail out so
            # callers can detect the condition rather than spin forever.
            self._open()
            try:
                line = next(self._iter)
            except StopIteration:
                self._close()
                raise

        # Skip blank lines instead of feeding them to the parser.
        while not line.strip():
            try:
                line = next(self._iter)
            except StopIteration:
                self._open()
                try:
                    line = next(self._iter)
                except StopIteration:
                    self._close()
                    raise
        return self._parse(line)


class MockChargingSource:
    """Deterministic-ish synthetic source used when no CSV is supplied.

    Walks through normal charging, the three fault conditions, and recovery,
    logging a phase-change event each time it transitions.
    """

    def __init__(self):
        self.start_time = time.time()
        self.phase = "normal"
        self.base_current = 20.0      # safe middle of 10-30 A
        self.base_voltage = 430.0     # safe middle of 400-460 V
        self.base_temperature = 40.0  # safe middle of 0-80 degC
        self.logged_phases: set[str] = set()

    def _log_phase(self, phase: str, details: str, response: str) -> None:
        if phase in self.logged_phases:
            return
        from ..models import EventLog

        EventLog.objects.create(event_type="INFO", details=details, response=response)
        self.logged_phases.add(phase)

    def next(self) -> PredictedValues:
        elapsed = time.time() - self.start_time

        if elapsed < 8:
            self._log_phase(
                "normal",
                "Simulation started: Normal charging phase",
                "Current: 10-30A, Voltage: 400-460V, Temperature: 0-80\u00b0C",
            )
            self.phase = "normal"
            current = self.base_current + random.uniform(-3, 3)
            voltage = self.base_voltage + random.uniform(-10, 10)
            temperature = self.base_temperature + random.uniform(-5, 5)
        elif elapsed < 15:
            if self.phase != "fault_current_high":
                from ..models import EventLog

                EventLog.objects.create(
                    event_type="INFO",
                    details=f"Simulation phase change: High current fault at {elapsed:.1f}s",
                    response="Simulating excessive current draw (>30A)",
                )
                self.phase = "fault_current_high"
            current = 35.0 + random.uniform(0, 10)
            voltage = self.base_voltage + random.uniform(-10, 10)
            temperature = self.base_temperature + random.uniform(-5, 5)
        elif elapsed < 25:
            if self.phase != "fault_voltage_low":
                from ..models import EventLog

                EventLog.objects.create(
                    event_type="INFO",
                    details=f"Simulation phase change: Low voltage fault at {elapsed:.1f}s",
                    response="Simulating undervoltage condition (<400V)",
                )
                self.phase = "fault_voltage_low"
            current = self.base_current + random.uniform(-3, 3)
            voltage = 350.0 + random.uniform(0, 30)
            temperature = self.base_temperature + random.uniform(-5, 5)
        elif elapsed < 35:
            if self.phase != "fault_temp_high":
                from ..models import EventLog

                EventLog.objects.create(
                    event_type="INFO",
                    details=f"Simulation phase change: High temperature fault at {elapsed:.1f}s",
                    response="Simulating overheating condition (>80\u00b0C)",
                )
                self.phase = "fault_temp_high"
            current = self.base_current + random.uniform(-3, 3)
            voltage = self.base_voltage + random.uniform(-10, 10)
            temperature = 85.0 + random.uniform(0, 15)
        elif elapsed < 45:
            if self.phase != "fault_temp_low":
                from ..models import EventLog

                EventLog.objects.create(
                    event_type="INFO",
                    details=f"Simulation phase change: Low temperature fault at {elapsed:.1f}s",
                    response="Simulating freezing condition (<0\u00b0C)",
                )
                self.phase = "fault_temp_low"
            current = self.base_current + random.uniform(-3, 3)
            voltage = self.base_voltage + random.uniform(-10, 10)
            temperature = -10.0 + random.uniform(0, 8)
        else:
            if self.phase != "recovery":
                from ..models import EventLog

                EventLog.objects.create(
                    event_type="INFO",
                    details=f"Simulation phase change: Recovery phase at {elapsed:.1f}s",
                    response="All parameters returning to safe operating ranges",
                )
                self.phase = "recovery"
            current = self.base_current + random.uniform(-3, 3)
            voltage = self.base_voltage + random.uniform(-10, 10)
            temperature = self.base_temperature + random.uniform(-5, 5)

        return PredictedValues(
            current=round(current, 2),
            voltage=round(voltage, 2),
            temperature=round(temperature, 2),
        )


_mock_source = MockChargingSource()


def mock_prediction() -> PredictedValues:
    """Return a synthetic sample from the shared :class:`MockChargingSource`."""
    return _mock_source.next()

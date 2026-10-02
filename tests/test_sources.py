"""Prediction sources: CSV column resolution, replay looping, and mock phases.

The critical regression pinned here: column indices used to be **positional and
hard-coded** to ``(3, 4, 5)``. ``ev_charging_data2.csv`` carries an extra
``Time-lap`` column, which shifts every later column by one, so the source
replayed power as voltage, voltage as current, and current as temperature --
silently feeding wrong units into fault detection. Resolution is now by header
name.

``csv_path`` was also ignored outright by the old implementation, so the
constructor argument is asserted too.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.monitoring.services import sources
from apps.monitoring.services.sources import (
    CURRENT_IDX,
    FALLBACK,
    TEMPERATURE_IDX,
    VOLTAGE_IDX,
    CsvPredictionSource,
    default_csv_path,
    resolve_columns,
)

# The two header shapes from the real datasets: data1 has no Time-lap column,
# data2 inserts one at index 1 and shifts everything after it.
HEADER_V1 = [
    "Time",
    "Charging Power_kW",
    "Charging Voltage_V",
    "Charging Current_A",
    "Battery Temperature_C",
]
HEADER_V2 = [
    "Time",
    "Time-lap",
    "Charging Power_kW",
    "Charging Voltage_V",
    "Charging Current_A",
    "Battery Temperature_C",
]


class TestResolveColumns:
    def test_resolves_by_name_not_position(self):
        """The whole point: these indices differ from the legacy (3, 4, 5)."""
        voltage, current, temperature = resolve_columns(HEADER_V1)
        assert (voltage, current, temperature) == (2, 3, 4)
        assert (voltage, current, temperature) != (
            VOLTAGE_IDX,
            CURRENT_IDX,
            TEMPERATURE_IDX,
        )

    def test_extra_time_lap_column_does_not_shift_the_mapping(self):
        """data2's index-1 column moves everything along by one; by-name
        resolution follows the header instead of the offset."""
        assert resolve_columns(HEADER_V2) == (3, 4, 5)

    def test_is_case_insensitive(self):
        header = ["TIME", "CHARGING VOLTAGE_V", "CHARGING CURRENT_A", "BATTERY TEMPERATURE_C"]
        assert resolve_columns(header) == (1, 2, 3)

    def test_prefers_the_most_specific_key(self):
        """`Charging Voltage_V` must win over a bare `Voltage` column."""
        header = ["Voltage", "Charging Voltage_V", "Current", "Charging Current_A"]
        voltage, current, _temperature = resolve_columns(header)
        assert voltage == 1
        assert current == 3

    def test_specific_key_wins_regardless_of_order(self):
        header = ["Charging Voltage_V", "Voltage"]
        assert resolve_columns(header)[0] == 0

    def test_missing_columns_fall_back_to_the_legacy_positions(self):
        """Headerless or unexpected files must still replay, not raise."""
        assert resolve_columns(["a", "b", "c", "d", "e", "f"]) == (3, 4, 5)

    def test_partial_header_falls_back_per_column(self):
        header = ["Time", "Charging Voltage_V", "x", "y", "z"]
        voltage, current, temperature = resolve_columns(header)
        assert voltage == 1
        assert current == CURRENT_IDX
        assert temperature == TEMPERATURE_IDX

    def test_temperature_beats_a_generic_column(self):
        header = ["Battery Temperature_C", "Something"]
        assert resolve_columns(header)[2] == 0


class TestDefaultCsvPath:
    def test_returns_the_first_existing_candidate(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        (tmp_path / "ev_charging_data1.csv").write_text("a\n1\n", encoding="utf-8")
        (tmp_path / "ev_charging_data2.csv").write_text("a\n1\n", encoding="utf-8")
        assert default_csv_path().name == "ev_charging_data1.csv"

    def test_skips_missing_candidates(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        (tmp_path / "ev_charging_data2.csv").write_text("a\n1\n", encoding="utf-8")
        assert default_csv_path().name == "ev_charging_data2.csv"

    def test_reports_the_canonical_name_when_nothing_exists(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        assert default_csv_path() == tmp_path / "ev_charging_data.csv"

    def test_candidates_are_documented(self):
        assert sources.CSV_CANDIDATES == (
            "ev_charging_data.csv",
            "ev_charging_data1.csv",
            "ev_charging_data2.csv",
        )


@pytest.fixture
def write_csv(tmp_path):
    def _write(text: str, name: str = "data.csv") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write


class TestCsvPredictionSource:
    def test_reads_the_columns_named_in_the_header(self, write_csv):
        """End-to-end version of the positional-index bug: with the header
        resolved by name, current/voltage/temperature come from the right
        columns even though they are at (2, 3, 4) rather than the legacy
        (4, 3, 5)."""
        path = write_csv(
            ",".join(HEADER_V1) + "\n" + "0,50,440,22,45\n"
        )
        sample = CsvPredictionSource(csv_path=path).next()
        assert sample.current == 22.0
        assert sample.voltage == 440.0
        assert sample.temperature == 45.0

    def test_the_shifted_dataset_reads_the_same_values(self, write_csv):
        path = write_csv(
            ",".join(HEADER_V2) + "\n" + "0,0,50,440,22,45\n"
        )
        sample = CsvPredictionSource(csv_path=path).next()
        assert sample.current == 22.0
        assert sample.voltage == 440.0
        assert sample.temperature == 45.0

    def test_csv_path_argument_is_honoured(self, write_csv):
        """The old implementation ignored this entirely and hardcoded a
        relative path."""
        path = write_csv(",".join(HEADER_V1) + "\n0,50,440,22,45\n", "custom.csv")
        other = write_csv(",".join(HEADER_V1) + "\n0,50,111,1,2\n", "other.csv")
        assert CsvPredictionSource(csv_path=path).next().voltage == 440.0
        assert CsvPredictionSource(csv_path=other).next().voltage == 111.0

    def test_uses_the_default_path_when_none_is_given(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        (tmp_path / "ev_charging_data.csv").write_text(
            ",".join(HEADER_V1) + "\n0,50,455,20,40\n", encoding="utf-8"
        )
        source = CsvPredictionSource()
        assert Path(source.csv_path).name == "ev_charging_data.csv"
        assert source.next().voltage == 455.0

    def test_missing_file_raises_a_helpful_error(self, tmp_path):
        source = CsvPredictionSource(csv_path=tmp_path / "nope.csv")
        with pytest.raises(FileNotFoundError) as excinfo:
            source.next()
        assert "DATA_DIR" in str(excinfo.value)
        assert "nope.csv" in str(excinfo.value)

    def test_replays_from_the_top_after_eof(self, write_csv):
        path = write_csv(",".join(HEADER_V1) + "\n0,50,440,22,45\n")
        source = CsvPredictionSource(csv_path=path)
        first = source.next()
        # The monitoring loop must not die on StopIteration.
        for _ in range(3):
            assert source.next() == first
        source.close()

    def test_skips_blank_lines(self, write_csv):
        path = write_csv(",".join(HEADER_V1) + "\n\n\n0,50,440,22,45\n")
        sample = CsvPredictionSource(csv_path=path).next()
        assert sample.voltage == 440.0

    def test_unparseable_line_returns_the_safe_fallback(self, write_csv):
        path = write_csv(",".join(HEADER_V1) + "\n0,50,not-a-number,22,45\n")
        assert CsvPredictionSource(csv_path=path).next() == FALLBACK

    def test_short_line_returns_the_safe_fallback(self, write_csv):
        path = write_csv(",".join(HEADER_V1) + "\n0,50\n")
        assert CsvPredictionSource(csv_path=path).next() == FALLBACK

    def test_header_only_file_raises_rather_than_spinning(self, write_csv):
        path = write_csv(",".join(HEADER_V1) + "\n")
        source = CsvPredictionSource(csv_path=path)
        with pytest.raises(StopIteration):
            source.next()

    def test_headerless_file_uses_positional_indices(self, write_csv):
        path = write_csv("a,b,c,440,22,45\n", "noheader.csv")
        sample = CsvPredictionSource(csv_path=path, has_header=False).next()
        assert (sample.voltage, sample.current, sample.temperature) == (440.0, 22.0, 45.0)

    def test_explicit_indices_pin_the_positions(self, write_csv):
        """An explicit index must not be second-guessed by the header."""
        path = write_csv("wibble,voltage,current,temperature\n0,440,22,45\n")
        sample = CsvPredictionSource(
            csv_path=path, voltage_idx=1, current_idx=2, temperature_idx=3
        ).next()
        assert (sample.current, sample.voltage, sample.temperature) == (22.0, 440.0, 45.0)


class TestSensorRanges:
    """Only physically impossible values are rejected; the thresholds decide
    what is *unsafe*."""

    @pytest.mark.parametrize(
        ("line", "attribute", "expected"),
        [
            # current beyond the 0-200 A sensor range
            (",".join(("0", "50", "440", "999", "45")), "current", 200.0),
            # voltage below 0 V
            (",".join(("0", "50", "-5", "22", "45")), "voltage", 0.0),
            # voltage beyond 1000 V
            (",".join(("0", "50", "5000", "22", "45")), "voltage", 1000.0),
            # temperature below the -40 C sensor floor
            (",".join(("0", "50", "440", "22", "-999")), "temperature", -40.0),
            # temperature beyond 150 C
            (",".join(("0", "50", "440", "22", "9999")), "temperature", 150.0),
        ],
    )
    def test_out_of_sensor_range_values_are_clamped(
        self, write_csv, line, attribute, expected
    ):
        path = write_csv(",".join(HEADER_V1) + "\n" + line + "\n")
        assert getattr(CsvPredictionSource(csv_path=path).next(), attribute) == expected

    def test_a_value_that_is_unsafe_but_plausible_is_not_clamped(self, write_csv):
        """35 A is outside the 10-30 A *safety* band but well within the
        sensor's 0-200 A range, so the source must pass it through untouched."""
        path = write_csv(",".join(HEADER_V1) + "\n0,50,440,35,45\n")
        assert CsvPredictionSource(csv_path=path).next().current == 35.0


class _StubTime:
    """Stand-in for the ``time`` module, exposing only ``time()``."""

    def __init__(self, now: float) -> None:
        self._now = now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.mark.django_db
class TestMockPhases:
    @staticmethod
    def _source_at(monkeypatch, elapsed: float) -> sources.MockChargingSource:
        clock = _StubTime(1_000.0)
        monkeypatch.setattr(sources, "time", clock)
        source = sources.MockChargingSource()  # start_time = 1000.0
        clock.advance(elapsed)
        return source

    def test_normal_phase_sits_mid_range(self, monkeypatch):
        values = self._source_at(monkeypatch, 1.0).next()
        assert 10.0 <= values.current <= 30.0
        assert 400.0 <= values.voltage <= 460.0
        assert 0.0 <= values.temperature <= 80.0

    @pytest.mark.parametrize(
        ("elapsed", "attribute", "predicate"),
        [
            (10.0, "current", lambda v: v > 30.0),     # over-current fault
            (20.0, "voltage", lambda v: v < 400.0),    # undervoltage fault
            (30.0, "temperature", lambda v: v > 80.0),  # overheating fault
            (40.0, "temperature", lambda v: v < 0.0),   # freezing fault
        ],
    )
    def test_fault_phases_leave_the_safe_band(
        self, monkeypatch, elapsed, attribute, predicate
    ):
        values = self._source_at(monkeypatch, elapsed).next()
        assert predicate(getattr(values, attribute)), values

    def test_recovery_returns_to_safe(self, monkeypatch):
        values = self._source_at(monkeypatch, 50.0).next()
        assert 10.0 <= values.current <= 30.0
        assert 400.0 <= values.voltage <= 460.0
        assert 0.0 <= values.temperature <= 80.0

    def test_phase_change_is_logged_once_per_phase(self, monkeypatch):
        from apps.monitoring.models import EventLog

        clock = _StubTime(1_000.0)
        monkeypatch.setattr(sources, "time", clock)
        source = sources.MockChargingSource()

        source.next()  # normal, logs via _log_phase
        source.next()
        assert EventLog.objects.filter(event_type="INFO").count() == 1

        clock.advance(10.0)  # -> fault_current_high
        source.next()
        source.next()
        phase_logs = EventLog.objects.filter(
            details__contains="Simulation phase change"
        )
        assert phase_logs.count() == 1

    def test_sample_is_rounded_to_two_decimals(self, monkeypatch):
        values = self._source_at(monkeypatch, 1.0).next()
        for field in (values.current, values.voltage, values.temperature):
            assert round(field, 2) == field

    def test_mock_prediction_returns_a_sample(self):
        values = sources.mock_prediction()
        assert isinstance(values.current, float)
        assert isinstance(values.voltage, float)
        assert isinstance(values.temperature, float)

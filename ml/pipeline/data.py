"""Dataset loading and feature engineering.

Both supplied CSVs are loaded and tagged with their source so evaluation can be
reported per source. They are *not* exchangeable: ``ev_charging_data1.csv`` is a
single 4.17 h session sampled every 5 s (1-7 kW, 200-500 V) while
``ev_charging_data2.csv`` is four sessions sampled every 10 s (5-50 kW,
300-800 V). Fitting ``TimeSeriesSplit`` across the two would put the first fold
across that regime boundary and score R2 = 0.72 on a model that actually scores
0.996 -- see ``evaluate.per_source_report``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ml.pipeline.config import CALENDAR_FEATURES, ENERGY_TARGET, RATE_TARGET

#: Column order the models were trained with. ``meta.json`` records the same
#: list and ``core.model_registry`` asserts on it at load time.
TIMESTAMP_COL = "Timestamp"
ELAPSED_COL = "Time Elapsed_s"

DATASET_FILES = ("ev_charging_data1.csv", "ev_charging_data2.csv")


def _default_data_dir() -> Path:
    try:
        from django.conf import settings

        return Path(settings.DATA_DIR)
    except Exception:  # pragma: no cover - notebook use without Django
        return Path(__file__).resolve().parents[2] / "data" / "raw"


def load_datasets(data_dir: Path | str | None = None) -> dict[str, pd.DataFrame]:
    """Load every CSV in ``data_dir`` keyed by source id (``d1``, ``d2`` ...).

    Timestamps are parsed, calendar features and the charging duration are
    derived, and the derived rate target is added. Rows with no target value
    are dropped -- ``ev_charging_data1.csv`` carries one all-NaN row.
    """
    directory = Path(data_dir) if data_dir else _default_data_dir()
    datasets: dict[str, pd.DataFrame] = {}
    for i, name in enumerate(DATASET_FILES, start=1):
        path = directory / name
        if not path.is_file():
            continue
        frame = pd.read_csv(path)
        frame = _engineer(frame)
        frame = frame.dropna(subset=[ENERGY_TARGET]).reset_index(drop=True)
        frame.insert(0, "source", f"d{i}")
        datasets[f"d{i}"] = frame
    if not datasets:
        raise FileNotFoundError(
            f"No dataset found in {directory} -- expected one of {', '.join(DATASET_FILES)}"
        )
    return datasets


def _engineer(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame[TIMESTAMP_COL] = pd.to_datetime(
        frame[TIMESTAMP_COL], dayfirst=True, errors="coerce"
    )
    stamp = frame[TIMESTAMP_COL]
    frame["hour"] = stamp.dt.hour
    frame["day_of_week"] = stamp.dt.dayofweek
    frame["month"] = stamp.dt.month
    frame["is_weekend"] = frame["day_of_week"].isin([5, 6]).astype(int)
    frame["Charging_Duration_h"] = frame[ELAPSED_COL] / 3600.0
    # Undefined for the t=0 rows; training drops them and the view guards the
    # same case at serving time (duration == 0 -> energy 0).
    frame[RATE_TARGET] = frame[ENERGY_TARGET] / frame["Charging_Duration_h"].mask(
        frame["Charging_Duration_h"] <= 0
    )
    return frame


def combined(datasets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """All sources concatenated and sorted into a single chronology."""
    return (
        pd.concat(list(datasets.values()), ignore_index=True)
        .sort_values(TIMESTAMP_COL)
        .reset_index(drop=True)
    )


def feature_row(**values) -> pd.DataFrame:
    """Build a one-row frame in exactly the order the models expect.

    The keys are the *dataset* column names, so callers (the Django view, the
    notebook, a test) cannot silently reorder or rename a feature.
    """
    ordered: dict[str, list] = {}
    for column, value in values.items():
        ordered[column] = [value]
    return pd.DataFrame(ordered)


def calendar_values(stamp) -> dict[str, int]:
    """Calendar features for a ``datetime`` -- shared by training and serving."""
    return {
        "hour": stamp.hour,
        "day_of_week": stamp.weekday(),
        "month": stamp.month,
        "is_weekend": 1 if stamp.weekday() in (5, 6) else 0,
    }


__all__ = [
    "CALENDAR_FEATURES",
    "DATASET_FILES",
    "combined",
    "feature_row",
    "calendar_values",
    "load_datasets",
]

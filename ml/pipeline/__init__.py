"""Training and evaluation pipeline for the EVOptima prediction models.

Two models are shipped, each with its own versioned artifact directory::

    ml/artifacts/
        current.json                 # {"power": "power-v1", "energy": "energy-v1"}
        model.joblib, scaler.joblib   # legacy alias == the active energy model
        power-v1/{model,scaler}.joblib + meta.json
        energy-v1/{model,scaler}.joblib + meta.json

``power``  (XGBoost)        charging power (kW) from V, I, temperature, SoC, calendar.
``energy`` (HistGB)         average charging *rate* (kW) from power, temperature and
                            SoC; the view integrates it: ``energy = rate * duration``.
                            Calendar features are excluded here -- see config.py.

Why the energy model predicts a rate rather than cumulative kWh: in the supplied
datasets ``Energy Supplied_kWh`` is cumulative, hence a strictly increasing ramp
of duration (``corr(Energy, Duration) = +1.0000``), so a tree model trained on it
can only reproduce durations it has already seen. Held out chronologically it
scores R2 = -3.26. The rate is stationary (CV 2.6-3.2%), so it *does* generalise,
and re-integrating it recovers the ramp: blocked-CV R2 = +0.9978.
"""
from ml.pipeline.config import (  # noqa: F401
    ENERGY_FEATURES,
    ENERGY_MODEL_NAME,
    POWER_FEATURES,
    POWER_MODEL_NAME,
)
from ml.pipeline.data import feature_row, load_datasets  # noqa: F401

__all__ = [
    "ENERGY_FEATURES",
    "ENERGY_MODEL_NAME",
    "POWER_FEATURES",
    "POWER_MODEL_NAME",
    "feature_row",
    "load_datasets",
]

"""Training and evaluation pipeline for the EVOptima prediction model.

One model is shipped, in its own versioned artifact directory::

    ml/artifacts/
        current.json                 # {"power": "power-v1"}
        model.joblib, scaler.joblib  # legacy alias == the active power model
        power-v1/{model,scaler}.joblib + meta.json

``power``  (XGBoost)        charging power (kW) from V, I, temperature, SoC,
                            calendar. Energy is then arithmetic, not inference:
                            ``energy = power * duration``.

Why there is no second model
----------------------------
The obvious candidate was a HistGB model for energy. It cannot be learned from
these CSVs, and the audit that shows this ships as ``leakage_audit.json``:

* Three identities hold in the raw columns: ``Charging Power_kW = V x I / 1000``
  (within 0.14 %), ``Charging Rate_kW x Charging_Duration_h`` **exactly equals**
  ``Energy Supplied_kWh`` (0.00000000 kWh), and ``Energy Supplied_kWh`` equals
  ``integral(P dt)`` to within 0.098 kWh per row.
* The second one is decisive: the shipped pipeline predicted ``rate`` and
  multiplied it back by ``duration`` -- but those two columns *are* the target,
  so the duration cancels. The published +0.9977 only ever asked "can you
  predict ``Energy / Duration``", and that quantity has CV 2.55 % (d1) /
  3.22 % (d2) against 43.88 % / 47.41 % for ``Charging Power_kW``, with
  ``corr(rate, power) = +0.035``. Nothing is learnable from a target that
  barely moves.
* A model fitted on it therefore learns a threshold, not a response. The
  shipped one answered 4.392 kW or 21.668 kW and nothing in between, and
  served 75.2 % mean absolute error against ``power * duration`` (251.3 % worst
  case) -- worse than the constant ``k`` that ``constant_k_r2`` scores at
  0.9999 / 0.9994.

Dropping it is also why inference got cheaper: one model load and one predict
per request instead of two.
"""
from ml.pipeline.config import (  # noqa: F401
    ENERGY_DERIVED,
    ENERGY_FEATURES,
    POWER_FEATURES,
    POWER_MODEL_NAME,
)
from ml.pipeline.data import feature_row, load_datasets  # noqa: F401

__all__ = [
    "ENERGY_DERIVED",
    "ENERGY_FEATURES",
    "POWER_FEATURES",
    "POWER_MODEL_NAME",
    "feature_row",
    "load_datasets",
]

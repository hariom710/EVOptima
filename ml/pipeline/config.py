"""Feature contracts, model factories and artifact paths.

Everything the training code and the serving layer must agree on lives here, so
the two cannot drift apart: the notebook imports the same feature lists the
Django view builds, and ``meta.json`` records them for the loader to assert.
"""
from __future__ import annotations

from collections.abc import Callable

# --------------------------------------------------------------- model names
POWER_MODEL_NAME = "power"

#: There is deliberately only one model. ``Charging Rate_kW x
#: Charging_Duration_h`` is *exactly* ``Energy Supplied_kWh`` in both supplied
#: CSVs, and ``Charging Power_kW`` equals ``V x I / 1000`` to within 0.14 %, so
#: the quantity a second model would have to predict is ``power x duration`` --
#: already determined the moment the operator presses submit. See
#: ``ENERGY_FEATURES`` below for the measurements that ruled a second model out.
#:
#: The legacy artifacts at the root of ``settings.MODEL_DIR`` are therefore the
#: power model -- that is what ``registry.get()`` has always returned.
DEFAULT_MODEL_NAME = POWER_MODEL_NAME

# ------------------------------------------------------------------ targets
POWER_TARGET = "Charging Power_kW"
ENERGY_TARGET = "Energy Supplied_kWh"
#: ``Energy / Duration``. Stationary, unlike the cumulative target.
RATE_TARGET = "Charging Rate_kW"

#: Calendar features derived from ``Timestamp``.
CALENDAR_FEATURES = ["hour", "day_of_week", "month", "is_weekend"]

#: Inputs the prediction form collects, in the order the models consume them.
FORM_INPUTS = {
    "Charging Voltage_V": ("voltage", "V"),
    "Charging Current_A": ("current", "A"),
    "Battery Temperature_C": ("battery_temp", "\u00b0C"),
    "State Of Charge_SoC": ("soc", "%"),
}

# ------------------------------------------------------------- feature sets
#: XGBoost power model. Grounded in P = V * I, so it is learnable in both
#: operating regimes (blocked-CV R2 = 0.9971).
#:
#: Calendar features *are* wanted here: the series is only observed between
#: 00:00 and 07:00, so a prediction taken during the working day is outside
#: the training range, and without ``month`` the model misses V*I by 7.2 kW at
#: 600 V / 80 A instead of 0.3 kW. ``month`` is also what disambiguates the
#: low-power and high-power sources at a shared (V, I) point.
POWER_FEATURES = [
    "Charging Voltage_V",
    "Charging Current_A",
    "Battery Temperature_C",
    "State Of Charge_SoC",
    "hour",
    "day_of_week",
    "month",
]

#: Feature list the leakage audit evaluates the *rejected* energy formulation
#: against. Nothing trained on it ships -- see ``SPECS``.
#:
#: Kept because it is the evidence: with these three inputs the target
#: ``Energy / Duration`` cannot be learned. ``Charging Rate_kW x
#: Charging_Duration_h`` *is* ``Energy Supplied_kWh`` exactly, so the ratio is
#: the running mean of a stationary power series: coefficient of variation
#: 2.55 % (d1) and 3.22 % (d2) against 43.88 % / 47.41 % for
#: ``Charging Power_kW``, with ``corr(rate, power) = +0.035``. A model fitted
#: here does not learn a response to current, it learns a threshold that says
#: "which dataset is this" -- the shipped one answered 4.392 kW or 21.668 kW and
#: nothing in between, and served 75.2 % mean absolute error against
#: ``power * duration`` (251.3 % worst case).
ENERGY_FEATURES = [
    "Charging Power_kW",
    "Battery Temperature_C",
    "State Of Charge_SoC",
]

# ------------------------------------------------------------------ protocol
CHRONOLOGICAL_HOLDOUT = 0.2
BLOCKED_CV_SPLITS = 5
RANDOM_STATE = 42

PROTOCOL = (
    "chronological 80/20 holdout plus TimeSeriesSplit("
    f"{BLOCKED_CV_SPLITS}) blocked CV, run independently within each source "
    "so no fold spans the d1 -> d2 operating-regime boundary"
)

# ------------------------------------------------------------ model factories
def power_estimator():
    """XGBoost regressor for charging power.

    Chosen over RandomForest on latency and size rather than accuracy: it is
    0.35 ms/row and 3.2 MB against RandomForest's 28.8 ms/row and 87.8 MB, for
    0.0025 of blocked-CV R2.
    """
    import xgboost as xgb

    return xgb.XGBRegressor(
        n_estimators=500,
        max_depth=7,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        min_child_weight=3,
        reg_lambda=1.0,
        tree_method="hist",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )


def energy_estimator():
    """HistGradientBoostingRegressor, used only to *audit* the rejected target.

    The audit has to fit the formulation it is ruling out in order to report a
    number for it; keeping the factory here means the audit and the notebook
    agree on hyperparameters. No instance of this is exported.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(
        max_iter=500,
        learning_rate=0.05,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=RANDOM_STATE,
    )


#: The single shipped model. A second entry would mean a second artifact to
#: version, load and keep in step -- for a quantity the view can compute
#: exactly in one line (``ENERGY_DERIVED``).
ESTIMATORS: dict[str, Callable[[], object]] = {
    POWER_MODEL_NAME: power_estimator,
}

#: How the view gets kWh. Exact rather than learned: ``Charging Rate_kW x
#: Charging_Duration_h`` equals ``Energy Supplied_kWh`` to 0.00000000 kWh and
#: ``integral(P dt)`` to within 0.098 kWh per row across both CSVs, and
#: ``Charging Power_kW`` equals ``V x I / 1000`` to within 0.14 % -- so once the
#: power model has forecast P the energy is arithmetic.
ENERGY_DERIVED = "energy_kwh = charging_power_kw * duration_h"

#: Model metadata -- which features, which target, and how the output is turned
#: into the value the UI shows.
SPECS: dict[str, dict] = {
    POWER_MODEL_NAME: {
        "algorithm": "XGBRegressor",
        "target": POWER_TARGET,
        "features": POWER_FEATURES,
        "output": "charging_power_kw",
        "derived": ENERGY_DERIVED,
        "estimator": power_estimator,
    },
}

# --------------------------------------------------------------- file names
MODEL_FILENAME = "model.joblib"
SCALER_FILENAME = "scaler.joblib"
META_FILENAME = "meta.json"
CURRENT_FILENAME = "current.json"

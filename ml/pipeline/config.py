"""Feature contracts, model factories and artifact paths.

Everything the training code and the serving layer must agree on lives here, so
the two cannot drift apart: the notebook imports the same feature lists the
Django view builds, and ``meta.json`` records them for the loader to assert.
"""
from __future__ import annotations

from collections.abc import Callable

# --------------------------------------------------------------- model names
POWER_MODEL_NAME = "power"
ENERGY_MODEL_NAME = "energy"

#: The legacy artifacts at the root of ``settings.MODEL_DIR`` are the energy
#: model -- that is what ``registry.get()`` has always returned.
DEFAULT_MODEL_NAME = ENERGY_MODEL_NAME

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

#: HistGB energy model -- predicts the *average charging rate* (kW), not
#: cumulative kWh. Duration is deliberately absent (it is the ramp the
#: cumulative target is built from) and is reapplied by the view as
#: ``energy = rate * duration``.
#:
#: Calendar features are excluded here on purpose, unlike the power model.
#: The two sources are a month apart, so ``month`` and ``day_of_week`` act as
#: perfect dataset identifiers: with them present the model answers 26.98 kW
#: for *any* current the operator enters, and a 12 kWh session comes back as
#: 54 kWh. Dropping them costs 0.0015 of blocked-CV R2 (0.9978 vs 0.9993) and
#: makes the prediction respond to the physical inputs again. Regime
#: information still reaches this model through the power model's output.
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
    """HistGradientBoostingRegressor for the average charging rate."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(
        max_iter=500,
        learning_rate=0.05,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=RANDOM_STATE,
    )


ESTIMATORS: dict[str, Callable[[], object]] = {
    POWER_MODEL_NAME: power_estimator,
    ENERGY_MODEL_NAME: energy_estimator,
}

#: Model metadata -- which features, which target, and how the output is turned
#: into the value the UI shows.
SPECS: dict[str, dict] = {
    POWER_MODEL_NAME: {
        "algorithm": "XGBRegressor",
        "target": POWER_TARGET,
        "features": POWER_FEATURES,
        "output": "charging_power_kw",
        "derived": None,
        "estimator": power_estimator,
    },
    ENERGY_MODEL_NAME: {
        "algorithm": "HistGradientBoostingRegressor",
        "target": RATE_TARGET,
        "features": ENERGY_FEATURES,
        "output": "charging_rate_kw",
        "derived": "energy_kwh = charging_rate_kw * duration_h",
        "estimator": energy_estimator,
    },
}

# --------------------------------------------------------------- file names
MODEL_FILENAME = "model.joblib"
SCALER_FILENAME = "scaler.joblib"
META_FILENAME = "meta.json"
CURRENT_FILENAME = "current.json"

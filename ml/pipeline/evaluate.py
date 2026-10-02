"""Honest evaluation: chronological holdout plus blocked cross-validation.

Two protocols, both reported because they answer different questions.

**Chronological holdout** -- sort by timestamp, train on the first 80 %, test on
the last 20 %. This is the deployment question: can the model predict the next
chunk of time? A random ``train_test_split`` is not usable here: the series is
sampled every 5-10 s, so row *t+1* is a near-duplicate of row *t* and a random
split leaks the answer into training (it inflates the shipped model's R2 from
0.95 to 1.00).

**Blocked CV** -- ``TimeSeriesSplit`` inside each source, so every fold trains
on the past and tests on the future, and no fold ever spans two sources. Runs
are averaged and reported per source with a row-weighted mean.

Reporting one pooled number over both sources would be misleading: the sources
are different operating regimes a month apart, and the fold that straddles that
boundary scores R2 = 0.72 for a model that scores 0.996 everywhere else.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

from ml.pipeline.config import BLOCKED_CV_SPLITS, CHRONOLOGICAL_HOLDOUT, ENERGY_TARGET
from ml.pipeline.data import TIMESTAMP_COL


@dataclass
class Score:
    """One protocol's result on one source."""

    r2: float
    mae: float
    n: int
    folds: list[float] = field(default_factory=list)

    @property
    def sd(self) -> float:
        return float(np.std(self.folds)) if self.folds else 0.0

    def as_dict(self) -> dict:
        return {"r2": round(self.r2, 4), "mae": round(self.mae, 4), "n": self.n, "sd": round(self.sd, 4)}


def chronological_holdout(frame: pd.DataFrame, frac: float = CHRONOLOGICAL_HOLDOUT):
    """Train on the past, test on the future -- within one source."""
    ordered = frame.sort_values(TIMESTAMP_COL).reset_index(drop=True)
    cut = int(len(ordered) * (1 - frac))
    return ordered.iloc[:cut], ordered.iloc[cut:]


def _score(y_true, y_pred) -> tuple[float, float]:
    return float(r2_score(y_true, y_pred)), float(mean_absolute_error(y_true, y_pred))


def evaluate_chronological(
    frame: pd.DataFrame,
    features: list[str],
    target: str,
    estimator: Callable[[], object],
    *,
    metric_target: str | None = None,
    integrate: Callable[[np.ndarray, pd.DataFrame], np.ndarray] | None = None,
    scaler=None,
) -> Score:
    """Fit on the first 80 % of ``frame`` and score on the last 20 %."""
    train, test = chronological_holdout(frame)
    model = _fit(estimator, train, features, target, scaler)
    y_hat = _predict(model, test, features, scaler, integrate)
    # Score against the cumulative quantity even though the model emits a rate.
    r2, mae = _score(test[metric_target or target], y_hat)
    return Score(r2=r2, mae=mae, n=len(test))


def evaluate_blocked(
    frame: pd.DataFrame,
    features: list[str],
    target: str,
    estimator: Callable[[], object],
    *,
    metric_target: str | None = None,
    integrate: Callable[[np.ndarray, pd.DataFrame], np.ndarray] | None = None,
    n_splits: int = BLOCKED_CV_SPLITS,
    scaler_factory: Callable[[], object] | None = None,
) -> Score:
    """``TimeSeriesSplit`` inside one source: train on past blocks, test on the next.

    ``integrate`` lets a model predict a *rate* while the metric is scored
    against the cumulative quantity (see ``ml.pipeline.config.SPECS``).
    """
    ordered = frame.dropna(subset=[metric_target or target]).sort_values(
        TIMESTAMP_COL
    ).reset_index(drop=True)
    x = ordered[features].fillna(0)
    y = ordered[target]
    truth = ordered[metric_target or target].values

    r2s: list[float] = []
    maes: list[float] = []
    fold_sizes: list[int] = []
    for train_idx, test_idx in TimeSeriesSplit(n_splits=n_splits).split(x):
        # The rate is undefined at duration == 0, so those rows are excluded
        # from *fitting* but still counted in the score (the view predicts 0).
        fitted = y.iloc[train_idx].notna()
        fold_scaler = scaler_factory() if scaler_factory else None
        model = _fit_from(
            estimator, x.iloc[train_idx][fitted], y.iloc[train_idx][fitted], fold_scaler
        )
        pred = _predict_from(model, x.iloc[test_idx], fold_scaler, integrate, ordered.iloc[test_idx])
        r2, mae = _score(truth[test_idx], pred)
        r2s.append(r2)
        maes.append(mae)
        fold_sizes.append(len(test_idx))

    # Fold-level mean, weighted by fold size, matches a pooled R2 closely enough
    # to be comparable while still exposing the spread across folds.
    weights = np.asarray(fold_sizes, dtype=float)
    pooled_r2 = float(np.average(r2s, weights=weights))
    pooled_mae = float(np.average(maes, weights=weights))
    return Score(r2=pooled_r2, mae=pooled_mae, n=len(ordered), folds=r2s)


def per_source_report(
    datasets: dict[str, pd.DataFrame],
    features: list[str],
    target: str,
    estimator: Callable[[], object],
    *,
    metric_target: str | None = None,
    integrate: Callable[[np.ndarray, pd.DataFrame], np.ndarray] | None = None,
    scaler_factory: Callable[[], object] | None = None,
) -> dict:
    """Chronological + blocked CV for every source, plus a row-weighted mean.

    The weighted mean is the headline number; the per-source rows are what make
    it auditable, because they show *where* a model generalises.
    """
    chrono: dict[str, Score] = {}
    blocked: dict[str, Score] = {}
    for source, frame in datasets.items():
        chrono[source] = evaluate_chronological(
            frame, features, target, estimator, metric_target=metric_target, integrate=integrate
        )
        blocked[source] = evaluate_blocked(
            frame,
            features,
            target,
            estimator,
            metric_target=metric_target,
            integrate=integrate,
            scaler_factory=scaler_factory,
        )

    total = sum(s.n for s in blocked.values())
    weighted = sum(s.r2 * s.n for s in blocked.values()) / total
    return {
        "chronological_holdout": {k: v.as_dict() for k, v in chrono.items()},
        "blocked_cv": {k: v.as_dict() for k, v in blocked.items()},
        "weighted_blocked_cv_r2": round(weighted, 4),
        "n_rows": total,
        "protocol": "chronological + blocked CV within source",
    }


# ------------------------------------------------------------------ helpers
def _fit(estimator, frame, features, target, scaler):
    x = frame[features].fillna(0)
    y = frame[target]
    mask = y.notna()
    return _fit_from(estimator, x[mask], y[mask], scaler)


def _fit_from(estimator, x, y, scaler):
    import numpy as np

    values = np.asarray(x)
    if scaler is not None:
        values = scaler.fit_transform(values)
    model = estimator()
    model.fit(values, y)
    return model


def _predict(model, frame, features, scaler, integrate):
    return _predict_from(model, frame[features].fillna(0), scaler, integrate, frame)


def _predict_from(model, x, scaler, integrate, frame):
    import numpy as np

    values = np.asarray(x)
    if scaler is not None:
        values = scaler.transform(values)
    pred = np.asarray(model.predict(values))
    if integrate is not None:
        pred = integrate(pred, frame)
    return pred


def integrate_energy(rate_hat: np.ndarray, frame: pd.DataFrame):
    """``energy = rate x duration`` -- the integration the view also performs."""
    duration = frame["Charging_Duration_h"].fillna(0).to_numpy()
    return np.asarray(rate_hat) * duration


def leakage_ablation(
    datasets: dict[str, pd.DataFrame],
    features: list[str],
    drop: list[str],
    target: str,
    estimator: Callable[[], object],
    *,
    metric_target: str | None = None,
    integrate: Callable[[np.ndarray, pd.DataFrame], np.ndarray] | None = None,
) -> dict:
    """Blocked-CV R2 with and without ``drop`` -- the size of the leak.

    A large gap means the dropped column is standing in for the target.
    """
    kept = [f for f in features if f not in drop]
    full = per_source_report(
        datasets, features, target, estimator, metric_target=metric_target, integrate=integrate
    )
    ablated = per_source_report(
        datasets, kept, target, estimator, metric_target=metric_target, integrate=integrate
    )
    return {
        "dropped": drop,
        "with": full["weighted_blocked_cv_r2"],
        "without": ablated["weighted_blocked_cv_r2"],
        "delta": round(full["weighted_blocked_cv_r2"] - ablated["weighted_blocked_cv_r2"], 4),
        "per_source_with": {k: v["r2"] for k, v in full["blocked_cv"].items()},
        "per_source_without": {k: v["r2"] for k, v in ablated["blocked_cv"].items()},
    }


def target_is_a_ramp(frame: pd.DataFrame) -> dict:
    """Diagnose the cumulative target: is it just duration in disguise?

    ``corr(Energy, Duration)`` at ~1 and ``corr(Energy, Power)`` at ~0 means the
    target was synthesised as ``k * duration`` and carries no information the
    duration column does not already have.
    """
    duration = frame["Charging_Duration_h"]
    energy = frame[ENERGY_TARGET]
    return {
        "corr_energy_duration": round(float(np.corrcoef(duration, energy)[0, 1]), 4),
        "corr_energy_power": round(
            float(np.corrcoef(frame["Charging Power_kW"], energy)[0, 1]), 4
        ),
        "constant_k_r2": round(
            float(
                r2_score(
                    energy,
                    frame["Charging Power_kW"].mean() * duration,
                )
            ),
            4,
        ),
    }

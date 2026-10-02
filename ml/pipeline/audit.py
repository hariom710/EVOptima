"""Leakage audit -- the section that decides which features are allowed in.

A feature leaks when it carries the answer rather than a cause. Inside
``ev_charging_data1.csv`` both ``State Of Charge_SoC`` and ``Time Elapsed_s``
correlate +1.0000 with ``Energy Supplied_kWh``, so a model trained with them
scores R2 = 1.00 by copying the ramp and learns nothing about charging.

The audit runs three competing formulations through the *same* blocked-CV
protocol and prints the numbers, so the choice stops being a matter of opinion:

``naive + duration``   predict cumulative kWh with duration as a feature.
                       Scores 1.00 on a random split and collapses to -3.26
                       when held out chronologically -- tree models cannot
                       extrapolate a ramp beyond the durations they were
                       trained on.  Pooled blocked-CV R2: -0.095.
``naive - duration``   same model, duration removed. Still fails on d1 and
                       loses most of d2.  Pooled blocked-CV R2: -0.330.
``deployed (rate)``    predict the stationary rate, then integrate by duration.
                       Generalises on both sources: +0.9977.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score

from ml.pipeline.config import ENERGY_TARGET, POWER_TARGET, RATE_TARGET
from ml.pipeline.data import TIMESTAMP_COL, combined
from ml.pipeline.evaluate import integrate_energy, leakage_ablation, per_source_report

#: Columns that are components of a target rather than causes of it. None of
#: them may be fed to a model as an *input*.
TAUTOLOGY_COLUMNS = ["Time Elapsed_s", "Charging_Duration_h"]
TARGET_LIKE_COLUMNS = ["Energy Supplied_kWh", "Charging Rate_kW", "Charging Power_kW"]

#: Why each column is excluded from the feature sets.
EXCLUSION_REASONS = {
    "Time Elapsed_s": "cumulative elapsed time -- the ramp the target is built from",
    "Charging_Duration_h": "same ramp; reapplied explicitly as energy = rate * duration",
    "Energy Supplied_kWh": "the cumulative target itself",
    "Charging Rate_kW": "the energy model's training target",
    "Charging Power_kW": "the power model's training target (an input to the energy model)",
    "Time-lap": "constant (always 10 s) -- carries no information",
    "Regenerative Power_kW": "all zeros in ev_charging_data1.csv",
    "Battery Voltage_BV": "near-duplicate of Charging Voltage_V; ev_charging_data2 only",
    "Battery Current_IB": "near-duplicate of Charging Current_A; ev_charging_data2 only",
}

#: ``State Of Charge_SoC`` correlates +1.0 with the cumulative target inside
#: ``ev_charging_data1.csv``, so on the naive formulation it is a leak. The
#: deployed model predicts the *rate* instead, where that correlation does not
#: hold: dropping SoC costs 0.0102 of blocked-CV R2 (0.9977 -> 0.9875), which is
#: why it is retained. ``audit()`` records the measurement as ``soc_ablation``
#: rather than asserting it in prose.
RETAINED_DESPITE_CORRELATION = "State Of Charge_SoC"

#: The naive formulation's feature set: the deployed one plus duration.
DURATION_COLUMN = "Charging_Duration_h"


def safe_corr(left: pd.Series, right: pd.Series) -> float:
    """Pearson correlation, 0.0 for a constant input.

    ``np.corrcoef`` returns NaN and warns when a column never varies -- two of
    the supplied columns are constant, and an audit that prints ``nan`` for
    them reads like a defect rather than a fact about the data.
    """
    a = left.fillna(0).to_numpy(dtype=float)
    b = right.fillna(0).to_numpy(dtype=float)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def correlations(frame: pd.DataFrame, target: str, top: int = 12) -> dict[str, float]:
    """Pearson correlation of every numeric column against ``target``."""
    numeric = frame.select_dtypes(include=[np.number])
    values = {
        column: safe_corr(numeric[column], numeric[target])
        for column in numeric.columns
        if column != target
    }
    top_values = sorted(values.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top]
    return {k: round(v, 4) for k, v in top_values}


def univariate_r2(frame: pd.DataFrame, target: str, top: int = 12) -> dict[str, float]:
    """R2 of a *single* feature fitted alone -- the cheapest leak detector."""
    from sklearn.tree import DecisionTreeRegressor

    y = frame[target].fillna(0)
    scores: dict[str, float] = {}
    for column in frame.select_dtypes(include=[np.number]).columns:
        if column == target:
            continue
        model = DecisionTreeRegressor(max_depth=8, random_state=0).fit(
            frame[[column]].fillna(0), y
        )
        scores[column] = float(r2_score(y, model.predict(frame[[column]].fillna(0))))
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return {k: round(v, 4) for k, v in ordered}


def feature_importances(model, features: list[str]) -> dict[str, float]:
    """Importance as fitted -- on the shipped model this read 0.54 / 0.46 on the
    two leaked columns and 0.0000 on all six real features."""
    if not hasattr(model, "feature_importances_"):
        return {}
    values = dict(
        zip(features, (float(v) for v in model.feature_importances_), strict=True)
    )
    return {k: round(v, 4) for k, v in sorted(values.items(), key=lambda kv: kv[1], reverse=True)}


def audit(
    datasets: dict[str, pd.DataFrame],
    *,
    energy_features: list[str],
    energy_estimator,
    power_features: list[str],
    power_estimator,
) -> dict:
    """Full audit as a JSON-serialisable dict (written to ``meta.json``)."""
    frame = combined(datasets).drop(columns=[TIMESTAMP_COL], errors="ignore")
    naive_with = energy_features + [DURATION_COLUMN]

    report: dict = {
        "ramp_diagnosis": {s: _ramp(f) for s, f in datasets.items()},
        "formulations": {},
        "targets": {},
        "allowed_features": {"power": power_features, "energy": energy_features},
        "excluded_features": EXCLUSION_REASONS,
        "retained_despite_correlation": {
            RETAINED_DESPITE_CORRELATION: "see soc_ablation"
        },
    }
    report["ramp_diagnosis"]["pooled"] = _ramp(frame)

    # --- formulation comparison, all under the same protocol ---------------
    report["formulations"]["naive_with_duration"] = per_source_report(
        datasets, naive_with, ENERGY_TARGET, energy_estimator
    )
    report["formulations"]["naive_without_duration"] = per_source_report(
        datasets, energy_features, ENERGY_TARGET, energy_estimator
    )
    report["formulations"]["deployed_rate"] = per_source_report(
        datasets,
        energy_features,
        RATE_TARGET,
        energy_estimator,
        metric_target=ENERGY_TARGET,
        integrate=integrate_energy,
    )

    # --- is SoC carrying the answer? --------------------------------------
    report["soc_ablation"] = leakage_ablation(
        datasets,
        energy_features,
        ["State Of Charge_SoC"],
        RATE_TARGET,
        energy_estimator,
        metric_target=ENERGY_TARGET,
        integrate=integrate_energy,
    )

    # --- per-target diagnostics -------------------------------------------
    for target, features in ((ENERGY_TARGET, energy_features), (POWER_TARGET, power_features)):
        report["targets"][target] = {
            "correlations": correlations(frame, target),
            "univariate_r2": univariate_r2(frame, target),
            "features": features,
        }
    return report


def _ramp(frame: pd.DataFrame) -> dict[str, float]:
    """Is the cumulative target just duration in disguise?"""
    duration = frame[DURATION_COLUMN]
    energy = frame[ENERGY_TARGET]
    return {
        "corr_energy_duration": round(safe_corr(duration, energy), 4),
        "corr_energy_power": round(safe_corr(frame["Charging Power_kW"], energy), 4),
        "constant_k_r2": round(
            float(r2_score(energy, frame["Charging Power_kW"].mean() * duration)), 4
        ),
    }


def render(report: dict) -> str:
    """Human-readable audit for the terminal / notebook."""
    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add("LEAKAGE AUDIT")
    add("=" * 78)

    add("\n[1] Is the cumulative target just duration in disguise?")
    for source, diag in report.get("ramp_diagnosis", {}).items():
        add(
            f"    {source:8} corr(E,D)={diag['corr_energy_duration']:+.4f}"
            f"  corr(E,P)={diag['corr_energy_power']:+.4f}"
            f"  R2(E = k*D)={diag['constant_k_r2']:.4f}"
        )

    add("\n[2] Three formulations, one protocol (blocked-CV R2, row-weighted)")
    for key, label in (
        ("naive_with_duration", "predict kWh with duration    "),
        ("naive_without_duration", "predict kWh without duration "),
        ("deployed_rate", "predict rate, integrate       "),
    ):
        block = report.get("formulations", {}).get(key, {})
        per = "  ".join(f"{s}={v['r2']:+.4f}" for s, v in block.get("blocked_cv", {}).items())
        add(f"    {label} {block.get('weighted_blocked_cv_r2', float('nan')):+.4f}   [{per}]")

    add("\n[3] Drop-State-Of-Charge ablation on the deployed formulation")
    ab = report.get("soc_ablation", {})
    if ab:
        add(
            f"    {ab['with']:+.4f} -> {ab['without']:+.4f}   delta={ab['delta']:+.4f}"
            f"   ({', '.join(ab['dropped'])})"
        )
        add(
            "    Retaining SoC is a measurement, not a guess: it correlates +1.0 with the"
        )
        add("    cumulative target inside ev_charging_data1.csv but not with the rate the")
        add("    deployed model actually predicts.")

    add("\n[4] Univariate R2 (one feature fitted alone, in-sample)")
    for target, block in report.get("targets", {}).items():
        add(f"    target = {target}")
        for column, value in block["univariate_r2"].items():
            add(f"        {column:32} {value:.4f}{'  <-- LEAK' if value > 0.9 else ''}")

    add("\n[5] Columns excluded from every feature set, and why")
    for column, reason in report.get("excluded_features", {}).items():
        add(f"    {column:26} {reason}")

    add("\n[6] Features the shipped models are allowed to use")
    for name, features in report.get("allowed_features", {}).items():
        add(f"    {name:7} ({len(features)}): {', '.join(features)}")
    return "\n".join(lines)

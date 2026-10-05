"""Train, evaluate and export the shipped model.

Layout written to ``settings.MODEL_DIR``::

    current.json                    {"power": "power-v1"}
    model.joblib / scaler.joblib    legacy alias == the active power model,
                                    so ``registry.get()`` keeps working
    power-v1/{model,scaler}.joblib + meta.json

Every version directory is self-describing: ``meta.json`` carries the feature
order, the input ranges the serving layer validates against, the evaluation
metrics for *both* protocols, and the training provenance. The loader asserts
the feature order at load time, so a retrained model cannot silently be fed
columns in a different order.

Only one model is trained. See ``ml.pipeline.config`` for why a second one
cannot be learned from these CSVs; the audit that proves it still runs on every
train and ships as ``leakage_audit.json``.
"""
from __future__ import annotations

import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklearn.preprocessing import StandardScaler

from ml.pipeline import audit as audit_module
from ml.pipeline.config import (
    BLOCKED_CV_SPLITS,
    CHRONOLOGICAL_HOLDOUT,
    CURRENT_FILENAME,
    DEFAULT_MODEL_NAME,
    ENERGY_FEATURES,
    META_FILENAME,
    MODEL_FILENAME,
    POWER_MODEL_NAME,
    PROTOCOL,
    SCALER_FILENAME,
    SPECS,
    energy_estimator,
)
from ml.pipeline.data import combined, load_datasets
from ml.pipeline.evaluate import per_source_report


# --------------------------------------------------------------------- train
def train_one(
    name: str,
    datasets: dict,
    *,
    fitted=None,
) -> tuple[Any, StandardScaler, dict]:
    """Fit one model on all sources and evaluate it under both protocols.

    Returns ``(estimator, scaler, meta)``. The scaler is fitted on the training
    features and applied identically at train and serve time; the tree models
    are invariant to it, but keeping it in the artifact preserves the
    ``(model, scaler)`` contract the serving layer and its tests depend on.
    """
    spec = SPECS[name]
    frame = combined(datasets)
    features = spec["features"]
    target = spec["target"]

    x = frame[features].fillna(0)
    y = frame[target]
    mask = y.notna()

    scaler = StandardScaler()
    values = scaler.fit_transform(x[mask])
    model = spec["estimator"]()
    model.fit(values, y[mask])

    metrics = per_source_report(
        datasets,
        features,
        target,
        spec["estimator"],
        scaler_factory=StandardScaler,
    )

    meta = {
        "name": name,
        "algorithm": spec["algorithm"],
        "target": target,
        "metric_target": target,
        "features": features,
        "output": spec["output"],
        "derived": spec["derived"],
        "scaled": True,
        "protocol": PROTOCOL,
        "evaluation": {
            "chronological_holdout_fraction": CHRONOLOGICAL_HOLDOUT,
            "blocked_cv_splits": BLOCKED_CV_SPLITS,
            **metrics,
        },
        "input_ranges": input_ranges(frame, features),
        "datasets": {s: int(len(f)) for s, f in datasets.items()},
        "training_rows": int(mask.sum()),
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "latency_ms_1row": round(_latency(model, scaler, x.iloc[:1]), 3),
        "size_bytes_model": None,  # filled in by export()
        "environment": {
            "python": platform.python_version(),
            "sklearn": _version("sklearn"),
            "xgboost": _version("xgboost"),
        },
    }
    return model, scaler, meta


def train_all(
    datasets: dict | None = None,
    *,
    names: tuple[str, ...] = (POWER_MODEL_NAME,),
    include_audit: bool = True,
) -> dict[str, dict]:
    """Train each model in ``names``, export it, and return their metadata."""
    datasets = datasets if datasets is not None else load_datasets()

    # The audit runs first so its filename can be recorded in every meta.json
    # before those are written -- it justifies the feature lists below, and it
    # is what documents why no second model is trained here.
    report = None
    if include_audit:
        report = audit_module.audit(
            datasets,
            energy_features=ENERGY_FEATURES,
            energy_estimator=energy_estimator,
            power_features=SPECS[POWER_MODEL_NAME]["features"],
            power_estimator=SPECS[POWER_MODEL_NAME]["estimator"],
        )

    results: dict[str, dict] = {}
    for name in names:
        model, scaler, meta = train_one(name, datasets)
        if report is not None:
            meta["audit_file"] = "leakage_audit.json"
        meta["version"] = _export(name, model, scaler, meta)
        results[name] = meta

    _write_current(results, export_root())
    if report is not None:
        root = export_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "leakage_audit.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    return results


# ------------------------------------------------------------------- export
def export_root() -> Path:
    from django.conf import settings

    return Path(settings.MODEL_DIR)


def _next_version(root: Path, name: str) -> str:
    existing = []
    for path in root.glob(f"{name}-v*"):
        suffix = path.name.rsplit("-v", 1)[-1]
        if suffix.isdigit():
            existing.append(int(suffix))
    return f"{name}-v{max(existing, default=0) + 1}"


def _export(name: str, model, scaler, meta: dict, root: Path | None = None) -> str:
    import joblib

    root = root or export_root()
    root.mkdir(parents=True, exist_ok=True)
    version = _next_version(root, name)
    target_dir = root / version
    target_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, target_dir / MODEL_FILENAME)
    joblib.dump(scaler, target_dir / SCALER_FILENAME)
    meta["version"] = version
    meta["size_bytes_model"] = (target_dir / MODEL_FILENAME).stat().st_size
    (target_dir / META_FILENAME).write_text(
        json.dumps(meta, indent=2, sort_keys=False), encoding="utf-8"
    )

    # Legacy alias: ``registry.get()`` with no name has always read
    # ``model.joblib`` at the root of MODEL_DIR, and the tests + Phase 3 gate
    # assert those two files exist. They hold the active default model, with a
    # copy of its meta.json so the loader can assert feature order either way.
    if name == DEFAULT_MODEL_NAME:
        joblib.dump(model, root / MODEL_FILENAME)
        joblib.dump(scaler, root / SCALER_FILENAME)
        (root / META_FILENAME).write_text(
            json.dumps(meta, indent=2, sort_keys=False), encoding="utf-8"
        )
    return version


def _write_current(results: dict[str, dict], root: Path) -> None:
    """Point ``current.json`` at the freshly trained versions.

    Existing entries are kept so retraining one model does not unset the other,
    but an entry is *dropped* when the directory it names is gone. Without that
    prune, retiring a model leaves ``current.json`` naming a version git never
    shipped -- the loader then fails on a clone that was working a commit ago.
    """
    pointers: dict[str, str] = {}
    path = root / CURRENT_FILENAME
    if path.is_file():
        try:
            pointers = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # pragma: no cover - corrupted pointer file
            pointers = {}
    for name, meta in results.items():
        pointers[name] = meta["version"]
    dangling = [
        name
        for name, version in pointers.items()
        if not (root / str(version)).is_dir()
    ]
    for name in dangling:
        del pointers[name]
    path.write_text(json.dumps(pointers, indent=2, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ helpers
def input_ranges(frame, features: list[str]) -> dict[str, list[float]]:
    """Min/max per feature over the training data.

    The serving layer compares user input against this and raises a warning
    event when something lands outside -- the old code fed 50 kW into a model
    trained on 1-7 kW, a z-score of +26.6, without noticing.
    """
    ranges: dict[str, list[float]] = {}
    for feature in features:
        series = frame[feature].dropna()
        if series.empty:
            continue
        ranges[feature] = [round(float(series.min()), 4), round(float(series.max()), 4)]
    return ranges


def _latency(model, scaler, sample) -> float:
    """Mean wall-clock milliseconds for a single-row prediction."""
    values = scaler.transform(sample)
    model.predict(values)  # warm up
    start = time.perf_counter()
    for _ in range(50):
        model.predict(values)
    return (time.perf_counter() - start) / 50 * 1000


def _version(package: str) -> str:
    try:
        from importlib.metadata import version

        return version(package)
    except Exception:  # pragma: no cover - metadata always present in practice
        return "unknown"


def summarise(results: dict[str, dict]) -> str:
    """Terminal summary of what was trained and how it scored."""
    lines: list[str] = []
    for name, meta in results.items():
        ev = meta["evaluation"]
        lines.append(f"{name} ({meta['algorithm']}) -> {meta['version']}")
        lines.append(f"    target      : {meta['target']}")
        lines.append(f"    features    : {', '.join(meta['features'])}")
        if meta.get("derived"):
            lines.append(f"    derived     : {meta['derived']}")
        blocked = ev["blocked_cv"]
        per_source = ", ".join(f"{source}={values['r2']:+.4f}" for source, values in blocked.items())
        lines.append(
            f"    blocked CV  : {ev['weighted_blocked_cv_r2']:+.4f}  [{per_source}]"
        )
        chrono = ev["chronological_holdout"]
        lines.append(
            "    chrono 80/20: "
            + ", ".join(f"{source}={values['r2']:+.4f}" for source, values in chrono.items())
        )
        lines.append(
            f"    cost        : {meta['latency_ms_1row']:.3f} ms/row, "
            f"{(meta['size_bytes_model'] or 0) / 1e6:.2f} MB"
        )
        lines.append(f"    trained_at  : {meta['trained_at']}")
        lines.append("")
    return "\n".join(lines)

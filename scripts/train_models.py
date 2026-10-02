"""Train and export the prediction models.

Runs the full protocol -- leakage audit, chronological holdout, blocked
cross-validation -- and writes versioned artifacts plus ``meta.json``:

    ml/artifacts/
        current.json                      active version per model
        model.joblib / scaler.joblib      legacy alias (= active energy model)
        power-vN/{model,scaler}.joblib + meta.json
        energy-vN/{model,scaler}.joblib + meta.json
        leakage_audit.json                the audit that justified the features

Usage::

    python scripts/train_models.py                 # train both models
    python scripts/train_models.py --only power    # retrain one model
    python scripts/train_models.py --no-audit      # skip the leakage audit
    python scripts/train_models.py --report        # print the audit and exit
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django

django.setup()

from ml.pipeline import audit as audit_module  # noqa: E402
from ml.pipeline.config import (  # noqa: E402
    ENERGY_FEATURES,
    ENERGY_MODEL_NAME,
    ENERGY_TARGET,
    POWER_FEATURES,
    POWER_MODEL_NAME,
    POWER_TARGET,
    SPECS,
)
from ml.pipeline.data import load_datasets  # noqa: E402
from ml.pipeline.train import export_root, summarise, train_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=[POWER_MODEL_NAME, ENERGY_MODEL_NAME],
        help="train a single model instead of both",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="skip the leakage audit (faster; ~1 min of refits)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the stored leakage audit and exit without training",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="directory holding the CSVs (defaults to settings.DATA_DIR)",
    )
    args = parser.parse_args(argv)

    if args.report:
        print(_stored_audit())
        return 0

    datasets = load_datasets(args.data_dir)
    total = sum(len(f) for f in datasets.values())
    print(f"Loaded {len(datasets)} source(s), {total} rows: "
          + ", ".join(f"{k}={len(v)}" for k, v in datasets.items()))
    print(f"Artifacts -> {export_root()}\n")

    names = (args.only,) if args.only else (POWER_MODEL_NAME, ENERGY_MODEL_NAME)
    results = train_all(datasets, names=names, include_audit=not args.no_audit)

    print(summarise(results))

    if not args.no_audit:
        print(_stored_audit())
    print(_serving_summary(results))
    return 0


def _stored_audit() -> str:
    import json

    path = export_root() / "leakage_audit.json"
    if not path.is_file():
        return (
            "No leakage_audit.json yet -- run `python scripts/train_models.py` "
            "to produce one."
        )
    return audit_module.render(json.loads(path.read_text(encoding="utf-8")))


def _serving_summary(results: dict) -> str:
    lines = ["-" * 78, "SERVING", "-" * 78]
    for name, meta in results.items():
        spec = SPECS[name]
        lines.append(f"{name}: {spec['algorithm']}")
        lines.append(f"    in : {', '.join(spec['features'])}")
        lines.append(f"    out: {spec['output']}"
                     + (f"   -> {spec['derived']}" if spec.get("derived") else ""))
        ranges = meta["input_ranges"]
        physical = [f for f in ("Charging Voltage_V", "Charging Current_A",
                                "Battery Temperature_C", "State Of Charge_SoC") if f in ranges]
        for feature in physical:
            lo, hi = ranges[feature]
            lines.append(f"    range {feature:26} [{lo}, {hi}]")
    lines.append("")
    lines.append("Power target : " + POWER_TARGET)
    lines.append("Energy target: " + ENERGY_TARGET
                 + " (model emits the rate; the view multiplies by duration)")
    lines.append(f"Power features : {', '.join(POWER_FEATURES)}")
    lines.append(f"Energy features: {', '.join(ENERGY_FEATURES)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

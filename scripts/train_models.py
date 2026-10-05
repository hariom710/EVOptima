"""Train and export the prediction model.

Runs the full protocol -- leakage audit, chronological holdout, blocked
cross-validation -- and writes versioned artifacts plus ``meta.json``:

    ml/artifacts/
        current.json                      active version per model
        model.joblib / scaler.joblib      legacy alias (= active power model)
        power-vN/{model,scaler}.joblib + meta.json
        leakage_audit.json                the audit that justified the features

Usage::

    python scripts/train_models.py                 # train the model
    python scripts/train_models.py --only power    # same, explicitly
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
    ENERGY_DERIVED,
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
        choices=[POWER_MODEL_NAME],
        help="train a single named model",
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

    names = (args.only,) if args.only else (POWER_MODEL_NAME,)
    results = train_all(datasets, names=names, include_audit=not args.no_audit)

    print(summarise(results))

    if not args.no_audit:
        print(_stored_audit())
    print(_serving_summary(results))
    note = _shipping_note(results)
    if note:
        print(note)
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
    lines.append("Energy       : " + ENERGY_DERIVED + "   (exact, not learned)")
    lines.append(f"Power features: {', '.join(POWER_FEATURES)}")
    return "\n".join(lines)


def _shipping_note(results: dict) -> str:
    """Warn when a freshly trained version is not committed to git.

    ``.gitignore`` ignores ``ml/artifacts/*-v[0-9]*`` so a retrain does not add
    ~3.5 MB to the repo, while a ``!`` rule keeps the shipped ``power-v1``
    tracked. Promoting a new version is therefore a two-step move, and doing
    only the ``current.json`` half silently re-breaks a fresh clone:
    the pointer would name a directory git never received.
    """
    lines: list[str] = []
    for name, meta in results.items():
        version = meta.get("version")
        target = export_root() / str(version) / "model.joblib"
        if not version or not target.is_file() or _is_git_tracked(target):
            continue
        lines += [
            "-" * 78,
            "SHIPPING NOTE",
            f"{name} -> {version} is NOT tracked by git, but current.json now",
            "points at it. A fresh clone would therefore have no",
            f"{name} model and /prediction/ would fail.",
            "",
            "To promote it, add this line to .gitignore:",
            f"    !ml/artifacts/{version}/",
            "then commit the rule together with the artifacts:",
            f"    git add .gitignore ml/artifacts/{version} "
            "ml/artifacts/current.json ml/artifacts/meta.json",
        ]
    return "\n".join(lines)


def _is_git_tracked(path: Path) -> bool:
    """True when ``path`` is already tracked; assumes true if git is absent."""
    import subprocess

    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:  # pragma: no cover - artifact outside the repo
        return True
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(rel)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return True
    return proc.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())

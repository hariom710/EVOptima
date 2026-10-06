"""Why the power model answered what it answered.

``meta.json`` says how good the model is on held-out rows; it says nothing
about the row in front of the operator. This module attributes one prediction
across the seven inputs that produced it, so ``/prediction/`` can show the
arithmetic instead of asking to be trusted.

The method is exact TreeSHAP, and it ships inside XGBoost -- no ``shap``
package, no new dependency. ``XGBRegressor.predict()`` cannot expose it (in
xgboost 3.0 it takes ``output_margin``/``validate_features``/``iteration_range``
and nothing else), so the call goes through the booster::

    contribs = model.get_booster().predict(
        DMatrix(X, feature_names=features), pred_contribs=True
    )            # -> [bias, shap_1 ... shap_n]

For ``reg:squarederror`` the margin *is* the prediction -- there is no link
function in between -- so every number below is already in kW.

Measured, not asserted (``tests/test_explain.py`` re-checks each one):

* **Additivity.** ``bias + sum(contributions)`` reproduces the served
  prediction to **5.1e-05 kW** on an in-range, a shifted and an out-of-range
  row -- four orders of magnitude below the 0.01 kW the page prints. The
  difference is reported as ``residual_kw`` rather than hidden, and the test
  bounds it at 1e-03 kW.
* **The bias is a property of the model, not of the row**: 21.6280 kW on all
  three probe rows. It is the ensemble's constant prior, landing within
  0.006 kW of the mean of ``Charging Power_kW`` over the 12,093 training rows
  (21.6224) -- the model's "average answer" before it has looked at your
  inputs. Restating that as ``prior + contributions = answer`` is exactly what
  the page prints.
* **Ordering.** Voltage and current carry 71-87 % of the total absolute
  attribution, which a ``V x I`` target must; the test pins a conservative
  0.50 floor so that a model which stopped responding to its own inputs fails
  loudly.

Two things this deliberately does not do:

* It does not explain *energy*. ``Charging_Duration_h`` is not a model input
  -- energy is ``power * duration`` arithmetic (see ``ml/pipeline/__init__``),
  so duration belongs to the second line of the page, not this one.
* It does not excuse an out-of-distribution row. ``/prediction/`` renders the
  breakdown *and* the OOD warning together; an extrapolation explained is not
  an extrapolation validated.

Cost: 8-14 ms per row against 0.6-1.5 ms for the prediction itself, paid once
per port on submit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xgboost as xgb


class ExplanationNotSupported(RuntimeError):
    """The artifact cannot attribute its own output.

    Raised when the loaded model is not a Booster-backed estimator (a future
    artifact swap, or a stub in a test). Callers degrade to no breakdown and
    record the reason -- never to an empty one, which would read as "this
    input does not matter".
    """


@dataclass(frozen=True)
class Contribution:
    """One input's share of one prediction.

    ``raw_value`` is what the operator typed, not the z-score the model was
    fed -- showing a scaled value would make the table uncheckable.
    ``bar_pct`` scales ``|kw|`` against the largest contribution in the same
    explanation, so bars are comparable within a row (they are not
    comparable between rows, and the module does not pretend otherwise).
    """

    feature: str
    raw_value: float
    kw: float
    bar_pct: int

    @property
    def pushes_up(self) -> bool:
        return self.kw >= 0.0


@dataclass(frozen=True)
class Explanation:
    """``bias_kw + sum(contributions) ~= predicted_kw`` -- all in kW."""

    predicted_kw: float
    bias_kw: float
    contributions: tuple[Contribution, ...]
    residual_kw: float

    @property
    def reconstructed_kw(self) -> float:
        """What the attributions add up to; the page prints the sum itself."""
        return self.bias_kw + sum(c.kw for c in self.contributions)

    @property
    def abs_total_kw(self) -> float:
        """Sum of ``|kw|`` -- the denominator for any share of the answer."""
        return sum(abs(c.kw) for c in self.contributions)

    @property
    def dominant(self) -> Contribution | None:
        """The input that moved the prediction furthest, if any moved it."""
        if not self.contributions:
            return None
        return max(self.contributions, key=lambda c: abs(c.kw))

    @property
    def dominant_share(self) -> float:
        """``|dominant| / sum(|kw|)``, 0.0 when nothing moved."""
        total = self.abs_total_kw
        if total == 0.0 or self.dominant is None:
            return 0.0
        return abs(self.dominant.kw) / total


def explain_power(
    *,
    model,
    scaled_row,
    feature_names: list[str],
    raw_inputs: dict,
    predicted_kw: float,
) -> Explanation:
    """Attribute ``predicted_kw`` across ``feature_names``.

    ``scaled_row`` must be the *same transformed row* that produced
    ``predicted_kw``: TreeSHAP explains the tree ensemble it is handed, and a
    row scaled by anything else would attribute a prediction the model never
    made. ``predicted_kw`` is taken from the caller rather than recomputed so
    that ``residual_kw`` is a genuine check against the value already shown to
    the operator instead of a difference of a value against itself.
    """
    if not hasattr(model, "get_booster"):
        raise ExplanationNotSupported(
            f"{type(model).__name__} has no Booster, so it cannot attribute "
            "its own output (pred_contribs is an XGBoost interface)"
        )

    missing = [name for name in feature_names if name not in raw_inputs]
    if missing:
        # Same failure mode _feature_frame() refuses to paper over: meta.json
        # and the serving code disagree, and a default of 0 would invent a
        # contribution for an input nobody supplied.
        raise ValueError("no value for model input(s): " + ", ".join(missing))

    matrix = np.atleast_2d(np.asarray(scaled_row, dtype=float))
    if matrix.shape[1] != len(feature_names):
        raise ValueError(
            f"scaled_row has {matrix.shape[1]} columns, meta.json declares "
            f"{len(feature_names)} features"
        )

    # feature_names makes XGBoost itself raise on an ordering drift instead of
    # silently attributing a column to the wrong input.
    contributions_raw = model.get_booster().predict(
        xgb.DMatrix(matrix, feature_names=list(feature_names)),
        pred_contribs=True,
    )[0]

    bias_kw = float(contributions_raw[-1])
    shap_kw = np.asarray(contributions_raw[:-1], dtype=float)
    max_abs = float(np.abs(shap_kw).max()) if shap_kw.size else 0.0

    contributions = tuple(
        Contribution(
            feature=name,
            raw_value=float(raw_inputs[name]),
            kw=float(value),
            # Round rather than truncate: a real but small effect should still
            # draw a sliver, and 0.5 kW of a 26 kW swing is not 0 %.
            bar_pct=int(round(100.0 * abs(float(value)) / max_abs)) if max_abs else 0,
        )
        for name, value in zip(feature_names, shap_kw, strict=True)
    )

    return Explanation(
        predicted_kw=float(predicted_kw),
        bias_kw=bias_kw,
        contributions=contributions,
        residual_kw=bias_kw + float(shap_kw.sum()) - float(predicted_kw),
    )

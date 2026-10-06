"""`ml/pipeline/explain.py` and the breakdown it puts on `/prediction/`.

The property everything else hangs off is additivity: TreeSHAP attributes a
prediction, so ``bias + sum(contributions)`` must land back on the number the
page already showed the operator. ``predicted_kw`` is therefore taken from the
caller rather than recomputed inside -- a difference of a value against itself
would be true even if the attributions were nonsense.

Measured on the shipped artifact (see the module docstring for why each one
matters): residual <= 5.1e-05 kW, bias 21.6280 kW on every row, voltage and
current carrying 71-87 % of the attribution.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest
from django.urls import reverse

from apps.monitoring.models import EventLog
from core.model_registry import registry
from ml.pipeline.config import POWER_MODEL_NAME, POWER_TARGET
from ml.pipeline.data import calendar_values, combined, load_datasets
from ml.pipeline.explain import ExplanationNotSupported, explain_power

#: hour 4, Wednesday, February -- inside every range in meta.json.
TIMESTAMP = "2025-02-20T04:00"
#: hour=12 and month=1 stray; the existing OOD tests use this one too.
TIMESTAMP_OUT_OF_RANGE = "2026-01-01T12:00"

FEATURE_OF_INTEREST = ("Charging Voltage_V", "Charging Current_A")


def _inputs(**overrides) -> dict:
    values = {
        "Charging Voltage_V": 400.0,
        "Charging Current_A": 32.5,
        "Battery Temperature_C": 25.0,
        "State Of Charge_SoC": 50.0,
        **calendar_values(datetime.datetime(2025, 2, 20, 4, 0)),
    }
    values.update(overrides)
    return values


def _form_fields(**overrides) -> dict:
    values = {
        "form0-battery_temp": "25",
        "form0-soc": "50",
        "form0-duration": "2",
        "form0-timestamp": TIMESTAMP,
    }
    values.update(overrides)
    return values


@pytest.fixture(scope="module")
def power():
    """The shipped artifact -- the only thing this module can legitimately test."""
    if not registry.available(POWER_MODEL_NAME):
        pytest.skip("power artifact not shipped")
    model, scaler = registry.get(POWER_MODEL_NAME)
    return model, scaler, registry.meta(POWER_MODEL_NAME)


def _explain(power, inputs):
    """Mirror the serving path exactly: same row, same transform, same predict."""
    model, scaler, meta = power
    row = pd.DataFrame(
        [[inputs[name] for name in meta["features"]]], columns=meta["features"]
    )
    scaled = scaler.transform(row)
    predicted = float(model.predict(scaled)[0])
    explanation = explain_power(
        model=model,
        scaled_row=scaled,
        feature_names=meta["features"],
        raw_inputs=inputs,
        predicted_kw=predicted,
    )
    return explanation, predicted


ROWS = {
    "in-range": _inputs(),
    "shifted": _inputs(**{"Charging Current_A": 80.0, "State Of Charge_SoC": 30.0}),
    "out-of-range": _inputs(
        hour=12,
        month=1,
        **{"Charging Voltage_V": 750.0, "Charging Current_A": 160.0},
    ),
}


class TestAdditivity:
    @pytest.mark.parametrize("label", list(ROWS))
    def test_contributions_reproduce_the_served_prediction(self, power, label):
        explanation, predicted = _explain(power, ROWS[label])
        assert abs(explanation.residual_kw) < 1e-3, (
            f"{label}: bias + contributions = {explanation.reconstructed_kw!r} "
            f"but the page served {predicted!r}"
        )

    @pytest.mark.parametrize("label", list(ROWS))
    def test_the_residual_is_reported_not_hidden(self, power, label):
        explanation, _ = _explain(power, ROWS[label])
        assert explanation.residual_kw == pytest.approx(
            explanation.reconstructed_kw - explanation.predicted_kw, abs=1e-12
        )

    @pytest.mark.parametrize("label", list(ROWS))
    def test_predictions_are_attributed_in_kilowatts(self, power, label):
        """reg:squarederror has no link function, so margins are already kW --
        a contribution in log-odds would break additivity instantly."""
        explanation, predicted = _explain(power, ROWS[label])
        assert predicted > 0
        assert explanation.predicted_kw == predicted
        # An attribution of comparable magnitude to the thing it explains, not
        # a probability or a z-score.
        assert explanation.abs_total_kw > 0
        assert explanation.abs_total_kw < 100 * abs(predicted) + 1000


class TestTheBias:
    def test_the_bias_does_not_move_with_the_row(self, power):
        """It is the ensemble's constant prior; only the contributions change."""
        biases = {_explain(power, inputs)[0].bias_kw for inputs in ROWS.values()}
        assert len(biases) == 1

    def test_the_bias_is_the_training_mean_of_the_target(self, power):
        """A relationship that survives a retrain, unlike the literal 21.628:
        XGBoost's base_score for squared error is the mean of the labels."""
        explanation, _ = _explain(power, ROWS["in-range"])
        try:
            mean_power = float(combined(load_datasets())[POWER_TARGET].mean())
        except OSError as exc:
            pytest.skip(f"training CSVs unavailable: {exc}")
        assert abs(explanation.bias_kw - mean_power) < 0.05


class TestAttributionShape:
    def test_one_contribution_per_model_feature_in_meta_order(self, power):
        _, _, meta = power
        explanation, _ = _explain(power, ROWS["in-range"])
        assert [c.feature for c in explanation.contributions] == meta["features"]

    def test_values_shown_are_the_operators_not_the_z_scores(self, power):
        """A scaled value would make the table uncheckable against the form."""
        inputs = ROWS["in-range"]
        explanation, _ = _explain(power, inputs)
        for contribution in explanation.contributions:
            assert contribution.raw_value == inputs[contribution.feature]
        # 400 V is the operator's number; the model saw a z-score near zero.
        voltage = next(
            c for c in explanation.contributions if c.feature == "Charging Voltage_V"
        )
        assert voltage.raw_value == 400.0
        assert abs(voltage.raw_value) > 1

    def test_voltage_and_current_carry_the_answer(self, power):
        """A V x I target that stopped responding to V and I has changed shape.
        Measured 0.71-0.87; the floor is deliberately loose."""
        explanation, _ = _explain(power, ROWS["in-range"])
        share = (
            sum(abs(c.kw) for c in explanation.contributions if c.feature in FEATURE_OF_INTEREST)
            / explanation.abs_total_kw
        )
        assert share >= 0.5
        assert explanation.dominant.feature in FEATURE_OF_INTEREST
        assert 0.0 < explanation.dominant_share <= 1.0

    def test_bars_scale_within_the_row(self, power):
        explanation, _ = _explain(power, ROWS["in-range"])
        percentages = [c.bar_pct for c in explanation.contributions]
        assert all(0 <= pct <= 100 for pct in percentages)
        assert max(percentages) == 100

    def test_the_same_row_explains_the_same_way_twice(self, power):
        first, _ = _explain(power, ROWS["in-range"])
        second, _ = _explain(power, ROWS["in-range"])
        assert first == second


class TestItFailsLoudly:
    def test_a_model_without_a_booster_raises(self, power):
        """Not an empty breakdown: that would read as 'no input matters'."""

        class FakeRegressor:
            def predict(self, X):  # noqa: N802 - sklearn's own spelling
                return [0.0]

        _, scaler, meta = power
        inputs = ROWS["in-range"]
        row = pd.DataFrame(
            [[inputs[name] for name in meta["features"]]], columns=meta["features"]
        )
        with pytest.raises(ExplanationNotSupported):
            explain_power(
                model=FakeRegressor(),
                scaled_row=scaler.transform(row),
                feature_names=meta["features"],
                raw_inputs=inputs,
                predicted_kw=0.0,
            )

    def test_a_missing_input_raises_rather_than_defaulting(self, power):
        model, scaler, meta = power
        inputs = dict(ROWS["in-range"])
        del inputs[meta["features"][0]]
        row = pd.DataFrame(
            [[ROWS["in-range"][f] for f in meta["features"]]], columns=meta["features"]
        )
        with pytest.raises(ValueError, match="no value for model input"):
            explain_power(
                model=model,
                scaled_row=scaler.transform(row),
                feature_names=meta["features"],
                raw_inputs=inputs,
                predicted_kw=0.0,
            )

    def test_a_column_count_mismatch_raises(self, power):
        model, _, meta = power
        with pytest.raises(ValueError, match="meta.json declares"):
            explain_power(
                model=model,
                scaled_row=[[0.0, 0.0, 0.0]],
                feature_names=meta["features"],
                raw_inputs=ROWS["in-range"],
                predicted_kw=0.0,
            )

    def test_feature_name_drift_is_rejected(self, power):
        """meta.json and the booster disagreeing must not attribute a column
        to the wrong input -- XGBoost checks the names itself."""
        model, scaler, meta = power
        inputs = ROWS["in-range"]
        row = pd.DataFrame(
            [[inputs[f] for f in meta["features"]]], columns=meta["features"]
        )
        renamed = ["not_a_feature"] + list(meta["features"][1:])
        with pytest.raises(ValueError):
            explain_power(
                model=model,
                scaled_row=scaler.transform(row),
                feature_names=renamed,
                raw_inputs=inputs,
                predicted_kw=0.0,
            )


@pytest.mark.django_db
class TestTheBlockOnThePredictionPage:
    def test_a_submit_renders_the_breakdown(self, auth_client):
        response = auth_client.post(reverse("prediction:predict"), _form_fields())
        html = response.content.decode()
        assert response.status_code == 200
        # The heading and the arithmetic footer both come from the block.
        assert "contributions =" in html
        for feature in registry.meta(POWER_MODEL_NAME)["features"]:
            assert feature in html

    def test_the_breakdown_matches_the_served_prediction(self, auth_client):
        response = auth_client.post(reverse("prediction:predict"), _form_fields())
        entry = response.context["predictions"][0]
        explanation = entry["explanation"]
        assert explanation is not None
        assert explanation.predicted_kw == pytest.approx(entry["charging_power"])
        assert abs(explanation.residual_kw) < 1e-3

    def test_an_in_range_input_shows_no_warning_banner(self, auth_client):
        html = auth_client.post(
            reverse("prediction:predict"), _form_fields()
        ).content.decode()
        assert "Outside trained range" not in html
        assert "extrapolation" not in html

    def test_an_out_of_range_input_is_flagged_beside_the_prediction(
        self, auth_client
    ):
        """The breakdown would otherwise lend authority to an extrapolation."""
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form0-timestamp": TIMESTAMP_OUT_OF_RANGE}),
        )
        html = response.content.decode()
        assert "Outside trained range" in html
        assert "extrapolation" in html
        entry = response.context["predictions"][0]
        assert entry["ood_notes"], "the card must name the stray columns"
        assert any(note.startswith("hour=12") for note in entry["ood_notes"])

    def test_the_zero_input_branch_explains_nothing(self, auth_client):
        """0 kW here is the view's 'no vehicle connected' decision, not a model
        output -- inventing a breakdown for it would be fiction."""
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form0-battery_temp": "0", "form0-soc": "0"}),
        )
        assert response.context["predictions"][0]["explanation"] is None
        assert response.context["predictions"][0]["ood_notes"] == []
        assert "contributions =" not in response.content.decode()

    def test_an_attribution_is_not_a_prediction_failure(self, auth_client):
        auth_client.post(reverse("prediction:predict"), _form_fields())
        assert not EventLog.objects.filter(event_type="PREDICTION_ERROR").exists()

    def test_a_get_shows_no_breakdown(self, auth_client):
        assert "contributions =" not in auth_client.get(
            reverse("prediction:predict")
        ).content.decode()

"""The ML pipeline's contract with the serving layer.

Three properties are easy to reintroduce silently, so each is pinned against the
*shipped artifact* rather than against a mock:

* **feature order** -- ``meta.json`` must equal ``ml/pipeline/config.py``.
  A tree model accepts any column width, so a retrained model fed a different
  order predicts nonsense without raising.
* **the evaluation claim** -- the provenance card publishes a blocked-CV R², so
  the artifact must actually carry one (>= 0.98) instead of a number typed into
  a template. The previously shipped model scored **-2.9** under an honest
  time split.
* **the serving budget** -- the previous artifact was 21.9 MB and 9.0 ms a row.

The view behaviours fixed alongside are pinned too: predictions keyed by form
index, the out-of-distribution guard, and the power -> energy chain. The last
of those is pinned *behaviourally*: raising the operator's current has to raise
the energy figure, which it did not while a second model sat between them.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from django.conf import settings
from django.urls import reverse

from apps.monitoring.models import EventLog
from apps.prediction.views import _feature_frame, _out_of_range
from core.model_registry import ModelNotAvailable, registry
from ml.pipeline.config import (
    POWER_FEATURES,
    POWER_MODEL_NAME,
    SPECS,
)

#: hour 4, Thursday, February -- inside every trained range, so no warning.
TIMESTAMP_IN_RANGE = "2025-02-20T04:00"
#: hour 12 and month 1, against trained ranges [0, 7] and [2, 3].
TIMESTAMP_OUT_OF_RANGE = "2026-01-01T12:00"

OOD_PREFIX = "Input outside training distribution"


def _form_fields(**overrides) -> dict:
    """One valid `form0` submission; the other two prefixes stay absent."""
    values = {
        "form0-voltage": "400",
        "form0-current": "15",
        "form0-battery_temp": "25",
        "form0-soc": "50",
        "form0-duration": "2",
        "form0-timestamp": TIMESTAMP_IN_RANGE,
    }
    values.update(overrides)
    return values


@pytest.fixture
def artifacts() -> dict:
    """``meta.json`` for every shipped model, skipping if not yet trained."""
    missing = [name for name in (POWER_MODEL_NAME,) if not registry.available(name)]
    if missing:
        pytest.skip(
            f"artifact(s) not published: {', '.join(missing)} -- "
            "run `python scripts/train_models.py`"
        )
    return {name: registry.meta(name) for name in (POWER_MODEL_NAME,)}


class TestArtifactContract:
    """Everything the loader and the provenance card assert on at request time."""

    def test_current_json_publishes_the_power_model(self, artifacts):
        pointers = registry.versions()
        assert set(pointers) == {POWER_MODEL_NAME}
        for name, version in pointers.items():
            assert version.startswith(f"{name}-v")
        # A retired model must not leave a pointer naming a directory that was
        # never shipped -- the loader would fail on an otherwise good clone.
        root = pathlib.Path(settings.MODEL_DIR)
        for version in pointers.values():
            assert (root / version).is_dir(), version

    def test_feature_order_matches_the_config_source_of_truth(self, artifacts):
        """The whole reason meta.json exists."""
        assert artifacts[POWER_MODEL_NAME]["features"] == POWER_FEATURES

    def test_legacy_root_alias_is_the_active_power_model(self, artifacts):
        """`registry.get()` with no name must keep resolving to a real model."""
        root = json.loads(
            (pathlib.Path(settings.MODEL_DIR) / "meta.json").read_text(encoding="utf-8")
        )
        assert root["features"] == POWER_FEATURES
        assert root["version"] == registry.versions()[POWER_MODEL_NAME]

    def test_energy_is_declared_derived_not_learned(self, artifacts):
        """The provenance card must not claim a model for the kWh figure."""
        from ml.pipeline.config import ENERGY_DERIVED

        meta = artifacts[POWER_MODEL_NAME]
        assert meta["derived"] == ENERGY_DERIVED

    def test_every_feature_has_a_recorded_training_range(self, artifacts):
        """The OOD guard compares against this; a gap means an input is never checked."""
        for name, meta in artifacts.items():
            assert set(meta["input_ranges"]) == set(meta["features"]), name

    def test_meta_reports_both_evaluation_protocols(self, artifacts):
        for name, meta in artifacts.items():
            assert "blocked" in meta["protocol"]
            evaluation = meta["evaluation"]
            assert set(evaluation["chronological_holdout"]) == {"d1", "d2"}, name
            assert set(evaluation["blocked_cv"]) == {"d1", "d2"}, name

    def test_shipped_models_still_report_the_claimed_accuracy(self, artifacts):
        """The number on the provenance card must be a measured one."""
        for name, meta in artifacts.items():
            blocked = meta["evaluation"]["weighted_blocked_cv_r2"]
            assert blocked >= 0.98, f"{name} blocked-CV R2 regressed to {blocked:+.4f}"
            for source, score in meta["evaluation"]["chronological_holdout"].items():
                assert score["r2"] >= 0.95, f"{name} chrono {source} -> {score['r2']:+.4f}"

    def test_artifacts_fit_the_serving_budget(self, artifacts):
        total = sum(meta["size_bytes_model"] for meta in artifacts.values())
        # One model now: 3.2 MB, against 21.9 MB when two shipped and 3.4 MB
        # for the pair after the first downgrade.
        assert total < 4_000_000, f"{total / 1e6:.2f} MB exceeds the 4 MB budget"

    def test_single_row_latency_fits_the_serving_budget(self, artifacts):
        for name, meta in artifacts.items():
            latency = meta["latency_ms_1row"]
            assert latency < 5.0, f"{name} {latency} ms/row (previously 9.0)"


class TestFeatureFrame:
    """The guard between an operator's form input and the model's columns."""

    def test_columns_follow_meta_json_order(self, artifacts):
        meta = artifacts[POWER_MODEL_NAME]
        frame = _feature_frame({f: 1.0 for f in meta["features"]}, meta["features"])
        assert list(frame.columns) == meta["features"]

    def test_a_missing_input_raises_instead_of_defaulting_to_zero(self, artifacts):
        meta = artifacts[POWER_MODEL_NAME]
        partial = {f: 1.0 for f in meta["features"][:-1]}
        with pytest.raises(ModelNotAvailable, match="no value for model input"):
            _feature_frame(partial, meta["features"])

    def test_out_of_range_guard_flags_the_offending_column(self, artifacts):
        ranges = artifacts[POWER_MODEL_NAME]["input_ranges"]
        warnings = _out_of_range({"Charging Voltage_V": 5000.0}, ranges)
        assert len(warnings) == 1
        assert warnings[0].startswith("Charging Voltage_V=5000")
        assert "outside trained range" in warnings[0]

    def test_out_of_range_guard_accepts_mid_range_input(self, artifacts):
        ranges = artifacts[POWER_MODEL_NAME]["input_ranges"]
        mid_range = {name: (lo + hi) / 2 for name, (lo, hi) in ranges.items()}
        assert _out_of_range(mid_range, ranges) == []

    def test_columns_without_a_recorded_range_are_not_flagged(self, artifacts):
        """Duration and predicted power are inputs the model was not trained on."""
        ranges = artifacts[POWER_MODEL_NAME]["input_ranges"]
        assert _out_of_range({"Charging_Duration_h": 99.0}, ranges) == []


@pytest.mark.django_db
class TestServingChain:
    """power -> rate -> energy, in the order the view chains them."""

    @pytest.fixture
    def entry(self, auth_client):
        response = auth_client.post(reverse("prediction:predict"), _form_fields())
        assert response.status_code == 200
        predictions = response.context["predictions"]
        assert len(predictions) == 1
        return predictions[0]

    def test_energy_is_the_rate_integrated_over_duration(self, entry):
        assert entry["predicted_value"] == pytest.approx(
            entry["charging_rate"] * entry["duration"], rel=1e-9
        )
        assert entry["predicted_value"] > 0

    def test_the_rate_is_the_forecast_power(self, entry):
        """The sustained rate of a session is its power -- Energy is exactly
        integral(P dt), so there is no separate quantity to model."""
        assert entry["charging_rate"] == pytest.approx(
            entry["charging_power"], rel=1e-9
        )

    @pytest.mark.parametrize("current", ["5", "15", "80"])
    def test_energy_responds_to_the_operator_s_current(self, auth_client, current):
        """Regression: with a second model between power and kWh, 5 A and 15 A
        both returned 8.78 kWh. Energy must scale with the drawn power."""
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form0-voltage": "400", "form0-current": current}),
        )
        entry = response.context["predictions"][0]
        assert entry["predicted_value"] == pytest.approx(
            entry["charging_power"] * entry["duration"], rel=1e-6
        )
        # 400 V x 5 A = 2 kW over 2 h is 4 kWh; at 80 A it is 64 kWh.
        assert entry["predicted_value"] > 0

    def test_energy_scales_monotonically_with_current(self, auth_client):
        """Doubling the current must not leave the energy figure unchanged."""
        energies = []
        for amps in ("5", "20", "80"):
            response = auth_client.post(
                reverse("prediction:predict"),
                _form_fields(**{"form0-voltage": "400", "form0-current": amps}),
            )
            energies.append(response.context["predictions"][0]["predicted_value"])
        assert energies[0] < energies[1] < energies[2], energies

    def test_derived_current_is_power_over_voltage(self, entry):
        """The fault check must use the operator's voltage, not a 400 V assumption."""
        assert entry["predicted_current"] == pytest.approx(
            entry["charging_power"] * 1000.0 / entry["predicted_voltage"], rel=1e-9
        )
        assert entry["predicted_voltage"] == 400.0

    def test_power_tracks_the_operator_s_volts_times_amps(self, entry):
        """P = V*I = 400 * 15 / 1000 = 6.0 kW. A model that ignores the form
        inputs would not land here."""
        assert entry["charging_power"] == pytest.approx(6.0, rel=0.25)

    def test_safe_defaults_raise_no_fault(self, entry):
        assert entry["fault_detected"] is False
        assert entry["fault_message"] is None

    def test_a_reading_is_persisted_for_every_prediction(self, auth_client, entry):
        from apps.monitoring.models import Reading

        assert Reading.objects.count() == 1

    def test_out_of_bus_voltage_faults_against_the_configured_limit(self, auth_client):
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form0-voltage": "600", "form0-current": "80"}),
        )
        entry = response.context["predictions"][0]
        assert entry["predicted_voltage"] == 600.0
        assert entry["predicted_current"] == pytest.approx(
            entry["charging_power"] * 1000.0 / 600.0, rel=1e-9
        )
        # Thresholds default to a 400-460 V bus, so 600 V is out of range.
        assert entry["fault_detected"] is True


@pytest.mark.django_db
class TestOutOfDistributionGuard:
    """Feeding the model something outside its training support must be visible."""

    def test_one_warning_event_per_submit_however_many_columns_stray(
        self, auth_client, artifacts
    ):
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form0-timestamp": TIMESTAMP_OUT_OF_RANGE}),
        )
        assert response.status_code == 200
        events = EventLog.objects.filter(details__startswith=OOD_PREFIX)
        # hour=12, day_of_week=0 and month=1 all stray; the view still says it once.
        assert events.count() == 1
        assert "hour=12" in events.get().details
        assert "month=1" in events.get().details

    def test_the_warning_does_not_stop_the_prediction(self, auth_client, artifacts):
        response = auth_client.post(
            reverse("prediction:predict"),
            _form_fields(**{"form0-timestamp": TIMESTAMP_OUT_OF_RANGE}),
        )
        entry = response.context["predictions"][0]
        assert entry["predicted_value"] > 0
        assert entry["fault_detected"] is False

    def test_an_in_range_input_logs_nothing(self, auth_client, artifacts):
        response = auth_client.post(reverse("prediction:predict"), _form_fields())
        assert response.status_code == 200
        assert not EventLog.objects.filter(details__startswith=OOD_PREFIX).exists()
        assert len(response.context["predictions"]) == 1


@pytest.mark.django_db
class TestPredictionsAreKeyedByFormIndex:
    """Regression: results were keyed 0,1,2 by *position*, so an invalid middle
    form made station 2 render station 3's numbers."""

    @pytest.fixture
    def payload(self):
        values = _form_fields()
        # Station 3 filled in, station 2 deliberately left incomplete.
        values.update(
            {
                "form2-voltage": "600",
                "form2-current": "40",
                "form2-battery_temp": "30",
                "form2-soc": "45",
                "form2-duration": "3",
                "form2-timestamp": TIMESTAMP_IN_RANGE,
            }
        )
        return values

    @pytest.fixture
    def response(self, auth_client, payload):
        return auth_client.post(reverse("prediction:predict"), payload)

    def test_only_the_valid_forms_come_back(self, response):
        assert {p["index"] for p in response.context["predictions"]} == {0, 2}

    def test_the_lookup_dict_uses_the_same_keys(self, response):
        assert set(response.context["predictions_dict"]) == {0, 2}
        assert 1 not in response.context["predictions_dict"]

    def test_each_station_keeps_its_own_numbers(self, response):
        by_index = response.context["predictions_dict"]
        # 400 V / 15 A against 600 V / 40 A cannot plausibly collide.
        assert by_index[0]["charging_power"] != by_index[2]["charging_power"]
        assert by_index[0]["predicted_voltage"] == 400.0
        assert by_index[2]["predicted_voltage"] == 600.0

    def test_the_page_renders_two_result_blocks_not_three(self, response):
        html = response.content.decode()
        assert html.count("Predicted Power:") == 2
        assert html.count("Energy Consumption:") == 2

    def test_the_incomplete_station_shows_no_result_at_all(self, response):
        html = response.content.decode()
        second = html.index("EV Charging Station 2")
        third = html.index("EV Charging Station 3")
        assert "Predicted Power:" not in html[second:third]


@pytest.mark.django_db
class TestProvenanceCard:
    """A prediction has to be attributable to a version and an algorithm."""

    def test_the_page_names_the_shipped_artifact(self, auth_client, artifacts):
        html = auth_client.get(reverse("prediction:predict")).content.decode()
        assert "Model Provenance" in html
        assert SPECS[POWER_MODEL_NAME]["algorithm"] in html
        assert artifacts[POWER_MODEL_NAME]["version"] in html

    def test_the_page_shows_the_target_and_the_derivation(self, auth_client, artifacts):
        """kWh is claimed as arithmetic, not as a second model's output."""
        from ml.pipeline.config import ENERGY_DERIVED

        html = auth_client.get(reverse("prediction:predict")).content.decode()
        assert artifacts[POWER_MODEL_NAME]["target"] in html
        assert ENERGY_DERIVED in html
        # The retired model must not still be advertised on the page.
        assert "HistGradientBoostingRegressor" not in html

    def test_the_page_shows_a_measured_r_squared(self, auth_client, artifacts):
        html = auth_client.get(reverse("prediction:predict")).content.decode()
        for meta in artifacts.values():
            assert str(meta["evaluation"]["weighted_blocked_cv_r2"]) in html

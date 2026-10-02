"""`core.model_registry` — lazy artifact loading and its failure modes.

The regression this pins: ``apps/prediction/views.py`` used to deserialize
``model.joblib`` + ``scaler.joblib`` (~21 MB) into module globals at import, so
every management command and every test run paid for it before doing anything
useful, and a missing file raised while Django was importing the URLconf.
"""
from __future__ import annotations

import logging
import threading

import pytest
from django.test import override_settings

from apps.prediction import views as prediction_views
from core.model_registry import ModelNotAvailable, ModelRegistry, registry


def _quiet_registry_logs(caplog):
    """The registry logs a failed load at ERROR by design; keep it visible to
    pytest without letting it read like an unhandled error on stderr."""
    return caplog.at_level(logging.ERROR, logger="core.model_registry")


class TestImportIsFree:
    def test_prediction_module_holds_no_artifacts(self):
        """The old code did `model = joblib.load(...)` at module scope."""
        assert not hasattr(prediction_views, "model")
        assert not hasattr(prediction_views, "scaler")

    def test_prediction_module_does_not_import_joblib(self):
        assert "joblib" not in prediction_views.__dict__


class TestPaths:
    def test_paths_come_from_settings(self, settings):
        assert registry.model_path.name == "model.joblib"
        assert registry.scaler_path.name == "scaler.joblib"
        assert registry.model_path.parent == settings.MODEL_DIR

    def test_paths_are_not_cached(self):
        """Tests and management commands repoint MODEL_DIR mid-process."""
        with override_settings(MODEL_DIR="/elsewhere"):
            assert registry.model_path.name == "model.joblib"
            assert registry.model_path.parent.name == "elsewhere"

    def test_available_checks_disk_without_touching_the_cache(self, tmp_path):
        """`available()` must be safe to call on a hot path: it stats two files
        and must never trigger (or record) a load."""
        state_before = registry._state
        assert registry.available() is True

        with override_settings(MODEL_DIR=str(tmp_path)):
            assert registry.available() is False

        assert registry._state == state_before


class TestSuccessfulLoad:
    def test_get_returns_both_artifacts(self):
        model, scaler = registry.get()
        assert model is not None
        assert scaler is not None
        assert registry._state == ModelRegistry.LOADED

    def test_second_call_is_served_from_memory(self):
        first = registry.get()
        second = registry.get()
        assert first[0] is second[0]
        assert first[1] is second[1]

    def test_eight_concurrent_callers_share_one_load(self):
        """The lock is held for the whole load, so no caller can race into
        deserializing a second copy."""
        registry.reload()

        barrier = threading.Barrier(8)
        results: list = []
        errors: list = []

        def worker() -> None:
            barrier.wait()
            try:
                results.append(registry.get())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, errors
        assert not any(t.is_alive() for t in threads), "a caller never returned"
        assert len(results) == 8
        # Identity, not equality: a fresh tuple is built per call, so compare
        # the objects inside it.
        assert len({id(r[0]) for r in results}) == 1
        assert len({id(r[1]) for r in results}) == 1

    def test_reload_clears_cached_state(self):
        registry.get()
        registry.reload()
        assert registry._state == ModelRegistry.EMPTY
        assert registry._model is None
        assert registry._scaler is None
        assert registry._error is None


class TestFailureIsRemembered:
    def test_missing_artifacts_raise(self, tmp_path, caplog):
        with _quiet_registry_logs(caplog), override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            with pytest.raises(ModelNotAvailable) as excinfo:
                registry.get()
        assert "Missing model artifact" in str(excinfo.value)

    def test_failure_is_not_retried(self, tmp_path, caplog, monkeypatch):
        """A broken artifact must cost one cheap error, not a 21 MB re-read on
        every request."""
        attempts = 0
        original_load = registry._load

        def counting_load():
            nonlocal attempts
            attempts += 1
            return original_load()

        monkeypatch.setattr(registry, "_load", counting_load)

        with _quiet_registry_logs(caplog), override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            with pytest.raises(ModelNotAvailable):
                registry.get()
            with pytest.raises(ModelNotAvailable):
                registry.get()
            with pytest.raises(ModelNotAvailable):
                registry.get()

        assert attempts == 1, f"load attempted {attempts} times, expected 1"
        assert registry._state == ModelRegistry.FAILED

    def test_failure_is_logged_once_at_error(self, tmp_path, caplog):
        with _quiet_registry_logs(caplog), override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            for _ in range(3):
                with pytest.raises(ModelNotAvailable):
                    registry.get()

        matching = [r for r in caplog.records if r.name == "core.model_registry"]
        assert len(matching) == 1
        assert matching[0].levelno == logging.ERROR

    def test_reload_clears_a_remembered_failure(self, tmp_path):
        with override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            with pytest.raises(ModelNotAvailable):
                registry.get()
            assert registry._state == ModelRegistry.FAILED

        registry.reload()
        assert registry._state == ModelRegistry.EMPTY
        # Recovery path: once state is cleared the real artifacts are usable
        # again, which is what `registry.reload()` is for after retraining.
        assert registry.available() is True

    def test_a_partial_set_of_artifacts_is_a_failure(self, tmp_path, caplog):
        """model.joblib present but scaler.joblib missing must not half-load."""
        (tmp_path / "model.joblib").write_bytes(b"not really a model")
        with _quiet_registry_logs(caplog), override_settings(MODEL_DIR=str(tmp_path)):
            registry.reload()
            with pytest.raises(ModelNotAvailable):
                registry.get()
        assert registry._state == ModelRegistry.FAILED

"""Lazy, process-wide access to the trained artifacts.

Why this exists
---------------
``apps/prediction/views.py`` used to do this at **module import time**::

    model = joblib.load('model.joblib')      # ~21 MB, every worker

That meant every management command, every test run, every ``manage.py check``
and every ASGI worker paid a 21 MB deserialisation cost before doing anything
useful, and a missing/corrupt artifact raised while Django was still importing
the URLconf — turning a deployment problem into an opaque startup traceback.

Here the artifacts are loaded on first *use*, exactly once per process, behind
a lock so concurrent requests cannot race into loading them twice. A failed
load is remembered too, so a broken artifact produces one cheap error instead
of re-parsing 21 MB on every request.

Two models are published, each in its own version directory::

    MODEL_DIR/
        current.json                    {"power": "power-v3", "energy": "energy-v1"}
        model.joblib / scaler.joblib    legacy alias == active energy model
        power-v3/{model,scaler}.joblib + meta.json
        energy-v1/{model,scaler}.joblib + meta.json

Typical use::

    from core.model_registry import ModelNotAvailable, registry

    try:
        power_model, power_scaler = registry.get("power")
        meta = registry.meta("power")          # feature order, input ranges
    except ModelNotAvailable as exc:
        ...  # show a friendly message, log, degrade gracefully

``registry.get()`` with no name still returns the default (energy) artifacts
from the root of ``MODEL_DIR`` — the original contract, kept for callers and
tests that predate named models.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: File names inside ``settings.MODEL_DIR`` (or a version directory).
MODEL_FILENAME = "model.joblib"
SCALER_FILENAME = "scaler.joblib"
META_FILENAME = "meta.json"
CURRENT_FILENAME = "current.json"


class ModelNotAvailable(RuntimeError):
    """Raised when the model or scaler cannot be loaded."""


class ModelRegistry:
    """Thread-safe lazy singleton for ``(model, scaler)``.

    The instance holds three states for the *default* artifacts: ``EMPTY``
    (never attempted), ``LOADED`` (both usable) and ``FAILED`` (attempted and
    remembered). Named models are cached alongside, keyed by name. The lock is
    held for the whole load, which serialises the (one-off) read but guarantees
    a single copy per worker.
    """

    EMPTY = "empty"
    LOADED = "loaded"
    FAILED = "failed"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = self.EMPTY
        self._model: Any | None = None
        self._scaler: Any | None = None
        self._error: str | None = None
        self._named: dict[str, tuple[Any, Any]] = {}
        self._named_failed: dict[str, str] = {}

    # -- paths -------------------------------------------------------------
    @staticmethod
    def paths(name: str | None = None) -> tuple[Path, Path]:
        """Resolve the artifact paths from settings (lazily, never cached).

        Resolved on every call so tests and management commands can point
        ``settings.MODEL_DIR`` somewhere else without restarting the process.

        ``name=None`` addresses the legacy artifacts at the root; any other
        name is resolved through ``current.json`` to its version directory.
        """
        from django.conf import settings

        model_dir = Path(settings.MODEL_DIR)
        parent = model_dir if name is None else model_dir / _version_of(model_dir, name)
        return parent / MODEL_FILENAME, parent / SCALER_FILENAME

    @property
    def model_path(self) -> Path:
        return self.paths()[0]

    @property
    def scaler_path(self) -> Path:
        return self.paths()[1]

    def available(self, name: str | None = None) -> bool:
        """True when both artifact files exist (does *not* load them)."""
        try:
            model_path, scaler_path = self.paths(name)
        except FileNotFoundError:
            return False
        return model_path.is_file() and scaler_path.is_file()

    def versions(self) -> dict[str, str]:
        """The ``current.json`` pointers, or ``{}`` when none are published."""
        return _read_pointers(self.model_path.parent)

    # -- metadata ----------------------------------------------------------
    def meta(self, name: str | None = None) -> dict:
        """``meta.json`` for a model: feature order, input ranges, metrics.

        Raises:
            ModelNotAvailable: when the metadata is missing or unreadable. The
                serving layer refuses to predict without it, because it is what
                proves the DataFrame columns match the training order.
        """
        try:
            model_path, _ = self.paths(name)
        except FileNotFoundError as exc:
            raise ModelNotAvailable(str(exc)) from exc
        path = model_path.parent / META_FILENAME
        if not path.is_file():
            raise ModelNotAvailable(
                f"Missing metadata: {path} — retrain with `python scripts/train_models.py`."
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ModelNotAvailable(f"Unreadable metadata {path}: {exc}") from exc

    # -- loading -----------------------------------------------------------
    def get(self, name: str | None = None) -> tuple[Any, Any]:
        """Return ``(model, scaler)``, loading them on first call.

        Args:
            name: ``None`` for the default artifacts at the root of
                ``MODEL_DIR``, otherwise a published model name such as
                ``"power"`` or ``"energy"``.

        Raises:
            ModelNotAvailable: if the artifacts are missing or unreadable. The
                failure is cached, so repeat calls raise immediately without
                re-reading the file.
        """
        if name is None:
            return self._get_default()
        return self._get_named(name)

    def _get_default(self) -> tuple[Any, Any]:
        with self._lock:
            if self._state == self.LOADED:
                return self._model, self._scaler  # type: ignore[return-value]
            if self._state == self.FAILED:
                raise ModelNotAvailable(self._error or "model artifacts unavailable")

            try:
                self._model, self._scaler = self._load()
            except Exception as exc:  # noqa: BLE001 - any load failure must degrade
                self._state = self.FAILED
                self._error = str(exc)
                logger.error("Failed to load model artifacts: %s", exc)
                raise ModelNotAvailable(self._error) from exc

            self._state = self.LOADED
            logger.info(
                "Loaded model artifacts: %s, %s",
                self.model_path.name,
                self.scaler_path.name,
            )
            return self._model, self._scaler

    def _get_named(self, name: str) -> tuple[Any, Any]:
        with self._lock:
            if name in self._named:
                return self._named[name]
            if name in self._named_failed:
                # Same contract as the default path: a broken artifact costs one
                # cheap error, not a deserialisation attempt on every request.
                raise ModelNotAvailable(self._named_failed[name])
            try:
                bundle = self._load_named(name)
            except Exception as exc:  # noqa: BLE001 - any load failure must degrade
                self._named_failed[name] = str(exc)
                logger.error("Failed to load model '%s': %s", name, exc)
                raise ModelNotAvailable(str(exc)) from exc
            self._named[name] = bundle
            logger.info("Loaded model '%s' from %s", name, self.paths(name)[0].parent)
            return bundle

    def failed(self, name: str | None = None) -> str | None:
        """The remembered load error for ``name``, or ``None`` if it loaded.

        Cheap enough for a health endpoint: it never touches the artifacts.
        """
        if name is None:
            return self._error if self._state == self.FAILED else None
        return self._named_failed.get(name)

    def _load(self) -> tuple[Any, Any]:
        """Load the default artifacts. Takes no arguments on purpose: tests
        and gates monkeypatch this method, so its signature must not change."""
        import joblib

        model_path, scaler_path = self.paths()
        missing = [str(p) for p in (model_path, scaler_path) if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                "Missing model artifact(s): "
                + ", ".join(missing)
                + " — train them with `python scripts/train_models.py` or set "
                "MODEL_DIR to the directory holding them."
            )
        return joblib.load(model_path), joblib.load(scaler_path)

    def _load_named(self, name: str) -> tuple[Any, Any]:
        import joblib

        model_path, scaler_path = self.paths(name)
        missing = [str(p) for p in (model_path, scaler_path) if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                "Missing model artifact(s): "
                + ", ".join(missing)
                + f" — train it with `python scripts/train_models.py --only {name}`."
            )
        return joblib.load(model_path), joblib.load(scaler_path)

    # -- lifecycle ---------------------------------------------------------
    def reload(self) -> None:
        """Drop the cached state so the next :meth:`get` re-reads from disk.

        Used by tests and after retraining a model in place.
        """
        with self._lock:
            self._state = self.EMPTY
            self._model = None
            self._scaler = None
            self._error = None
            self._named.clear()
            self._named_failed.clear()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModelRegistry state={self._state} path={self.model_path}>"


# --------------------------------------------------------------- resolution
def _read_pointers(root: Path) -> dict[str, str]:
    path = root / CURRENT_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _version_of(model_dir: Path, name: str) -> str:
    """Map a model name to its version directory via ``current.json``."""
    pointers = _read_pointers(model_dir)
    if name not in pointers:
        available = ", ".join(sorted(pointers)) or "none"
        raise FileNotFoundError(
            f"No active version for model '{name}' in {model_dir / CURRENT_FILENAME} "
            f"(published: {available}) — train it with "
            f"`python scripts/train_models.py --only {name}`."
        )
    return str(pointers[name])


#: The process-wide singleton.
registry = ModelRegistry()

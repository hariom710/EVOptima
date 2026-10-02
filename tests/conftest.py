"""Shared fixtures.

Two pieces of module-level state live *outside* the database and would
otherwise leak between tests. Django's ``db`` fixture rolls the database back
around each test, but neither of these is a row, so each needs explicit
handling:

* ``apps.monitoring.services.faults`` caches the ``Thresholds`` row.
* ``core.model_registry.registry`` caches the deserialised artifacts.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.monitoring.services.faults import invalidate_thresholds
from core.model_registry import registry


@pytest.fixture(autouse=True)
def _threshold_cache():
    """Start and end every test with a cold thresholds cache.

    Without this, a test that reads ``get_thresholds()`` and a later test that
    edits the row would interact through an invisible module global.
    """
    invalidate_thresholds()
    yield
    invalidate_thresholds()


@pytest.fixture(autouse=True)
def _model_registry():
    """Snapshot the registry so tests cannot leak state into each other.

    Restoring the previous state rather than calling ``reload()`` means the
    artifacts are deserialised once for the whole session instead of once per
    test that touches prediction.

    All six attributes are snapshotted, not just the default-state quartet:
    the named caches were added alongside ``registry.get("power")``, and a test
    that leaves ``_named_failed`` populated would make every later test see a
    poisoned model it never asked to unload.
    """
    saved = (
        registry._state,
        registry._model,
        registry._scaler,
        registry._error,
        dict(registry._named),
        dict(registry._named_failed),
    )
    yield
    (
        registry._state,
        registry._model,
        registry._scaler,
        registry._error,
        registry._named,
        registry._named_failed,
    ) = saved


@pytest.fixture
def user(db):
    """A plain user, distinct from the seeded admin."""
    return get_user_model().objects.create_user(username="tester", password="pw")


@pytest.fixture
def auth_client(client, user):
    """Logged-in client. Every monitoring/prediction endpoint requires login."""
    client.force_login(user)
    return client

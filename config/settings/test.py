"""Test settings: deterministic, fast, and unable to touch real resources.

Selected by pytest-django through ``DJANGO_SETTINGS_MODULE`` in
``pyproject.toml`` (and usable directly via ``--settings=config.settings.test``).

Everything else inherits from ``dev``: SQLite (pytest-django builds a throwaway
``test_*`` database and drops it, so ``db.sqlite3`` is never opened), an
``InMemoryChannelLayer`` (tests need no Redis), and the dev ``SECRET_KEY``.
"""
from .dev import *  # noqa: F401,F403

DEBUG = False

# pytest-django calls setup_test_environment(), which already appends
# "testserver", but listing it keeps `manage.py test --settings=...` working
# without depending on that side effect.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

# A prediction fault raises send_mail(); the suite must never reach an SMTP
# server. locmem also lets assertions read django.core.mail.outbox.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Hashing dominates user-creation time and nothing in the suite asserts on the
# hash format.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# The thresholds cache under test is in-process; this only stops the framework
# from reaching for a backend that does not exist in CI.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

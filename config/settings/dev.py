"""Development settings: DEBUG on, SQLite, in-memory channel layer.

Selected by default (manage.py/asgi.py/wsgi.py fall back to this module).
"""
import os  # noqa: F401

from .base import *  # noqa: F401,F403

DEBUG = True

SECRET_KEY = os.getenv(  # noqa: F405
    "DJANGO_SECRET_KEY",
    "django-insecure-_e+x2xwlo3mr73ig=w710b&4=7wzvogoki*o=c9(u8feap$367",
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# No Redis needed in development.
CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Serve static files directly from the source tree when DEBUG is on.
STATICFILES_DIRS = [BASE_DIR / "static"]  # noqa: F405

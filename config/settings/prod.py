"""Production settings: everything comes from the environment.

Required environment variables:
    DJANGO_SECRET_KEY   secret key (no default — startup fails without it)
    DJANGO_DEBUG        "1"/"true" to enable DEBUG (default: off)
    DJANGO_ALLOWED_HOSTS  comma-separated hostnames
    DATABASE_URL        postgresql://user:pass@host:5432/evoptima
Optional:
    DJANGO_CSRF_TRUSTED_ORIGINS  comma-separated origins
    REDIS_URL          e.g. redis://redis:6379/0 (default: local redis)
    DJANGO_SECURE_SSL_REDIRECT, DJANGO_HSTS_SECONDS, DJANGO_EMAIL_*, DJANGO_LOG_LEVEL

See ``.env.example`` for a documented copy of every variable.
"""
import os  # noqa: F401

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = os.getenv("DJANGO_DEBUG", "").lower() in ("1", "true", "yes", "on")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")  # noqa: F405
if not SECRET_KEY:
    raise ImproperlyConfigured(  # noqa: F405
        "DJANGO_SECRET_KEY must be set when running with config.settings.prod"
    )

ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]  # noqa: F405
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# ------------------------------------------------------------------ database
_database_url = os.getenv("DATABASE_URL")  # noqa: F405
if _database_url:
    if _database_url.startswith(("postgres", "postgresql")):
        from urllib.parse import urlparse

        _u = urlparse(_database_url)
        DATABASES = {  # noqa: F405
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": _u.path.lstrip("/"),
                "USER": _u.username or "",
                "PASSWORD": _u.password or "",
                "HOST": _u.hostname or "",
                "PORT": str(_u.port or 5432),
                "CONN_MAX_AGE": 60,
            }
        }
    else:
        raise ImproperlyConfigured("DATABASE_URL must be postgresql:// (SQLite is dev-only)")  # noqa: F405
else:
    raise ImproperlyConfigured("DATABASE_URL must be set when running config.settings.prod")  # noqa: F405

# ------------------------------------------------------------- channel layer
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [REDIS_URL]}}
}

# ------------------------------------------------------------- static files
# whitenoise serves compressed static files straight from the ASGI/WSGI
# process, so no external web-server alias is required. It must sit directly
# after SecurityMiddleware (it is the only place that needs to see the request
# before the session/csrf machinery).
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
)

# NOTE: STATICFILES_STORAGE was removed in Django 5 in favour of STORAGES.
# WhiteNoise's backend subclasses CompressedManifestFilesStorage, so hashed
# filenames + on-the-fly gzip/brotli compression both come for free.
STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# ------------------------------------------------------------------ security
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------- email
EMAIL_BACKEND = os.getenv(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("DJANGO_EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "noreply@evoptima.local")

# ------------------------------------------------------------------ logging
# JSON lines on stdout: one object per record, ready for a log shipper.
# dev.py keeps the human-readable console format instead.
_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "core.logging.JsonFormatter"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {"handlers": ["stdout"], "level": _LOG_LEVEL},
    "loggers": {
        "django": {
            "handlers": ["stdout"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        # Warnings/errors raised while handling a request.
        "django.request": {
            "handlers": ["stdout"],
            "level": "WARNING",
            "propagate": False,
        },
        # Security-relevant events (logins, admin changes, 403/404/5xx).
        "django.security": {
            "handlers": ["stdout"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {"handlers": ["stdout"], "level": _LOG_LEVEL, "propagate": False},
    },
}

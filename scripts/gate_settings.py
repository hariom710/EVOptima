"""Phase-2 verification gate: settings split + JSON log formatting.

Asserts the three settings modules really do hold what Phase 2 promises, then
renders a real log record through core.logging.JsonFormatter and parses it
back as JSON.

Usage:
    python scripts/gate_settings.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROD_ENV = {
    "DJANGO_SECRET_KEY": "7qN2vJ5xR8bW3yZ6aC1dF4gH7jK0mP9sT2uV5wX8yA0bD3eF6gI1jL4nQ7rS0uV",
    "DJANGO_DEBUG": "0",
    "DJANGO_ALLOWED_HOSTS": "evoptima.example.com,localhost",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://evoptima.example.com",
    "DATABASE_URL": "postgresql://evoptima:secret@db:5432/evoptima",
    "REDIS_URL": "redis://redis:6379/0",
    "DJANGO_LOG_LEVEL": "INFO",
}

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))


def load(module: str, env: dict | None = None):
    """Import a settings module under a clean environment."""
    for key in list(os.environ):
        if key.startswith("DJANGO_") or key in ("DATABASE_URL", "REDIS_URL"):
            del os.environ[key]
    if env:
        os.environ.update(env)
    os.environ["DJANGO_SETTINGS_MODULE"] = module
    for name in [n for n in sys.modules if n.startswith("config.")]:
        del sys.modules[name]
    for name in ("config.settings.base", "config.settings.dev", "config.settings.prod"):
        sys.modules.pop(name, None)
    import importlib

    return importlib.import_module(module)


# ---------------------------------------------------------------- base.py
base = load("config.settings.base")
check("base: no SECRET_KEY", not hasattr(base, "SECRET_KEY"), repr(getattr(base, "SECRET_KEY", None))[:40])
check("base: no DEBUG", not hasattr(base, "DEBUG"))
check("base: DATA_DIR overridable", base.DATA_DIR.name == "raw", str(base.DATA_DIR))
check("base: MODEL_DIR overridable", base.MODEL_DIR.name == "artifacts", str(base.MODEL_DIR))
check("base: no whitenoise", not any("whitenoise" in m for m in base.MIDDLEWARE))

# base must not encode environment-specific choices: no channel layer (so the
# dev/prod split owns it), no secrets, no host allow-list.
_base_src = (ROOT / "config" / "settings" / "base.py").read_text(encoding="utf-8")
check("base: declares no CHANNEL_LAYERS", "CHANNEL_LAYERS" not in _base_src)
check("base: declares no SECRET_KEY assignment", "SECRET_KEY =" not in _base_src)
check("base: declares no ALLOWED_HOSTS", "ALLOWED_HOSTS" not in _base_src)
check("base: no redis reference", "redis" not in _base_src.lower())

# ----------------------------------------------------------------- dev.py
dev = load("config.settings.dev")
check("dev: DEBUG=True", dev.DEBUG is True)
check("dev: SQLite", "sqlite3" in dev.DATABASES["default"]["ENGINE"], dev.DATABASES["default"]["NAME"])
check("dev: InMemoryChannelLayer", "InMemory" in dev.CHANNEL_LAYERS["default"]["BACKEND"])
check("dev: console email backend", "console" in dev.EMAIL_BACKEND)
check("dev: whitenoise absent", not any("whitenoise" in m for m in dev.MIDDLEWARE))
check("dev: human-readable log formatter", dev.LOGGING["formatters"].get("json") is None)

# ---------------------------------------------------------------- prod.py
prod = load("config.settings.prod", PROD_ENV)
check("prod: DEBUG=False", prod.DEBUG is False)
check("prod: no insecure DEBUG", prod.DEBUG is False)
mw = prod.MIDDLEWARE
si, wi = mw.index("django.middleware.security.SecurityMiddleware"), mw.index(
    "whitenoise.middleware.WhiteNoiseMiddleware"
)
check("prod: whitenoise directly after SecurityMiddleware", wi == si + 1, str(mw[si : wi + 1]))
check(
    "prod: whitenoise manifest storage",
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
    == prod.STORAGES["staticfiles"]["BACKEND"],
    prod.STORAGES["staticfiles"]["BACKEND"],
)
check("prod: not the removed STATICFILES_STORAGE", not hasattr(prod, "STATICFILES_STORAGE"))
check("prod: Redis channel layer", "RedisChannelLayer" in prod.CHANNEL_LAYERS["default"]["BACKEND"])
check("prod: DATABASES from URL", prod.DATABASES["default"]["ENGINE"].endswith("postgresql"))
check("prod: HSTS enabled", prod.SECURE_HSTS_SECONDS >= 31536000, str(prod.SECURE_HSTS_SECONDS))
check("prod: HSTS includeSubdomains", prod.SECURE_HSTS_INCLUDE_SUBDOMAINS is True)
check("prod: HSTS preload", prod.SECURE_HSTS_PRELOAD is True)
check("prod: SSL redirect", prod.SECURE_SSL_REDIRECT is True)
check("prod: secure session cookie", prod.SESSION_COOKIE_SECURE is True)
check("prod: secure csrf cookie", prod.CSRF_COOKIE_SECURE is True)
check(
    "prod: security + whitenoise chain intact",
    "django.middleware.security.SecurityMiddleware" in mw
    and "whitenoise.middleware.WhiteNoiseMiddleware" in mw,
    " | ".join(mw[:3]),
)
check("prod: JSON log formatter", prod.LOGGING["formatters"]["json"]["()"] == "core.logging.JsonFormatter")
check("prod: logger 'apps' present", "apps" in prod.LOGGING["loggers"])
check("prod: logger 'django.security' present", "django.security" in prod.LOGGING["loggers"])
check("prod: ALLOWED_HOSTS parsed", prod.ALLOWED_HOSTS == ["evoptima.example.com", "localhost"], str(prod.ALLOWED_HOSTS))
check("prod: CSRF origins parsed", prod.CSRF_TRUSTED_ORIGINS == ["https://evoptima.example.com"])

# ------------------------------------------------- JSON formatter round-trip
from core.logging import JsonFormatter  # noqa: E402

fmt = JsonFormatter()
record = logging.LogRecord(
    name="apps.monitoring",
    level=logging.WARNING,
    pathname=__file__,
    lineno=42,
    msg="threshold breached: %s",
    args=(30.1,),
    exc_info=None,
)
record.request_id = "abc-123"
line = fmt.format(record)
parsed = json.loads(line)
check("json: parses", isinstance(parsed, dict))
check("json: message formatted", parsed["message"] == "threshold breached: 30.1", parsed["message"])
check("json: level", parsed["level"] == "WARNING")
check("json: logger name", parsed["logger"] == "apps.monitoring")
check("json: utc timestamp", parsed["ts"].endswith("+00:00"), parsed["ts"])
check("json: extra captured", parsed.get("extra", {}).get("request_id") == "abc-123", str(parsed.get("extra")))
check("json: one line only", "\n" not in line)

# an exception must serialize, never raise
try:
    raise ValueError("boom")
except ValueError:
    import sys as _sys

    er = logging.LogRecord("apps", logging.ERROR, __file__, 1, "failed", (), _sys.exc_info())
check("json: traceback serialized", "ValueError: boom" in json.loads(fmt.format(er)).get("exception", ""))

# an unserializable extra must not blow up the formatter
er2 = logging.LogRecord("apps", logging.INFO, __file__, 1, "x", (), None)
er2.payload = {1, 2, 3}
check("json: unserializable extra coerced", "extra" in json.loads(fmt.format(er2)))

# ------------------------------------------- dictConfig instantiates it
# The `()` callable form only works if Django's logging config can import and
# build the class; prove it end-to-end rather than only calling it directly.
import io  # noqa: E402
import logging.config  # noqa: E402

logging.config.dictConfig(prod.LOGGING)
_handler = logging.getLogger("apps").handlers[0]
_probe = io.StringIO()
_original_stream = _handler.stream
try:
    _handler.stream = _probe
    logging.getLogger("apps.monitoring.services").warning("sim start pid=%s", 7)
    _handler.flush()
    emitted = _probe.getvalue().strip()
finally:
    _handler.stream = _original_stream
    logging.config.dictConfig(
        {"version": 1, "disable_existing_loggers": False, "handlers": {}, "loggers": {}}
    )

_emitted = json.loads(emitted) if emitted else {}
check(
    "dictConfig: JsonFormatter built and used",
    _emitted.get("message") == "sim start pid=7",
    str(_emitted)[:110],
)
check("dictConfig: routed to logger apps", _emitted.get("logger") == "apps.monitoring.services")

# --------------------------------------------------------------- reporting
print()
failed = 0
for label, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if not ok:
        failed += 1
    suffix = f"   [{detail}]" if detail else ""
    print(f"  {mark}  {label}{suffix}")

print()
print(f"{len(results) - failed}/{len(results)} assertions passed")
print("GATE: PASS" if not failed else f"GATE: FAIL ({failed} failed)")
sys.exit(0 if not failed else 1)

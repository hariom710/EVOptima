"""Phase-2 verification gate: `manage.py check --deploy` under prod settings.

Runs the Django deployment system checks with every variable prod.py reads
exported, and fails on any ERROR or WARNING. ``--deploy`` reports (W004, W008,
W009, W012, W016, W018, W020, W021, W022, W025, ...) are all fatal here: the
whole point of the phase is that prod boots without them.

Usage:
    python scripts/gate_deploy.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A realistic prod environment. The secret key is 50+ random characters so it
# clears W009 (too short) and does not look like an insecure default (W008).
ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": "7qN2vJ5xR8bW3yZ6aC1dF4gH7jK0mP9sT2uV5wX8yA0bD3eF6gI1jL4nQ7rS0uV",
    "DJANGO_DEBUG": "0",
    "DJANGO_ALLOWED_HOSTS": "evoptima.example.com,localhost,127.0.0.1",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://evoptima.example.com",
    "DATABASE_URL": "postgresql://evoptima:secret@db:5432/evoptima",
    "REDIS_URL": "redis://redis:6379/0",
    "DJANGO_SECURE_SSL_REDIRECT": "1",
    "DJANGO_HSTS_SECONDS": "31536000",
    "DJANGO_LOG_LEVEL": "INFO",
    "PYTHONIOENCODING": "utf-8",
}


def main() -> int:
    run_env = {**os.environ, **ENV}
    # Make sure a stray local .env cannot flip DEBUG or DATABASE_URL on.
    for var in ("DJANGO_DEBUG", "DATABASE_URL", "DJANGO_SECRET_KEY", "REDIS_URL"):
        run_env[var] = ENV[var]

    print(f"DJANGO_SETTINGS_MODULE = {run_env['DJANGO_SETTINGS_MODULE']}")
    print(f"DJANGO_DEBUG           = {run_env['DJANGO_DEBUG']}")
    print(f"DJANGO_ALLOWED_HOSTS   = {run_env['DJANGO_ALLOWED_HOSTS']}")
    print(f"DATABASE_URL           = {run_env['DATABASE_URL']}")
    print(f"REDIS_URL              = {run_env['REDIS_URL']}")
    print()

    proc = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy"],
        cwd=ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.rstrip())

    print()
    if proc.returncode != 0:
        print("GATE: FAIL (check --deploy exited non-zero)")
        return 1

    # "System check identified no issues (0 silenced)." == clean.
    # Anything else means at least one W/E was reported.
    if "identified no issues" in out:
        print("GATE: PASS (0 warnings, 0 errors under config.settings.prod)")
        return 0

    print("GATE: FAIL (deployment warnings/errors reported above)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

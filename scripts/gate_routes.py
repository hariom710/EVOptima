"""Print the resolved route map (used by the Phase-1 gate)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from django.urls import resolve  # noqa: E402

ROUTES = [
    "/api/monitoring/status/",
    "/api/monitoring/thresholds/",
    "/api/monitoring/events/",
    "/api/monitoring/simulate/start/",
    "/api/monitoring/simulate/stop/",
    "/api/status/",
    "/api/thresholds/",
    "/api/events/",
    "/api/dashboard/",
    "/monitoring/dashboard/",
    "/",
    "/home/",
    "/welcome/",
    "/prediction/",
    "/visualization/",
    "/accounts/login/",
    "/admin/",
]

# Routes the two former front-ends call verbatim.
REQUIRED = {
    "/api/monitoring/status/",
    "/api/monitoring/thresholds/",
    "/api/monitoring/events/",
    "/api/status/",
    "/api/events/",
    "/monitoring/dashboard/",
}

failed = []
for path in ROUTES:
    try:
        r = resolve(path)
        label = f"{r.namespace}:{r.url_name}" if r.namespace else r.url_name
        flag = "" if path not in REQUIRED else "  [required]"
        print(f"  {path:34s} -> {label}{flag}")
    except Exception:
        print(f"  {path:34s} -> UNRESOLVED")
        if path in REQUIRED:
            failed.append(path)

print()
print("GATE:", "PASS" if not failed else f"FAIL unresolved: {failed}")
sys.exit(0 if not failed else 1)

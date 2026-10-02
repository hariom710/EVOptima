"""Phase-1 verification gate: ``group_send`` -> WebSocket broadcast round-trip.

The browser path is: services.monitor.MonitoringService calls
``channel_layer.group_send("monitoring", {"type": "monitoring_update", ...})``
and MonitoringConsumer replays it to every connected client.

InMemoryChannelLayer (dev) only fans out inside a single process, so the probe
runs the whole round-trip in-process with a Channels ``WebsocketCommunicator``
bound to ``config.asgi.application``.

Usage:
    python scripts/gate_groupcast.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Running `python scripts/<name>.py` puts scripts/ (not the repo root) on
# sys.path, so make the project importable explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from channels.layers import get_channel_layer  # noqa: E402
from channels.testing import WebsocketCommunicator  # noqa: E402

from config.asgi import application  # noqa: E402

PATH = "/ws/monitoring/"
GROUP = "monitoring"


async def main() -> int:
    comm = WebsocketCommunicator(application, PATH)
    connected, subprotocol = await comm.connect()
    if not connected:
        print(f"FAIL: could not connect to {PATH}")
        return 1
    print(f"connected to {PATH}")

    # 1. initial status_update pushed by MonitoringConsumer.connect()
    first = await comm.receive_json_from(timeout=10)
    print(f"initial frame  : type={first.get('type')!r}")
    if first.get("type") != "status_update":
        print(f"FAIL: expected status_update, got {first.get('type')!r}")
        await comm.disconnect()
        return 1

    # 2. simulate what MonitoringService broadcasts every sample cycle
    payload = {
        "latest_reading": {"current": 41.5, "voltage": 430.0, "temperature": 44.0},
        "state": "FAULT",
    }
    layer = get_channel_layer()
    await layer.group_send(GROUP, {"type": "monitoring_update", "data": payload})

    second = await comm.receive_json_from(timeout=10)
    print(f"broadcast frame: state={second.get('state')!r}")
    if second.get("state") != "FAULT":
        print(f"FAIL: broadcast did not arrive intact, got {second!r}")
        await comm.disconnect()
        return 1

    await comm.disconnect()
    print("GATE: PASS (connect -> status_update -> group_send -> broadcast)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

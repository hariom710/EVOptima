"""Phase-1 verification gate: WebSocket channel establishment.

Connects to ``ws://<host>/ws/monitoring/`` through the same ProtocolTypeRouter
+ AuthMiddlewareStack stack the browser uses, then verifies the consumer
accepts the connection and pushes its initial ``status_update`` payload.

Usage:
    python scripts/gate_ws.py ws://127.0.0.1:8023/ws/monitoring/
"""
import asyncio
import json
import sys

import websockets

WS_URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8023/ws/monitoring/"
TIMEOUT = 10.0


async def main() -> int:
    print(f"connecting: {WS_URL}")
    try:
        async with websockets.connect(WS_URL, open_timeout=TIMEOUT) as ws:
            print("  connected (HTTP 101 -> websocket upgrade OK)")

            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            print(f"  first frame: type={msg.get('type')!r}")

            data = msg.get("data", {})
            print(f"    state       = {data.get('state')!r}")
            print(f"    thresholds  = {data.get('thresholds') is not None}")
            print(f"    latest      = {data.get('latest_reading') is not None}")
            print(f"    events      = {len(data.get('events') or [])}")

            if msg.get("type") != "status_update":
                print(f"FAIL: expected 'status_update', got {msg.get('type')!r}")
                return 1

    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print("GATE: PASS (WebSocket accepted, initial status_update received)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""One-shot smoke test for Fleet Control FSM / session / custom. Not for CI longevity."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, Optional

BASE = "http://127.0.0.1:8787"
TOKEN = "dev-change-me"
DEADLINE = float(os.environ.get("FLEET_STARTING_DEADLINE_SEC", "15"))


def post_json(path: str, data: dict, headers: Optional[Dict[str, str]] = None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(
        BASE + path, data=json.dumps(data).encode(), headers=h, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def get_json(path: str, headers: dict):
    req = urllib.request.Request(BASE + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    sess = post_json("/api/session", {"token": TOKEN})
    ticket = sess["ticket"]
    assert ticket
    print("session ok")

    try:
        post_json("/api/session", {"token": "nope"})
        raise SystemExit("FAIL bad token accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 401
        print("bad token rejected", e.code)

    h = {"X-Fleet-Session": ticket, "Content-Type": "application/json"}
    snap = get_json("/api/status", h)
    print("initial", snap["pool_state"], snap["desired_state"])

    snap = post_json("/api/pool/enable", {"preset": "pool-7b"}, h)
    print("after enable", snap["pool_state"], snap["message"])
    assert snap["pool_state"] in ("starting", "degraded")
    assert snap["pool_state"] != "ready"

    time.sleep(DEADLINE + 2)
    snap = get_json("/api/status", h)
    print("after deadline", snap["pool_state"], snap["message"])
    assert snap["pool_state"] == "degraded", snap

    try:
        post_json("/api/nodes/i7/toggle", {}, h)
        raise SystemExit("FAIL toggle while on")
    except urllib.error.HTTPError as e:
        assert e.code == 409
        print("toggle locked", e.code)

    snap = post_json("/api/pool/disable", {}, h)
    print("disabled", snap["pool_state"], snap["desired_state"])
    assert snap["desired_state"] == "off"
    # Agents may take a moment to stop shards → allow stopping then off
    for _ in range(20):
        if snap["pool_state"] == "off":
            break
        time.sleep(0.5)
        snap = get_json("/api/status", h)
    assert snap["pool_state"] == "off", snap

    snap = post_json("/api/preset/custom", {}, h)
    assert snap["preset"] == "custom"
    snap = post_json("/api/nodes/i7/toggle", {}, h)
    print("custom after toggle", snap["preset"], snap["plan_dirty"])
    assert snap["preset"] == "custom"
    inc = {n["id"]: n["included"] for n in snap["nodes"]}
    assert inc["i7"] is False

    hb = {
        "node_id": "main",
        "label": "<img src=x onerror=alert(1)>",
        "role": "aggregator+shard_a",
        "gross_ram_gb": 16,
        "ram_total_mb": 16000,
        "ram_used_mb": 8000,
        "ram_available_mb": 8000,
        "shard_rss_mb": 0,
        "shard_running": False,
    }
    post_json("/api/agent/heartbeat", hb, h)
    snap = get_json("/api/status", h)
    label = next(n["label"] for n in snap["nodes"] if n["id"] == "main")
    assert "<img" in label
    print("xss label stored as text ok")

    # compose doc check
    from pathlib import Path

    compose = Path(__file__).resolve().parents[1] / "compose" / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert 'profiles: ["pihole"]' in text
    assert "${PI_LAN_IP}:53:53/udp" in text
    assert "- \"53:53/udp\"" not in text
    print("compose pihole bind ok")
    print("SMOKE_OK")


if __name__ == "__main__":
    main()

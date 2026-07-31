"""Enable small-local with a live agent; expect ready + RSS then clean off."""
from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8787"


def main() -> None:
    req = urllib.request.Request(
        BASE + "/api/session",
        data=json.dumps({"token": "dev-change-me"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ticket = json.loads(urllib.request.urlopen(req).read())["ticket"]
    h = {"X-Fleet-Session": ticket, "Content-Type": "application/json"}

    def post(path: str, data: dict):
        r = urllib.request.Request(
            BASE + path, data=json.dumps(data).encode(), headers=h, method="POST"
        )
        return json.loads(urllib.request.urlopen(r).read())

    def get(path: str):
        r = urllib.request.Request(BASE + path, headers=h)
        return json.loads(urllib.request.urlopen(r).read())

    post("/api/pool/disable", {})
    time.sleep(1)
    post("/api/preset/small-local", {})
    snap = post("/api/pool/enable", {"preset": "small-local"})
    print("enable", snap["pool_state"])

    ready = False
    for i in range(30):
        snap = get("/api/status")
        main = next(n for n in snap["nodes"] if n["id"] == "main")
        print(
            "t",
            i,
            snap["pool_state"],
            "rss",
            snap["pool"]["shard_rss_mb"],
            "ver",
            main.get("agent_version"),
            "running",
            main.get("shard_running"),
        )
        if snap["pool_state"] == "ready" and snap["pool"]["shard_rss_mb"] > 0:
            ready = True
            break
        time.sleep(1)
    assert ready, snap

    post("/api/pool/disable", {})
    off = False
    for i in range(20):
        snap = get("/api/status")
        if snap["pool_state"] == "off" and snap["pool"]["shard_rss_mb"] == 0:
            off = True
            print("disable ok")
            break
        time.sleep(1)
    assert off, snap
    print("AGENT_SMOKE_OK")


if __name__ == "__main__":
    main()

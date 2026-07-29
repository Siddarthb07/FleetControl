"""Stub shard server — holds a configurable memory slab to simulate model weights."""
from __future__ import annotations

import os
import signal
import sys
import time

import psutil

try:
    from fastapi import FastAPI
    import uvicorn
except ImportError:
    FastAPI = None  # type: ignore


def main() -> None:
    node_id = os.environ.get("FLEET_NODE_ID", "shard")
    stub_mb = int(os.environ.get("FLEET_STUB_RAM_MB", "256"))
    port = int(os.environ.get("FLEET_SHARD_PORT", "0"))  # 0 = no HTTP

    # Allocate roughly stub_mb of RAM (bytearray)
    print(f"[shard] node={node_id} allocating stub_ram_mb={stub_mb}", flush=True)
    slab = bytearray(stub_mb * 1024 * 1024)
    # touch pages so RSS reflects allocation on Windows/Linux
    for i in range(0, len(slab), 4096):
        slab[i] = 1

    def _shutdown(*_args: object) -> None:
        print("[shard] shutdown", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _shutdown)

    if FastAPI is not None and port > 0:
        app = FastAPI()

        @app.get("/health")
        def health() -> dict:
            p = psutil.Process()
            return {
                "node_id": node_id,
                "stub_ram_mb": stub_mb,
                "rss_mb": round(p.memory_info().rss / (1024 * 1024), 1),
            }

        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    else:
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()

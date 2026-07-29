"""Minimal LLM aggregator stub — alive while pool is enabled."""
from __future__ import annotations

import os

from fastapi import FastAPI
import uvicorn

app = FastAPI(title="LLM Fleet Aggregator", version="0.1.0")
STATE = os.environ.get("FLEET_POOL_STATE", "ready")


@app.get("/health")
def health() -> dict:
    return {
        "status": STATE,
        "service": "llm_fleet.aggregator",
        "note": "stub aggregator — process surface for Fleet Control only",
    }


@app.post("/generate")
def generate() -> dict:
    if STATE == "off":
        return {"ok": False, "error": "pool disabled"}
    return {
        "ok": True,
        "message": "aggregator stub — not real HF pipeline-parallel",
    }


def main() -> None:
    port = int(os.environ.get("FLEET_AGG_PORT", "8790"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

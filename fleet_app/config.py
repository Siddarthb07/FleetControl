"""Fleet Control configuration."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


FLEET_TOKEN = _env("FLEET_TOKEN", "dev-change-me")
CONTROL_HOST = _env("FLEET_CONTROL_HOST", "127.0.0.1")
CONTROL_PORT = int(_env("FLEET_CONTROL_PORT", "8787"))

# Optional global default; preset "runtime" wins on enable.
FLEET_RUNTIME = _env("FLEET_RUNTIME", "")  # "" | stub | anima

ANIMA_ROOT = Path(_env("ANIMA_ROOT") or (ROOT.parent / "Anima"))
ANIMA_PORT = int(_env("FLEET_ANIMA_PORT", "8010"))
ANIMA_PYTHON = _env("FLEET_ANIMA_PYTHON", "")  # empty → same as fleet python
ANIMA_STARTING_DEADLINE_SEC = float(_env("FLEET_ANIMA_STARTING_DEADLINE_SEC", "180"))

DEFAULT_NODES = {
    "main": {
        "label": "Main PC",
        "role": "aggregator+shard_a",
        "gross_ram_gb": 16,
        "include_default": True,
    },
    "i7": {
        "label": "i7 worker",
        "role": "shard_b",
        "gross_ram_gb": 12,
        "include_default": True,
    },
    "mac": {
        "label": "Mac",
        "role": "shard_c",
        "gross_ram_gb": 8,
        "include_default": False,
    },
}

PRESETS: Dict[str, Dict[str, Any]] = {
    "custom": {
        "nodes": [],
        "stub_ram_mb": {},
        "label": "No preset (current includes)",
        "runtime": "stub",
    },
    "anima-local": {
        "nodes": ["main"],
        "stub_ram_mb": {},
        "label": "Anima on main (probes live)",
        "runtime": "anima",
    },
    "small-local": {
        "nodes": ["main"],
        "stub_ram_mb": {"main": 512},
        "label": "small-local (stub pool)",
        "runtime": "stub",
    },
    "pool-7b": {
        "nodes": ["main", "i7"],
        "stub_ram_mb": {"main": 4096, "i7": 4096},
        "label": "pool-7b (stub pool)",
        "runtime": "stub",
    },
    "pool-max": {
        "nodes": ["main", "i7", "mac"],
        "stub_ram_mb": {"main": 4096, "i7": 4096, "mac": 2048},
        "label": "pool-max (stub pool)",
        "runtime": "stub",
    },
}

DEFAULT_PRESET = _env("FLEET_PRESET", "pool-7b")
if DEFAULT_PRESET not in PRESETS:
    DEFAULT_PRESET = "pool-7b"

HEARTBEAT_STALE_SEC = float(_env("FLEET_HEARTBEAT_STALE_SEC", "5"))
STARTING_DEADLINE_SEC = float(_env("FLEET_STARTING_DEADLINE_SEC", "15"))
COMMAND_TTL_SEC = float(_env("FLEET_COMMAND_TTL_SEC", "60"))
CUSTOM_DEFAULT_STUB_MB = int(_env("FLEET_CUSTOM_STUB_MB", "512"))
SESSION_TTL_SEC = float(_env("FLEET_SESSION_TTL_SEC", "86400"))

LLM_FLEET_DIR = ROOT / "llm_fleet"
PYTHON = _env("FLEET_PYTHON", os.environ.get("PYTHON", "python"))


def preset_runtime(preset: str) -> str:
    """Resolve runtime for a preset. Preset field wins; else FLEET_RUNTIME; else stub."""
    meta = PRESETS.get(preset) or {}
    rt = str(meta.get("runtime") or "").strip().lower()
    if rt in ("anima", "stub"):
        return rt
    if FLEET_RUNTIME in ("anima", "stub"):
        return FLEET_RUNTIME
    return "stub"


def starting_deadline_for(preset: str) -> float:
    if preset_runtime(preset) == "anima":
        return ANIMA_STARTING_DEADLINE_SEC
    return STARTING_DEADLINE_SEC


def clamp_stub_ram_mb(node_id: str, requested_mb: int, gross_ram_gb: float) -> int:
    """Clamp stub allocation to leave ~4 GB OS/apps headroom."""
    headroom_mb = max(256, int((gross_ram_gb - 4.0) * 1024))
    return max(256, min(int(requested_mb), headroom_mb))

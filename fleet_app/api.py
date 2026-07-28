"""Fleet Control API — enable/disable LLM pool + live telemetry."""
from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fleet_app.config import (
    CONTROL_HOST,
    CONTROL_PORT,
    FLEET_TOKEN,
    LLM_FLEET_DIR,
    LOG_DIR,
    PRESETS,
    PYTHON,
    ROOT,
    SESSION_TTL_SEC,
    preset_runtime,
)
from fleet_app.state import store

STATIC = Path(__file__).resolve().parent / "static"
AGG_PROC = None  # type: Optional[subprocess.Popen]
SESSIONS: Dict[str, float] = {}  # ticket -> expiry

app = FastAPI(title="Fleet Control", version="0.2.0")


def _token_ok(candidate: Optional[str]) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), FLEET_TOKEN.encode("utf-8"))


def _session_ok(ticket: Optional[str]) -> bool:
    if not ticket:
        return False
    exp = SESSIONS.get(ticket)
    if exp is None:
        return False
    if time.time() > exp:
        SESSIONS.pop(ticket, None)
        return False
    return True


def require_token(
    authorization: Optional[str] = Header(default=None),
    x_fleet_token: Optional[str] = Header(default=None),
    x_fleet_session: Optional[str] = Header(default=None),
) -> None:
    if _session_ok(x_fleet_session):
        return
    token = None
    if x_fleet_token:
        token = x_fleet_token
    elif authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not _token_ok(token):
        raise HTTPException(status_code=401, detail="invalid fleet token")


class EnableBody(BaseModel):
    preset: Optional[str] = None


class ToggleBody(BaseModel):
    included: Optional[bool] = None


class HeartbeatBody(BaseModel):
    node_id: str
    label: Optional[str] = None
    role: Optional[str] = None
    gross_ram_gb: Optional[float] = None
    ram_total_mb: float = 0
    ram_used_mb: float = 0
    ram_available_mb: float = 0
    shard_rss_mb: float = 0
    shard_running: bool = False
    agent_version: str = "0.1.0"
    detail: str = ""


class SessionBody(BaseModel):
    token: str


class AckBody(BaseModel):
    ids: List[str] = []


def _open_log(name: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return open(LOG_DIR / name, "a", encoding="utf-8", buffering=1)


def _start_aggregator() -> None:
    global AGG_PROC
    if AGG_PROC is not None and AGG_PROC.poll() is None:
        return
    script = LLM_FLEET_DIR / "aggregator.py"
    out = _open_log("aggregator.out.log")
    err = _open_log("aggregator.err.log")
    AGG_PROC = subprocess.Popen(
        [PYTHON, str(script)],
        cwd=str(ROOT),
        env=dict(os.environ, FLEET_POOL_STATE="on"),
        stdout=out,
        stderr=err,
    )


def _stop_aggregator() -> None:
    global AGG_PROC
    if AGG_PROC is not None and AGG_PROC.poll() is None:
        AGG_PROC.terminate()
        try:
            AGG_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            AGG_PROC.kill()
    AGG_PROC = None


@app.on_event("startup")
async def _startup_derive_loop() -> None:
    async def loop() -> None:
        while True:
            try:
                store.derive_pool_state()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(2.0)

    asyncio.create_task(loop())


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/session")
def create_session(body: SessionBody) -> Dict[str, Any]:
    if not _token_ok(body.token):
        raise HTTPException(status_code=401, detail="invalid fleet token")
    ticket = secrets.token_urlsafe(32)
    SESSIONS[ticket] = time.time() + SESSION_TTL_SEC
    return {"ticket": ticket, "expires_in": int(SESSION_TTL_SEC)}


@app.get("/api/status")
def status(_: None = Depends(require_token)) -> Dict[str, Any]:
    store.derive_pool_state()
    return store.snapshot()


@app.post("/api/pool/enable")
def pool_enable(body: EnableBody, _: None = Depends(require_token)) -> Dict[str, Any]:
    preset = body.preset or store.state.preset
    if preset not in PRESETS:
        raise HTTPException(400, "unknown preset: {0}".format(preset))
    try:
        store.set_desired_on(preset)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    runtime = preset_runtime(preset)
    if runtime == "anima":
        store.enqueue(
            "main",
            {
                "op": "start_anima",
                "runtime": "anima",
                "preset": preset,
                "shard_gen": store.state.shard_gen,
                "ts": time.time(),
            },
        )
    else:
        for nid in store.included_nodes():
            snap_n = next((n for n in store.snapshot()["nodes"] if n["id"] == nid), None)
            stub = int(snap_n["target_stub_ram_mb"]) if snap_n else 512
            store.enqueue(
                nid,
                {
                    "op": "start_shard",
                    "runtime": "stub",
                    "preset": preset,
                    "stub_ram_mb": stub,
                    "shard_gen": store.state.shard_gen,
                    "ts": time.time(),
                },
            )
        try:
            _start_aggregator()
        except Exception as exc:  # noqa: BLE001
            store.state.desired_state = "off"
            store.derive_pool_state()
            store.state.pool_state = "error"
            store.state.message = "aggregator failed: {0}".format(exc)
            raise HTTPException(500, str(exc)) from exc

    store.derive_pool_state()
    return store.snapshot()


@app.post("/api/pool/disable")
def pool_disable(_: None = Depends(require_token)) -> Dict[str, Any]:
    runtime = preset_runtime(store.state.preset)
    store.set_desired_off()
    if runtime == "anima":
        store.enqueue(
            "main",
            {
                "op": "stop_anima",
                "runtime": "anima",
                "shard_gen": store.state.shard_gen,
                "ts": time.time(),
            },
        )
    else:
        for nid in store.state.nodes:
            store.enqueue(
                nid,
                {
                    "op": "stop_shard",
                    "runtime": "stub",
                    "shard_gen": store.state.shard_gen,
                    "ts": time.time(),
                },
            )
        _stop_aggregator()
    store.derive_pool_state()
    return store.snapshot()

@app.post("/api/nodes/{node_id}/toggle")
def toggle_node(node_id: str, body: ToggleBody, _: None = Depends(require_token)) -> Dict[str, Any]:
    if node_id not in store.state.nodes:
        raise HTTPException(404, "unknown node")
    if store.membership_locked():
        raise HTTPException(409, "disable pool before changing node membership")
    try:
        store.toggle_node(node_id, body.included)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return store.snapshot()


@app.post("/api/preset/{name}")
def set_preset(name: str, _: None = Depends(require_token)) -> Dict[str, Any]:
    if store.membership_locked():
        raise HTTPException(409, "disable pool before changing preset")
    try:
        store.set_preset(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return store.snapshot()


@app.post("/api/agent/heartbeat")
def agent_heartbeat(body: HeartbeatBody, _: None = Depends(require_token)) -> Dict[str, str]:
    store.heartbeat(body.model_dump())
    return {"ok": "1"}


@app.get("/api/agent/desired/{node_id}")
def agent_desired(node_id: str, _: None = Depends(require_token)) -> Dict[str, Any]:
    return store.desired_for_node(node_id)


@app.get("/api/agent/pending/{node_id}")
def agent_pending(node_id: str, _: None = Depends(require_token)) -> Dict[str, Any]:
    # Back-compat: same as commands slice of desired
    return {"commands": store.desired_for_node(node_id)["commands"]}


@app.post("/api/agent/ack/{node_id}")
def agent_ack(node_id: str, body: AckBody, _: None = Depends(require_token)) -> Dict[str, str]:
    store.ack_commands(node_id, body.ids or [])
    return {"ok": "1"}


def _ws_extract_token(ws: WebSocket) -> Optional[str]:
    # Prefer Sec-WebSocket-Protocol: "fleet,<token>" or "fleet,<session-ticket>"
    proto = ws.headers.get("sec-websocket-protocol") or ""
    parts = [p.strip() for p in proto.split(",") if p.strip()]
    if len(parts) >= 2 and parts[0].lower() == "fleet":
        return parts[1]
    # Legacy query param rejected intentionally — return None
    return None


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    candidate = _ws_extract_token(ws)
    ok = _token_ok(candidate) or _session_ok(candidate)
    if not ok:
        await ws.close(code=4401)
        return
    # Echo selected subprotocol back
    await ws.accept(subprotocol="fleet")
    try:
        while True:
            store.derive_pool_state()
            await ws.send_json(store.snapshot())
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC / "index.html"))


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("FLEET_CONTROL_HOST", CONTROL_HOST)
    port = int(os.environ.get("FLEET_CONTROL_PORT", str(CONTROL_PORT)))
    sys.path.insert(0, str(ROOT))
    uvicorn.run("fleet_app.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()

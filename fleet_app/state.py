"""In-memory fleet state (single control process)."""
from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from fleet_app.config import (
    ANIMA_PORT,
    COMMAND_TTL_SEC,
    CUSTOM_DEFAULT_STUB_MB,
    DEFAULT_NODES,
    DEFAULT_PRESET,
    HEARTBEAT_STALE_SEC,
    PRESETS,
    clamp_stub_ram_mb,
    preset_runtime,
    starting_deadline_for,
)


@dataclass
class NodeState:
    id: str
    label: str
    role: str
    gross_ram_gb: float
    included: bool = True
    last_heartbeat: float = 0.0
    ram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_available_mb: float = 0.0
    shard_rss_mb: float = 0.0
    shard_running: bool = False
    agent_version: str = ""
    detail: str = ""


@dataclass
class FleetState:
    desired_state: str = "off"  # off | on
    pool_state: str = "off"  # off | starting | ready | degraded | error | stopping
    preset: str = DEFAULT_PRESET
    plan_dirty: bool = False
    model_id: str = "stub-pool"
    message: str = ""
    updated_at: float = field(default_factory=time.time)
    desired_on_at: float = 0.0
    shard_gen: int = 0
    nodes: Dict[str, NodeState] = field(default_factory=dict)
    pending_commands: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    acked_commands: Dict[str, Set[str]] = field(default_factory=dict)
    # node_id -> desired stub_ram_mb when pool desired on
    desired_stub_ram_mb: Dict[str, int] = field(default_factory=dict)


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        nodes = {}
        for nid, meta in DEFAULT_NODES.items():
            nodes[nid] = NodeState(
                id=nid,
                label=meta["label"],
                role=meta["role"],
                gross_ram_gb=meta["gross_ram_gb"],
                included=bool(meta["include_default"]),
            )
        self.state = FleetState(
            nodes=nodes,
            pending_commands={nid: [] for nid in nodes},
            acked_commands={nid: set() for nid in nodes},
        )
        if DEFAULT_PRESET in PRESETS and DEFAULT_PRESET != "custom":
            self._apply_preset_includes(DEFAULT_PRESET)

    def _apply_preset_includes(self, preset: str) -> None:
        if preset == "custom":
            return
        wanted = set(PRESETS[preset]["nodes"])
        for nid, node in self.state.nodes.items():
            node.included = nid in wanted

    def _node_online(self, n: NodeState, now: float) -> bool:
        if not n.last_heartbeat:
            return False
        return (now - n.last_heartbeat) <= HEARTBEAT_STALE_SEC

    def _target_stub_mb(self, node_id: str) -> int:
        n = self.state.nodes[node_id]
        preset = self.state.preset
        if node_id in self.state.desired_stub_ram_mb:
            requested = self.state.desired_stub_ram_mb[node_id]
        elif preset == "custom":
            requested = CUSTOM_DEFAULT_STUB_MB
        else:
            requested = int(
                PRESETS.get(preset, {}).get("stub_ram_mb", {}).get(node_id, CUSTOM_DEFAULT_STUB_MB)
            )
        return clamp_stub_ram_mb(node_id, requested, n.gross_ram_gb)

    def derive_pool_state(self, now: Optional[float] = None) -> None:
        """Recompute pool_state from desired_state + observed node heartbeats."""
        with self._lock:
            now = now if now is not None else time.time()
            included = [n for n in self.state.nodes.values() if n.included]
            any_shard = any(
                self._node_online(n, now) and n.shard_running for n in self.state.nodes.values()
            )

            if self.state.desired_state == "off":
                # Drop observed shard flags for stale/offline nodes so a dead agent
                # cannot pin the FSM in "stopping" forever.
                for n in self.state.nodes.values():
                    if not self._node_online(n, now):
                        n.shard_running = False
                        n.shard_rss_mb = 0.0
                any_shard = any(
                    self._node_online(n, now) and n.shard_running
                    for n in self.state.nodes.values()
                )
                if any_shard:
                    self.state.pool_state = "stopping"
                    self.state.message = "stopping pool — waiting for shards to exit"
                else:
                    self.state.pool_state = "off"
                    self.state.message = "pool disabled"
                self.state.updated_at = now
                return

            # desired on
            if not included:
                self.state.pool_state = "error"
                self.state.message = "no nodes included in plan"
                self.state.updated_at = now
                return

            missing = []
            not_running = []
            for n in included:
                online = self._node_online(n, now)
                if not online:
                    missing.append(n.id)
                elif not n.shard_running:
                    not_running.append(n.id)

            all_ready = not missing and not not_running
            elapsed = (now - self.state.desired_on_at) if self.state.desired_on_at else 0.0
            deadline = starting_deadline_for(self.state.preset)
            runtime = preset_runtime(self.state.preset)
            workload = "Anima" if runtime == "anima" else "shard"

            if all_ready:
                self.state.pool_state = "ready"
                if runtime == "anima":
                    self.state.message = "Anima ready on main · probes live · :{0}".format(ANIMA_PORT)
                else:
                    self.state.message = "pool ready ({0})".format(self.state.preset)
            elif elapsed < deadline and (missing or not_running):
                self.state.pool_state = "starting"
                wait = missing + not_running
                self.state.message = "waiting for {0}".format(", ".join(wait))
            else:
                self.state.pool_state = "degraded"
                parts = []
                if missing:
                    parts.append("missing: " + ", ".join(missing))
                if not_running:
                    parts.append("no {0}: {1}".format(workload, ", ".join(not_running)))
                if elapsed >= deadline:
                    parts.append("timed out after {0:.0f}s".format(deadline))
                self.state.message = "; ".join(parts) if parts else "degraded"
            self.state.updated_at = now

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            nodes_out = []
            pool_rss = 0.0
            online_included = 0
            included = 0
            target_mb = 0
            runtime = preset_runtime(self.state.preset)
            for n in self.state.nodes.values():
                stale = (now - n.last_heartbeat) > HEARTBEAT_STALE_SEC if n.last_heartbeat else True
                online = (not stale) and bool(n.last_heartbeat)
                included += int(n.included)
                if n.included and online:
                    online_included += 1
                if n.included and online and n.shard_running:
                    pool_rss += n.shard_rss_mb
                stub = self._target_stub_mb(n.id) if n.included else 0
                if n.included and runtime == "stub":
                    target_mb += stub
                d = asdict(n)
                d["online"] = online
                d["stale"] = stale
                d["desired_shard_running"] = bool(
                    self.state.desired_state == "on" and n.included
                )
                d["target_stub_ram_mb"] = stub if runtime == "stub" else 0
                nodes_out.append(d)

            planning_gb = 0.0
            for n in self.state.nodes.values():
                if n.included:
                    planning_gb += max(0.0, n.gross_ram_gb - 4.0)

            preset_meta = []
            for key, meta in PRESETS.items():
                preset_meta.append(
                    {
                        "id": key,
                        "label": meta.get("label", key),
                        "runtime": preset_runtime(key),
                    }
                )

            model_id = "anima" if runtime == "anima" else "stub-pool"
            return {
                "desired_state": self.state.desired_state,
                "pool_state": self.state.pool_state,
                "preset": self.state.preset,
                "runtime": runtime,
                "plan_dirty": self.state.plan_dirty,
                "model_id": model_id,
                "message": self.state.message,
                "updated_at": self.state.updated_at,
                "shard_gen": self.state.shard_gen,
                "anima": {
                    "port": ANIMA_PORT,
                    "url": "http://127.0.0.1:{0}".format(ANIMA_PORT),
                },
                "nodes": nodes_out,
                "pool": {
                    "shard_rss_mb": round(pool_rss, 1),
                    "workload_rss_mb": round(pool_rss, 1),
                    "target_stub_ram_mb": target_mb,
                    "included_nodes": included,
                    "online_included": online_included,
                    "planning_usable_gb": round(planning_gb, 1),
                    "optimistic_ceiling_gb": 24.0,
                },
                "presets": [p["id"] for p in preset_meta],
                "preset_meta": preset_meta,
            }

    def _clear_pending(self) -> None:
        for nid in self.state.nodes:
            self.state.pending_commands[nid] = []
            self.state.acked_commands[nid] = set()

    def set_desired_on(self, preset: Optional[str] = None) -> None:
        with self._lock:
            if preset is not None:
                self._set_preset_unlocked(preset)
            self.state.desired_state = "on"
            self.state.desired_on_at = time.time()
            self.state.shard_gen += 1
            self._clear_pending()
            self.state.desired_stub_ram_mb = {}
            for nid in self.state.nodes:
                if self.state.nodes[nid].included:
                    self.state.desired_stub_ram_mb[nid] = self._target_stub_mb(nid)
            self.state.updated_at = time.time()
        self.derive_pool_state()

    def set_desired_off(self) -> None:
        with self._lock:
            self.state.desired_state = "off"
            self.state.shard_gen += 1
            self._clear_pending()
            self.state.desired_stub_ram_mb = {}
            self.state.updated_at = time.time()
        self.derive_pool_state()

    def _set_preset_unlocked(self, preset: str) -> None:
        if preset not in PRESETS:
            raise ValueError("unknown preset: {0}".format(preset))
        self.state.preset = preset
        if preset != "custom":
            self._apply_preset_includes(preset)
            self.state.plan_dirty = False
        self.state.updated_at = time.time()

    def set_preset(self, preset: str) -> None:
        with self._lock:
            if self.state.desired_state == "on":
                raise RuntimeError("disable pool before changing preset")
            self._set_preset_unlocked(preset)

    def toggle_node(self, node_id: str, included: Optional[bool] = None) -> NodeState:
        with self._lock:
            if self.state.desired_state == "on":
                raise RuntimeError("disable pool before changing node membership")
            node = self.state.nodes[node_id]
            node.included = (not node.included) if included is None else bool(included)
            if self.state.preset != "custom":
                self.state.preset = "custom"
            self.state.plan_dirty = True
            self.state.updated_at = time.time()
            return deepcopy(node)

    def enqueue(self, node_id: str, command: Dict[str, Any]) -> str:
        with self._lock:
            cmd_id = command.get("id") or str(uuid.uuid4())
            envelope = {
                "id": cmd_id,
                "op": command["op"],
                "ts": command.get("ts", time.time()),
                "ttl": command.get("ttl", COMMAND_TTL_SEC),
                **{k: v for k, v in command.items() if k not in {"id", "op", "ts", "ttl"}},
            }
            self.state.pending_commands.setdefault(node_id, []).append(envelope)
            return cmd_id

    def drain_commands(self, node_id: str) -> List[Dict[str, Any]]:
        """Return pending non-expired, non-acked commands without clearing (peek+filter)."""
        with self._lock:
            now = time.time()
            acked = self.state.acked_commands.setdefault(node_id, set())
            kept: List[Dict[str, Any]] = []
            out: List[Dict[str, Any]] = []
            for cmd in self.state.pending_commands.get(node_id, []):
                age = now - float(cmd.get("ts", now))
                ttl = float(cmd.get("ttl", COMMAND_TTL_SEC))
                if age > ttl:
                    continue
                kept.append(cmd)
                if cmd.get("id") in acked:
                    continue
                out.append(cmd)
            self.state.pending_commands[node_id] = kept
            return out

    def ack_commands(self, node_id: str, ids: List[str]) -> None:
        with self._lock:
            acked = self.state.acked_commands.setdefault(node_id, set())
            for i in ids:
                acked.add(i)
            # prune delivered commands
            pending = self.state.pending_commands.get(node_id, [])
            self.state.pending_commands[node_id] = [
                c for c in pending if c.get("id") not in acked
            ]
            if len(acked) > 200:
                # keep set bounded
                self.state.acked_commands[node_id] = set(list(acked)[-100:])

    def desired_for_node(self, node_id: str) -> Dict[str, Any]:
        with self._lock:
            n = self.state.nodes.get(node_id)
            included = bool(n.included) if n else False
            runtime = preset_runtime(self.state.preset)
            desired_running = self.state.desired_state == "on" and included
            # Anima workload only on main; other nodes stay telemetry-only.
            if runtime == "anima" and node_id != "main":
                desired_running = False
            stub = self._target_stub_mb(node_id) if n and desired_running and runtime == "stub" else 0
            cmds = self.drain_commands(node_id)
            return {
                "node_id": node_id,
                "desired_shard_running": desired_running,
                "desired_workload": desired_running,
                "runtime": runtime,
                "stub_ram_mb": stub,
                "shard_gen": self.state.shard_gen,
                "preset": self.state.preset,
                "anima_port": ANIMA_PORT,
                "commands": cmds,
            }

    def heartbeat(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            nid = payload["node_id"]
            if nid not in self.state.nodes:
                self.state.nodes[nid] = NodeState(
                    id=nid,
                    label=str(payload.get("label") or nid),
                    role=str(payload.get("role") or "worker"),
                    gross_ram_gb=float(payload.get("gross_ram_gb") or 0),
                    included=False,
                )
                self.state.pending_commands.setdefault(nid, [])
                self.state.acked_commands.setdefault(nid, set())
            n = self.state.nodes[nid]
            # Never trust agent-supplied label/role for HTML — store as plain strings
            if payload.get("label"):
                n.label = str(payload["label"])[:120]
            if payload.get("role"):
                n.role = str(payload["role"])[:80]
            if payload.get("gross_ram_gb"):
                n.gross_ram_gb = float(payload["gross_ram_gb"])
            n.last_heartbeat = time.time()
            n.ram_total_mb = float(payload.get("ram_total_mb", 0))
            n.ram_used_mb = float(payload.get("ram_used_mb", 0))
            n.ram_available_mb = float(payload.get("ram_available_mb", 0))
            n.shard_rss_mb = float(payload.get("shard_rss_mb", 0))
            n.shard_running = bool(payload.get("shard_running", False))
            n.agent_version = str(payload.get("agent_version", ""))[:40]
            n.detail = str(payload.get("detail", ""))[:200]
            self.state.updated_at = time.time()
        self.derive_pool_state()

    def included_nodes(self) -> List[str]:
        with self._lock:
            return [nid for nid, n in self.state.nodes.items() if n.included]

    def membership_locked(self) -> bool:
        with self._lock:
            return self.state.desired_state == "on"


store = Store()

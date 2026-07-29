"""Fleet agent — heartbeat + converge local stub shard or Anima workload."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
sys.path.insert(0, str(ROOT))

from llm_fleet import anima_launcher  # noqa: E402

AGENT_VERSION = "0.3.0"
SHARD_PROC = None  # type: Optional[subprocess.Popen]
LAST_STUB_MB = 0
LAST_SHARD_GEN = -1
LAST_RUNTIME = ""
WORKLOAD_DETAIL = ""


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def ram_snapshot() -> dict:
    vm = psutil.virtual_memory()
    return {
        "ram_total_mb": vm.total / (1024 * 1024),
        "ram_used_mb": vm.used / (1024 * 1024),
        "ram_available_mb": vm.available / (1024 * 1024),
    }


def _open_log(name: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return open(LOG_DIR / name, "a", encoding="utf-8", buffering=1)


def shard_rss_mb() -> float:
    global SHARD_PROC
    if SHARD_PROC is None or SHARD_PROC.poll() is not None:
        SHARD_PROC = None
        return 0.0
    try:
        p = psutil.Process(SHARD_PROC.pid)
        rss = p.memory_info().rss
        for c in p.children(recursive=True):
            try:
                rss += c.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return rss / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def start_shard(stub_ram_mb: int, node_id: str, control_url: str, token: str) -> None:
    global SHARD_PROC, LAST_STUB_MB, WORKLOAD_DETAIL
    stop_shard()
    anima_launcher.stop()
    script = ROOT / "llm_fleet" / "shard_server.py"
    env = {
        **os.environ,
        "FLEET_NODE_ID": node_id,
        "FLEET_STUB_RAM_MB": str(stub_ram_mb),
        "FLEET_TOKEN": token,
        "FLEET_CONTROL_URL": control_url,
    }
    out = _open_log("shard-{0}.out.log".format(node_id))
    err = _open_log("shard-{0}.err.log".format(node_id))
    SHARD_PROC = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
    )
    LAST_STUB_MB = stub_ram_mb
    WORKLOAD_DETAIL = "stub pid={0} mb={1}".format(SHARD_PROC.pid, stub_ram_mb)
    print("[agent] started shard stub_ram_mb={0} pid={1}".format(stub_ram_mb, SHARD_PROC.pid))


def stop_shard() -> None:
    global SHARD_PROC, WORKLOAD_DETAIL
    if SHARD_PROC is None:
        return
    if SHARD_PROC.poll() is None:
        SHARD_PROC.terminate()
        try:
            SHARD_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            SHARD_PROC.kill()
    SHARD_PROC = None
    WORKLOAD_DETAIL = "stub stopped"
    print("[agent] stopped shard")


def shard_alive() -> bool:
    return SHARD_PROC is not None and SHARD_PROC.poll() is None


def apply_commands(commands: List[dict], node_id: str, control_url: str, token: str) -> List[str]:
    global LAST_SHARD_GEN, LAST_RUNTIME, WORKLOAD_DETAIL
    acked = []
    for cmd in commands:
        cid = cmd.get("id")
        op = cmd.get("op")
        try:
            if op == "start_shard":
                mb = int(cmd.get("stub_ram_mb", 256))
                start_shard(mb, node_id, control_url, token)
                LAST_RUNTIME = "stub"
                if cmd.get("shard_gen") is not None:
                    LAST_SHARD_GEN = int(cmd["shard_gen"])
            elif op == "stop_shard":
                stop_shard()
                LAST_RUNTIME = "stub"
                if cmd.get("shard_gen") is not None:
                    LAST_SHARD_GEN = int(cmd["shard_gen"])
            elif op == "start_anima":
                if node_id != "main":
                    WORKLOAD_DETAIL = "anima ignored on non-main"
                else:
                    stop_shard()
                    ok, det = anima_launcher.start()
                    WORKLOAD_DETAIL = det
                    LAST_RUNTIME = "anima"
                    print("[agent] start_anima ok={0} {1}".format(ok, det))
                if cmd.get("shard_gen") is not None:
                    LAST_SHARD_GEN = int(cmd["shard_gen"])
            elif op == "stop_anima":
                anima_launcher.stop()
                WORKLOAD_DETAIL = anima_launcher.detail()
                LAST_RUNTIME = "anima"
                if cmd.get("shard_gen") is not None:
                    LAST_SHARD_GEN = int(cmd["shard_gen"])
                print("[agent] stop_anima")
            if cid:
                acked.append(cid)
        except Exception as exc:  # noqa: BLE001
            print("[agent] command {0} failed: {1}".format(op, exc))
            WORKLOAD_DETAIL = "cmd fail: {0}".format(exc)
    return acked


def reconcile(desired: dict, node_id: str, control_url: str, token: str) -> None:
    global LAST_SHARD_GEN, LAST_STUB_MB, LAST_RUNTIME, WORKLOAD_DETAIL
    want = bool(desired.get("desired_workload", desired.get("desired_shard_running")))
    runtime = str(desired.get("runtime") or "stub")
    stub = int(desired.get("stub_ram_mb") or 0)
    gen = int(desired.get("shard_gen") or 0)

    if runtime == "anima":
        if node_id != "main":
            # Telemetry-only: ensure no leftover stub
            if shard_alive():
                stop_shard()
            return
        running, det = anima_launcher.ensure_desired(want)
        WORKLOAD_DETAIL = det
        LAST_RUNTIME = "anima"
        if want and running:
            LAST_SHARD_GEN = gen
        elif not want:
            LAST_SHARD_GEN = gen
        return

    # stub runtime
    if anima_launcher.alive():
        anima_launcher.stop()
    alive = shard_alive()
    if want and (not alive or stub != LAST_STUB_MB or gen != LAST_SHARD_GEN or LAST_RUNTIME != "stub"):
        start_shard(stub or 512, node_id, control_url, token)
        LAST_SHARD_GEN = gen
        LAST_RUNTIME = "stub"
    elif not want and alive:
        stop_shard()
        LAST_SHARD_GEN = gen
        LAST_RUNTIME = "stub"


def workload_running(runtime: str, node_id: str) -> bool:
    if runtime == "anima":
        if node_id != "main":
            return False
        return anima_launcher.alive() and anima_launcher.health_ok()
    return shard_alive()


def workload_rss(runtime: str, node_id: str) -> float:
    if runtime == "anima" and node_id == "main":
        return anima_launcher.rss_mb()
    return shard_rss_mb()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fleet Control agent")
    ap.add_argument("--node-id", default=_env("FLEET_NODE_ID", "main"))
    ap.add_argument("--control-url", default=_env("FLEET_CONTROL_URL", "http://127.0.0.1:8787"))
    ap.add_argument("--token", default=_env("FLEET_TOKEN", "dev-change-me"))
    ap.add_argument("--label", default=_env("FLEET_NODE_LABEL", ""))
    ap.add_argument("--role", default=_env("FLEET_NODE_ROLE", "worker"))
    ap.add_argument("--gross-ram-gb", type=float, default=float(_env("FLEET_GROSS_RAM_GB", "0") or 0))
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()

    label = args.label or args.node_id
    headers = {"X-Fleet-Token": args.token}
    print("[agent] node={0} control={1}".format(args.node_id, args.control_url))

    with httpx.Client(timeout=5.0) as client:
        while True:
            runtime = "stub"
            try:
                r = client.get(
                    "{0}/api/agent/desired/{1}".format(args.control_url, args.node_id),
                    headers=headers,
                )
                r.raise_for_status()
                desired = r.json()
                runtime = str(desired.get("runtime") or "stub")
                acked = apply_commands(
                    desired.get("commands") or [],
                    args.node_id,
                    args.control_url,
                    args.token,
                )
                if acked:
                    client.post(
                        "{0}/api/agent/ack/{1}".format(args.control_url, args.node_id),
                        headers=headers,
                        json={"ids": acked},
                    ).raise_for_status()
                reconcile(desired, args.node_id, args.control_url, args.token)
            except Exception as exc:  # noqa: BLE001
                print("[agent] desired poll error: {0}".format(exc))

            ram = ram_snapshot()
            running = workload_running(runtime, args.node_id)
            rss = workload_rss(runtime, args.node_id)
            body = {
                "node_id": args.node_id,
                "label": label,
                "role": args.role,
                "gross_ram_gb": args.gross_ram_gb or round(ram["ram_total_mb"] / 1024, 1),
                **ram,
                "shard_rss_mb": round(rss, 1),
                "shard_running": running,
                "agent_version": AGENT_VERSION,
                "detail": (WORKLOAD_DETAIL or anima_launcher.detail())[:200],
            }
            try:
                client.post(
                    "{0}/api/agent/heartbeat".format(args.control_url),
                    headers=headers,
                    json=body,
                ).raise_for_status()
            except Exception as exc:  # noqa: BLE001
                print("[agent] heartbeat error: {0}".format(exc))

            time.sleep(args.interval)


if __name__ == "__main__":
    main()

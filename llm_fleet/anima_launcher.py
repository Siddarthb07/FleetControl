"""Spawn / stop / health-check the Anima FastAPI server for Fleet Control."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"

_PROC: Optional[subprocess.Popen] = None
_DETAIL = "anima idle"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def anima_root() -> Path:
    raw = _env("ANIMA_ROOT")
    if raw:
        return Path(raw)
    return ROOT.parent / "Anima"


def anima_port() -> int:
    return int(_env("FLEET_ANIMA_PORT", "8010"))


def anima_base_url() -> str:
    return "http://127.0.0.1:{0}".format(anima_port())


def _open_log(name: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return open(LOG_DIR / name, "a", encoding="utf-8", buffering=1)


def detail() -> str:
    return _DETAIL


def alive() -> bool:
    global _PROC
    if _PROC is None:
        return False
    if _PROC.poll() is not None:
        _PROC = None
        return False
    return True


def rss_mb() -> float:
    if not alive() or _PROC is None:
        return 0.0
    try:
        import psutil

        p = psutil.Process(_PROC.pid)
        rss = p.memory_info().rss
        for c in p.children(recursive=True):
            try:
                rss += c.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return rss / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return 0.0


def health_ok(timeout: float = 2.0) -> bool:
    try:
        r = httpx.get("{0}/health".format(anima_base_url()), timeout=timeout)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def start() -> Tuple[bool, str]:
    """Start Anima if not running. Returns (ok, detail)."""
    global _PROC, _DETAIL
    if alive():
        if health_ok():
            _DETAIL = "anima ready port={0} pid={1}".format(anima_port(), _PROC.pid)
            return True, _DETAIL
        _DETAIL = "anima starting port={0} pid={1}".format(anima_port(), _PROC.pid)
        return True, _DETAIL

    root = anima_root()
    if not root.is_dir():
        _DETAIL = "ANIMA_ROOT missing: {0}".format(root)
        return False, _DETAIL

    py = _env("FLEET_ANIMA_PYTHON") or sys.executable
    port = anima_port()
    # Prefer `anima api` console script; fall back to uvicorn module path.
    cmd = [py, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", str(port)]
    # Try anima CLI if installed in that env
    anima_cli = root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "anima.exe" if os.name == "nt" else "anima"
    )
    if anima_cli.is_file():
        cmd = [str(anima_cli), "api", "--port", str(port), "--host", "127.0.0.1"]

    out = _open_log("anima.out.log")
    err = _open_log("anima.err.log")
    env = dict(os.environ)
    env.setdefault("ANIMA_API_PORT", str(port))
    try:
        _PROC = subprocess.Popen(
            cmd,
            cwd=str(root),
            env=env,
            stdout=out,
            stderr=err,
        )
    except Exception as exc:  # noqa: BLE001
        _DETAIL = "anima spawn failed: {0}".format(exc)
        _PROC = None
        return False, _DETAIL

    _DETAIL = "anima loading port={0} pid={1}".format(port, _PROC.pid)
    return True, _DETAIL


def wait_healthy(deadline_sec: float = 180.0) -> bool:
    global _DETAIL
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        if not alive():
            _DETAIL = "anima process exited early — see logs/anima.err.log"
            return False
        if health_ok():
            _DETAIL = "anima ready port={0} pid={1}".format(
                anima_port(), _PROC.pid if _PROC else "-"
            )
            return True
        time.sleep(1.0)
    _DETAIL = "anima health timeout after {0:.0f}s".format(deadline_sec)
    return False


def stop() -> None:
    global _PROC, _DETAIL
    if _PROC is None:
        _DETAIL = "anima idle"
        return
    if _PROC.poll() is None:
        _PROC.terminate()
        try:
            _PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _PROC.kill()
    _PROC = None
    _DETAIL = "anima stopped"


def ensure_desired(want: bool) -> Tuple[bool, str]:
    """Converge to want running. Returns (workload_running, detail)."""
    global _DETAIL
    if want:
        ok, det = start()
        if not ok:
            return False, det
        if health_ok():
            _DETAIL = "anima ready port={0} pid={1}".format(
                anima_port(), _PROC.pid if _PROC else "-"
            )
            return True, _DETAIL
        _DETAIL = "anima starting port={0} pid={1}".format(
            anima_port(), _PROC.pid if _PROC else "-"
        )
        # Report running once process is up; fleet FSM waits for shard_running
        # which we set True only when healthy — see agent.
        return False, _DETAIL
    stop()
    return False, _DETAIL


if __name__ == "__main__":
    ok, msg = start()
    print(ok, msg)
    if ok:
        print("healthy", wait_healthy(60))

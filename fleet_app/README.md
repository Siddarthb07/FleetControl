# Fleet Control

Dual-mode control plane:

1. **Stub memory pool** — enable/disable bytearray shards across main / i7 / Mac; live RSS.
2. **Anima on main** — enable/disable real Anima (HF hooks + AffectProbe) on the main PC only.

Modes are mutually exclusive per enable (disable → change Starting shape → enable). Both stay fully supported.

Copy-paste commands: [docs/COMMANDS.md](../docs/COMMANDS.md).

## Quick start

```powershell
cd C:\path\to\FleetControl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
$env:FLEET_TOKEN="dev-change-me"
$env:ANIMA_ROOT="C:\path\to\Anima"
python -m fleet_app.api
```

Open `http://127.0.0.1:8787/` → Save token → pick shape → Enable.

### Agent (main)

```powershell
$env:FLEET_TOKEN="dev-change-me"
$env:FLEET_NODE_ID="main"
$env:FLEET_GROSS_RAM_GB="16"
$env:ANIMA_ROOT="C:\path\to\Anima"
python -m fleet_app.agent --node-id main --control-url http://127.0.0.1:8787
```

### Stub pool on i7 / Mac

```powershell
$env:FLEET_TOKEN="..."
$env:FLEET_NODE_ID="i7"
$env:FLEET_GROSS_RAM_GB="12"
python -m fleet_app.agent --node-id i7 --control-url http://<main-tailscale-ip>:8787
```

## Starting shapes

| Shape | Runtime | Nodes | Workload |
| --- | --- | --- | --- |
| **Anima on main (probes live)** | `anima` | main | Anima API (`FLEET_ANIMA_PORT`, default 8010) |
| `small-local` | `stub` | main | 512 MB slab |
| `pool-7b` | `stub` | main+i7 | 4+4 GB slabs |
| `pool-max` | `stub` | main+i7+mac | 4+4+2 GB |
| **No preset** | `stub` | toggles | 512 MB/node (clamped) |

## Honesty

- Stub "planning usable GB" is theoretical across hosts — not shared RAM on one box, and **not** Anima's heap.
- Anima needs a working checkout at `ANIMA_ROOT` with HF + probe zoo as required by Anima itself.
- Cross-LAN Anima probes on sharded layers are **not** implemented (Anima hooks are in-process).

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/session` | Token → session ticket |
| GET | `/api/status` | Snapshot (`runtime`, `anima.url`, pool RSS) |
| POST | `/api/pool/enable` | `{ "preset": "anima-local" \| "pool-7b" \| … }` |
| POST | `/api/pool/disable` | Stop workload |
| GET | `/api/agent/desired/{id}` | Desired workload + commands |
| WS | `/ws/telemetry` | `Sec-WebSocket-Protocol: fleet, <ticket>` |

## Security

- Change `FLEET_TOKEN` before non-loopback bind.
- Default bind `127.0.0.1`.
- Logs under `logs/`.

See also the root [README.md](../README.md) and [docs/COMMANDS.md](../docs/COMMANDS.md).

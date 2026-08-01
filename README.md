# FleetControl

Dual-mode control plane for a small home fleet:

1. **Stub memory pool** — enable/disable RAM-slab shards across main / i7 / Mac; live RSS dash.
2. **Anima on main** — enable/disable real Anima (HF hooks + AffectProbe) on the main PC only.

Modes are mutually exclusive per enable (disable → change Starting shape → enable).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
$env:FLEET_TOKEN="dev-change-me"
$env:ANIMA_ROOT="C:\path\to\Anima"
python -m fleet_app.api
# other terminal:
python -m fleet_app.agent --node-id main --control-url http://127.0.0.1:8787
```

Dash: http://127.0.0.1:8787/

Copy-paste commands: [docs/COMMANDS.md](docs/COMMANDS.md)

## Honesty

- Stub "planning usable GB" is theoretical across hosts — not shared RAM on one box.
- Anima probes require a local HF model in-process; they do **not** attach to stub slabs or LAN shards.

## License

MIT © Siddarth Boggarapu

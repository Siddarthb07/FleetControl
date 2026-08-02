# Commands cheat sheet (FleetControl)

## Fleet Control (main PC)

```powershell
cd FleetControl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env

$env:FLEET_TOKEN="dev-change-me"
$env:FLEET_CONTROL_HOST="127.0.0.1"
$env:PYTHONPATH=(Get-Location).Path
$env:ANIMA_ROOT="C:\path\to\Anima"
$env:FLEET_ANIMA_PORT="8010"

python -m fleet_app.api
# or: pwsh scripts\run_control.ps1
```

Dash: http://127.0.0.1:8787/

## Agents

### Main

```powershell
$env:FLEET_TOKEN="dev-change-me"
$env:FLEET_NODE_ID="main"
$env:FLEET_GROSS_RAM_GB="16"
$env:ANIMA_ROOT="C:\path\to\Anima"
python -m fleet_app.agent --control-url http://127.0.0.1:8787
```

### i7 / Mac (stub pool)

```powershell
$env:FLEET_TOKEN="..."
$env:FLEET_NODE_ID="i7"
$env:FLEET_GROSS_RAM_GB="12"
python -m fleet_app.agent --control-url http://<main-tailscale-ip>:8787
```

## Starting shapes

| Shape | Runtime | What runs |
| --- | --- | --- |
| Anima on main | anima | Anima HF + probes on main |
| small-local / pool-7b / pool-max / custom | stub | RAM slabs |

Disable before switching shapes.

## Smoke tests

```powershell
python scripts\_smoke_fleet.py
python scripts\_smoke_agent.py
```

## Cleanup

```powershell
Get-NetTCPConnection -LocalPort 8787,8010,8790 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
```

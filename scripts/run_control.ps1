param(
  [string]$Token = $env:FLEET_TOKEN,
  [string]$HostBind = $env:FLEET_CONTROL_HOST
)
if (-not $Token) { $Token = "dev-change-me" }
if (-not $HostBind) { $HostBind = "127.0.0.1" }
$env:FLEET_TOKEN = $Token
$env:FLEET_CONTROL_HOST = $HostBind
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m fleet_app.api

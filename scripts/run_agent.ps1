param(
  [Parameter(Mandatory = $true)][string]$NodeId,
  [string]$ControlUrl = "http://127.0.0.1:8787",
  [string]$Token = $env:FLEET_TOKEN,
  [double]$GrossRamGb = 0
)
if (-not $Token) { $Token = "dev-change-me" }
$env:FLEET_TOKEN = $Token
$env:FLEET_NODE_ID = $NodeId
if ($GrossRamGb -gt 0) { $env:FLEET_GROSS_RAM_GB = "$GrossRamGb" }
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m fleet_app.agent --node-id $NodeId --control-url $ControlUrl --token $Token

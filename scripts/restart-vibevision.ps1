param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env")
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

Write-Host "Restarting VibeVision services."
& (Join-Path $Root "scripts\stop-all.ps1") -ConfigPath $ConfigPath -LocalConfigPath $LocalConfigPath
& (Join-Path $Root "scripts\start-all.ps1") -ConfigPath $ConfigPath -LocalConfigPath $LocalConfigPath
Write-Host "VibeVision restart requested."

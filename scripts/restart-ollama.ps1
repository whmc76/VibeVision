param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env")
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

Write-Host "Restarting Ollama."
& (Join-Path $Root "scripts\stop-ollama.ps1") -ConfigPath $ConfigPath -LocalConfigPath $LocalConfigPath
& (Join-Path $Root "scripts\start-ollama.ps1") -ConfigPath $ConfigPath -LocalConfigPath $LocalConfigPath
Write-Host "Ollama restart requested."

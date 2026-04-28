param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env")
)

$ErrorActionPreference = "Stop"

function Import-VibeVisionConfig {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  foreach ($RawLine in Get-Content $Path) {
    $Line = $RawLine.Trim()
    if (-not $Line -or $Line.StartsWith("#")) {
      continue
    }

    $Separator = $Line.IndexOf("=")
    if ($Separator -lt 0) {
      continue
    }

    $Name = $Line.Substring(0, $Separator).Trim()
    $Value = $Line.Substring($Separator + 1).Trim()
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
  }
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

if (-not $env:COMFYUI_ROOT) {
  throw "COMFYUI_ROOT is not configured."
}

$Port = [int]$env:COMFYUI_PORT
$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
  Write-Host "ComfyUI is already listening on port $Port."
  return
}

$ScriptName = if ($env:COMFYUI_START_SCRIPT) { $env:COMFYUI_START_SCRIPT } else { "run_nvidia_gpu.bat" }
$ScriptPath = Join-Path $env:COMFYUI_ROOT $ScriptName
if (-not (Test-Path -LiteralPath $ScriptPath)) {
  throw "ComfyUI start script not found: $ScriptPath"
}

Start-Process `
  -FilePath "cmd.exe" `
  -ArgumentList @("/c", $ScriptName) `
  -WorkingDirectory $env:COMFYUI_ROOT `
  -WindowStyle Hidden

Write-Host "Started ComfyUI backend service on port $Port."

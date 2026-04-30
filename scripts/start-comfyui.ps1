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
$HostAddress = if ($env:COMFYUI_HOST) { $env:COMFYUI_HOST } else { "127.0.0.1" }
$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
  Write-Host "ComfyUI is already listening on port $Port."
  return
}

$PythonPath = Join-Path $env:COMFYUI_ROOT "python_embeded\python.exe"
$MainPath = Join-Path $env:COMFYUI_ROOT "ComfyUI\main.py"
if (-not (Test-Path -LiteralPath $PythonPath)) {
  throw "ComfyUI embedded Python not found: $PythonPath"
}
if (-not (Test-Path -LiteralPath $MainPath)) {
  throw "ComfyUI main.py not found: $MainPath"
}

Write-Host "Starting ComfyUI backend service on port $Port. This can take a while."
Start-Process `
  -FilePath $PythonPath `
  -ArgumentList @("-s", "ComfyUI\main.py", "--windows-standalone-build", "--listen", $HostAddress, "--port", "$Port", "--disable-auto-launch") `
  -WorkingDirectory $env:COMFYUI_ROOT `
  -WindowStyle Hidden

Write-Host "ComfyUI start requested on port $Port."

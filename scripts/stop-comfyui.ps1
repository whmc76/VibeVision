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

function Test-ComfyUICommandLine {
  param([string]$CommandLine)

  if (-not $CommandLine) {
    return $false
  }
  if ($env:COMFYUI_ROOT -and $CommandLine -like "*$($env:COMFYUI_ROOT)*") {
    return $true
  }
  if ($CommandLine -like "*run_nvidia_gpu.bat*" -or $CommandLine -like "*ComfyUI\main.py*") {
    return $true
  }
  return $false
}

function Stop-ProcessTree {
  param(
    [int]$ProcessId,
    [string]$Label,
    [bool]$TrustedRoot = $false
  )

  $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
  if (-not $Process) {
    return
  }

  $Trusted = $TrustedRoot -or (Test-ComfyUICommandLine -CommandLine $Process.CommandLine)
  if (-not $Trusted) {
    throw "Refusing to stop PID $ProcessId; it does not look like configured ComfyUI."
  }

  $Children = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ParentProcessId -eq $ProcessId }

  foreach ($Child in $Children) {
    Stop-ProcessTree -ProcessId ([int]$Child.ProcessId) -Label $Label -TrustedRoot $Trusted
  }

  Write-Host "Stopping $Label, PID $ProcessId."
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

$Port = [int]$env:COMFYUI_PORT
$Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $Connections) {
  Write-Host "ComfyUI is not listening on port $Port."
  return
}

foreach ($Connection in $Connections) {
  $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction SilentlyContinue
  Stop-ProcessTree -ProcessId ([int]$Connection.OwningProcess) -Label "ComfyUI on port $Port"
  if ($Process.ParentProcessId) {
    $Parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.ParentProcessId)" -ErrorAction SilentlyContinue
    if ($Parent.CommandLine -and ($Parent.CommandLine -like "*start-comfyui.ps1*" -or $Parent.CommandLine -like "*run_nvidia_gpu*")) {
      Stop-ProcessTree -ProcessId ([int]$Parent.ProcessId) -Label "ComfyUI launcher"
    }
  }
}

Write-Host "ComfyUI stop requested."

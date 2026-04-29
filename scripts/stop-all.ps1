param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env")
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

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

function Test-VibeVisionCommandLine {
  param([string]$CommandLine)

  if (-not $CommandLine) {
    return $false
  }

  $RootText = [string]$Root
  if ($CommandLine -like "*$RootText*") {
    return $true
  }
  if ($CommandLine -like "*app.services.telegram_poller*") {
    return $true
  }
  if ($CommandLine -like "*uvicorn*app.main:app*") {
    return $true
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

  $Trusted = $TrustedRoot -or (Test-VibeVisionCommandLine -CommandLine $Process.CommandLine)
  $Children = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ParentProcessId -eq $ProcessId }

  foreach ($Child in $Children) {
    if ($Trusted -or (Test-VibeVisionCommandLine -CommandLine $Child.CommandLine)) {
      Stop-ProcessTree -ProcessId ([int]$Child.ProcessId) -Label $Label -TrustedRoot $Trusted
    }
  }

  Write-Host "Stopping $Label, PID $ProcessId."
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-Listener {
  param(
    [int]$Port,
    [string]$Label
  )

  $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($Connection in $Connections) {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction SilentlyContinue
    Stop-ProcessTree -ProcessId ([int]$Connection.OwningProcess) -Label "$Label on port $Port"
    if ($Process.ParentProcessId) {
      $Parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.ParentProcessId)" -ErrorAction SilentlyContinue
      if ($Parent.CommandLine -and ($Parent.CommandLine -like "*start-backend.ps1*" -or $Parent.CommandLine -like "*start-frontend.ps1*" -or $Parent.CommandLine -like "*run_nvidia_gpu*")) {
        Stop-ProcessTree -ProcessId ([int]$Parent.ProcessId) -Label "$Label launcher"
      }
    }
  }
}

function Stop-TelegramPoller {
  $Processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*app.services.telegram_poller*" }

  foreach ($Process in $Processes) {
    Stop-ProcessTree -ProcessId ([int]$Process.ProcessId) -Label "Telegram local poller"
    if ($Process.ParentProcessId) {
      $Parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.ParentProcessId)" -ErrorAction SilentlyContinue
      if ($Parent.CommandLine -and $Parent.CommandLine -like "*start-telegram-poller.ps1*") {
        Stop-ProcessTree -ProcessId ([int]$Parent.ProcessId) -Label "Telegram local poller launcher"
      }
    }
  }
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

Stop-TelegramPoller
Stop-Listener -Port ([int]$env:ADMIN_FRONTEND_PORT) -Label "admin frontend"
Stop-Listener -Port ([int]$env:API_PORT) -Label "VibeVision API"

Write-Host "VibeVision services stopped. ComfyUI and Ollama were left running because they are external services."

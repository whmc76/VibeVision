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

function Test-PortListening {
  param([int]$Port)
  return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-TelegramPollerRunning {
  $Process = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*app.services.telegram_poller*" } |
    Select-Object -First 1
  return [bool]$Process
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

Write-Host "Starting missing VibeVision services. Existing listeners will be left running."
Write-Host "ComfyUI and Ollama are external services; use their control GUI buttons to start or restart them."

if (-not (Test-PortListening -Port ([int]$env:API_PORT))) {
  Start-Process `
    -FilePath "powershell" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\start-backend.ps1"), "-ConfigPath", $ConfigPath, "-LocalConfigPath", $LocalConfigPath) `
    -WindowStyle Hidden
  Write-Host "Started VibeVision API on port $($env:API_PORT)."
} else {
  Write-Host "VibeVision API is already listening on port $($env:API_PORT)."
}

if (-not (Test-PortListening -Port ([int]$env:ADMIN_FRONTEND_PORT))) {
  Start-Process `
    -FilePath "powershell" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\start-frontend.ps1"), "-ConfigPath", $ConfigPath, "-LocalConfigPath", $LocalConfigPath) `
    -WindowStyle Hidden
  Write-Host "Started admin frontend on port $($env:ADMIN_FRONTEND_PORT)."
} else {
  Write-Host "Admin frontend is already listening on port $($env:ADMIN_FRONTEND_PORT)."
}

if ($env:TELEGRAM_BOT_TOKEN) {
  if (-not (Test-TelegramPollerRunning)) {
    Start-Process `
      -FilePath "powershell" `
      -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\start-telegram-poller.ps1"), "-ConfigPath", $ConfigPath, "-LocalConfigPath", $LocalConfigPath) `
      -WindowStyle Hidden
    Write-Host "Started Telegram local poller."
  } else {
    Write-Host "Telegram local poller is already running."
  }
} else {
  Write-Host "TELEGRAM_BOT_TOKEN is not configured; skipping Telegram poller."
}

Write-Host "VibeVision services requested."

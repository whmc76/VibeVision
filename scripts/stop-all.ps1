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

function Test-ServiceCommandLine {
  param([string]$CommandLine)

  if (-not $CommandLine) {
    return $false
  }

  return (
    $CommandLine -like "*app.services.telegram_poller*" -or
    $CommandLine -like "*app.services.task_queue_worker*" -or
    $CommandLine -like "*start-telegram-poller.ps1*" -or
    $CommandLine -like "*start-task-worker.ps1*" -or
    $CommandLine -like "*start-backend.ps1*" -or
    $CommandLine -like "*start-frontend.ps1*" -or
    $CommandLine -like "*uvicorn*app.main:app*" -or
    $CommandLine -like "*npm*run*dev*" -or
    $CommandLine -like "*node*vite*"
  )
}

function Get-ProcessSnapshot {
  $Snapshot = @{}
  foreach ($Process in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    $Snapshot[[int]$Process.ProcessId] = $Process
  }
  return $Snapshot
}

function Stop-ProcessId {
  param(
    [int]$ProcessId,
    [string]$Label
  )

  if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
    return
  }

  Write-Host "Stopping $Label, PID $ProcessId."
  & cmd.exe /d /c "taskkill.exe /PID $ProcessId /T /F >NUL 2>NUL"
  if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }
}

function Stop-ParentServices {
  param(
    [int]$ProcessId,
    [hashtable]$Snapshot,
    [string]$Label
  )

  $Seen = @{}
  $CurrentId = $ProcessId
  while ($Snapshot.ContainsKey($CurrentId)) {
    $Process = $Snapshot[$CurrentId]
    $ParentId = [int]$Process.ParentProcessId
    if (-not $Snapshot.ContainsKey($ParentId) -or $Seen.ContainsKey($ParentId)) {
      return
    }

    $Parent = $Snapshot[$ParentId]
    if (-not (Test-ServiceCommandLine -CommandLine $Parent.CommandLine)) {
      return
    }

    $Seen[$ParentId] = $true
    Stop-ProcessId -ProcessId $ParentId -Label $Label
    $CurrentId = $ParentId
  }
}

function Stop-Listener {
  param(
    [int]$Port,
    [string]$Label
  )

  $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  $Snapshot = Get-ProcessSnapshot
  foreach ($Connection in $Connections) {
    $ProcessId = [int]$Connection.OwningProcess
    if ($Snapshot.ContainsKey($ProcessId) -and -not (Test-ServiceCommandLine -CommandLine $Snapshot[$ProcessId].CommandLine)) {
      Write-Host "Skipping $Label on port $Port, PID $ProcessId does not look like a VibeVision service."
      continue
    }
    Stop-ProcessId -ProcessId $ProcessId -Label "$Label on port $Port"
    Stop-ParentServices -ProcessId $ProcessId -Snapshot $Snapshot -Label "$Label launcher"
  }
}

function Stop-MatchingProcesses {
  param(
    [string]$Pattern,
    [string]$Label
  )

  $Snapshot = Get-ProcessSnapshot
  $Processes = @(
    $Snapshot.Values |
      Where-Object { $_.CommandLine -and $_.CommandLine -like $Pattern } |
      Sort-Object ProcessId -Descending
  )
  foreach ($Process in $Processes) {
    Stop-ProcessId -ProcessId ([int]$Process.ProcessId) -Label $Label
    Stop-ParentServices -ProcessId ([int]$Process.ProcessId) -Snapshot $Snapshot -Label "$Label launcher"
  }
}

function Stop-TelegramPoller {
  Stop-MatchingProcesses -Pattern "*app.services.telegram_poller*" -Label "Telegram local poller"
}

function Stop-TaskQueueWorker {
  Stop-MatchingProcesses -Pattern "*app.services.task_queue_worker*" -Label "task queue worker"
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

Stop-TelegramPoller
Stop-TaskQueueWorker
Stop-Listener -Port ([int]$env:ADMIN_FRONTEND_PORT) -Label "admin frontend"
Stop-Listener -Port ([int]$env:API_PORT) -Label "VibeVision API"

Write-Host "VibeVision services stopped. ComfyUI and Ollama were left running because they are external services."

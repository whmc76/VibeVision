param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env"),
  [int]$DependencyWaitSeconds = 180,
  [int]$ServiceWaitSeconds = 60
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
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

function Get-EnvText {
  param(
    [string]$Name,
    [string]$DefaultValue
  )

  $Value = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ($Value) {
    return $Value
  }
  return $DefaultValue
}

function Get-EnvInt {
  param(
    [string]$Name,
    [int]$DefaultValue
  )

  $Value = [Environment]::GetEnvironmentVariable($Name, "Process")
  $Parsed = 0
  if ([int]::TryParse($Value, [ref]$Parsed)) {
    return $Parsed
  }
  return $DefaultValue
}

function Get-LlmProvider {
  return (Get-EnvText -Name "LLM_PROVIDER" -DefaultValue "ollama").Trim().ToLowerInvariant()
}

function Get-LlmLogicProvider {
  return (Get-EnvText -Name "LLM_LOGIC_PROVIDER" -DefaultValue (Get-LlmProvider)).Trim().ToLowerInvariant()
}

function Get-LlmPromptProvider {
  return (Get-EnvText -Name "LLM_PROMPT_PROVIDER" -DefaultValue (Get-LlmProvider)).Trim().ToLowerInvariant()
}

function Get-LlmVisionProvider {
  return (Get-EnvText -Name "LLM_VISION_PROVIDER" -DefaultValue "minimax_mcp").Trim().ToLowerInvariant()
}

function Test-LlmUsesOllama {
  return (Get-LlmLogicProvider) -eq "ollama" -or (Get-LlmPromptProvider) -eq "ollama" -or (Get-LlmVisionProvider) -eq "ollama"
}

function Test-PortListening {
  param([int]$Port)
  return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-HttpOk {
  param([string]$Url)

  try {
    $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
    return $Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500
  } catch {
    return $false
  }
}

function Wait-HttpOk {
  param(
    [string]$Name,
    [string]$Url,
    [int]$TimeoutSeconds
  )

  Write-Host "Waiting for $Name health at $Url (timeout ${TimeoutSeconds}s)."
  $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $LastProgressAt = Get-Date
  do {
    if (Test-HttpOk -Url $Url) {
      Write-Host "OK  $Name health check passed." -ForegroundColor Green
      return
    }

    Start-Sleep -Seconds 2
    $Now = Get-Date
    if (($Now - $LastProgressAt).TotalSeconds -ge 10) {
      $RemainingSeconds = [Math]::Max(0, [int][Math]::Ceiling(($Deadline - $Now).TotalSeconds))
      Write-Host "Still waiting for $Name (${RemainingSeconds}s remaining)."
      $LastProgressAt = $Now
    }
  } while ((Get-Date) -lt $Deadline)

  throw "$Name did not pass health check within ${TimeoutSeconds}s: $Url"
}

function Test-TelegramPollerRunning {
  $Process = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*app.services.telegram_poller*" } |
    Select-Object -First 1
  return [bool]$Process
}

function Test-TaskQueueWorkerRunning {
  $Process = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*app.services.task_queue_worker*" } |
    Select-Object -First 1
  return [bool]$Process
}

function Show-ServiceActionPlan {
  param(
    [int]$ApiPort,
    [bool]$ApiListening,
    [int]$FrontendPort,
    [bool]$FrontendListening,
    [int]$ComfyPort,
    [bool]$ComfyListening,
    [int]$OllamaPort,
    [bool]$UsesOllama,
    [bool]$OllamaListening,
    [bool]$TelegramConfigured,
    [bool]$TelegramRunning,
    [bool]$TaskWorkerRunning
  )

  Write-Host "Service action plan:"
  if ($ComfyListening) {
    Write-Host "  - ComfyUI: already listening on port $ComfyPort; verify /system_stats before accepting tasks."
  } else {
    Write-Host "  - ComfyUI: start it first, then wait for /system_stats before accepting tasks."
  }

  if ($UsesOllama) {
    if ($OllamaListening) {
      Write-Host "  - Ollama: already listening on port $OllamaPort; verify /api/tags."
    } else {
      Write-Host "  - Ollama: start it and wait for /api/tags."
    }
  } else {
    Write-Host "  - Ollama: not required by current LLM routing."
  }

  if ($ApiListening) {
    Write-Host "  - API: already listening on port $ApiPort; verify /api/health."
  } else {
    Write-Host "  - API: start backend service on port $ApiPort, then verify /api/health."
  }

  if ($FrontendListening) {
    Write-Host "  - Admin frontend: already listening on port $FrontendPort; verify HTTP response."
  } else {
    Write-Host "  - Admin frontend: start frontend service on port $FrontendPort, then verify HTTP response."
  }

  if ($TaskWorkerRunning) {
    Write-Host "  - Task queue worker: already running; no duplicate will be started."
  } else {
    Write-Host "  - Task queue worker: start only after ComfyUI/API are healthy."
  }

  if (-not $TelegramConfigured) {
    Write-Host "  - Telegram poller: skip because TELEGRAM_BOT_TOKEN is not configured."
  } elseif ($TelegramRunning) {
    Write-Host "  - Telegram poller: already running; no duplicate will be started."
  } else {
    Write-Host "  - Telegram poller: start last, after all dependencies are healthy."
  }
}

function Start-HiddenScript {
  param([string]$ScriptName)

  Start-Process `
    -FilePath "powershell" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\$ScriptName"), "-ConfigPath", $ConfigPath, "-LocalConfigPath", $LocalConfigPath) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden
}

function Ensure-ComfyUIReady {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )

  $Url = "http://$(Get-EnvText -Name "COMFYUI_HOST" -DefaultValue "127.0.0.1"):$Port/system_stats"
  if (-not (Test-PortListening -Port $Port)) {
    Write-Host "Starting ComfyUI backend service on port $Port."
    Start-HiddenScript -ScriptName "start-comfyui.ps1"
  } else {
    Write-Host "ComfyUI is already listening on port $Port."
  }
  Wait-HttpOk -Name "ComfyUI" -Url $Url -TimeoutSeconds $TimeoutSeconds
}

function Ensure-OllamaReady {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )

  if (-not (Test-LlmUsesOllama)) {
    return
  }

  $Url = "http://$(Get-EnvText -Name "OLLAMA_HOST" -DefaultValue "127.0.0.1"):$Port/api/tags"
  if (-not (Test-PortListening -Port $Port)) {
    Write-Host "Starting Ollama on port $Port."
    Start-HiddenScript -ScriptName "start-ollama.ps1"
  } else {
    Write-Host "Ollama is already listening on port $Port."
  }
  Wait-HttpOk -Name "Ollama" -Url $Url -TimeoutSeconds $TimeoutSeconds
}

function Ensure-ApiReady {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )

  $Url = "http://$(Get-EnvText -Name "API_HOST" -DefaultValue "127.0.0.1"):$Port/api/health"
  if (-not (Test-PortListening -Port $Port)) {
    Start-HiddenScript -ScriptName "start-backend.ps1"
    Write-Host "Started VibeVision API on port $Port."
  } else {
    Write-Host "VibeVision API is already listening on port $Port."
  }
  Wait-HttpOk -Name "VibeVision API" -Url $Url -TimeoutSeconds $TimeoutSeconds
}

function Ensure-FrontendReady {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )

  $Url = "http://$(Get-EnvText -Name "ADMIN_FRONTEND_HOST" -DefaultValue "127.0.0.1"):$Port"
  if (-not (Test-PortListening -Port $Port)) {
    Start-HiddenScript -ScriptName "start-frontend.ps1"
    Write-Host "Started admin frontend on port $Port."
  } else {
    Write-Host "Admin frontend is already listening on port $Port."
  }
  Wait-HttpOk -Name "Admin frontend" -Url $Url -TimeoutSeconds $TimeoutSeconds
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

$ApiPort = Get-EnvInt -Name "API_PORT" -DefaultValue 18751
$FrontendPort = Get-EnvInt -Name "ADMIN_FRONTEND_PORT" -DefaultValue 18742
$ComfyPort = Get-EnvInt -Name "COMFYUI_PORT" -DefaultValue 8401
$OllamaPort = Get-EnvInt -Name "OLLAMA_PORT" -DefaultValue 11434
$UsesOllama = Test-LlmUsesOllama

$ApiListening = Test-PortListening -Port $ApiPort
$FrontendListening = Test-PortListening -Port $FrontendPort
$ComfyListening = Test-PortListening -Port $ComfyPort
$OllamaListening = Test-PortListening -Port $OllamaPort
$TelegramConfigured = [bool]$env:TELEGRAM_BOT_TOKEN
$TelegramRunning = if ($TelegramConfigured) { Test-TelegramPollerRunning } else { $false }
$TaskWorkerRunning = Test-TaskQueueWorkerRunning

Write-Host "Starting VibeVision in stable order. Existing listeners will be reused."
Show-ServiceActionPlan `
  -ApiPort $ApiPort `
  -ApiListening $ApiListening `
  -FrontendPort $FrontendPort `
  -FrontendListening $FrontendListening `
  -ComfyPort $ComfyPort `
  -ComfyListening $ComfyListening `
  -OllamaPort $OllamaPort `
  -UsesOllama $UsesOllama `
  -OllamaListening $OllamaListening `
  -TelegramConfigured $TelegramConfigured `
  -TelegramRunning $TelegramRunning `
  -TaskWorkerRunning $TaskWorkerRunning

Ensure-ComfyUIReady -Port $ComfyPort -TimeoutSeconds $DependencyWaitSeconds
Ensure-OllamaReady -Port $OllamaPort -TimeoutSeconds $DependencyWaitSeconds
Ensure-ApiReady -Port $ApiPort -TimeoutSeconds $ServiceWaitSeconds
Ensure-FrontendReady -Port $FrontendPort -TimeoutSeconds $ServiceWaitSeconds

if (-not (Test-TaskQueueWorkerRunning)) {
  Start-HiddenScript -ScriptName "start-task-worker.ps1"
  Write-Host "Started task queue worker."
} else {
  Write-Host "Task queue worker is already running."
}

if ($TelegramConfigured) {
  if (-not (Test-TelegramPollerRunning)) {
    Start-HiddenScript -ScriptName "start-telegram-poller.ps1"
    Write-Host "Started Telegram local poller."
  } else {
    Write-Host "Telegram local poller is already running."
  }
} else {
  Write-Host "TELEGRAM_BOT_TOKEN is not configured; skipping Telegram poller."
}

Write-Host "VibeVision stable startup requested."

param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env"),
  [switch]$Repair,
  [switch]$NoGui,
  [switch]$SkipInstall,
  [int]$WaitSeconds = 45
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"

function Write-Step {
  param([string]$Message)
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
  param([string]$Message)
  Write-Host "OK  $Message" -ForegroundColor Green
}

function Write-WarnLine {
  param([string]$Message)
  Write-Host "WARN $Message" -ForegroundColor Yellow
}

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

function Test-LlmUsesMiniMax {
  return (Get-LlmLogicProvider) -eq "minimax" -or (Get-LlmPromptProvider) -eq "minimax" -or (Get-LlmVisionProvider) -eq "minimax_mcp"
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

function Get-MiniMaxModelDisplay {
  $BaseModel = Get-EnvText -Name "MINIMAX_MODEL" -DefaultValue ""
  $LogicModel = Get-EnvText -Name "MINIMAX_LOGIC_MODEL" -DefaultValue ""
  $PromptModel = Get-EnvText -Name "MINIMAX_PROMPT_MODEL" -DefaultValue ""

  if (-not $LogicModel) {
    if ($BaseModel) {
      $LogicModel = $BaseModel
    } elseif ($PromptModel) {
      $LogicModel = $PromptModel
    }
  }

  if (-not $PromptModel) {
    if ($BaseModel) {
      $PromptModel = $BaseModel
    } elseif ($LogicModel) {
      $PromptModel = $LogicModel
    }
  }

  if ($LogicModel -and $LogicModel -eq $PromptModel) {
    return "$LogicModel (logic + prompt)"
  }

  $Parts = @()
  if ($LogicModel) {
    $Parts += "logic=$LogicModel"
  }
  if ($PromptModel) {
    $Parts += "prompt=$PromptModel"
  }

  if ($Parts.Count -eq 0) {
    return "Models are not configured"
  }
  return ($Parts -join ", ")
}

function Get-OllamaModelRoleMap {
  $LegacyModel = Get-EnvText -Name "OLLAMA_MODEL" -DefaultValue ""
  $LogicModel = Get-EnvText -Name "OLLAMA_LOGIC_MODEL" -DefaultValue ""
  $PromptModel = Get-EnvText -Name "OLLAMA_PROMPT_MODEL" -DefaultValue ""

  if (-not $LogicModel) {
    if ($LegacyModel) {
      $LogicModel = $LegacyModel
    } elseif ($PromptModel) {
      $LogicModel = $PromptModel
    }
  }

  if (-not $PromptModel) {
    if ($LegacyModel) {
      $PromptModel = $LegacyModel
    } elseif ($LogicModel) {
      $PromptModel = $LogicModel
    }
  }

  return [ordered]@{
    logic = $LogicModel
    prompt = $PromptModel
  }
}

function Test-PortListening {
  param([int]$Port)
  return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Port {
  param(
    [string]$Name,
    [int]$Port,
    [int]$TimeoutSeconds
  )

  Write-Host "Waiting for $Name to listen on port $Port (timeout ${TimeoutSeconds}s)."
  $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $LastProgressAt = Get-Date
  do {
    if (Test-PortListening -Port $Port) {
      Write-Ok "$Name is listening on port $Port."
      return $true
    }
    Start-Sleep -Seconds 2

    $Now = Get-Date
    if (($Now - $LastProgressAt).TotalSeconds -ge 10) {
      $RemainingSeconds = [Math]::Max(0, [int][Math]::Ceiling(($Deadline - $Now).TotalSeconds))
      Write-Host "Still waiting for $Name on port $Port (${RemainingSeconds}s remaining)."
      $LastProgressAt = $Now
    }
  } while ((Get-Date) -lt $Deadline)

  Write-WarnLine "$Name is not listening on port $Port after ${TimeoutSeconds}s."
  return $false
}

function Ensure-LocalConfig {
  if (Test-Path -LiteralPath $LocalConfigPath) {
    return
  }

  $Template = @"
# Local private overrides for VibeVision.
# This file is ignored by git.
LLM_LOGIC_PROVIDER=minimax
LLM_PROMPT_PROVIDER=ollama
LLM_VISION_PROVIDER=minimax_mcp
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_POLLER_MAX_WORKERS=4
MINIMAX_API_KEY=
"@
  Set-Content -LiteralPath $LocalConfigPath -Value $Template -Encoding UTF8
  Write-Ok "Created local config: $LocalConfigPath"
}

function Ensure-Uv {
  $Uv = Get-Command "uv" -ErrorAction SilentlyContinue
  if ($Uv) {
    Write-Ok "uv found: $($Uv.Source)"
    return
  }

  $Python = Get-Command "python" -ErrorAction SilentlyContinue
  if (-not $Python) {
    throw "Python is required to bootstrap uv, but python was not found in PATH."
  }

  Write-WarnLine "uv not found. Installing uv with python -m pip --user."
  python -m pip install --user uv

  $Uv = Get-Command "uv" -ErrorAction SilentlyContinue
  if (-not $Uv) {
    $UserScripts = python -c "import site, pathlib; print(pathlib.Path(site.USER_BASE) / 'Scripts')"
    if ($UserScripts -and (Test-Path -LiteralPath $UserScripts)) {
      $env:PATH = "$UserScripts;$env:PATH"
    }
  }

  $Uv = Get-Command "uv" -ErrorAction SilentlyContinue
  if (-not $Uv) {
    throw "uv install finished, but uv is still not available in PATH."
  }
  Write-Ok "uv installed: $($Uv.Source)"
}

function Sync-Backend {
  Write-Step "Checking backend Python environment"
  Ensure-Uv
  Push-Location $BackendDir
  try {
    uv sync --extra dev --link-mode=copy
    uv run python -c "from app.main import app; print(app.title)" | Out-Null
    Write-Ok "Backend environment is ready."
  } finally {
    Pop-Location
  }
}

function Sync-Frontend {
  Write-Step "Checking frontend Node environment"
  $Npm = Get-Command "npm" -ErrorAction SilentlyContinue
  if (-not $Npm) {
    throw "npm was not found. Install Node.js before starting the frontend."
  }
  Write-Ok "npm found: $($Npm.Source)"

  $NodeModules = Join-Path $FrontendDir "node_modules"
  $PackageLock = Join-Path $FrontendDir "package-lock.json"
  $PackageJson = Join-Path $FrontendDir "package.json"

  $NeedsInstall = $Repair -or -not (Test-Path -LiteralPath $NodeModules)
  if (-not $NeedsInstall -and (Test-Path -LiteralPath $PackageLock)) {
    $NodeModulesTime = (Get-Item -LiteralPath $NodeModules).LastWriteTime
    $PackageLockTime = (Get-Item -LiteralPath $PackageLock).LastWriteTime
    $PackageJsonTime = (Get-Item -LiteralPath $PackageJson).LastWriteTime
    $NeedsInstall = $PackageLockTime -gt $NodeModulesTime -or $PackageJsonTime -gt $NodeModulesTime
  }

  if ($NeedsInstall) {
    Push-Location $FrontendDir
    try {
      npm install
      Write-Ok "Frontend dependencies are ready."
    } finally {
      Pop-Location
    }
  } else {
    Write-Ok "Frontend dependencies already installed."
  }
}

function Test-ComfyUIConfig {
  Write-Step "Checking ComfyUI configuration"
  $ComfyRoot = Get-EnvText -Name "COMFYUI_ROOT" -DefaultValue ""
  $StartScript = Get-EnvText -Name "COMFYUI_START_SCRIPT" -DefaultValue "run_nvidia_gpu.bat"

  if (-not $ComfyRoot) {
    Write-WarnLine "COMFYUI_ROOT is not configured."
    return
  }
  if (-not (Test-Path -LiteralPath $ComfyRoot)) {
    Write-WarnLine "COMFYUI_ROOT does not exist: $ComfyRoot"
    return
  }
  $ScriptPath = Join-Path $ComfyRoot $StartScript
  if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-WarnLine "ComfyUI start script not found: $ScriptPath"
    return
  }
  Write-Ok "ComfyUI root is ready: $ComfyRoot"
}

function Test-OllamaConfig {
  Write-Step "Checking Ollama"
  $Ollama = Get-Command "ollama" -ErrorAction SilentlyContinue
  if (-not $Ollama) {
    Write-WarnLine "ollama command not found. VibeVision can start, but prompt understanding may fall back or fail."
    return
  }
  Write-Ok "Ollama found: $($Ollama.Source)"

  $RoleMap = Get-OllamaModelRoleMap
  $UniqueModels = @{}
  foreach ($Role in $RoleMap.Keys) {
    $Model = [string]$RoleMap[$Role]
    if (-not $Model) {
      continue
    }
    if (-not $UniqueModels.ContainsKey($Model)) {
      $UniqueModels[$Model] = @()
    }
    $UniqueModels[$Model] = @($UniqueModels[$Model]) + $Role
  }

  if ($UniqueModels.Count -eq 0) {
    return
  }

  try {
    $Models = & ollama list 2>$null
    foreach ($Entry in $UniqueModels.GetEnumerator()) {
      $Model = [string]$Entry.Key
      $Roles = @($Entry.Value) -join "+"
      if ($Models -and ($Models -match [regex]::Escape($Model))) {
        Write-Ok "Ollama $Roles model is available: $Model"
      } else {
        Write-WarnLine "Ollama $Roles model not found locally: $Model"
        Write-WarnLine "Run this if needed: ollama pull $Model"
      }
    }
  } catch {
    Write-WarnLine "Could not inspect Ollama models: $($_.Exception.Message)"
  }
}

function Test-MiniMaxConfig {
  Write-Step "Checking MiniMax"
  $ApiKey = Get-EnvText -Name "MINIMAX_API_KEY" -DefaultValue ""
  $BaseUrl = Get-EnvText -Name "MINIMAX_BASE_URL" -DefaultValue "https://api.minimaxi.com/v1"
  if (-not $ApiKey) {
    Write-WarnLine "MINIMAX_API_KEY is not configured. LLM routing will fall back to heuristic routing."
    return
  }
  Write-Ok "MiniMax API key is configured."
  Write-Ok "MiniMax endpoint: $BaseUrl"
  Write-Ok "MiniMax models: $(Get-MiniMaxModelDisplay)"
}

function Test-LlmConfig {
  if (Test-LlmUsesMiniMax) {
    Test-MiniMaxConfig
  }
  if (Test-LlmUsesOllama) {
    Test-OllamaConfig
  }
}

function Start-Services {
  Write-Step "Starting missing VibeVision services"
  & (Join-Path $Root "scripts\start-all.ps1") -ConfigPath $ConfigPath -LocalConfigPath $LocalConfigPath
}

function Show-ServiceStatus {
  Write-Step "Checking service status"
  $ApiPort = Get-EnvInt -Name "API_PORT" -DefaultValue 18751
  $FrontendPort = Get-EnvInt -Name "ADMIN_FRONTEND_PORT" -DefaultValue 18742
  $ComfyPort = Get-EnvInt -Name "COMFYUI_PORT" -DefaultValue 8401
  $OllamaPort = Get-EnvInt -Name "OLLAMA_PORT" -DefaultValue 11434

  [void](Wait-Port -Name "VibeVision API" -Port $ApiPort -TimeoutSeconds $WaitSeconds)
  [void](Wait-Port -Name "Admin frontend" -Port $FrontendPort -TimeoutSeconds $WaitSeconds)

  if (Test-PortListening -Port $ComfyPort) {
    Write-Ok "ComfyUI is listening on port $ComfyPort."
  } else {
    Write-WarnLine "ComfyUI is not listening on port $ComfyPort. Use the control GUI button to start or restart it."
  }

  if (Test-LlmUsesOllama) {
    if (Test-PortListening -Port $OllamaPort) {
      Write-Ok "Ollama is listening on port $OllamaPort."
    } else {
      Write-WarnLine "Ollama is not listening on port $OllamaPort. Use the control GUI button to start or restart it."
    }
  }

  $ApiUrl = "http://$(Get-EnvText -Name "API_HOST" -DefaultValue "127.0.0.1"):$ApiPort"
  $FrontendUrl = "http://$(Get-EnvText -Name "ADMIN_FRONTEND_HOST" -DefaultValue "127.0.0.1"):$FrontendPort"
  Write-Host ""
  Write-Host "Admin frontend: $FrontendUrl" -ForegroundColor Green
  Write-Host "API health:     $ApiUrl/api/health" -ForegroundColor Green
}

function Open-Monitor {
  if ($NoGui) {
    return
  }
  Write-Step "Opening VibeVision control GUI"
  Start-Process `
    -FilePath "powershell" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\vibevision-control.ps1"), "-NoAutoStart") `
    -WorkingDirectory $Root
  Write-Ok "Control GUI launched."
}

Write-Host "VibeVision one-click bootstrap" -ForegroundColor Green
Write-Host "Root: $Root"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Config file not found: $ConfigPath"
}

Ensure-LocalConfig
Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath
New-Item -ItemType Directory -Path (Join-Path $Root "data") -Force | Out-Null

if (-not $SkipInstall) {
  Sync-Backend
  Sync-Frontend
} else {
  Write-WarnLine "Skipping dependency installation because -SkipInstall was set."
}

Test-ComfyUIConfig
Test-LlmConfig
Start-Services
Show-ServiceStatus
Open-Monitor

Write-Host ""
Write-Host "VibeVision bootstrap complete." -ForegroundColor Green

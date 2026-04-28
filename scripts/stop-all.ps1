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

function Stop-Listener {
  param(
    [int]$Port,
    [string]$Label
  )

  $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($Connection in $Connections) {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction SilentlyContinue
    Write-Host "Stopping $Label on port $Port, PID $($Connection.OwningProcess)."
    Stop-Process -Id $Connection.OwningProcess -Force -ErrorAction SilentlyContinue
    if ($Process.ParentProcessId) {
      $Parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.ParentProcessId)" -ErrorAction SilentlyContinue
      if ($Parent.CommandLine -and ($Parent.CommandLine -like "*start-backend.ps1*" -or $Parent.CommandLine -like "*start-frontend.ps1*" -or $Parent.CommandLine -like "*run_nvidia_gpu*")) {
        Stop-Process -Id $Parent.ProcessId -Force -ErrorAction SilentlyContinue
      }
    }
  }
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

Stop-Listener -Port ([int]$env:ADMIN_FRONTEND_PORT) -Label "admin frontend"
Stop-Listener -Port ([int]$env:API_PORT) -Label "VibeVision API"
Stop-Listener -Port ([int]$env:COMFYUI_PORT) -Label "ComfyUI"

Write-Host "VibeVision services stopped. Ollama is monitored but left running because it may be shared by other local tools."

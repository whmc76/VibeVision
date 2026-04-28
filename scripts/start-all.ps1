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

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

if (-not (Test-PortListening -Port ([int]$env:OLLAMA_PORT))) {
  $OllamaCommand = Get-Command "ollama" -ErrorAction SilentlyContinue
  if ($OllamaCommand) {
    Start-Process `
      -FilePath $OllamaCommand.Source `
      -ArgumentList @("serve") `
      -WindowStyle Hidden
    Write-Host "Started Ollama service on port $($env:OLLAMA_PORT)."
  } else {
    Write-Host "Ollama command not found; skipping Ollama start."
  }
} else {
  Write-Host "Ollama is already listening on port $($env:OLLAMA_PORT)."
}

& (Join-Path $Root "scripts\start-comfyui.ps1") -ConfigPath $ConfigPath -LocalConfigPath $LocalConfigPath

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

Write-Host "VibeVision services requested."

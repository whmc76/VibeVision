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

function Test-OllamaProcess {
  param(
    [string]$Name,
    [string]$CommandLine
  )

  if ($Name -and $Name -like "*ollama*") {
    return $true
  }
  if ($CommandLine -and $CommandLine -like "*ollama*") {
    return $true
  }
  return $false
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

$Port = [int]$env:OLLAMA_PORT
$Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $Connections) {
  Write-Host "Ollama is not listening on port $Port."
  return
}

foreach ($Connection in $Connections) {
  $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction SilentlyContinue
  if (-not $Process) {
    continue
  }
  if (-not (Test-OllamaProcess -Name $Process.Name -CommandLine $Process.CommandLine)) {
    throw "Refusing to stop PID $($Process.ProcessId); it does not look like Ollama."
  }
  Write-Host "Stopping Ollama on port $Port, PID $($Process.ProcessId)."
  Stop-Process -Id ([int]$Process.ProcessId) -Force -ErrorAction SilentlyContinue
}

Write-Host "Ollama stop requested."

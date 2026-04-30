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

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

$env:PYTHONPATH = Join-Path $Root "backend"
Set-Location (Join-Path $Root "backend")

$VenvPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
  & $VenvPython -m app.services.task_queue_worker
} else {
  $UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
  if ($UvCommand) {
    uv run python -m app.services.task_queue_worker
  } else {
    python -m app.services.task_queue_worker
  }
}

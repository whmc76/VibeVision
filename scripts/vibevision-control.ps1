param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env"),
  [switch]$NoAutoStart,
  [switch]$StatusSnapshot
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogPath = Join-Path $env:TEMP "vibevision-control.log"
$ScriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }

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

function Get-OllamaModelDisplay {
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

function Get-TelegramWebhookStatus {
  $Poller = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*app.services.telegram_poller*" } |
    Select-Object -First 1

  if (-not $env:TELEGRAM_BOT_TOKEN) {
    return [pscustomobject]@{
      Status = "unconfigured"
      Url = $null
      Detail = "Set TELEGRAM_BOT_TOKEN in local config"
      Pid = $null
    }
  }

  try {
    $Uri = "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/getWebhookInfo"
    $Response = Invoke-RestMethod -Uri $Uri -Method Post
    if (-not $Response.ok) {
      throw "Telegram API returned ok=false."
    }
  } catch {
    return [pscustomobject]@{
      Status = "offline"
      Url = $null
      Detail = "Telegram webhook check failed: $($_.Exception.Message)"
      Pid = if ($Poller) { [int]$Poller.ProcessId } else { $null }
    }
  }

  $Info = $Response.result
  $WebhookUrl = if ($Info.url) { [string]$Info.url } else { $null }
  $PendingCount = if ($null -ne $Info.pending_update_count) { [int]$Info.pending_update_count } else { 0 }
  $LastError = if ($Info.last_error_message) { [string]$Info.last_error_message } else { $null }

  if (-not $WebhookUrl) {
    if ($Poller) {
      return [pscustomobject]@{
        Status = "configured"
        Url = "local getUpdates polling"
        Detail = "Local Telegram poller is running."
        Pid = [int]$Poller.ProcessId
      }
    }

    $Detail = "Bot token is configured, but no webhook URL is registered."
    if ($PendingCount -gt 0) {
      $Detail += " Pending updates: $PendingCount."
    }
    return [pscustomobject]@{
      Status = "unconfigured"
      Url = $null
      Detail = $Detail
      Pid = $null
    }
  }

  $Detail = "Webhook registered."
  if ($PendingCount -gt 0) {
    $Detail += " Pending updates: $PendingCount."
  }
  if ($LastError) {
    $Detail += " Last error: $LastError"
  }

  return [pscustomobject]@{
    Status = if ($LastError) { "offline" } else { "configured" }
    Url = $WebhookUrl
    Detail = $Detail
    Pid = if ($Poller) { [int]$Poller.ProcessId } else { $null }
  }
}

function Get-ListenerPid {
  param([int]$Port)

  if ($Port -le 0) {
    return $null
  }

  $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if (-not $Connection) {
    return $null
  }
  return $Connection.OwningProcess
}

function Get-ListenerPidMap {
  param([int[]]$Ports)

  $Map = @{}
  $ValidPorts = @($Ports | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
  if ($ValidPorts.Count -eq 0) {
    return $Map
  }

  $PortSet = @{}
  foreach ($Port in $ValidPorts) {
    $PortSet[[int]$Port] = $true
  }

  $Lines = & netstat.exe -ano -p TCP 2>$null
  foreach ($Line in $Lines) {
    if ($Line -notmatch "LISTENING") {
      continue
    }

    if ($Line -match "^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$") {
      $Port = [int]$Matches[1]
      if ($PortSet.ContainsKey($Port) -and -not $Map.ContainsKey($Port)) {
        $Map[$Port] = [int]$Matches[2]
      }
    }
  }

  return $Map
}

function Get-ProcessLabelMap {
  param([object[]]$Pids)

  $Map = @{}
  $ValidPids = @(
    $Pids |
      Where-Object { $_ } |
      ForEach-Object { [int]$_ } |
      Sort-Object -Unique
  )
  if ($ValidPids.Count -eq 0) {
    return $Map
  }

  foreach ($Process in (Get-Process -Id $ValidPids -ErrorAction SilentlyContinue)) {
    $Map[[int]$Process.Id] = "$($Process.ProcessName) is listening"
  }

  return $Map
}

function Get-ProcessLabelFromMap {
  param(
    [object]$ProcessIdValue,
    [hashtable]$ProcessLabelMap
  )

  if (-not $ProcessIdValue) {
    return "Not listening"
  }

  $ProcessId = [int]$ProcessIdValue
  if ($ProcessLabelMap.ContainsKey($ProcessId)) {
    return $ProcessLabelMap[$ProcessId]
  }

  return "Listening"
}

function Get-ProcessLabel {
  param([object]$ProcessIdValue)

  if (-not $ProcessIdValue) {
    return "Not listening"
  }

  $Process = Get-Process -Id $ProcessIdValue -ErrorAction SilentlyContinue
  if (-not $Process) {
    return "Listening"
  }
  return "$($Process.ProcessName) is listening"
}

function New-ServiceRow {
  param(
    [string]$Name,
    [string]$Status,
    [object]$Port,
    [object]$ProcessIdValue,
    [string]$Url,
    [string]$Detail
  )

  $PidText = "-"
  if ($ProcessIdValue) {
    $PidText = [string]$ProcessIdValue
  }

  [pscustomobject]@{
    Service = $Name
    Status = $Status
    Port = $Port
    PID = $PidText
    URL = $Url
    Detail = $Detail
  }
}

function Get-ServiceRows {
  $ApiHost = Get-EnvText -Name "API_HOST" -DefaultValue "localhost"
  $FrontendHost = Get-EnvText -Name "ADMIN_FRONTEND_HOST" -DefaultValue "localhost"
  $ComfyHost = Get-EnvText -Name "COMFYUI_HOST" -DefaultValue "localhost"
  $OllamaHost = Get-EnvText -Name "OLLAMA_HOST" -DefaultValue "localhost"
  $UsesOllama = Test-LlmUsesOllama
  $UsesMiniMax = Test-LlmUsesMiniMax

  $ApiPort = Get-EnvInt -Name "API_PORT" -DefaultValue 18751
  $FrontendPort = Get-EnvInt -Name "ADMIN_FRONTEND_PORT" -DefaultValue 18742
  $ComfyPort = Get-EnvInt -Name "COMFYUI_PORT" -DefaultValue 8401
  $OllamaPort = Get-EnvInt -Name "OLLAMA_PORT" -DefaultValue 11434

  $ApiUrl = "http://$($ApiHost):$ApiPort"
  $FrontendUrl = "http://$($FrontendHost):$FrontendPort"
  $ComfyUrl = "http://$($ComfyHost):$ComfyPort"
  $OllamaUrl = "http://$($OllamaHost):$OllamaPort"

  $Ports = @($ApiPort, $FrontendPort, $ComfyPort)
  if ($UsesOllama) {
    $Ports += $OllamaPort
  }
  $PidMap = Get-ListenerPidMap -Ports $Ports
  $ApiPid = if ($PidMap.ContainsKey($ApiPort)) { $PidMap[$ApiPort] } else { $null }
  $FrontendPid = if ($PidMap.ContainsKey($FrontendPort)) { $PidMap[$FrontendPort] } else { $null }
  $ComfyPid = if ($PidMap.ContainsKey($ComfyPort)) { $PidMap[$ComfyPort] } else { $null }
  $OllamaPid = if ($PidMap.ContainsKey($OllamaPort)) { $PidMap[$OllamaPort] } else { $null }
  $ProcessLabelMap = Get-ProcessLabelMap -Pids @($ApiPid, $FrontendPid, $ComfyPid, $OllamaPid)

  $Telegram = Get-TelegramWebhookStatus

  @(
    New-ServiceRow -Name "API" -Status $(if ($ApiPid) { "online" } else { "offline" }) -Port $ApiPort -ProcessIdValue $ApiPid -Url $ApiUrl -Detail (Get-ProcessLabelFromMap -ProcessIdValue $ApiPid -ProcessLabelMap $ProcessLabelMap)
    New-ServiceRow -Name "Frontend" -Status $(if ($FrontendPid) { "online" } else { "offline" }) -Port $FrontendPort -ProcessIdValue $FrontendPid -Url $FrontendUrl -Detail (Get-ProcessLabelFromMap -ProcessIdValue $FrontendPid -ProcessLabelMap $ProcessLabelMap)
    New-ServiceRow -Name "ComfyUI" -Status $(if ($ComfyPid) { "online" } else { "offline" }) -Port $ComfyPort -ProcessIdValue $ComfyPid -Url $ComfyUrl -Detail (Get-EnvText -Name "COMFYUI_ROOT" -DefaultValue "COMFYUI_ROOT is not configured")
    if ($UsesMiniMax) {
      New-ServiceRow -Name "MiniMax LLM" -Status $(if ($env:MINIMAX_API_KEY) { "configured" } else { "unconfigured" }) -Port "-" -ProcessIdValue $null -Url (Get-EnvText -Name "MINIMAX_BASE_URL" -DefaultValue "https://api.minimaxi.com/v1") -Detail (Get-MiniMaxModelDisplay)
    }
    if ($UsesOllama) {
      New-ServiceRow -Name "Ollama" -Status $(if ($OllamaPid) { "online" } else { "offline" }) -Port $OllamaPort -ProcessIdValue $OllamaPid -Url $OllamaUrl -Detail (Get-OllamaModelDisplay)
    }
    New-ServiceRow -Name "Telegram" -Status $Telegram.Status -Port $ApiPort -ProcessIdValue $Telegram.Pid -Url $Telegram.Url -Detail $Telegram.Detail
  )
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

if ($StatusSnapshot) {
  Get-ServiceRows | ConvertTo-Json -Compress
  return
}

function Invoke-ControlScript {
  param([string]$ScriptName)

  $OperationLogPath = Join-Path $env:TEMP ("vibevision-control-{0}-{1}.log" -f ([IO.Path]::GetFileNameWithoutExtension($ScriptName)), ([guid]::NewGuid().ToString("N")))
  $OperationErrorLogPath = Join-Path $env:TEMP ("vibevision-control-{0}-{1}.err.log" -f ([IO.Path]::GetFileNameWithoutExtension($ScriptName)), ([guid]::NewGuid().ToString("N")))
  $Arguments = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    (Join-Path $Root "scripts\$ScriptName"),
    "-ConfigPath",
    $ConfigPath,
    "-LocalConfigPath",
    $LocalConfigPath
  )

  $Process = Start-Process `
    -FilePath "powershell" `
    -ArgumentList $Arguments `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OperationLogPath `
    -RedirectStandardError $OperationErrorLogPath `
    -PassThru

  [pscustomobject]@{
    Name = $ScriptName
    Process = $Process
    LogPath = $OperationLogPath
    ErrorLogPath = $OperationErrorLogPath
    Offset = 0
    ErrorOffset = 0
  }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$ColorWindow = [System.Drawing.Color]::FromArgb(244, 246, 243)
$ColorSurface = [System.Drawing.Color]::FromArgb(255, 255, 252)
$ColorSurfaceMuted = [System.Drawing.Color]::FromArgb(238, 242, 236)
$ColorBorder = [System.Drawing.Color]::FromArgb(218, 224, 216)
$ColorText = [System.Drawing.Color]::FromArgb(24, 29, 25)
$ColorMuted = [System.Drawing.Color]::FromArgb(91, 101, 92)
$ColorAccent = [System.Drawing.Color]::FromArgb(22, 64, 50)
$ColorAccentSoft = [System.Drawing.Color]::FromArgb(223, 238, 229)
$ColorLog = [System.Drawing.Color]::FromArgb(19, 24, 21)

$Form = New-Object System.Windows.Forms.Form
$Form.Text = "VibeVision Control"
$Form.StartPosition = "CenterScreen"
$Form.Width = 1160
$Form.Height = 760
$Form.MinimumSize = New-Object System.Drawing.Size(1100, 660)
$Form.BackColor = $ColorWindow
$Form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$HeaderPanel = New-Object System.Windows.Forms.Panel
$HeaderPanel.Location = New-Object System.Drawing.Point(24, 20)
$HeaderPanel.Size = New-Object System.Drawing.Size(1096, 96)
$HeaderPanel.Anchor = "Top,Left,Right"
$HeaderPanel.BackColor = $ColorSurface
$HeaderPanel.BorderStyle = "None"
$Form.Controls.Add($HeaderPanel)

$Title = New-Object System.Windows.Forms.Label
$Title.Text = "VibeVision Control Center"
$Title.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
$Title.ForeColor = $ColorText
$Title.AutoSize = $true
$Title.Location = New-Object System.Drawing.Point(20, 18)
$HeaderPanel.Controls.Add($Title)

$Subtitle = New-Object System.Windows.Forms.Label
$Subtitle.Text = "Local service status, runtime logs, and quick actions."
$Subtitle.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$Subtitle.ForeColor = $ColorMuted
$Subtitle.AutoSize = $true
$Subtitle.Location = New-Object System.Drawing.Point(22, 58)
$HeaderPanel.Controls.Add($Subtitle)

$ButtonPanel = New-Object System.Windows.Forms.FlowLayoutPanel
$ButtonPanel.FlowDirection = "LeftToRight"
$ButtonPanel.WrapContents = $false
$ButtonPanel.Anchor = "Top,Right"
$ButtonPanel.Location = New-Object System.Drawing.Point(492, 28)
$ButtonPanel.Size = New-Object System.Drawing.Size(584, 44)
$ButtonPanel.Padding = New-Object System.Windows.Forms.Padding(0)
$ButtonPanel.BackColor = $ColorSurface
$HeaderPanel.Controls.Add($ButtonPanel)

function New-ControlButton {
  param(
    [string]$Text,
    [int]$Width = 112,
    [switch]$Primary
  )

  $Button = New-Object System.Windows.Forms.Button
  $Button.Text = $Text
  $Button.Width = $Width
  $Button.Height = 36
  $Button.Margin = New-Object System.Windows.Forms.Padding(4)
  $Button.FlatStyle = "Flat"
  $Button.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
  $Button.FlatAppearance.BorderSize = 1
  if ($Primary) {
    $Button.BackColor = $ColorAccent
    $Button.ForeColor = [System.Drawing.Color]::White
    $Button.FlatAppearance.BorderColor = $ColorAccent
    $Button.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(31, 86, 67)
    $Button.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::FromArgb(15, 45, 35)
  } else {
    $Button.BackColor = $ColorSurface
    $Button.ForeColor = $ColorText
    $Button.FlatAppearance.BorderColor = $ColorBorder
    $Button.FlatAppearance.MouseOverBackColor = $ColorAccentSoft
    $Button.FlatAppearance.MouseDownBackColor = $ColorSurfaceMuted
  }
  return $Button
}

$AdminPageButton = New-ControlButton -Text "Open Admin" -Width 112 -Primary
$VibeVisionButton = New-ControlButton -Text "Restart VibeVision" -Width 146
$ComfyUIButton = New-ControlButton -Text "Restart ComfyUI" -Width 132
$OllamaButton = New-ControlButton -Text "Restart Ollama" -Width 124

$ButtonPanel.Controls.Add($AdminPageButton)
$ButtonPanel.Controls.Add($VibeVisionButton)
$ButtonPanel.Controls.Add($ComfyUIButton)
$ButtonPanel.Controls.Add($OllamaButton)

$ServicesLabel = New-Object System.Windows.Forms.Label
$ServicesLabel.Text = "Service Status"
$ServicesLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$ServicesLabel.ForeColor = $ColorText
$ServicesLabel.AutoSize = $true
$ServicesLabel.Location = New-Object System.Drawing.Point(28, 140)
$Form.Controls.Add($ServicesLabel)

$ServicesHint = New-Object System.Windows.Forms.Label
$ServicesHint.Text = "Auto-refreshes every few seconds"
$ServicesHint.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$ServicesHint.ForeColor = $ColorMuted
$ServicesHint.AutoSize = $true
$ServicesHint.Anchor = "Top,Right"
$ServicesHint.Location = New-Object System.Drawing.Point(918, 142)
$Form.Controls.Add($ServicesHint)

$Grid = New-Object System.Windows.Forms.DataGridView
$Grid.Location = New-Object System.Drawing.Point(28, 170)
$Grid.Size = New-Object System.Drawing.Size(1092, 286)
$Grid.Anchor = "Top,Left,Right"
$Grid.ReadOnly = $true
$Grid.AllowUserToAddRows = $false
$Grid.AllowUserToDeleteRows = $false
$Grid.AllowUserToResizeRows = $false
$Grid.RowHeadersVisible = $false
$Grid.SelectionMode = "FullRowSelect"
$Grid.AutoSizeColumnsMode = "Fill"
$Grid.BackgroundColor = $ColorSurface
$Grid.BorderStyle = "None"
$Grid.GridColor = [System.Drawing.Color]::FromArgb(231, 235, 229)
$Grid.CellBorderStyle = "SingleHorizontal"
$Grid.ColumnHeadersBorderStyle = "None"
$Grid.RowTemplate.Height = 44
$Grid.ColumnHeadersHeight = 40
$Grid.ColumnHeadersHeightSizeMode = "DisableResizing"
$Grid.EnableHeadersVisualStyles = $false
$Grid.ColumnHeadersDefaultCellStyle.BackColor = $ColorSurfaceMuted
$Grid.ColumnHeadersDefaultCellStyle.ForeColor = $ColorMuted
$Grid.ColumnHeadersDefaultCellStyle.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$Grid.ColumnHeadersDefaultCellStyle.Padding = New-Object System.Windows.Forms.Padding(8, 0, 8, 0)
$Grid.DefaultCellStyle.BackColor = $ColorSurface
$Grid.DefaultCellStyle.ForeColor = $ColorText
$Grid.DefaultCellStyle.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$Grid.DefaultCellStyle.Padding = New-Object System.Windows.Forms.Padding(8, 0, 8, 0)
$Grid.DefaultCellStyle.SelectionBackColor = $ColorAccentSoft
$Grid.DefaultCellStyle.SelectionForeColor = $ColorText
$Grid.AlternatingRowsDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(250, 251, 248)
$Grid.AlternatingRowsDefaultCellStyle.ForeColor = $ColorText
$Form.Controls.Add($Grid)

$LogLabel = New-Object System.Windows.Forms.Label
$LogLabel.Text = "Runtime Log"
$LogLabel.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$LogLabel.ForeColor = $ColorText
$LogLabel.AutoSize = $true
$LogLabel.Location = New-Object System.Drawing.Point(28, 482)
$LogLabel.Anchor = "Top,Left"
$Form.Controls.Add($LogLabel)

$LogBox = New-Object System.Windows.Forms.TextBox
$LogBox.Location = New-Object System.Drawing.Point(28, 512)
$LogBox.Size = New-Object System.Drawing.Size(1092, 150)
$LogBox.Anchor = "Top,Left,Right,Bottom"
$LogBox.Multiline = $true
$LogBox.ReadOnly = $true
$LogBox.ScrollBars = "Vertical"
$LogBox.BorderStyle = "None"
$LogBox.BackColor = $ColorLog
$LogBox.ForeColor = [System.Drawing.Color]::FromArgb(226, 232, 224)
$LogBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$Form.Controls.Add($LogBox)

$StatusLabel = New-Object System.Windows.Forms.Label
$StatusLabel.Text = "Ready."
$StatusLabel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$StatusLabel.ForeColor = $ColorMuted
$StatusLabel.AutoSize = $true
$StatusLabel.Location = New-Object System.Drawing.Point(28, 676)
$StatusLabel.Anchor = "Left,Bottom"
$Form.Controls.Add($StatusLabel)

$StartOnOpen = New-Object System.Windows.Forms.CheckBox
$StartOnOpen.Text = "Start missing VibeVision services when this window opens"
$StartOnOpen.Checked = (-not $NoAutoStart)
$StartOnOpen.AutoSize = $true
$StartOnOpen.Location = New-Object System.Drawing.Point(28, 700)
$StartOnOpen.Anchor = "Left,Bottom"
$StartOnOpen.ForeColor = $ColorText
$StartOnOpen.BackColor = $ColorWindow
$Form.Controls.Add($StartOnOpen)

$StopOnExit = New-Object System.Windows.Forms.CheckBox
$StopOnExit.Text = "Stop VibeVision services when this window exits"
$StopOnExit.Checked = $true
$StopOnExit.AutoSize = $true
$StopOnExit.Location = New-Object System.Drawing.Point(388, 700)
$StopOnExit.Anchor = "Left,Bottom"
$StopOnExit.ForeColor = $ColorText
$StopOnExit.BackColor = $ColorWindow
$Form.Controls.Add($StopOnExit)

$RunningOperations = New-Object System.Collections.ArrayList
$script:RefreshOperation = $null
$script:LastRefreshStarted = [datetime]::MinValue
$RefreshInterval = [timespan]::FromSeconds(7)

function Add-LogLine {
  param([string]$Message)

  $Line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  if ($LogBox.TextLength -gt 60000) {
    $LogBox.Text = $LogBox.Text.Substring($LogBox.TextLength - 40000)
    $LogBox.SelectionStart = $LogBox.TextLength
  }
  [void]$LogBox.AppendText($Line + [Environment]::NewLine)
  try {
    Add-Content -LiteralPath $LogPath -Value $Line -ErrorAction SilentlyContinue
  } catch {
  }
}

function Get-TextFileSafe {
  param([string]$Path)

  if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
    return ""
  }

  $Content = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
  if ($null -eq $Content) {
    return ""
  }

  return [string]$Content
}

function Get-ProcessExitCodeSafe {
  param(
    [System.Diagnostics.Process]$Process,
    [int]$WaitMilliseconds = 250
  )

  if (-not $Process) {
    return $null
  }

  try {
    [void]$Process.WaitForExit($WaitMilliseconds)
  } catch {
  }

  try {
    $Process.Refresh()
  } catch {
  }

  try {
    if (-not $Process.HasExited) {
      return $null
    }
  } catch {
    return $null
  }

  try {
    return [int]$Process.ExitCode
  } catch {
    return $null
  }
}

function Set-OperationButtons {
  param([bool]$Enabled)

  $VibeVisionButton.Enabled = $Enabled
  $ComfyUIButton.Enabled = $Enabled
  $OllamaButton.Enabled = $Enabled
}

function Set-GridRows {
  param([object[]]$Rows)

  $Grid.SuspendLayout()
  try {
    $Grid.DataSource = [System.Collections.ArrayList]$Rows
    if ($Grid.Columns["Service"]) {
      $Grid.Columns["Service"].FillWeight = 70
    }
    if ($Grid.Columns["Status"]) {
      $Grid.Columns["Status"].FillWeight = 60
    }
    if ($Grid.Columns["Port"]) {
      $Grid.Columns["Port"].FillWeight = 45
    }
    if ($Grid.Columns["PID"]) {
      $Grid.Columns["PID"].FillWeight = 55
    }
    if ($Grid.Columns["URL"]) {
      $Grid.Columns["URL"].FillWeight = 140
    }
    if ($Grid.Columns["Detail"]) {
      $Grid.Columns["Detail"].FillWeight = 190
    }
    $StatusLabel.Text = "Last refresh: $(Get-Date -Format 'HH:mm:ss')"
  } finally {
    $Grid.ResumeLayout()
  }
}

function Start-StatusRefresh {
  param(
    [string]$Reason = "auto",
    [switch]$Force
  )

  if ($script:RefreshOperation -and -not $script:RefreshOperation.Process.HasExited) {
    return
  }

  $Now = Get-Date
  if (-not $Force -and (($Now - $script:LastRefreshStarted) -lt $RefreshInterval)) {
    return
  }

  $script:LastRefreshStarted = $Now
  $OutputPath = Join-Path $env:TEMP ("vibevision-status-{0}.json" -f ([guid]::NewGuid().ToString("N")))
  $ErrorPath = Join-Path $env:TEMP ("vibevision-status-{0}.err.log" -f ([guid]::NewGuid().ToString("N")))
  $Arguments = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $ScriptPath,
    "-ConfigPath",
    $ConfigPath,
    "-LocalConfigPath",
    $LocalConfigPath,
    "-StatusSnapshot"
  )

  try {
    $Process = Start-Process `
      -FilePath "powershell" `
      -ArgumentList $Arguments `
      -WindowStyle Hidden `
      -RedirectStandardOutput $OutputPath `
      -RedirectStandardError $ErrorPath `
      -PassThru

    $script:RefreshOperation = [pscustomobject]@{
      Process = $Process
      OutputPath = $OutputPath
      ErrorPath = $ErrorPath
      Reason = $Reason
    }
    $StatusLabel.Text = "Refreshing status..."
  } catch {
    $script:RefreshOperation = $null
    $StatusLabel.Text = "Refresh start failed."
    Add-LogLine "Refresh start failed: $($_.Exception.Message)"
  }
}

function Complete-StatusRefresh {
  if (-not $script:RefreshOperation) {
    return
  }

  if (-not $script:RefreshOperation.Process.HasExited) {
    return
  }

  $Operation = $script:RefreshOperation
  $script:RefreshOperation = $null

  try {
    $Json = Get-TextFileSafe -Path $Operation.OutputPath
    $ErrorText = (Get-TextFileSafe -Path $Operation.ErrorPath).Trim()
    $ExitCode = Get-ProcessExitCodeSafe -Process $Operation.Process

    if ($null -ne $ExitCode -and $ExitCode -ne 0) {
      if (-not $ErrorText) {
        $OutputText = $Json.Trim()
        if ($OutputText) {
          $ErrorText = $OutputText
        } else {
          $ErrorText = "status process exited with code $ExitCode"
        }
      }
      throw $ErrorText
    }

    if (-not $Json.Trim()) {
      if ($ErrorText) {
        throw $ErrorText
      }
      throw "status process returned no data."
    }

    $ParsedRows = $Json | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $ParsedRows) {
      $Rows = @()
    } elseif ($ParsedRows -is [System.Array]) {
      $Rows = $ParsedRows
    } else {
      $Rows = @($ParsedRows)
    }
    Set-GridRows -Rows $Rows
  } catch {
    $StatusLabel.Text = "Refresh failed."
    Add-LogLine "Refresh failed: $($_.Exception.Message)"
  } finally {
    Remove-Item -LiteralPath $Operation.OutputPath, $Operation.ErrorPath -ErrorAction SilentlyContinue
  }
}

function Refresh-Grid {
  Start-StatusRefresh -Reason "manual" -Force
}

function Get-AdminFrontendUrl {
  $FrontendHost = Get-EnvText -Name "ADMIN_FRONTEND_HOST" -DefaultValue "localhost"
  $FrontendPort = Get-EnvInt -Name "ADMIN_FRONTEND_PORT" -DefaultValue 18742
  return "http://$($FrontendHost):$FrontendPort"
}

function Open-AdminPage {
  $Url = Get-AdminFrontendUrl
  try {
    Add-LogLine "Opening admin page: $Url"
    Start-Process -FilePath $Url
  } catch {
    $StatusLabel.Text = "Open admin page failed."
    Add-LogLine "Open admin page failed: $($_.Exception.Message)"
  }
}

function Start-ControlOperation {
  param(
    [string]$ScriptName,
    [string]$Label
  )

  try {
    Add-LogLine "$Label requested."
    $StatusLabel.Text = "$Label running..."
    Set-OperationButtons -Enabled $false
    $Operation = Invoke-ControlScript -ScriptName $ScriptName
    [void]$RunningOperations.Add($Operation)
  } catch {
    Set-OperationButtons -Enabled $true
    $StatusLabel.Text = "$Label failed."
    Add-LogLine "$Label failed: $($_.Exception.Message)"
  }
}

function Read-OperationOutput {
  param([object]$Operation)

  Read-LogFileOutput -Path $Operation.LogPath -OffsetProperty "Offset" -Operation $Operation
  Read-LogFileOutput -Path $Operation.ErrorLogPath -OffsetProperty "ErrorOffset" -Operation $Operation
}

function Read-LogFileOutput {
  param(
    [string]$Path,
    [string]$OffsetProperty,
    [object]$Operation
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  $Content = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
  if (-not $Content) {
    return
  }

  $Offset = $Operation.$OffsetProperty
  if ($Content.Length -le $Offset) {
    return
  }

  $NewText = $Content.Substring($Offset)
  $Operation.$OffsetProperty = $Content.Length
  foreach ($Line in ($NewText -split "\r?\n")) {
    if ($Line.Trim()) {
      Add-LogLine $Line.Trim()
    }
  }
}

function Update-Operations {
  if ($RunningOperations.Count -eq 0) {
    return
  }

  $Completed = New-Object System.Collections.ArrayList
  foreach ($Operation in $RunningOperations) {
    Read-OperationOutput -Operation $Operation
    if ($Operation.Process.HasExited) {
      Read-OperationOutput -Operation $Operation
      $ExitCode = Get-ProcessExitCodeSafe -Process $Operation.Process
      $ExitText = if ($null -ne $ExitCode) { [string]$ExitCode } else { "unknown" }
      Add-LogLine "$($Operation.Name) exited with code $ExitText."
      [void]$Completed.Add($Operation)
    }
  }

  foreach ($Operation in $Completed) {
    [void]$RunningOperations.Remove($Operation)
  }

  if ($RunningOperations.Count -eq 0) {
    Set-OperationButtons -Enabled $true
    Start-StatusRefresh -Reason "operation complete" -Force
  }
}

$VibeVisionButton.Add_Click({
  Start-ControlOperation -ScriptName "restart-vibevision.ps1" -Label "Start/Restart VibeVision"
})

$ComfyUIButton.Add_Click({
  Start-ControlOperation -ScriptName "restart-comfyui.ps1" -Label "Start/Restart ComfyUI"
})

$OllamaButton.Add_Click({
  Start-ControlOperation -ScriptName "restart-ollama.ps1" -Label "Start/Restart Ollama"
})

$AdminPageButton.Add_Click({
  Open-AdminPage
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 1000
$Timer.Add_Tick({
  Update-Operations
  Complete-StatusRefresh
  Start-StatusRefresh -Reason "auto"
})
$Timer.Start()

$Form.Add_Shown({
  Add-LogLine "Control window opened."
  Refresh-Grid
  if ($StartOnOpen.Checked) {
    Start-ControlOperation -ScriptName "start-all.ps1" -Label "Start missing VibeVision services"
  }
})

$Form.Add_FormClosing({
  if ($StopOnExit.Checked) {
    Add-LogLine "Exit requested; launching background stop."
    try {
      [void](Invoke-ControlScript -ScriptName "stop-all.ps1")
      Add-LogLine "stop-all.ps1 launched in the background."
    } catch {
      Add-LogLine "Stop on exit failed: $($_.Exception.Message)"
    }
  } else {
    Add-LogLine "Exit requested; background services left running."
  }
})

[void][System.Windows.Forms.Application]::Run($Form)

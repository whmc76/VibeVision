param(
  [string]$ConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.env"),
  [string]$LocalConfigPath = (Join-Path $PSScriptRoot "..\config\vibevision.local.env"),
  [switch]$NoAutoStart
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogPath = Join-Path $env:TEMP "vibevision-control.log"

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

function Get-ProcessLabel {
  param([object]$Pid)

  if (-not $Pid) {
    return "Not listening"
  }

  $Process = Get-Process -Id $Pid -ErrorAction SilentlyContinue
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
    [object]$Pid,
    [string]$Url,
    [string]$Detail
  )

  $PidText = "-"
  if ($Pid) {
    $PidText = [string]$Pid
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

  $ApiPort = Get-EnvInt -Name "API_PORT" -DefaultValue 18751
  $FrontendPort = Get-EnvInt -Name "ADMIN_FRONTEND_PORT" -DefaultValue 18742
  $ComfyPort = Get-EnvInt -Name "COMFYUI_PORT" -DefaultValue 8401
  $OllamaPort = Get-EnvInt -Name "OLLAMA_PORT" -DefaultValue 11434

  $ApiUrl = "http://$($ApiHost):$ApiPort"
  $FrontendUrl = "http://$($FrontendHost):$FrontendPort"
  $ComfyUrl = "http://$($ComfyHost):$ComfyPort"
  $OllamaUrl = "http://$($OllamaHost):$OllamaPort"

  $ApiPid = Get-ListenerPid -Port $ApiPort
  $FrontendPid = Get-ListenerPid -Port $FrontendPort
  $ComfyPid = Get-ListenerPid -Port $ComfyPort
  $OllamaPid = Get-ListenerPid -Port $OllamaPort

  $TelegramStatus = "unconfigured"
  $TelegramDetail = "Set TELEGRAM_BOT_TOKEN in local config"
  if ($env:TELEGRAM_BOT_TOKEN) {
    $TelegramStatus = "configured"
    $TelegramDetail = "Webhook endpoint is served by the API"
  }

  @(
    New-ServiceRow -Name "API" -Status $(if ($ApiPid) { "online" } else { "offline" }) -Port $ApiPort -Pid $ApiPid -Url $ApiUrl -Detail (Get-ProcessLabel -Pid $ApiPid)
    New-ServiceRow -Name "Frontend" -Status $(if ($FrontendPid) { "online" } else { "offline" }) -Port $FrontendPort -Pid $FrontendPid -Url $FrontendUrl -Detail (Get-ProcessLabel -Pid $FrontendPid)
    New-ServiceRow -Name "ComfyUI" -Status $(if ($ComfyPid) { "online" } else { "offline" }) -Port $ComfyPort -Pid $ComfyPid -Url $ComfyUrl -Detail (Get-EnvText -Name "COMFYUI_ROOT" -DefaultValue "COMFYUI_ROOT is not configured")
    New-ServiceRow -Name "Ollama" -Status $(if ($OllamaPid) { "online" } else { "offline" }) -Port $OllamaPort -Pid $OllamaPid -Url $OllamaUrl -Detail (Get-EnvText -Name "OLLAMA_MODEL" -DefaultValue "Model is not configured")
    New-ServiceRow -Name "Telegram" -Status $TelegramStatus -Port $ApiPort -Pid $ApiPid -Url "$ApiUrl/api/telegram/webhook" -Detail $TelegramDetail
  )
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

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$Form = New-Object System.Windows.Forms.Form
$Form.Text = "VibeVision Control"
$Form.StartPosition = "CenterScreen"
$Form.Width = 1080
$Form.Height = 720
$Form.MinimumSize = New-Object System.Drawing.Size(900, 600)
$Form.BackColor = [System.Drawing.Color]::FromArgb(246, 244, 239)

$Title = New-Object System.Windows.Forms.Label
$Title.Text = "VibeVision service monitor"
$Title.Font = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold)
$Title.AutoSize = $true
$Title.Location = New-Object System.Drawing.Point(24, 20)
$Form.Controls.Add($Title)

$Subtitle = New-Object System.Windows.Forms.Label
$Subtitle.Text = "Start, stop, refresh, and inspect local API, frontend, ComfyUI, Ollama, and Telegram status."
$Subtitle.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$Subtitle.ForeColor = [System.Drawing.Color]::FromArgb(92, 88, 80)
$Subtitle.AutoSize = $true
$Subtitle.Location = New-Object System.Drawing.Point(27, 58)
$Form.Controls.Add($Subtitle)

$Grid = New-Object System.Windows.Forms.DataGridView
$Grid.Location = New-Object System.Drawing.Point(28, 94)
$Grid.Size = New-Object System.Drawing.Size(1005, 300)
$Grid.Anchor = "Top,Left,Right"
$Grid.ReadOnly = $true
$Grid.AllowUserToAddRows = $false
$Grid.AllowUserToDeleteRows = $false
$Grid.AllowUserToResizeRows = $false
$Grid.RowHeadersVisible = $false
$Grid.SelectionMode = "FullRowSelect"
$Grid.AutoSizeColumnsMode = "Fill"
$Grid.BackgroundColor = [System.Drawing.Color]::White
$Grid.BorderStyle = "FixedSingle"
$Grid.EnableHeadersVisualStyles = $false
$Grid.ColumnHeadersDefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(236, 231, 221)
$Grid.ColumnHeadersDefaultCellStyle.ForeColor = [System.Drawing.Color]::FromArgb(38, 36, 32)
$Grid.DefaultCellStyle.SelectionBackColor = [System.Drawing.Color]::FromArgb(223, 241, 230)
$Grid.DefaultCellStyle.SelectionForeColor = [System.Drawing.Color]::FromArgb(28, 27, 24)
$Form.Controls.Add($Grid)

$LogBox = New-Object System.Windows.Forms.TextBox
$LogBox.Location = New-Object System.Drawing.Point(28, 410)
$LogBox.Size = New-Object System.Drawing.Size(1005, 185)
$LogBox.Anchor = "Top,Left,Right,Bottom"
$LogBox.Multiline = $true
$LogBox.ReadOnly = $true
$LogBox.ScrollBars = "Vertical"
$LogBox.BackColor = [System.Drawing.Color]::FromArgb(32, 31, 29)
$LogBox.ForeColor = [System.Drawing.Color]::FromArgb(232, 229, 222)
$LogBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$Form.Controls.Add($LogBox)

$StatusLabel = New-Object System.Windows.Forms.Label
$StatusLabel.Text = "Ready."
$StatusLabel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$StatusLabel.ForeColor = [System.Drawing.Color]::FromArgb(92, 88, 80)
$StatusLabel.AutoSize = $true
$StatusLabel.Location = New-Object System.Drawing.Point(28, 612)
$StatusLabel.Anchor = "Left,Bottom"
$Form.Controls.Add($StatusLabel)

$StartOnOpen = New-Object System.Windows.Forms.CheckBox
$StartOnOpen.Text = "Start background services when this window opens"
$StartOnOpen.Checked = (-not $NoAutoStart)
$StartOnOpen.AutoSize = $true
$StartOnOpen.Location = New-Object System.Drawing.Point(28, 642)
$StartOnOpen.Anchor = "Left,Bottom"
$Form.Controls.Add($StartOnOpen)

$StopOnExit = New-Object System.Windows.Forms.CheckBox
$StopOnExit.Text = "Stop background services when this window exits"
$StopOnExit.Checked = $true
$StopOnExit.AutoSize = $true
$StopOnExit.Location = New-Object System.Drawing.Point(360, 642)
$StopOnExit.Anchor = "Left,Bottom"
$Form.Controls.Add($StopOnExit)

$ButtonPanel = New-Object System.Windows.Forms.FlowLayoutPanel
$ButtonPanel.FlowDirection = "LeftToRight"
$ButtonPanel.WrapContents = $false
$ButtonPanel.Anchor = "Right,Bottom"
$ButtonPanel.Location = New-Object System.Drawing.Point(610, 604)
$ButtonPanel.Size = New-Object System.Drawing.Size(425, 46)
$ButtonPanel.Padding = New-Object System.Windows.Forms.Padding(0)
$Form.Controls.Add($ButtonPanel)

function New-ControlButton {
  param(
    [string]$Text,
    [int]$Width = 78
  )

  $Button = New-Object System.Windows.Forms.Button
  $Button.Text = $Text
  $Button.Width = $Width
  $Button.Height = 34
  $Button.Margin = New-Object System.Windows.Forms.Padding(4)
  $Button.FlatStyle = "Flat"
  $Button.BackColor = [System.Drawing.Color]::White
  $Button.ForeColor = [System.Drawing.Color]::FromArgb(28, 27, 24)
  return $Button
}

$StartButton = New-ControlButton -Text "Start all"
$StopButton = New-ControlButton -Text "Stop all"
$RefreshButton = New-ControlButton -Text "Refresh"
$OpenButton = New-ControlButton -Text "Open admin" -Width 92
$ExitButton = New-ControlButton -Text "Exit"

$ButtonPanel.Controls.Add($StartButton)
$ButtonPanel.Controls.Add($StopButton)
$ButtonPanel.Controls.Add($RefreshButton)
$ButtonPanel.Controls.Add($OpenButton)
$ButtonPanel.Controls.Add($ExitButton)

$RunningOperations = New-Object System.Collections.ArrayList

function Add-LogLine {
  param([string]$Message)

  $Line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  [void]$LogBox.AppendText($Line + [Environment]::NewLine)
  try {
    Add-Content -LiteralPath $LogPath -Value $Line -ErrorAction SilentlyContinue
  } catch {
  }
}

function Set-OperationButtons {
  param([bool]$Enabled)

  $StartButton.Enabled = $Enabled
  $StopButton.Enabled = $Enabled
  $RefreshButton.Enabled = $true
  $OpenButton.Enabled = $true
  $ExitButton.Enabled = $true
}

function Refresh-Grid {
  try {
    $Rows = Get-ServiceRows
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
  } catch {
    $StatusLabel.Text = "Refresh failed."
    Add-LogLine "Refresh failed: $($_.Exception.Message)"
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
      $ExitCode = $Operation.Process.ExitCode
      Add-LogLine "$($Operation.Name) exited with code $ExitCode."
      [void]$Completed.Add($Operation)
    }
  }

  foreach ($Operation in $Completed) {
    [void]$RunningOperations.Remove($Operation)
  }

  if ($RunningOperations.Count -eq 0) {
    Set-OperationButtons -Enabled $true
    Refresh-Grid
  }
}

$StartButton.Add_Click({
  Start-ControlOperation -ScriptName "start-all.ps1" -Label "Start all"
})

$StopButton.Add_Click({
  Start-ControlOperation -ScriptName "stop-all.ps1" -Label "Stop all"
})

$RefreshButton.Add_Click({
  Add-LogLine "Refresh requested."
  Refresh-Grid
})

$OpenButton.Add_Click({
  $Url = "http://$(Get-EnvText -Name "ADMIN_FRONTEND_HOST" -DefaultValue "localhost"):$(Get-EnvInt -Name "ADMIN_FRONTEND_PORT" -DefaultValue 18742)"
  Add-LogLine "Opening admin frontend: $Url"
  Start-Process $Url
})

$ExitButton.Add_Click({
  $Form.Close()
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 2000
$Timer.Add_Tick({
  Update-Operations
  Refresh-Grid
})
$Timer.Start()

$Form.Add_Shown({
  Add-LogLine "Control window opened."
  Refresh-Grid
  if ($StartOnOpen.Checked) {
    Start-ControlOperation -ScriptName "start-all.ps1" -Label "Start all"
  }
})

$Form.Add_FormClosing({
  if ($StopOnExit.Checked) {
    Add-LogLine "Exit requested; stopping background services."
    try {
      $Operation = Invoke-ControlScript -ScriptName "stop-all.ps1"
      $Exited = $Operation.Process.WaitForExit(15000)
      Read-OperationOutput -Operation $Operation
      if ($Exited) {
        Add-LogLine "stop-all.ps1 exited with code $($Operation.Process.ExitCode)."
      } else {
        Add-LogLine "stop-all.ps1 is still running after 15 seconds; closing the monitor."
      }
    } catch {
      Add-LogLine "Stop on exit failed: $($_.Exception.Message)"
    }
  } else {
    Add-LogLine "Exit requested; background services left running."
  }
})

[void][System.Windows.Forms.Application]::Run($Form)

param(
  [ValidateSet("info", "set", "delete")]
  [string]$Action = "info",
  [string]$PublicBaseUrl,
  [string]$WebhookUrl,
  [switch]$DropPendingUpdates,
  [string]$SecretToken,
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

function Invoke-TelegramApi {
  param(
    [string]$Method,
    [hashtable]$Body = @{}
  )

  if (-not $env:TELEGRAM_BOT_TOKEN) {
    throw "TELEGRAM_BOT_TOKEN is not configured."
  }

  $Uri = "https://api.telegram.org/bot$($env:TELEGRAM_BOT_TOKEN)/$Method"
  $Response = Invoke-RestMethod -Uri $Uri -Method Post -Body $Body
  if (-not $Response.ok) {
    throw "Telegram API returned ok=false for $Method."
  }
  return $Response.result
}

function Resolve-WebhookUrl {
  param(
    [string]$RawWebhookUrl,
    [string]$RawPublicBaseUrl
  )

  if ($RawWebhookUrl) {
    return $RawWebhookUrl.Trim()
  }

  if (-not $RawPublicBaseUrl) {
    throw "Provide -WebhookUrl or -PublicBaseUrl when using Action=set."
  }

  $BaseUrl = $RawPublicBaseUrl.Trim().TrimEnd("/")
  if ($BaseUrl.EndsWith("/api/telegram/webhook")) {
    return $BaseUrl
  }
  return "$BaseUrl/api/telegram/webhook"
}

function Format-WebhookSummary {
  param([object]$Info)

  $Url = if ($Info.url) { [string]$Info.url } else { "<not set>" }
  $Pending = if ($null -ne $Info.pending_update_count) { [int]$Info.pending_update_count } else { 0 }
  $LastError = if ($Info.last_error_message) { [string]$Info.last_error_message } else { "" }
  $LastErrorDate = if ($Info.last_error_date) { [DateTimeOffset]::FromUnixTimeSeconds([int64]$Info.last_error_date).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss zzz") } else { "-" }

  [pscustomobject]@{
    webhook_url = $Url
    pending_update_count = $Pending
    last_error_date = $LastErrorDate
    last_error_message = if ($LastError) { $LastError } else { "-" }
    has_custom_certificate = [bool]$Info.has_custom_certificate
    ip_address = if ($Info.ip_address) { [string]$Info.ip_address } else { "-" }
  }
}

Import-VibeVisionConfig -Path $ConfigPath
Import-VibeVisionConfig -Path $LocalConfigPath

switch ($Action) {
  "info" {
    $Info = Invoke-TelegramApi -Method "getWebhookInfo"
    Format-WebhookSummary -Info $Info | Format-List
    break
  }
  "set" {
    $TargetWebhookUrl = Resolve-WebhookUrl -RawWebhookUrl $WebhookUrl -RawPublicBaseUrl $PublicBaseUrl
    $Body = @{
      url = $TargetWebhookUrl
    }
    $EffectiveSecretToken = if ($PSBoundParameters.ContainsKey("SecretToken")) { $SecretToken } else { $env:TELEGRAM_WEBHOOK_SECRET }
    if ($EffectiveSecretToken) {
      $Body["secret_token"] = $EffectiveSecretToken
    }
    if ($DropPendingUpdates.IsPresent) {
      $Body["drop_pending_updates"] = $true
    }

    [void](Invoke-TelegramApi -Method "setWebhook" -Body $Body)
    Write-Output "Webhook set to $TargetWebhookUrl"
    $Info = Invoke-TelegramApi -Method "getWebhookInfo"
    Format-WebhookSummary -Info $Info | Format-List
    break
  }
  "delete" {
    $Body = @{}
    if ($DropPendingUpdates.IsPresent) {
      $Body["drop_pending_updates"] = $true
    }
    [void](Invoke-TelegramApi -Method "deleteWebhook" -Body $Body)
    Write-Output "Webhook deleted."
    $Info = Invoke-TelegramApi -Method "getWebhookInfo"
    Format-WebhookSummary -Info $Info | Format-List
    break
  }
}

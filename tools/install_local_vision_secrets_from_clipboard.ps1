param(
  [string]$SecretsPath = (Join-Path $env:USERPROFILE ".audit_robot\secrets\vision.env"),
  [string]$ClipboardText = "",
  [string]$Model = "qwen3.7-plus"
)

$ErrorActionPreference = "Stop"
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBomEncoding
[Console]::OutputEncoding = $Utf8NoBomEncoding

function Get-FirstRegexValue {
  param(
    [Parameter(Mandatory = $true)][string]$Text,
    [Parameter(Mandatory = $true)][string]$Pattern
  )

  $match = [regex]::Match($Text, $Pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if ($match.Success) {
    for ($index = $match.Groups.Count - 1; $index -ge 1; $index--) {
      $value = $match.Groups[$index].Value.Trim()
      if (-not [string]::IsNullOrWhiteSpace($value)) {
        return $value.TrimEnd(",", ";", ")", "]", "}", ">", '"', "'")
      }
    }
  }
  return ""
}

if ([string]::IsNullOrWhiteSpace($ClipboardText)) {
  $ClipboardText = Get-Clipboard -Raw -ErrorAction Stop
}

$baseUrl = Get-FirstRegexValue -Text $ClipboardText -Pattern '(VISION_API_BASE_URL\s*=\s*)?(https?://[^\s"''<>]+)'
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
  $baseUrl = Get-FirstRegexValue -Text $ClipboardText -Pattern '(BASE_URL\s*=\s*)?(https?://[^\s"''<>]+)'
}
$apiKey = Get-FirstRegexValue -Text $ClipboardText -Pattern '(VISION_API_KEY\s*=\s*)?(sk-[A-Za-z0-9._-]+)'
if ([string]::IsNullOrWhiteSpace($apiKey)) {
  $apiKey = Get-FirstRegexValue -Text $ClipboardText -Pattern '(?:VISION_API_KEY|API_KEY|密钥|key)\s*[:=]\s*([A-Za-z0-9._-]{24,})'
}

if ([string]::IsNullOrWhiteSpace($baseUrl)) {
  throw "Clipboard text does not contain a vision API base URL"
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
  throw "Clipboard text does not contain a vision API key"
}

$secretDir = Split-Path -Parent $SecretsPath
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null

$content = @(
  "# Local-only file. Do not commit.",
  "VISION_API_BASE_URL=$baseUrl",
  "VISION_API_KEY=$apiKey",
  "VISION_MODEL_NAME=$Model"
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText($SecretsPath, $content + [Environment]::NewLine, $Utf8NoBomEncoding)

try {
  & icacls $SecretsPath /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
} catch {
  Write-Warning "Local secrets file was written, but Windows ACL hardening failed."
}

[pscustomobject]@{
  status = "saved"
  secretsPath = $SecretsPath
  visionApiBaseUrl = "set"
  visionApiKey = "set"
  visionModelName = "set"
} | ConvertTo-Json -Depth 3

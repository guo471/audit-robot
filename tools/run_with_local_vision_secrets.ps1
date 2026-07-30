param(
  [string]$SecretsPath = (Join-Path $env:USERPROFILE ".audit_robot\secrets\vision.env"),
  [Alias("Command")][string]$RunCommand = "",
  [string[]]$CommandArgs = @()
)

$ErrorActionPreference = "Stop"
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBomEncoding
[Console]::OutputEncoding = $Utf8NoBomEncoding

$allowedKeys = @(
  "VISION_API_BASE_URL",
  "VISION_API_KEY",
  "VISION_MODEL_NAME"
)
$requiredKeys = @(
  "VISION_API_BASE_URL",
  "VISION_API_KEY"
)

function ConvertFrom-LocalEnvLine {
  param([Parameter(Mandatory = $true)][string]$Line)

  if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
  $trimmed = $Line.Trim()
  if ($trimmed.StartsWith("#")) { return $null }

  $match = [regex]::Match($trimmed, '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
  if (-not $match.Success) {
    throw "Malformed env line in local vision secrets file"
  }

  $key = $match.Groups[1].Value.Trim()
  $value = $match.Groups[2].Value.Trim()
  if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))) {
    $value = $value.Substring(1, $value.Length - 2)
  }

  return [pscustomobject]@{
    Key = $key
    Value = $value
  }
}

if (-not (Test-Path -LiteralPath $SecretsPath -PathType Leaf)) {
  throw "Local vision secrets file not found: $SecretsPath"
}

$loaded = @{}
foreach ($line in [System.IO.File]::ReadAllLines($SecretsPath)) {
  $entry = ConvertFrom-LocalEnvLine -Line $line
  if ($null -eq $entry) { continue }
  if ($entry.Key -notin $allowedKeys) { continue }
  if ([string]::IsNullOrWhiteSpace($entry.Value)) {
    throw "Local vision secret value is empty: $($entry.Key)"
  }
  [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
  $loaded[$entry.Key] = $true
}

foreach ($key in $requiredKeys) {
  if (-not $loaded.ContainsKey($key)) {
    throw "Required local vision secret is missing: $key"
  }
}

if (-not $loaded.ContainsKey("VISION_MODEL_NAME") -or
    [string]::IsNullOrWhiteSpace($env:VISION_MODEL_NAME)) {
  $env:VISION_MODEL_NAME = "qwen3.7-plus"
  $loaded["VISION_MODEL_NAME"] = $true
}

if (-not [string]::IsNullOrWhiteSpace($RunCommand)) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $RunCommand
  exit $LASTEXITCODE
}

if ($CommandArgs -and $CommandArgs.Count -gt 0) {
  $program = $CommandArgs[0]
  $programArgs = @()
  if ($CommandArgs.Count -gt 1) {
    $programArgs = $CommandArgs[1..($CommandArgs.Count - 1)]
  }
  & $program @programArgs
  exit $LASTEXITCODE
}

[pscustomobject]@{
  status = "loaded"
  secretsPath = $SecretsPath
  visionApiBaseUrl = "set"
  visionApiKey = "set"
  visionModelName = "set"
} | ConvertTo-Json -Depth 3

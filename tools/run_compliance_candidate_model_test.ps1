param(
  [ValidateSet("self-check", "run")][string]$Action = "self-check",
  [Parameter(Mandatory = $true)][string]$Dataset,
  [Parameter(Mandatory = $true)][string]$OutputDir,
  [string]$CacheDir = "",
  [Parameter(Mandatory = $true)][int]$ExpectedOrderCount,
  [string]$PythonExe = "",
  [string]$SecretsPath = (Join-Path $env:USERPROFILE ".audit_robot\secrets\vision.env"),
  [switch]$RetryServiceFailures
)

$ErrorActionPreference = "Stop"
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBomEncoding
[Console]::OutputEncoding = $Utf8NoBomEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $PythonExe = (& py -3.14 -c "import sys; print(sys.executable)").Trim()
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($PythonExe)) {
    throw "Python 3.14 could not be resolved by the py launcher"
  }
}
if ($PythonExe -match "(?i)HUAWEI") {
  throw "Legacy HUAWEI interpreter paths are forbidden: $PythonExe"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
  throw "Python executable not found: $PythonExe"
}

$entry = Join-Path $PSScriptRoot "compliance_candidate_model_test.py"
$secretLoader = Join-Path $PSScriptRoot "run_with_local_vision_secrets.ps1"
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
  throw "Compliance model-test entry not found: $entry"
}
if (-not (Test-Path -LiteralPath $secretLoader -PathType Leaf)) {
  throw "Local vision secret loader not found: $secretLoader"
}

if ([string]::IsNullOrWhiteSpace($CacheDir)) {
  $resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($resolvedOutput)
    $digest = $sha.ComputeHash($bytes)
  }
  finally {
    $sha.Dispose()
  }
  $shortHash = ([System.BitConverter]::ToString($digest)).Replace("-", "").Substring(0, 16).ToLowerInvariant()
  $CacheDir = Join-Path $env:TEMP ("audit_robot_cc_" + $shortHash)
}

$commandArgs = @(
  $PythonExe,
  $entry,
  $Action,
  "--dataset", $Dataset,
  "--output-dir", $OutputDir,
  "--cache-dir", $CacheDir,
  "--expected-order-count", [string]$ExpectedOrderCount
)
if ($RetryServiceFailures) {
  $commandArgs += "--retry-service-failures"
}

& $secretLoader -SecretsPath $SecretsPath -CommandArgs $commandArgs
exit $LASTEXITCODE

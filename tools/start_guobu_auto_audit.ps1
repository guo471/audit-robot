param(
  [switch]$Loop,
  [string]$StateDir = "",
  [string]$TempDir = "",
  [int]$PollIntervalSeconds = 600,
  [int]$PendingHeartbeatThreshold = 5,
  [int]$AuditLeaseSeconds = 3600,
  [int]$PageSize = 20,
  [int]$MaxFetchPages = 0,
  [string]$PythonBin = ""
)

$ErrorActionPreference = "Stop"
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBomEncoding
[Console]::OutputEncoding = $Utf8NoBomEncoding

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TokenEnvPath = Join-Path $env:USERPROFILE ".audit_robot\secrets\guobu_auto_audit.env"
$RunWithSecrets = Join-Path $ProjectRoot "tools\run_with_local_vision_secrets.ps1"
$Collector = Join-Path $ProjectRoot "tools\guobu_one_click_collect.js"

function Resolve-PythonBin {
  param([string]$Requested)

  if (-not [string]::IsNullOrWhiteSpace($Requested)) { return $Requested }
  if (-not [string]::IsNullOrWhiteSpace($env:GUOBU_PYTHON_BIN)) { return $env:GUOBU_PYTHON_BIN }
  $localPython314 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"
  if (Test-Path -LiteralPath $localPython314 -PathType Leaf) { return $localPython314 }
  return "python"
}

function Assert-Python314 {
  param([Parameter(Mandatory = $true)][string]$Candidate)

  if ((Split-Path -Parent $Candidate) -and -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
    throw "Python executable not found: $Candidate"
  }
  $version = (& $Candidate --version 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "Python version check failed: $Candidate"
  }
  if (-not $version.StartsWith("Python 3.14")) {
    throw "Python 3.14 is required, got: $version"
  }
}

function Import-EnvFile {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
  foreach ($line in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $trimmed = $line.Trim()
    if ($trimmed.StartsWith("#")) { continue }
    $match = [regex]::Match($trimmed, '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
    if (-not $match.Success) { continue }
    $key = $match.Groups[1].Value
    $value = $match.Groups[2].Value.Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($key, $value, "Process")
  }
}

function Quote-ForPowerShell {
  param([Parameter(Mandatory = $true)][string]$Value)
  return "'" + $Value.Replace("'", "''") + "'"
}

function Quote-ForNativeArgument {
  param([Parameter(Mandatory = $true)][string]$Value)
  return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-TokenBootstrapWithTimeout {
  param(
    [Parameter(Mandatory = $true)][string]$NodeBin,
    [Parameter(Mandatory = $true)][string]$CollectorPath,
    [Parameter(Mandatory = $true)][string]$OutputEnvPath
  )

  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $NodeBin
  $startInfo.Arguments = @(
    (Quote-ForNativeArgument $CollectorPath),
    "--probe-only",
    "--expect-total", "0",
    "--save-token-env",
    (Quote-ForNativeArgument $OutputEnvPath)
  ) -join " "
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = [System.Diagnostics.Process]::Start($startInfo)
  try {
    Wait-Process -Timeout 30 -Id $process.Id -ErrorAction Stop
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
      Write-Warning ("Token bootstrap failed with exit code " + $process.ExitCode + ".")
    }
    return $process.ExitCode
  } catch {
    try { $process.Kill() } catch {}
    Write-Error "Token bootstrap timed out after 30 seconds."
    return 124
  } finally {
    $process.Dispose()
  }
}

Set-Location -LiteralPath $ProjectRoot
$PythonBin = Resolve-PythonBin -Requested $PythonBin
Assert-Python314 -Candidate $PythonBin

$tokenBootstrapExitCode = Invoke-TokenBootstrapWithTimeout -NodeBin "node" -CollectorPath $Collector -OutputEnvPath $TokenEnvPath
if ($tokenBootstrapExitCode -ne 0) {
  if (-not (Test-Path -LiteralPath $TokenEnvPath -PathType Leaf)) {
    Write-Error "Cached token env is missing after token bootstrap failure."
    exit $tokenBootstrapExitCode
  }
  Write-Warning "Token bootstrap failed; falling back to cached token env."
}

Import-EnvFile -Path (Join-Path $ProjectRoot ".env")
Import-EnvFile -Path $TokenEnvPath

if ([string]::IsNullOrWhiteSpace($env:GUOBU_COLLECTOR_BASE_URL)) {
  $env:GUOBU_COLLECTOR_BASE_URL = "https://approval.jhddsz.com"
}

if ([string]::IsNullOrWhiteSpace($env:GUOBU_AUTH_TOKEN)) {
  throw "GUOBU_AUTH_TOKEN is missing after token bootstrap."
}
if ([string]::IsNullOrWhiteSpace($env:MACHINE_APPROVAL_AUTH_TOKEN)) {
  throw "MACHINE_APPROVAL_AUTH_TOKEN is missing after token bootstrap."
}

if ([string]::IsNullOrWhiteSpace($env:GUOBU_APPROVAL_BASE_URL) -and
    -not [string]::IsNullOrWhiteSpace($env:GUOBU_COLLECTOR_BASE_URL)) {
  $env:GUOBU_APPROVAL_BASE_URL = $env:GUOBU_COLLECTOR_BASE_URL
}

$env:SN_POLICY_VERSION = "v2"
$env:SN_BARCODE_MODE = "enforce"
$env:DIGITAL_ACTIVATION_EVIDENCE_MODE = "on"
$env:PHOTO_AUTHENTICITY_MODE = "enforce"
$env:PHOTO_AUTHENTICITY_NEW_RULE_ENABLED = "true"
$env:PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED = "false"
$env:PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED = "false"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$auditArgs = @(
  "-m", "tools.guobu_linux_auto_audit",
  "--state-dir", $(if ($StateDir) { $StateDir } else { Join-Path $ProjectRoot "data\audit_state" }),
  "--temp-dir", $(if ($TempDir) { $TempDir } else { Join-Path $env:TEMP "audit_robot_guobu" }),
  "--poll-interval-seconds", [string]$PollIntervalSeconds,
  "--pending-heartbeat-threshold", [string]$PendingHeartbeatThreshold,
  "--audit-lease-seconds", [string]$AuditLeaseSeconds,
  "--page-size", [string]$PageSize,
  "--max-fetch-pages", [string]$MaxFetchPages
)
if (-not $Loop) {
  $auditArgs += "--once"
}

$quotedArgs = @((Quote-ForPowerShell $PythonBin))
foreach ($arg in $auditArgs) {
  $quotedArgs += Quote-ForPowerShell ([string]$arg)
}
$auditCommand = "& " + ($quotedArgs -join " ")

& $RunWithSecrets -Command $auditCommand
exit $LASTEXITCODE

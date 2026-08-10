param(
  [Alias("dry-run")][switch]$DryRun,
  [Alias("run")][switch]$RunMode,
  [Alias("resume")][switch]$ResumeMode,
  [switch]$InternalWorker,
  [string]$ConfigPath = "",
  [string]$ProjectRoot = (Get-Location).Path,
  [Parameter(Mandatory = $false)][string]$TasksDir = "",
  [Parameter(Mandatory = $false)][string]$RunName = "",
  [ValidateSet("qwen3.7-plus")][string]$Model = "qwen3.7-plus",
  [ValidateSet("fast", "hybrid", "v2", "sn_only")][string]$Mode = "hybrid",
  [ValidateSet("v1", "v2")][string]$SnPolicyVersion = "v2",
  [ValidateSet("off", "shadow", "enforce")][string]$SnBarcodeMode = "enforce",
  [ValidateSet("off", "shadow", "enforce")][string]$PhotoAuthenticityMode = "enforce",
  [ValidateSet("true", "false")][string]$PhotoAuthenticityNewRuleEnabled = "true",
  [int]$Workers = 1,
  [switch]$DisableDigitalActivationEvidence,
  [switch]$SkipTimeoutRerun
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBomEncoding
[Console]::InputEncoding = $Utf8NoBomEncoding
[Console]::OutputEncoding = $Utf8NoBomEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$FixedPython = "C:\Users\guoru\AppData\Local\Programs\Python\Python314\python.exe"

function Write-JsonFile {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  $json = $Value | ConvertTo-Json -Depth 30
  [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $Utf8NoBomEncoding)
}

function Read-JsonFile {
  param([Parameter(Mandatory = $true)][string]$Path)
  [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}

function Quote-CommandArg {
  param([Parameter(Mandatory = $true)][string]$Value)
  "'" + ($Value -replace "'", "''") + "'"
}

function Quote-ProcessArg {
  param([Parameter(Mandatory = $true)][string]$Value)
  '"' + ($Value -replace '"', '\"') + '"'
}

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
      -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") })
    } finally {
      $stream.Dispose()
    }
  } finally {
    $sha.Dispose()
  }
}

function Get-TaskSetHash {
  param([Parameter(Mandatory = $true)][string]$TasksPath)
  $lines = New-Object System.Collections.Generic.List[string]
  Get-ChildItem -LiteralPath $TasksPath -Filter "*.json" -File |
    Sort-Object Name |
    ForEach-Object {
      $lines.Add(($_.Name + "`t" + (Get-Sha256 -Path $_.FullName)))
    }
  $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") })
  } finally {
    $sha.Dispose()
  }
}

function Resolve-ExistingPath {
  param(
    [Parameter(Mandatory = $true)][string]$Base,
    [Parameter(Mandatory = $true)][string]$Value,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $candidate = if ([System.IO.Path]::IsPathRooted($Value)) { $Value } else { Join-Path $Base $Value }
  if (-not (Test-Path -LiteralPath $candidate)) {
    throw "$Label not found: $candidate"
  }
  (Resolve-Path -LiteralPath $candidate).Path
}

function Assert-ValidRunName {
  param([Parameter(Mandatory = $true)][string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "RunName is required."
  }
  if ($Value -notmatch '^[A-Za-z0-9_-]+$') {
    throw "RunName may only contain ASCII letters, numbers, underscore, and hyphen: $Value"
  }
}

function Assert-NoRunCollision {
  param(
    [Parameter(Mandatory = $true)]$Config,
    [switch]$AllowResume
  )
  $paths = @(
    $Config.firstOutDir,
    $Config.firstCacheDir,
    $Config.secondOutDir,
    $Config.secondCacheDir,
    $Config.retryTasksDir,
    $Config.retrySelectionJson,
    $Config.combinedXlsx,
    $Config.combinedJson,
    $Config.completeMarker,
    $Config.launcherManifest
  )
  $existing = @($paths | Where-Object { Test-Path -LiteralPath $_ })
  if ($existing.Count -gt 0 -and -not $AllowResume) {
    throw "RunName '$($Config.runName)' already exists. Use a new RunName or --resume."
  }
}

function Assert-ShortPath {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label
  )
  $full = [System.IO.Path]::GetFullPath($Path)
  if ($full.Length -gt 180) {
    throw "$Label is too long before model calls: $full"
  }
}

function New-LauncherConfig {
  param(
    [Parameter(Mandatory = $true)][string]$EffectiveMode
  )
  $projectPath = Resolve-ExistingPath -Base (Get-Location).Path -Value $ProjectRoot -Label "ProjectRoot"
  if ([string]::IsNullOrWhiteSpace($TasksDir)) {
    throw "TasksDir is required."
  }
  $tasksPath = Resolve-ExistingPath -Base $projectPath -Value $TasksDir -Label "TasksDir"
  Assert-ValidRunName -Value $RunName
  if ($Workers -lt 1) { throw "Workers must be at least 1." }

  $taskCount = (Get-ChildItem -LiteralPath $tasksPath -Filter "*.json" -File | Measure-Object).Count
  if ($taskCount -lt 1) { throw "No task JSON files found in TasksDir: $tasksPath" }

  $reportRoot = Join-Path $projectPath "reports\model_audit"
  $tempBaseName = "gar_" + $RunName
  if ($tempBaseName.Length -gt 40) {
    $tempBaseName = $tempBaseName.Substring(0, 40)
  }
  $shortRoot = Join-Path ([System.IO.Path]::GetTempPath()) $tempBaseName
  $cacheRoot = Join-Path $shortRoot "cache"
  $tempRoot = Join-Path $shortRoot "tmp"
  $logRoot = Join-Path $shortRoot "logs"

  Assert-ShortPath -Path $shortRoot -Label "Temp audit root"
  Assert-ShortPath -Path $cacheRoot -Label "CacheRoot"
  Assert-ShortPath -Path $tempRoot -Label "TempRoot"
  Assert-ShortPath -Path $logRoot -Label "LogRoot"

  $firstOut = Join-Path $reportRoot ($RunName + "_first")
  $firstCache = Join-Path $cacheRoot ("cache_" + $RunName + "_first")
  $secondOut = Join-Path $reportRoot ($RunName + "_network_rerun")
  $secondCache = Join-Path $cacheRoot ("cache_" + $RunName + "_network_rerun")
  $retryTasks = Join-Path $tempRoot ($RunName + "_network_retry_tasks")
  $retrySelection = Join-Path $tempRoot ($RunName + "_network_retry_selection.json")
  $combinedXlsx = Join-Path $reportRoot ($RunName + "_combined.xlsx")
  $combinedJson = Join-Path $reportRoot ($RunName + "_combined.json")
  $launcherManifest = Join-Path $reportRoot ($RunName + "_launcher_manifest.json")
  $completeMarker = Join-Path $reportRoot ($RunName + ".complete")

  [ordered]@{
    mode = $EffectiveMode
    projectRoot = $projectPath
    tasksDir = $tasksPath
    taskCount = $taskCount
    taskSetHash = Get-TaskSetHash -TasksPath $tasksPath
    runName = $RunName
    model = $Model
    auditMode = $Mode
    snPolicyVersion = $SnPolicyVersion
    snBarcodeMode = $SnBarcodeMode
    photoAuthenticityMode = $PhotoAuthenticityMode
    photoAuthenticityNewRuleEnabled = ($PhotoAuthenticityNewRuleEnabled -eq "true")
    workers = $Workers
    disableDigitalActivationEvidence = [bool]$DisableDigitalActivationEvidence
    skipTimeoutRerun = [bool]$SkipTimeoutRerun
    pythonPath = $FixedPython
    reportRoot = $reportRoot
    shortRoot = $shortRoot
    cacheRoot = $cacheRoot
    tempRoot = $tempRoot
    logRoot = $logRoot
    stdout = Join-Path $logRoot "audit_stdout.log"
    stderr = Join-Path $logRoot "audit_stderr.log"
    firstOutDir = $firstOut
    firstCacheDir = $firstCache
    secondOutDir = $secondOut
    secondCacheDir = $secondCache
    retryTasksDir = $retryTasks
    retrySelectionJson = $retrySelection
    combinedXlsx = $combinedXlsx
    combinedJson = $combinedJson
    launcherManifest = $launcherManifest
    workerConfig = Join-Path $shortRoot "worker_config.json"
    completeMarker = $completeMarker
    gitCommit = (& git -C $projectPath rev-parse HEAD 2>$null)
    startedAtUtc = [DateTime]::UtcNow.ToString("o")
  }
}

function Assert-PythonRuntime {
  param([Parameter(Mandatory = $true)]$Config)
  if ($Config.pythonPath -ne $FixedPython) {
    throw "Python path is not the fixed local Python 3.14 path: $($Config.pythonPath)"
  }
  if (-not (Test-Path -LiteralPath $Config.pythonPath -PathType Leaf)) {
    throw "Fixed Python 3.14 not found: $($Config.pythonPath)"
  }
  $preflight = @'
import json
import platform
import sys

modules = 'cv2,joblib,numpy,PIL,zxingcpp'.split(',')
loaded = {}
for name in modules:
    try:
        mod = __import__(name)
    except Exception as exc:
        raise SystemExit('{} import failed: {}: {}'.format(name, type(exc).__name__, exc))
    loaded[name] = getattr(mod, '__version__', '')

print(json.dumps({
    'executable': sys.executable,
    'version': platform.python_version(),
    'modules': loaded,
}, ensure_ascii=False))
'@
  $output = & $Config.pythonPath -X utf8 -c $preflight 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Python dependency preflight failed: $($output -join [Environment]::NewLine)"
  }
  $info = ($output | Select-Object -Last 1) | ConvertFrom-Json
  if (-not ([string]$info.version).StartsWith("3.14.")) {
    throw "Python version must be 3.14.x, got $($info.version)"
  }
  $info
}

function New-BatchCommand {
  param(
    [Parameter(Mandatory = $true)]$Config,
    [switch]$PlanOnly,
    [switch]$ResumeRun
  )
  $batchScript = Join-Path $Config.projectRoot "tools\run_guobu_audit_batch.ps1"
  $parts = @(
    "&", (Quote-CommandArg $batchScript),
    "-ProjectRoot", (Quote-CommandArg $Config.projectRoot),
    "-TasksDir", (Quote-CommandArg $Config.tasksDir),
    "-RunName", (Quote-CommandArg $Config.runName),
    "-PythonExe", (Quote-CommandArg $Config.pythonPath),
    "-CacheRoot", (Quote-CommandArg $Config.cacheRoot),
    "-TempRoot", (Quote-CommandArg $Config.tempRoot),
    "-Model", (Quote-CommandArg $Config.model),
    "-Mode", (Quote-CommandArg $Config.auditMode),
    "-SnPolicyVersion", (Quote-CommandArg $Config.snPolicyVersion),
    "-SnBarcodeMode", (Quote-CommandArg $Config.snBarcodeMode),
    "-PhotoAuthenticityMode", (Quote-CommandArg $Config.photoAuthenticityMode),
    "-PhotoAuthenticityNewRuleEnabled", (Quote-CommandArg ($(if ($Config.photoAuthenticityNewRuleEnabled) { "true" } else { "false" }))),
    "-Workers", (Quote-CommandArg ([string]$Config.workers))
  )
  if ($Config.disableDigitalActivationEvidence) { $parts += "-DisableDigitalActivationEvidence" }
  if ($Config.skipTimeoutRerun) { $parts += "-SkipTimeoutRerun" }
  if ($PlanOnly) { $parts += "-PlanOnly" }
  if ($ResumeRun) { $parts += "-Resume" }
  $parts -join " "
}

function Invoke-SecretWrappedCommand {
  param(
    [Parameter(Mandatory = $true)]$Config,
    [Parameter(Mandatory = $true)][string]$Command
  )
  $wrapper = Join-Path $Config.projectRoot "tools\run_with_local_vision_secrets.ps1"
  if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) {
    throw "Local vision secret wrapper not found: $wrapper"
  }
  & $wrapper -Command $Command
}

function Write-LauncherManifest {
  param(
    [Parameter(Mandatory = $true)]$Config,
    [Parameter(Mandatory = $true)]$PythonInfo,
    [Parameter(Mandatory = $false)]$BatchPlan
  )
  New-Item -ItemType Directory -Force -Path $Config.reportRoot, $Config.shortRoot, $Config.cacheRoot, $Config.tempRoot, $Config.logRoot | Out-Null
  $manifest = [ordered]@{
    launcher = "tools/start_guobu_audit.ps1"
    mode = $Config.mode
    runName = $Config.runName
    taskCount = $Config.taskCount
    taskSetHash = $Config.taskSetHash
    pythonPath = $Config.pythonPath
    pythonVersion = $PythonInfo.version
    dependencyVersions = $PythonInfo.modules
    model = $Config.model
    auditMode = $Config.auditMode
    snPolicyVersion = $Config.snPolicyVersion
    snBarcodeMode = $Config.snBarcodeMode
    photoAuthenticityMode = $Config.photoAuthenticityMode
    photoAuthenticityNewRuleEnabled = $Config.photoAuthenticityNewRuleEnabled
    workers = $Config.workers
    cacheRoot = $Config.cacheRoot
    tempRoot = $Config.tempRoot
    logRoot = $Config.logRoot
    stdout = $Config.stdout
    stderr = $Config.stderr
    firstOutDir = $Config.firstOutDir
    secondOutDir = $Config.secondOutDir
    combinedXlsx = $Config.combinedXlsx
    combinedJson = $Config.combinedJson
    completeMarker = $Config.completeMarker
    gitCommit = $Config.gitCommit
    startedAtUtc = $Config.startedAtUtc
    batchPlan = $BatchPlan
  }
  Write-JsonFile -Path $Config.launcherManifest -Value $manifest
}

function Assert-ResumeManifestMatches {
  param([Parameter(Mandatory = $true)]$Config)
  if (-not (Test-Path -LiteralPath $Config.launcherManifest -PathType Leaf)) {
    throw "RunName '$($Config.runName)' cannot resume because launcher manifest is missing: $($Config.launcherManifest)"
  }
  $firstManifest = Join-Path $Config.firstOutDir "run_manifest.json"
  if (-not (Test-Path -LiteralPath $firstManifest -PathType Leaf)) {
    throw "RunName '$($Config.runName)' cannot resume because the first run manifest is missing: $firstManifest"
  }
  $previous = Read-JsonFile -Path $Config.launcherManifest
  if ($previous.taskSetHash -ne $Config.taskSetHash) {
    throw "RunName '$($Config.runName)' cannot resume because task set hash does not match manifest."
  }
  foreach ($name in @("model", "auditMode", "snPolicyVersion", "snBarcodeMode", "photoAuthenticityMode", "photoAuthenticityNewRuleEnabled", "workers")) {
    if ([string]$previous.$name -ne [string]$Config.$name) {
      throw "RunName '$($Config.runName)' cannot resume because $name does not match manifest."
    }
  }
}

function Invoke-InternalWorker {
  if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    throw "Internal worker requires ConfigPath."
  }
  $config = Read-JsonFile -Path $ConfigPath
  $command = New-BatchCommand -Config $config -ResumeRun:($config.mode -eq "resume")
  try {
    Push-Location $config.projectRoot
    try {
      Invoke-SecretWrappedCommand -Config $config -Command $command
      if ($LASTEXITCODE -ne 0) {
        throw "Audit command failed with exit code $LASTEXITCODE"
      }
      if (-not (Test-Path -LiteralPath $config.combinedJson -PathType Leaf)) {
        throw "Combined JSON was not created: $($config.combinedJson)"
      }
      [ordered]@{
        completedAtUtc = [DateTime]::UtcNow.ToString("o")
        runName = $config.runName
        combinedJson = $config.combinedJson
        combinedXlsx = $config.combinedXlsx
        launcherManifest = $config.launcherManifest
      } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $config.completeMarker -Encoding UTF8
      exit 0
    } finally {
      Pop-Location
    }
  } catch {
    Write-Error $_
    exit 1
  }
}

if ($InternalWorker) {
  Invoke-InternalWorker
}

$selectedModes = @($DryRun.IsPresent, $RunMode.IsPresent, $ResumeMode.IsPresent) | Where-Object { $_ }
if ($selectedModes.Count -ne 1) {
  throw "Choose exactly one mode: --dry-run, --run, or --resume."
}
$effectiveMode = if ($DryRun) { "dry-run" } elseif ($RunMode) { "run" } else { "resume" }

$config = New-LauncherConfig -EffectiveMode $effectiveMode
$pythonInfo = Assert-PythonRuntime -Config $config
Assert-NoRunCollision -Config $config -AllowResume:$ResumeMode
if ($ResumeMode) {
  Assert-ResumeManifestMatches -Config $config
}

$planCommand = New-BatchCommand -Config $config -PlanOnly -ResumeRun:$false
Push-Location $config.projectRoot
try {
  $planText = Invoke-SecretWrappedCommand -Config $config -Command $planCommand
} finally {
  Pop-Location
}
if ($LASTEXITCODE -ne 0) {
  throw "Dry preflight through local secret wrapper failed."
}
$batchPlan = ($planText -join [Environment]::NewLine) | ConvertFrom-Json
if ([int]$batchPlan.taskCount -ne [int]$config.taskCount) {
  throw "Preflight task count mismatch: launcher=$($config.taskCount), batch=$($batchPlan.taskCount)"
}
if (-not [bool]$batchPlan.snBarcodeRuntimeAvailable -and $config.snBarcodeMode -ne "off") {
  throw "SN barcode runtime is unavailable; install zxing-cpp before model calls."
}
if ([string]$batchPlan.pythonPath -ne $config.pythonPath) {
  throw "Batch preflight Python path mismatch: $($batchPlan.pythonPath)"
}

Write-LauncherManifest -Config $config -PythonInfo $pythonInfo -BatchPlan $batchPlan
Write-JsonFile -Path $config.workerConfig -Value $config

if ($DryRun) {
  [ordered]@{
    mode = "dry-run"
    wouldStartModel = $false
    taskCount = $config.taskCount
    runName = $config.runName
    pythonPath = $config.pythonPath
    snPolicyVersion = $config.snPolicyVersion
    snBarcodeMode = $config.snBarcodeMode
    photoAuthenticityMode = $config.photoAuthenticityMode
    photoAuthenticityNewRuleEnabled = $config.photoAuthenticityNewRuleEnabled
    cacheRoot = $config.cacheRoot
    tempRoot = $config.tempRoot
    logRoot = $config.logRoot
    launcherManifest = $config.launcherManifest
    completeMarker = $config.completeMarker
    combinedJson = $config.combinedJson
    combinedXlsx = $config.combinedXlsx
  } | ConvertTo-Json -Depth 10
  exit 0
}

$launcher = Join-Path $config.projectRoot "tools\start_guobu_audit.ps1"
$workerArguments = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  (Quote-ProcessArg $launcher),
  "-InternalWorker",
  "-ConfigPath",
  (Quote-ProcessArg $config.workerConfig)
) -join " "
$process = Start-Process -FilePath "powershell.exe" `
  -ArgumentList $workerArguments `
  -WorkingDirectory $config.projectRoot `
  -RedirectStandardOutput $config.stdout `
  -RedirectStandardError $config.stderr `
  -WindowStyle Hidden `
  -PassThru

[ordered]@{
  mode = $effectiveMode
  pid = $process.Id
  taskCount = $config.taskCount
  runName = $config.runName
  stdout = $config.stdout
  stderr = $config.stderr
  launcherManifest = $config.launcherManifest
  completeMarker = $config.completeMarker
  firstOutDir = $config.firstOutDir
  secondOutDir = $config.secondOutDir
  combinedJson = $config.combinedJson
  combinedXlsx = $config.combinedXlsx
} | ConvertTo-Json -Depth 10

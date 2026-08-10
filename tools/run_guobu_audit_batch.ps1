
param(
  [string]$ProjectRoot = (Get-Location).Path,
  [string]$PythonExe = "",
  [string]$CacheRoot = "",
  [string]$TempRoot = "",
  [Parameter(Mandatory = $true)][string]$TasksDir,
  [string]$RunName = "",
  [ValidateSet("qwen3.7-plus")][string]$Model = "qwen3.7-plus",
  [ValidateSet("fast", "hybrid", "v2", "sn_only")][string]$Mode = "hybrid",
  [ValidateSet("v1", "v2")][string]$SnPolicyVersion = "v2",
  [ValidateSet("off", "shadow", "enforce")][string]$SnBarcodeMode = "enforce",
  [ValidateSet("", "off", "shadow", "enforce")][string]$PhotoAuthenticityMode = "",
  [ValidateSet("", "true", "false")][string]$PhotoAuthenticityNewRuleEnabled = "",
  [int]$Workers = 1,
  [switch]$EnableTargetedSnReview,
  [switch]$EnableSnCharReview,
  [switch]$EnableSnCharReviewV2,
  [switch]$EnableSnLabelAuthReview,
  [switch]$EnablePhotoAuthEdgeMapping,
  [switch]$EnablePhotoAuthenticityLocalTreeConfirmation,
  [switch]$DisablePhotoAuthenticityNewRules,
  [switch]$DisablePhotoAuthenticityLocalTree,
  [switch]$DisableDigitalActivationEvidence,
  [switch]$SkipTimeoutRerun,
  [switch]$Resume,
  [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Utf8NoBomEncoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
$OutputEncoding = $Utf8NoBomEncoding
[Console]::InputEncoding = $Utf8NoBomEncoding
[Console]::OutputEncoding = $Utf8NoBomEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Write-Utf8Json {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  $json = $Value | ConvertTo-Json -Depth 30
  [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $Utf8NoBomEncoding)
}

if ([string]::IsNullOrWhiteSpace($RunName)) {
  $RunName = "guobu_audit_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
$RunName = $RunName -replace '[^A-Za-z0-9_-]', '_'

$projectPath = (Resolve-Path -LiteralPath $ProjectRoot).Path
$modelScript = Join-Path $projectPath "tools\run_guobu_model_audit_v2.py"
$businessGenerator = Join-Path $projectPath "tools\guobu_audit_report.py"
$selector = Join-Path $projectPath "tools\select_guobu_tasks.py"
$contractValidator = Join-Path $projectPath "tools\guobu_audit_contract.py"
$tasksPath = if ([System.IO.Path]::IsPathRooted($TasksDir)) {
  (Resolve-Path -LiteralPath $TasksDir).Path
} else {
  (Resolve-Path -LiteralPath (Join-Path $projectPath $TasksDir)).Path
}
if (-not (Test-Path -LiteralPath $modelScript)) { throw "Missing audit entry point: $modelScript" }
if (-not (Test-Path -LiteralPath $selector)) { throw "Missing task selector: $selector" }
if (-not (Test-Path -LiteralPath $contractValidator)) { throw "Missing audit contract validator: $contractValidator" }
if (-not (Test-Path -LiteralPath $businessGenerator)) { throw "Missing business report generator: $businessGenerator" }

$taskCount = (Get-ChildItem -LiteralPath $tasksPath -Filter "*.json" | Measure-Object).Count
if ($taskCount -le 0) { throw "No task JSON files found in $tasksPath" }

$reportRoot = Join-Path $projectPath "reports\model_audit"
$tempRoot = if ([string]::IsNullOrWhiteSpace($TempRoot)) {
  Join-Path $projectPath "temp"
} elseif ([System.IO.Path]::IsPathRooted($TempRoot)) {
  [System.IO.Path]::GetFullPath($TempRoot)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $projectPath $TempRoot))
}
$cacheRoot = if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
  $reportRoot
} elseif ([System.IO.Path]::IsPathRooted($CacheRoot)) {
  [System.IO.Path]::GetFullPath($CacheRoot)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $projectPath $CacheRoot))
}
if (-not [string]::IsNullOrWhiteSpace($CacheRoot) -and $cacheRoot.Length -gt 180) {
  throw "CacheRoot is too long for safe Windows model-cache writes before model calls: $cacheRoot"
}
if (-not [string]::IsNullOrWhiteSpace($TempRoot) -and $tempRoot.Length -gt 180) {
  throw "TempRoot is too long for safe Windows retry/temp writes before model calls: $tempRoot"
}
$firstOut = Join-Path $reportRoot ($RunName + "_first")
$firstCache = Join-Path $cacheRoot ("cache_" + $RunName + "_first")
$secondOut = Join-Path $reportRoot ($RunName + "_network_rerun")
$secondCache = Join-Path $cacheRoot ("cache_" + $RunName + "_network_rerun")
$retryTasks = Join-Path $tempRoot ($RunName + "_network_retry_tasks")
$retrySelectionSummary = Join-Path $tempRoot ($RunName + "_network_retry_selection.json")
$combinedXlsx = Join-Path $reportRoot ($RunName + "_combined.xlsx")
$combinedJson = Join-Path $reportRoot ($RunName + "_combined.json")
$firstManifest = Join-Path $firstOut "run_manifest.json"
$secondManifest = Join-Path $secondOut "run_manifest.json"
$runReservation = Join-Path $reportRoot ($RunName + "_reservation.lock")
if ($EnableSnCharReview -and $EnableSnCharReviewV2) {
  throw "EnableSnCharReview and EnableSnCharReviewV2 cannot be enabled together"
}
$snCharReviewMode = if ($EnableSnCharReviewV2) {
  "v2"
} elseif ($EnableSnCharReview) {
  "on"
} else {
  "off"
}
if ($Mode -eq "sn_only" -and $snCharReviewMode -ne "off") {
  throw "SN character review plugins are not applied in sn_only mode"
}
$photoAuthenticityMode = if ($Mode -ne "hybrid") {
  "off"
} elseif (-not [string]::IsNullOrWhiteSpace($PhotoAuthenticityMode)) {
  $PhotoAuthenticityMode
} elseif ([string]::IsNullOrWhiteSpace($env:PHOTO_AUTHENTICITY_MODE)) {
  "enforce"
} else {
  $configuredMode = $env:PHOTO_AUTHENTICITY_MODE.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("off", "shadow", "enforce")) {
    throw "PHOTO_AUTHENTICITY_MODE must be off, shadow, or enforce"
  }
  $configuredMode
}
$photoAuthenticityNewRuleEnabled = if ($photoAuthenticityMode -eq "off" -or $DisablePhotoAuthenticityNewRules) {
  "false"
} elseif (-not [string]::IsNullOrWhiteSpace($PhotoAuthenticityNewRuleEnabled)) {
  $PhotoAuthenticityNewRuleEnabled
} elseif ([string]::IsNullOrWhiteSpace($env:PHOTO_AUTHENTICITY_NEW_RULE_ENABLED)) {
  "true"
} else {
  $configuredMode = $env:PHOTO_AUTHENTICITY_NEW_RULE_ENABLED.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("true", "false")) {
    throw "PHOTO_AUTHENTICITY_NEW_RULE_ENABLED must be true or false"
  }
  $configuredMode
}
$snLabelAuthReviewMode = if ($photoAuthenticityNewRuleEnabled -eq "false") {
  "off"
} elseif ($EnableSnLabelAuthReview) {
  "on"
} elseif ([string]::IsNullOrWhiteSpace($env:SN_LABEL_AUTH_REVIEW_MODE)) {
  "off"
} else {
  $configuredMode = $env:SN_LABEL_AUTH_REVIEW_MODE.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("on", "off")) {
    throw "SN_LABEL_AUTH_REVIEW_MODE must be on or off"
  }
  $configuredMode
}
$photoAuthEdgeMappingMode = if ($photoAuthenticityNewRuleEnabled -eq "false") {
  "off"
} elseif ($EnablePhotoAuthEdgeMapping) {
  "on"
} elseif ([string]::IsNullOrWhiteSpace($env:PHOTO_AUTH_EDGE_MAPPING_MODE)) {
  "off"
} else {
  $configuredMode = $env:PHOTO_AUTH_EDGE_MAPPING_MODE.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("on", "off")) {
    throw "PHOTO_AUTH_EDGE_MAPPING_MODE must be on or off"
  }
  $configuredMode
}
if ($Mode -ne "hybrid" -and $photoAuthEdgeMappingMode -ne "off") {
  throw "Photo authenticity edge mapping plugin is only applied in hybrid mode"
}
$digitalActivationEvidenceMode = if ($DisableDigitalActivationEvidence) {
  "off"
} elseif ([string]::IsNullOrWhiteSpace($env:DIGITAL_ACTIVATION_EVIDENCE_MODE)) {
  "on"
} else {
  $configuredMode = $env:DIGITAL_ACTIVATION_EVIDENCE_MODE.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("on", "off")) {
    throw "DIGITAL_ACTIVATION_EVIDENCE_MODE must be on or off"
  }
  $configuredMode
}
$photoAuthenticityLocalTreeEnabled = if ($photoAuthenticityNewRuleEnabled -eq "false") {
  "false"
} elseif ($DisablePhotoAuthenticityLocalTree) {
  "false"
} elseif ([string]::IsNullOrWhiteSpace($env:PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED)) {
  "false"
} else {
  $configuredMode = $env:PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("true", "false")) {
    throw "PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED must be true or false"
  }
  $configuredMode
}
$photoAuthenticityLocalTreeConfirmationEnabled = if ($photoAuthenticityNewRuleEnabled -eq "false") {
  "false"
} elseif ($EnablePhotoAuthenticityLocalTreeConfirmation) {
  "true"
} elseif ([string]::IsNullOrWhiteSpace($env:PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED)) {
  "false"
} else {
  $configuredMode = $env:PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED.Trim().ToLowerInvariant()
  if ($configuredMode -notin @("true", "false")) {
    throw "PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED must be true or false"
  }
  $configuredMode
}
if ($Workers -lt 1) { throw "Workers must be at least 1" }

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $PythonExe = Join-Path $projectPath ".venv-photo-auth\Scripts\python.exe"
}
$pythonCandidate = if ([System.IO.Path]::IsPathRooted($PythonExe)) {
  $PythonExe
} else {
  Join-Path $projectPath $PythonExe
}
if (-not (Test-Path -LiteralPath $pythonCandidate -PathType Leaf)) {
  throw "PythonExe not found: $pythonCandidate"
}
$pythonPath = (Resolve-Path -LiteralPath $pythonCandidate).Path
$requireCv2Preflight = $photoAuthenticityNewRuleEnabled -eq "true"
$requireSnBarcodePreflight = $SnBarcodeMode -ne "off"
$env:GUOBU_REQUIRE_CV2_PREFLIGHT = if ($requireCv2Preflight) { "1" } else { "0" }
$env:GUOBU_REQUIRE_SN_BARCODE_PREFLIGHT = if ($requireSnBarcodePreflight) { "1" } else { "0" }
$pythonPreflightScript = @'
import json
import os
import platform
import sys

cv2_version = ''
zxingcpp_available = False
zxingcpp_version = ''
if os.environ.get('GUOBU_REQUIRE_CV2_PREFLIGHT') == '1':
    try:
        import cv2
    except Exception as exc:
        raise SystemExit('cv2 preflight failed: {}: {}'.format(type(exc).__name__, exc))
    cv2_version = getattr(cv2, '__version__', '')
if os.environ.get('GUOBU_REQUIRE_SN_BARCODE_PREFLIGHT') == '1':
    try:
        import zxingcpp
    except Exception as exc:
        raise SystemExit('zxingcpp preflight failed: {}: {}'.format(type(exc).__name__, exc))
    zxingcpp_available = True
    zxingcpp_version = getattr(zxingcpp, '__version__', '')

print(json.dumps({
    'path': sys.executable,
    'version': platform.python_version(),
    'cv2_version': cv2_version,
    'zxingcpp_available': zxingcpp_available,
    'zxingcpp_version': zxingcpp_version,
}, ensure_ascii=False))
'@
$pythonPreflightOutput = & $pythonPath -X utf8 -c $pythonPreflightScript 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Python dependency preflight failed for ${pythonPath}: $($pythonPreflightOutput -join [Environment]::NewLine)"
}
$pythonInfo = ($pythonPreflightOutput | Select-Object -Last 1) | ConvertFrom-Json

$promptPaths = [ordered]@{
  "sn_similar_char_review.txt" = Join-Path $projectPath "prompts\sn_similar_char_review.txt"
  "sn_similar_char_review_v2.txt" = Join-Path $projectPath "prompts\sn_similar_char_review_v2.txt"
  "digital_activation_evidence_review.txt" = Join-Path $projectPath "prompts\digital_activation_evidence_review.txt"
}
if ($photoAuthenticityNewRuleEnabled -eq "true") {
  $promptPaths["sn_label_authenticity_review.txt"] = Join-Path $projectPath "prompts\sn_label_authenticity_review.txt"
}
if ($photoAuthEdgeMappingMode -eq "on") {
  $promptPaths["photo_auth_edge_mapping_review.txt"] = Join-Path $projectPath "prompts\photo_auth_edge_mapping_review.txt"
}

$runtimePaths = [ordered]@{
  "tools/run_guobu_audit_batch.ps1" = Join-Path $projectPath "tools\run_guobu_audit_batch.ps1"
  "tools/run_guobu_model_audit_v2.py" = $modelScript
  "tools/guobu_sn_policy_v2.py" = Join-Path $projectPath "tools\guobu_sn_policy_v2.py"
  "tools/guobu_sn_barcode.py" = Join-Path $projectPath "tools\guobu_sn_barcode.py"
  "tools/guobu_audit_contract.py" = $contractValidator
  "tools/guobu_audit_report.py" = $businessGenerator
  "tools/select_guobu_tasks.py" = $selector
  "tools/photo_authenticity_mainline.py" = Join-Path $projectPath "tools\photo_authenticity_mainline.py"
  "modules/__init__.py" = Join-Path $projectPath "modules\__init__.py"
  "modules/address_checker.py" = Join-Path $projectPath "modules\address_checker.py"
  "modules/audit_models.py" = Join-Path $projectPath "modules\audit_models.py"
  "modules/audit_runner.py" = Join-Path $projectPath "modules\audit_runner.py"
  "modules/category_classifier.py" = Join-Path $projectPath "modules\category_classifier.py"
  "modules/code_extractor.py" = Join-Path $projectPath "modules\code_extractor.py"
  "modules/id_card_parser.py" = Join-Path $projectPath "modules\id_card_parser.py"
  "modules/image_forensics.py" = Join-Path $projectPath "modules\image_forensics.py"
  "modules/image_role.py" = Join-Path $projectPath "modules\image_role.py"
  "modules/ocr_engine.py" = Join-Path $projectPath "modules\ocr_engine.py"
}
if ($photoAuthenticityLocalTreeEnabled -eq "true") {
  $runtimePaths["tools/non_real_local_features.py"] = Join-Path $projectPath "tools\non_real_local_features.py"
  $runtimePaths["tools/black_edge_shadow_detector.py"] = Join-Path $projectPath "tools\black_edge_shadow_detector.py"
} elseif ($photoAuthEdgeMappingMode -eq "on") {
  $runtimePaths["tools/black_edge_shadow_detector.py"] = Join-Path $projectPath "tools\black_edge_shadow_detector.py"
}
function Get-Sha256Map {
  param(
    [Parameter(Mandatory = $true)]$Paths
  )
  $sha256 = [ordered]@{}
  foreach ($entry in $Paths.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value -PathType Leaf)) {
      throw "Missing required runtime/prompt file: $($entry.Value)"
    }
    $sha256[$entry.Key] = (Get-FileHash -LiteralPath $entry.Value -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  return $sha256
}

function Get-GitCommit {
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $gitCommitOutput = & git -C $projectPath rev-parse HEAD 2>$null
  $gitExitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousErrorActionPreference
  if ($gitExitCode -eq 0) {
    $gitCommit = [string]($gitCommitOutput | Select-Object -First 1)
    return $gitCommit.Trim()
  }
  return ""
}

function Get-GitWorktreeDirty {
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $gitStatusOutput = & git -C $projectPath status --porcelain --untracked-files=all 2>$null
  $gitExitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousErrorActionPreference
  if ($gitExitCode -ne 0) {
    return $false
  }
  return @($gitStatusOutput | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }).Count -gt 0
}

function New-RunManifest {
  [ordered]@{
    created_at_utc = [DateTime]::UtcNow.ToString("o")
    run_name = $RunName
    model = $Model
    mode = $Mode
    sn_policy_version = $SnPolicyVersion
    sn_barcode_mode = $SnBarcodeMode
    workers = $Workers
    targeted_sn_review = [bool]$EnableTargetedSnReview
    sn_char_review_mode = $snCharReviewMode
    sn_label_auth_review_mode = $snLabelAuthReviewMode
    photo_auth_edge_mapping_mode = $photoAuthEdgeMappingMode
    digital_activation_evidence_mode = $digitalActivationEvidenceMode
    photo_authenticity_mode = $photoAuthenticityMode
    photo_authenticity_new_rule_enabled = $photoAuthenticityNewRuleEnabled
    photo_authenticity_local_tree_enabled = $photoAuthenticityLocalTreeEnabled
    photo_authenticity_local_tree_confirmation_enabled = $photoAuthenticityLocalTreeConfirmationEnabled
    order_timeout_seconds = 60
    cache_root = $cacheRoot
    temp_root = $tempRoot
    git_commit = (Get-GitCommit)
    python_path = $pythonPath
    python_version = [string]$pythonInfo.version
    cv2_version = [string]$pythonInfo.cv2_version
    git_worktree_dirty = [bool](Get-GitWorktreeDirty)
    runtime_sha256 = (Get-Sha256Map -Paths $runtimePaths)
    prompt_sha256 = (Get-Sha256Map -Paths $promptPaths)
  }
}

$runManifest = New-RunManifest

$plan = [ordered]@{
  projectRoot = $projectPath
  tasksDir = $tasksPath
  taskCount = $taskCount
  runName = $RunName
  model = $Model
  mode = $Mode
  snPolicyVersion = $SnPolicyVersion
  snBarcodeMode = $SnBarcodeMode
  workers = $Workers
  targetedSnReview = [bool]$EnableTargetedSnReview
  snCharReview = $snCharReviewMode -ne "off"
  snCharReviewMode = $snCharReviewMode
  snLabelAuthReview = $snLabelAuthReviewMode -eq "on"
  photoAuthEdgeMapping = $photoAuthEdgeMappingMode -eq "on"
  digitalActivationEvidence = $digitalActivationEvidenceMode -eq "on"
  photoAuthenticityMode = $photoAuthenticityMode
  photoAuthenticityNewRuleEnabled = $photoAuthenticityNewRuleEnabled -eq "true"
  photoAuthenticityLocalTreeEnabled = $photoAuthenticityLocalTreeEnabled -eq "true"
  photoAuthenticityLocalTreeConfirmationEnabled = $photoAuthenticityLocalTreeConfirmationEnabled -eq "true"
  timeoutRerun = -not [bool]$SkipTimeoutRerun
  resume = [bool]$Resume
  pythonPath = $pythonPath
  pythonVersion = [string]$pythonInfo.version
  cv2Version = [string]$pythonInfo.cv2_version
  snBarcodeRuntimeAvailable = [bool]$pythonInfo.zxingcpp_available
  zxingcppVersion = [string]$pythonInfo.zxingcpp_version
  cacheRoot = $cacheRoot
  tempRoot = $tempRoot
  firstCacheDir = $firstCache
  secondCacheDir = $secondCache
  runManifest = $runManifest
  firstManifest = $firstManifest
  secondManifest = $secondManifest
  reportGenerator = $businessGenerator
  firstOutDir = $firstOut
  secondOutDir = $secondOut
  retryTasksDir = $retryTasks
  combinedXlsx = $combinedXlsx
  combinedJson = $combinedJson
}

if ($PlanOnly) {
  $plan | ConvertTo-Json -Depth 10
  exit 0
}

if ([string]::IsNullOrWhiteSpace($env:VISION_API_BASE_URL)) {
  throw "VISION_API_BASE_URL is not set"
}
if ([string]::IsNullOrWhiteSpace($env:VISION_API_KEY)) {
  throw "VISION_API_KEY is not set"
}

function Assert-RunNameUnused {
  param([switch]$IgnoreReservation)
  $runSpecificPaths = @(
    $firstOut,
    $firstCache,
    $secondOut,
    $secondCache,
    $retryTasks,
    $retrySelectionSummary,
    $combinedXlsx,
    $combinedJson
  )
  if (-not $IgnoreReservation) {
    $runSpecificPaths += $runReservation
  }
  $existing = @($runSpecificPaths | Where-Object { Test-Path -LiteralPath $_ })
  if ($existing.Count -gt 0) {
    throw "RunName '$RunName' already has existing output/cache/retry/selection path(s). Use a new RunName."
  }
}

if (-not $Resume) {
  Assert-RunNameUnused
} elseif (-not (Test-Path -LiteralPath $firstManifest -PathType Leaf)) {
  throw "RunName '$RunName' cannot resume because the first run manifest does not exist: $firstManifest"
}

New-Item -ItemType Directory -Force -Path $reportRoot, $tempRoot, $cacheRoot | Out-Null
$reservationStream = $null
$persistentReservationReady = $false
try {
  $reservationStream = [System.IO.File]::Open(
    $runReservation,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
  $reservationText = "pid=$PID created_at_utc=$([DateTime]::UtcNow.ToString('o')) run_name=$RunName" + [Environment]::NewLine
  $reservationBytes = $Utf8NoBomEncoding.GetBytes($reservationText)
  $reservationStream.Write($reservationBytes, 0, $reservationBytes.Length)
  $reservationStream.Flush()
} catch {
  throw "RunName '$RunName' is already reserved by an existing or concurrent run. Use a new RunName."
}
try {
  if (-not $Resume) {
    Assert-RunNameUnused -IgnoreReservation
    New-Item -ItemType Directory -Path $firstOut -ErrorAction Stop | Out-Null
  } else {
    New-Item -ItemType Directory -Force -Path $firstOut | Out-Null
  }
  $persistentReservationReady = $true
} catch {
  throw "RunName '$RunName' is already reserved by an existing or concurrent run. Use a new RunName."
} finally {
  if ($null -ne $reservationStream) {
    $reservationStream.Close()
    $reservationStream.Dispose()
  }
  if ($persistentReservationReady -and (Test-Path -LiteralPath $runReservation)) {
    Remove-Item -LiteralPath $runReservation -Force
  }
}
New-Item -ItemType Directory -Force -Path $firstCache | Out-Null
Write-Utf8Json -Path $firstManifest -Value $runManifest

function Invoke-AuditRun {
  param(
    [string]$InputTasks,
    [string]$OutDir,
    [string]$CacheDir,
    [string]$LogPath
  )

  New-Item -ItemType Directory -Force -Path $OutDir, $CacheDir | Out-Null
  $arguments = @(
    "-u", $modelScript,
    "--tasks-dir", $InputTasks,
    "--out-dir", $OutDir,
    "--cache-dir", $CacheDir,
    "--model", $Model,
    "--mode", $Mode,
    "--sn-policy-version", $SnPolicyVersion,
    "--sn-barcode-mode", $SnBarcodeMode,
    "--workers", [string]$Workers,
    "--sn-char-review-mode", $snCharReviewMode,
    "--sn-label-auth-review-mode", $snLabelAuthReviewMode,
    "--photo-auth-edge-mapping-mode", $photoAuthEdgeMappingMode,
    "--digital-activation-evidence-mode", $digitalActivationEvidenceMode,
    "--photo-authenticity-mode", $photoAuthenticityMode,
    "--photo-authenticity-new-rule-enabled", $photoAuthenticityNewRuleEnabled,
    "--photo-authenticity-local-tree-enabled", $photoAuthenticityLocalTreeEnabled,
    "--photo-authenticity-local-tree-confirmation-enabled", $photoAuthenticityLocalTreeConfirmationEnabled
  )
  if (-not $EnableTargetedSnReview) { $arguments += "--no-targeted-sn-review" }

  & $pythonPath @arguments 2>&1 | Tee-Object -FilePath $LogPath | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { throw "Audit process exited with code $LASTEXITCODE" }
  $jsonl = Get-ChildItem -LiteralPath $OutDir -Filter "*.jsonl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $jsonl) { throw "Audit process did not create JSONL in $OutDir" }
  return $jsonl.FullName
}

Push-Location $projectPath
try {
  $firstLog = Join-Path $firstOut "run_stdout.log"
  $firstJsonl = Invoke-AuditRun -InputTasks $tasksPath -OutDir $firstOut -CacheDir $firstCache -LogPath $firstLog
  & $pythonPath "-u" $contractValidator "--tasks-dir" $tasksPath "--first-jsonl" $firstJsonl | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "First-run completeness validation failed" }

  $secondJsonl = ""
  $retryCount = 0
  if (-not $SkipTimeoutRerun) {
    $selectionSummary = $retrySelectionSummary
    $expectedRetryTasks = Join-Path $tempRoot ($RunName + "_network_retry_tasks")
    $tempRootResolved = [System.IO.Path]::GetFullPath($tempRoot).TrimEnd('\')
    $retryTasksResolved = [System.IO.Path]::GetFullPath($retryTasks)
    if ($retryTasksResolved -ne [System.IO.Path]::GetFullPath($expectedRetryTasks) -or
        -not $retryTasksResolved.StartsWith($tempRootResolved + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Unsafe retry task directory: $retryTasks"
    }
    if (Test-Path -LiteralPath $retryTasks) {
      Remove-Item -LiteralPath $retryTasks -Recurse -Force
    }
    New-Item -ItemType Directory -Path $retryTasks | Out-Null
    $selectionArgs = @(
      "-u",
      $selector,
      "--source-dir", $tasksPath,
      "--out-dir", $retryTasks,
      "--timeout-jsonl", $firstJsonl,
      "--summary-json", $selectionSummary
    )
    $selectionText = & $pythonPath @selectionArgs
    if ($LASTEXITCODE -ne 0) { throw "Network-retry task selection failed" }
    $selection = $selectionText | Select-Object -Last 1 | ConvertFrom-Json
    $retryCount = [int]$selection.selected

    if ($retryCount -gt 0) {
      $retryManifest = New-RunManifest
      $retryManifestJson = $retryManifest | ConvertTo-Json -Depth 30 -Compress
      $previousRetryManifestJson = $env:GUOBU_RETRY_MANIFEST_JSON
      $env:GUOBU_RETRY_MANIFEST_JSON = $retryManifestJson
      try {
        & $pythonPath "-u" $contractValidator "--first-manifest" $firstManifest "--retry-manifest-json-env" "GUOBU_RETRY_MANIFEST_JSON" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Network-retry manifest compatibility validation failed" }
      } finally {
        if ($null -eq $previousRetryManifestJson) {
          Remove-Item Env:\GUOBU_RETRY_MANIFEST_JSON -ErrorAction SilentlyContinue
        } else {
          $env:GUOBU_RETRY_MANIFEST_JSON = $previousRetryManifestJson
        }
      }
      New-Item -ItemType Directory -Force -Path $secondOut, $secondCache | Out-Null
      Write-Utf8Json -Path $secondManifest -Value $retryManifest
      $secondLog = Join-Path $secondOut "run_stdout.log"
      $secondJsonl = Invoke-AuditRun -InputTasks $retryTasks -OutDir $secondOut -CacheDir $secondCache -LogPath $secondLog
    }
  }

  $reportArgs = @(
      "-u",
      $businessGenerator,
      "--first-jsonl", $firstJsonl,
      "--output-xlsx", $combinedXlsx,
      "--output-json", $combinedJson,
      "--overwrite"
    )
  if (-not [string]::IsNullOrWhiteSpace($secondJsonl)) {
    & $pythonPath "-u" $contractValidator "--first-manifest" $firstManifest "--retry-manifest" $secondManifest | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Network-retry manifest compatibility validation failed" }
    $reportArgs += @("--retry-jsonl", $secondJsonl, "--retry-selection-json", $selectionSummary)
  }

  $priceValues = @(
      $env:QWEN_INPUT_PRICE_PER_MILLION,
      $env:QWEN_CACHED_INPUT_PRICE_PER_MILLION,
      $env:QWEN_OUTPUT_PRICE_PER_MILLION
    )
  $parsedPrices = @()
  $pricesValid = $true
  foreach ($value in $priceValues) {
      $parsed = 0.0
      if ([string]::IsNullOrWhiteSpace($value) -or
          -not [double]::TryParse($value, [Globalization.NumberStyles]::Float,
            [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed) -or
          [double]::IsNaN($parsed) -or [double]::IsInfinity($parsed) -or $parsed -lt 0) {
        $pricesValid = $false
        break
      }
      $parsedPrices += $parsed
  }
  if ($pricesValid) {
    $reportArgs += @(
        "--input-price-per-million", $parsedPrices[0].ToString("R", [Globalization.CultureInfo]::InvariantCulture),
        "--cached-input-price-per-million", $parsedPrices[1].ToString("R", [Globalization.CultureInfo]::InvariantCulture),
        "--output-price-per-million", $parsedPrices[2].ToString("R", [Globalization.CultureInfo]::InvariantCulture)
    )
  }
  $mergeOutput = & $pythonPath @reportArgs
  if ($LASTEXITCODE -ne 0) { throw "Combined report generation failed" }
  $combined = [System.IO.File]::ReadAllText($combinedJson, $Utf8NoBomEncoding) | ConvertFrom-Json

  [ordered]@{
    taskCount = $taskCount
    networkRetryCount = $retryCount
    firstJsonl = $firstJsonl
    secondJsonl = $secondJsonl
    combinedXlsx = $combinedXlsx
    combinedJson = $combinedJson
    runManifest = $firstManifest
    summary = $combined.summary
    plan = $plan
  } | ConvertTo-Json -Depth 20
} finally {
  Pop-Location
}

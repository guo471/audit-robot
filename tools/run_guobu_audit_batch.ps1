
param(
  [string]$ProjectRoot = (Get-Location).Path,
  [Parameter(Mandatory = $true)][string]$TasksDir,
  [string]$RunName = "",
  [string]$Model = "qwen3.7-plus",
  [ValidateSet("fast", "hybrid", "v2", "sn_only")][string]$Mode = "hybrid",
  [int]$Workers = 1,
  [switch]$EnableTargetedSnReview,
  [switch]$SkipTimeoutRerun,
  [ValidateSet("business", "legacy")][string]$ReportFormat = "business",
  [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ([string]::IsNullOrWhiteSpace($RunName)) {
  $RunName = "guobu_audit_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
$RunName = $RunName -replace '[^A-Za-z0-9_-]', '_'

$projectPath = (Resolve-Path -LiteralPath $ProjectRoot).Path
$modelScript = Join-Path $projectPath "tools\run_guobu_model_audit_v2.py"
$businessGenerator = Join-Path $projectPath "tools\guobu_audit_report.py"
$selector = Join-Path $projectPath "tools\select_guobu_tasks.py"
$contractValidator = Join-Path $projectPath "tools\guobu_audit_contract.py"
$legacyMerger = Join-Path $projectPath "tools\merge_guobu_audit_results.py"
$tasksPath = if ([System.IO.Path]::IsPathRooted($TasksDir)) {
  (Resolve-Path -LiteralPath $TasksDir).Path
} else {
  (Resolve-Path -LiteralPath (Join-Path $projectPath $TasksDir)).Path
}
if (-not (Test-Path -LiteralPath $modelScript)) { throw "Missing audit entry point: $modelScript" }
if (-not (Test-Path -LiteralPath $selector)) { throw "Missing task selector: $selector" }
if (-not (Test-Path -LiteralPath $contractValidator)) { throw "Missing audit contract validator: $contractValidator" }
if ($ReportFormat -eq "business") {
  if (-not (Test-Path -LiteralPath $businessGenerator)) { throw "Missing business report generator: $businessGenerator" }
} elseif (-not (Test-Path -LiteralPath $legacyMerger)) {
  throw "Missing legacy result merger: $legacyMerger"
}

$taskCount = (Get-ChildItem -LiteralPath $tasksPath -Filter "*.json" | Measure-Object).Count
if ($taskCount -le 0) { throw "No task JSON files found in $tasksPath" }

$reportRoot = Join-Path $projectPath "reports\model_audit"
$tempRoot = Join-Path $projectPath "temp"
$firstOut = Join-Path $reportRoot ($RunName + "_first")
$firstCache = Join-Path $reportRoot ("cache_" + $RunName + "_first")
$secondOut = Join-Path $reportRoot ($RunName + "_network_rerun")
$secondCache = Join-Path $reportRoot ("cache_" + $RunName + "_network_rerun")
$retryTasks = Join-Path $tempRoot ($RunName + "_network_retry_tasks")
$combinedXlsx = Join-Path $reportRoot ($RunName + "_combined.xlsx")
$combinedJson = Join-Path $reportRoot ($RunName + "_combined.json")

$plan = [ordered]@{
  projectRoot = $projectPath
  tasksDir = $tasksPath
  taskCount = $taskCount
  runName = $RunName
  model = $Model
  mode = $Mode
  workers = $Workers
  targetedSnReview = [bool]$EnableTargetedSnReview
  timeoutRerun = -not [bool]$SkipTimeoutRerun
  reportFormat = $ReportFormat
  reportGenerator = if ($ReportFormat -eq "business") { $businessGenerator } else { $legacyMerger }
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
if ($Workers -lt 1) { throw "Workers must be at least 1" }

foreach ($path in @($firstOut, $firstCache, $reportRoot, $tempRoot)) {
  New-Item -ItemType Directory -Force -Path $path | Out-Null
}
if ((Test-Path -LiteralPath $combinedXlsx) -or (Test-Path -LiteralPath $combinedJson)) {
  throw "Combined output already exists for RunName '$RunName'. Use a new RunName."
}
if ((Get-ChildItem -LiteralPath $firstOut -Filter "*.jsonl" -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0) {
  throw "First-run output already exists for RunName '$RunName'. Use a new RunName."
}

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
    "--workers", [string]$Workers
  )
  if (-not $EnableTargetedSnReview) { $arguments += "--no-targeted-sn-review" }

  & python @arguments 2>&1 | Tee-Object -FilePath $LogPath | ForEach-Object { Write-Host $_ }
  if ($LASTEXITCODE -ne 0) { throw "Audit process exited with code $LASTEXITCODE" }
  $jsonl = Get-ChildItem -LiteralPath $OutDir -Filter "*.jsonl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $jsonl) { throw "Audit process did not create JSONL in $OutDir" }
  return $jsonl.FullName
}

Push-Location $projectPath
try {
  $firstLog = Join-Path $firstOut "run_stdout.log"
  $firstJsonl = Invoke-AuditRun -InputTasks $tasksPath -OutDir $firstOut -CacheDir $firstCache -LogPath $firstLog
  & python $contractValidator "--tasks-dir" $tasksPath "--first-jsonl" $firstJsonl | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "First-run completeness validation failed" }

  $secondJsonl = ""
  $retryCount = 0
  if (-not $SkipTimeoutRerun) {
    $selectionSummary = Join-Path $tempRoot ($RunName + "_network_retry_selection.json")
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
      $selector,
      "--source-dir", $tasksPath,
      "--out-dir", $retryTasks,
      "--timeout-jsonl", $firstJsonl,
      "--summary-json", $selectionSummary
    )
    $selectionText = & python @selectionArgs
    if ($LASTEXITCODE -ne 0) { throw "Network-retry task selection failed" }
    $selection = $selectionText | Select-Object -Last 1 | ConvertFrom-Json
    $retryCount = [int]$selection.selected

    if ($retryCount -gt 0) {
      New-Item -ItemType Directory -Force -Path $secondOut, $secondCache | Out-Null
      $secondLog = Join-Path $secondOut "run_stdout.log"
      $secondJsonl = Invoke-AuditRun -InputTasks $retryTasks -OutDir $secondOut -CacheDir $secondCache -LogPath $secondLog
    }
  }

  if ($ReportFormat -eq "business") {
    $reportArgs = @(
      $businessGenerator,
      "--first-jsonl", $firstJsonl,
      "--output-xlsx", $combinedXlsx,
      "--output-json", $combinedJson,
      "--overwrite"
    )
    if (-not [string]::IsNullOrWhiteSpace($secondJsonl)) {
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
  } else {
    $reportArgs = @(
    $legacyMerger,
    "--first-jsonl", $firstJsonl,
    "--output-xlsx", $combinedXlsx,
    "--output-json", $combinedJson
  )
  if (-not [string]::IsNullOrWhiteSpace($secondJsonl)) {
      $reportArgs += @("--second-jsonl", $secondJsonl)
    }
  }
  $mergeOutput = & python @reportArgs
  if ($LASTEXITCODE -ne 0) { throw "Combined report generation failed" }
  $combined = Get-Content -LiteralPath $combinedJson -Raw | ConvertFrom-Json

  [ordered]@{
    taskCount = $taskCount
    networkRetryCount = $retryCount
    firstJsonl = $firstJsonl
    secondJsonl = $secondJsonl
    combinedXlsx = $combinedXlsx
    combinedJson = $combinedJson
    summary = $combined.summary
    plan = $plan
  } | ConvertTo-Json -Depth 20
} finally {
  Pop-Location
}

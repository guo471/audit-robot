param(
  [string]$OutputDir = ([Environment]::GetFolderPath('Desktop')),
  [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

$dirty = (git status --porcelain | Out-String).Trim()
if ($dirty -and -not $AllowDirty) {
  throw "worktree is dirty; commit or clean changes before building production package, or pass -AllowDirty only for local validation"
}

$head = (git rev-parse HEAD).Trim()
$short = (git rev-parse --short HEAD).Trim()
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$packageName = "guobu_auto_audit_idiot_proof_prod_${stamp}_${short}"
$packageDir = Join-Path $OutputDir $packageName
$appDir = Join-Path $packageDir 'audit_robot'
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

$runtimeInclude = @(
  'requirements.txt',
  'config.py',
  'photo_authenticity/requirements-runtime.txt',
  'deploy/linux/.env.example',
  'deploy/linux/00_README_FIRST.md',
  'deploy/linux/README_DEPLOY.md',
  'deploy/linux/PRODUCTION_ACCEPTANCE_CHECKLIST.md',
  'deploy/linux/configure_env.sh',
  'deploy/linux/emergency_stop.sh',
  'deploy/linux/install.sh',
  'deploy/linux/install_dependencies.sh',
  'deploy/linux/install_systemd.sh',
  'deploy/linux/logs.sh',
  'deploy/linux/preflight.sh',
  'deploy/linux/run_once.sh',
  'deploy/linux/start.sh',
  'deploy/linux/status.sh',
  'deploy/linux/stop.sh',
  'deploy/linux/validate_deployment.sh',
  'deploy/linux/xxl_job_command.txt',
  'deploy/linux/build_release_zip.ps1',
  'deploy/linux/lib/common.sh',
  'deploy/linux/systemd/guobu-auto-audit.service',
  'modules',
  'modules/__init__.py',
  'modules/category_classifier.py',
  'prompts/digital_activation_evidence_review.txt',
  'prompts/photo_auth_edge_mapping_review.txt',
  'prompts/sn_label_authenticity_review.txt',
  'prompts/sn_similar_char_review.txt',
  'prompts/sn_similar_char_review_v2.txt',
  'tools/auto_audit_dashboard_server.py',
  'tools/black_edge_shadow_detector.py',
  'tools/guobu_linux_auto_audit.py',
  'tools/guobu_machine_approval_feedback.js',
  'tools/guobu_one_click_collect.js',
  'tools/guobu_sn_barcode.py',
  'tools/guobu_sn_policy_v2.py',
  'tools/non_real_local_features.py',
  'tools/photo_authenticity_mainline.py',
  'tools/run_guobu_model_audit_v2.py',
  'tools/start_guobu_auto_audit.ps1',
  'tools/start_guobu_linux_auto_audit.sh',
  'photo_authenticity/prompts/non_real_photo_auditor_v4.txt',
  'photo_authenticity/models/releases/non-real-photo-v2',
  'photo_authenticity/models/releases/non-real-local-tree-v1/tree.json'
)

function Test-AllowedPackageFile {
  param([string]$RelativePath)
  $normalized = $RelativePath.Replace('\', '/')
  $blockedMetaDirs = @('.git', ('.work' + 'trees'), '.venv', 'node_modules', '__pycache__', '.pytest_cache')
  foreach ($blocked in $blockedMetaDirs) {
    if ($normalized -like "*/$blocked/*" -or $normalized -like "$blocked/*") { return $false }
  }
  if ($normalized -match '(^|/)\.env$') { return $false }
  if ($normalized -match '\.(sqlite|db|log|pyc|bundle|zip|tar)$') { return $false }
  return $true
}

function Add-RuntimePath {
  param([string]$RelativePath)
  $source = Join-Path $root $RelativePath
  if (-not (Test-Path -LiteralPath $source)) {
    throw "runtime path missing: $RelativePath"
  }
  $sourceItem = Get-Item -LiteralPath $source
  if ($sourceItem.PSIsContainer) {
    Get-ChildItem -Recurse -File -LiteralPath $source | ForEach-Object {
      $relative = $_.FullName.Substring($root.Length + 1).Replace('\', '/')
      if (-not (Test-AllowedPackageFile $relative)) { return }
      Copy-RuntimeFile $relative
    }
  } else {
    $relative = $sourceItem.FullName.Substring($root.Length + 1).Replace('\', '/')
    if (Test-AllowedPackageFile $relative) {
      Copy-RuntimeFile $relative
    }
  }
}

function Copy-RuntimeFile {
  param([string]$RelativePath)
  $source = Join-Path $root $RelativePath
  $target = Join-Path $appDir $RelativePath
  $targetParent = Split-Path -Parent $target
  New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
  Copy-Item -LiteralPath $source -Destination $target -Force
}

foreach ($path in $runtimeInclude) {
  Add-RuntimePath $path
}

$created = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
$version = @(
  'Guobu auto audit idiot-proof production package',
  "CreatedAt: $created",
  "SourceBranch: $branch",
  "SourceCommit: $head",
  "ShortCommit: $short",
  '',
  'ProductionPolicy:',
  'SN_POLICY_VERSION=v2',
  'SN_BARCODE_MODE=enforce',
  'SN_HOME_APPLIANCE_EXACT_MATCH_CONFLICT_RESCUE=true',
  'DIGITAL_ACTIVATION_EVIDENCE_MODE=on',
  'PHOTO_AUTHENTICITY_MODE=enforce',
  'PHOTO_AUTHENTICITY_NEW_RULE_ENABLED=true',
  'PHOTO_AUTHENTICITY_LOCAL_TREE_ENABLED=false',
  'PHOTO_AUTHENTICITY_LOCAL_TREE_CONFIRMATION_ENABLED=false',
  '',
  'ReadFirst:',
  'deploy/linux/00_README_FIRST.md'
) -join "`n"
[System.IO.File]::WriteAllText((Join-Path $appDir 'VERSION.txt'), $version, [System.Text.UTF8Encoding]::new($false))

$handoff = @(
  '# Deployment Handoff',
  '',
  "Final ZIP: $packageName.zip",
  "Source commit: $head",
  '',
  'Read first:',
  'audit_robot/deploy/linux/00_README_FIRST.md',
  '',
  'Shortest command sequence:',
  'cd /opt/audit_robot',
  'bash deploy/linux/install.sh',
  'bash deploy/linux/configure_env.sh',
  'bash deploy/linux/preflight.sh',
  'bash deploy/linux/run_once.sh',
  '',
  'Mode selection:',
  'run_once.sh is only for one-loop acceptance.',
  'XXL-JOB runs one loop per scheduler trigger.',
  'systemd/start.sh is the long-running loop mode.',
  'Use XXL-JOB or systemd, not both.',
  '',
  'Checklist:',
  'audit_robot/deploy/linux/PRODUCTION_ACCEPTANCE_CHECKLIST.md',
  '',
  'Note: the package must not contain real .env, secrets, tokens, SQLite state, runtime logs, caches, or Git metadata.'
) -join "`n"
[System.IO.File]::WriteAllText((Join-Path $packageDir 'DEPLOYMENT_HANDOFF.md'), $handoff, [System.Text.UTF8Encoding]::new($false))

$manifest = Get-ChildItem -Recurse -File -LiteralPath $appDir | ForEach-Object {
  $_.FullName.Substring($appDir.Length + 1).Replace('\','/')
} | Sort-Object
if ($manifest.Count -lt 35) { throw "manifest too small: $($manifest.Count)" }
[System.IO.File]::WriteAllLines((Join-Path $packageDir 'PACKAGE_CONTENTS.txt'), $manifest, [System.Text.UTF8Encoding]::new($false))

$zipPath = Join-Path $OutputDir ($packageName + '.zip')
Compress-Archive -LiteralPath $appDir, (Join-Path $packageDir 'DEPLOYMENT_HANDOFF.md'), (Join-Path $packageDir 'PACKAGE_CONTENTS.txt') -DestinationPath $zipPath -Force
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath
$hashLine = "SHA256  $($hash.Hash)  $([System.IO.Path]::GetFileName($zipPath))`n"
[System.IO.File]::WriteAllText((Join-Path $packageDir 'SHA256.txt'), $hashLine, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $OutputDir ($packageName + '.SHA256.txt')), $hashLine, [System.Text.UTF8Encoding]::new($false))

[PSCustomObject]@{
  PackageDir = $packageDir
  ZipPath = $zipPath
  Sha256 = $hash.Hash
  Sha256File = (Join-Path $OutputDir ($packageName + '.SHA256.txt'))
  FileCount = $manifest.Count
  Commit = $head
} | ConvertTo-Json -Depth 3

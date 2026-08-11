param(
  [string]$OutputDir = ([Environment]::GetFolderPath('Desktop'))
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

$head = (git rev-parse HEAD).Trim()
$short = (git rev-parse --short HEAD).Trim()
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$packageName = "guobu_auto_audit_idiot_proof_prod_${stamp}_${short}"
$packageDir = Join-Path $OutputDir $packageName
$appDir = Join-Path $packageDir 'audit_robot'
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

$tracked = git -c core.quotepath=false ls-files
$include = @()
foreach ($p in $tracked) {
  if ($p -cmatch '[^\x00-\x7F]') { continue }
  if ($p -match '^(tests|\.superpowers)/') { continue }
  if ($p -match '^(data|outputs|logs|temp|reports)/') { continue }
  if ($p -match '^(photo_authenticity/data|photo_authenticity/reports)/') { continue }
  if ($p -match '\.(sqlite|db|log|pyc|bundle|zip)$') { continue }
  if ($p -eq '.gitignore') { continue }
  if ($p -match '^(docs/superpowers|docs/.*handoff|docs/.*memory)') { continue }
  $include += $p
}
if ($include.Count -lt 50) { throw "include list too small: $($include.Count)" }

$tarPath = Join-Path $packageDir 'source.tar'
& git archive --format=tar --output=$tarPath HEAD -- @include
if ($LASTEXITCODE -ne 0) { throw "git archive failed with $LASTEXITCODE" }
& tar -xf $tarPath -C $appDir
if ($LASTEXITCODE -ne 0) { throw "tar extract failed with $LASTEXITCODE" }
Remove-Item -LiteralPath $tarPath

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
  'Note: the package must not contain real .env, secrets, tokens, SQLite state, runtime logs, sample libraries, caches, or Git metadata.'
) -join "`n"
[System.IO.File]::WriteAllText((Join-Path $packageDir 'DEPLOYMENT_HANDOFF.md'), $handoff, [System.Text.UTF8Encoding]::new($false))

$manifest = Get-ChildItem -Recurse -File -LiteralPath $appDir | ForEach-Object {
  $_.FullName.Substring($appDir.Length + 1).Replace('\','/')
} | Sort-Object
if ($manifest.Count -lt 50) { throw "manifest too small: $($manifest.Count)" }
[System.IO.File]::WriteAllLines((Join-Path $packageDir 'PACKAGE_CONTENTS.txt'), $manifest, [System.Text.UTF8Encoding]::new($false))

$zipPath = Join-Path $OutputDir ($packageName + '.zip')
Compress-Archive -LiteralPath $appDir, (Join-Path $packageDir 'DEPLOYMENT_HANDOFF.md'), (Join-Path $packageDir 'PACKAGE_CONTENTS.txt') -DestinationPath $zipPath -Force
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath
[System.IO.File]::WriteAllText((Join-Path $packageDir 'SHA256.txt'), "SHA256  $($hash.Hash)  $([System.IO.Path]::GetFileName($zipPath))`n", [System.Text.UTF8Encoding]::new($false))

[PSCustomObject]@{
  PackageDir = $packageDir
  ZipPath = $zipPath
  Sha256 = $hash.Hash
  FileCount = $manifest.Count
  Commit = $head
} | ConvertTo-Json -Depth 3

$ErrorActionPreference = "SilentlyContinue"
$run = "guobu_20260723_204824_all1199_bg"
$base = "C:\Users\HUAWEI\Desktop\audit_robot\reports\model_audit"
$first = Join-Path $base ($run + "_first")
$combinedXlsx = Join-Path $base ($run + "_combined.xlsx")
$combinedJson = Join-Path $base ($run + "_combined.json")
$total = 1199

function Count-JsonlRows {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return 0 }
  $stream = $null
  try {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    $buffer = New-Object byte[] 1048576
    $count = 0
    while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
      for ($i = 0; $i -lt $read; $i++) {
        if ($buffer[$i] -eq 10) { $count++ }
      }
    }
    return $count
  } catch {
    return "读取中"
  } finally {
    if ($stream) { $stream.Dispose() }
  }
}

while ($true) {
  Clear-Host
  Write-Host "国补审核后台监控" -ForegroundColor Cyan
  Write-Host "批次: $run"
  Write-Host "时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  Write-Host ""
  $jsonl = Get-ChildItem -LiteralPath $first -Filter "*.jsonl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($jsonl) {
    $rows = Count-JsonlRows -Path $jsonl.FullName
    Write-Host "已审核: $rows / $total"
    Write-Host "结果文件: $($jsonl.FullName)"
    Write-Host "最后更新: $($jsonl.LastWriteTime)"
    Write-Host "文件大小: $($jsonl.Length) bytes"
  } else {
    Write-Host "还没有找到 jsonl 结果文件"
  }
  Write-Host ""
  if (Test-Path $combinedXlsx) { Write-Host "最终Excel已生成: $combinedXlsx" -ForegroundColor Green }
  else { Write-Host "最终Excel: 尚未生成" }
  if (Test-Path $combinedJson) { Write-Host "最终JSON已生成: $combinedJson" -ForegroundColor Green }
  else { Write-Host "最终JSON: 尚未生成" }
  Write-Host ""
  Write-Host "如果这个窗口关闭，不影响隐藏后台审核进程。按 Ctrl+C 可关闭监控窗口。" -ForegroundColor Yellow
  Start-Sleep -Seconds 10
}

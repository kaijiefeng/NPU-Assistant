# NPU-Assistant 数据备份（SQLite 快照 + uploads + wiki 快照）
# 用法: .\scripts\backup.ps1 [-OutDir backups]
param(
    [string]$OutDir = "backups"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$dataDir = Join-Path $root "backend\data"
$outPath = Join-Path $root $OutDir
New-Item -ItemType Directory -Force -Path $outPath | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$work = Join-Path $env:TEMP "npu-backup-$stamp"
New-Item -ItemType Directory -Force -Path $work | Out-Null

Write-Host "[1/3] 生成 SQLite 一致性快照 ..." -ForegroundColor Cyan
& $python -c @"
import sqlite3
src = sqlite3.connect(r'$dataDir\app.db')
dst = sqlite3.connect(r'$work\app.db')
src.backup(dst)
dst.close(); src.close()
print('SQLite snapshot ok')
"@

Write-Host "[2/3] 复制上传文件与 Wiki 快照 ..." -ForegroundColor Cyan
if (Test-Path (Join-Path $dataDir "uploads")) {
    Copy-Item (Join-Path $dataDir "uploads") (Join-Path $work "uploads") -Recurse -Force
}
if (Test-Path (Join-Path $dataDir "wiki_seed.json")) {
    Copy-Item (Join-Path $dataDir "wiki_seed.json") (Join-Path $work "wiki_seed.json") -Force
}
Copy-Item (Join-Path $dataDir "seed_docs") (Join-Path $work "seed_docs") -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[3/3] 打包 ..." -ForegroundColor Cyan
$zip = Join-Path $outPath "npu-assistant-$stamp.zip"
Compress-Archive -Path (Join-Path $work "*") -DestinationPath $zip -Force
Remove-Item $work -Recurse -Force

Write-Host "备份完成：$zip" -ForegroundColor Green
Write-Host "恢复：停止服务 → 解压覆盖 backend\data → 重启（不要直接复制运行中的 app.db-wal）" -ForegroundColor DarkGray

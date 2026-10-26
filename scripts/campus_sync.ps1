# 定时同步公开校园栏目（建议 Windows 任务计划每 6 小时运行）
# 用法: .\scripts\campus_sync.ps1 [-DryRun] [-Source xgb-zdgk] [-Limit 3]
param(
    [switch]$DryRun,
    [string[]]$Source = @(),
    [int]$Limit = 0
)
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "scripts\campus_sync.py"
if (-not (Test-Path $python)) { throw "未找到 .venv，请先运行 scripts\bootstrap.ps1" }

$cmdArgs = @($script)
if ($DryRun) { $cmdArgs += "--dry-run" }
if ($Limit -gt 0) { $cmdArgs += @("--limit", "$Limit") }
foreach ($item in $Source) { $cmdArgs += @("--source", $item) }

$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $python @cmdArgs 2>&1 | ForEach-Object { Write-Output $_ }
exit $LASTEXITCODE

# 启动 NPU-Assistant 开发服务器
# 用法:  .\scripts\run.ps1 [-Port 8000] [-BindHost 127.0.0.1] [-Reload]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "未找到虚拟环境 $python。请先执行: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt"
}

$env:PYTHONUTF8 = "1"
$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--app-dir", (Join-Path $root "backend"),
    "--host", $BindHost,
    "--port", "$Port"
)
if ($Reload) { $uvicornArgs += "--reload" }

Write-Host "NPU-Assistant -> http://${BindHost}:$Port" -ForegroundColor Cyan
& $python @uvicornArgs

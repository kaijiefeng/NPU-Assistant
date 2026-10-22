# NPU-Assistant 一键初始化（Windows PowerShell）
# 作用：创建虚拟环境 -> 安装依赖 -> 启动服务
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[1/3] 创建虚拟环境 .venv ..." -ForegroundColor Cyan
    python -m venv .venv
} else {
    Write-Host "[1/3] 虚拟环境已存在，跳过创建" -ForegroundColor DarkGray
}

Write-Host "[2/3] 安装后端依赖 ..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt --quiet

Write-Host "[3/3] 启动服务：http://127.0.0.1:8000 （Ctrl+C 停止）" -ForegroundColor Green
& .\scripts\run.ps1 -Port 8000

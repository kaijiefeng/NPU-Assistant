# 局域网启动（兼容旧用法；等价于 .\scripts\run.ps1 -Lan）
# 用法: .\scripts\serve-lan.ps1 [-Port 8000]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "run.ps1") -Port $Port -Lan

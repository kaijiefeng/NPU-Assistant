#!/usr/bin/env pwsh
# RSI 端到端质量门禁
#   .\scripts\rsi.ps1                 # 独立跑关键场景（含移动端）
#   .\scripts\rsi.ps1 -FromArtifacts  # 复用最近一次全量 E2E 产物
param(
    [switch]$FromArtifacts,
    [switch]$Headed,
    [double]$MinCoverage = 90
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "未找到虚拟环境：$python" }
$env:PYTHONUTF8 = "1"
Push-Location $root
try {
    if ($FromArtifacts) {
        & $python scripts\rsi_verify.py --from-artifacts --min-coverage $MinCoverage
    } elseif ($Headed) {
        & $python scripts\rsi_verify.py --headed --min-coverage $MinCoverage
    } else {
        & $python scripts\rsi_verify.py --headless --min-coverage $MinCoverage
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

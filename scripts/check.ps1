# 运行后端测试
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
Push-Location (Join-Path $root "backend")
try {
    & $python -m pytest tests -q
} finally {
    Pop-Location
}

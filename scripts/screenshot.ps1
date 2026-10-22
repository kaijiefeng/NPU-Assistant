# 用 Edge 无头模式给前端各页面截图（需服务已启动）
# 用法: .\scripts\screenshot.ps1 [-BaseUrl http://127.0.0.1:8000] [-OutDir docs\screenshots]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$OutDir = "docs\screenshots"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$edge = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $edge) { throw "未找到 Microsoft Edge，可用任意 Chromium 浏览器手动截图。" }

$outPath = Join-Path $root $OutDir
New-Item -ItemType Directory -Force -Path $outPath | Out-Null

$pages = [ordered]@{
    "chat"      = "#/chat"
    "knowledge" = "#/knowledge"
    "models"    = "#/models"
    "bots"      = "#/bots"
    "settings"  = "#/settings"
}

foreach ($name in $pages.Keys) {
    $file = Join-Path $outPath "$name.png"
    $url = "$BaseUrl/$($pages[$name])"
    & $edge --headless=new --disable-gpu --hide-scrollbars --window-size=1440,960 `
        --virtual-time-budget=6000 --screenshot="$file" $url 2>$null | Out-Null
    if (Test-Path $file) {
        Write-Host "  [OK] $name -> $file" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $name 截图失败" -ForegroundColor Red
    }
}

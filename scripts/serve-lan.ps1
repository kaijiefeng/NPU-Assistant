# 局域网启动（手机同 Wi-Fi 访问）
# 用法: .\scripts\serve-lan.ps1 [-Port 8000]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "未找到虚拟环境，请先运行 .\scripts\bootstrap.ps1" }

Write-Host "== NPU-Assistant 局域网启动 ==" -ForegroundColor Cyan
$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress -Unique

if (-not $ips) {
    Write-Host "未找到局域网 IPv4 地址，请检查网络连接。" -ForegroundColor Yellow
} else {
    Write-Host "手机（同一 Wi-Fi）访问：" -ForegroundColor Green
    foreach ($ip in $ips) {
        Write-Host "    http://${ip}:$Port" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "首次使用若手机打不开，请用管理员 PowerShell 放行防火墙：" -ForegroundColor Yellow
Write-Host "    New-NetFirewallRule -DisplayName `"NPU-Assistant $Port`" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "安全提示：公网访问请走 HTTPS 隧道（Cloudflare Tunnel/ngrok/frp），不要把 8000 端口直接映射到公网。" -ForegroundColor Yellow

# 对外监听必须启用管理令牌（服务端会拒绝无令牌的 0.0.0.0 启动）。
# 未设置时自动生成一个，并打印给本次启动使用；手机端在「设置 → 管理令牌」填入。
if (-not $env:NPU_ADMIN_TOKEN) {
    $env:NPU_ADMIN_TOKEN = [guid]::NewGuid().ToString("N")
    Write-Host ""
    Write-Host "已自动生成管理令牌（本次启动有效）：" -ForegroundColor Green
    Write-Host "    $env:NPU_ADMIN_TOKEN" -ForegroundColor Cyan
    Write-Host "手机首次管理操作前，请在「设置 → 移动端与管理令牌」填入上面的令牌。" -ForegroundColor DarkGray
}
Write-Host ""

$env:NPU_HOST = "0.0.0.0"
$env:NPU_PORT = "$Port"
$env:PYTHONUTF8 = "1"
& $python -m uvicorn app.main:app --app-dir (Join-Path $root "backend") --host 0.0.0.0 --port $Port

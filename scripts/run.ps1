# 启动 NPU-Assistant 服务（Web + 手机端 + API 同一进程）
# 用法:  .\scripts\run.ps1 [-Port 8000] [-BindHost 127.0.0.1] [-Reload] [-Lan] [-PublicUrl https://...]
#   -Lan        绑定 0.0.0.0，让同一 Wi-Fi 的手机扫码/访问；自动生成并打印管理令牌
#   -PublicUrl  固定公网地址（命名隧道/反向代理），手机扫码优先使用
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$Reload,
    [switch]$Lan,
    [string]$PublicUrl = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "未找到虚拟环境 $python。请先执行: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt"
}

if ($Lan) { $BindHost = "0.0.0.0" }
$isLoopback = $BindHost -in @("127.0.0.1", "localhost", "::1")
if ($PublicUrl) { $env:NPU_PUBLIC_URL = $PublicUrl.TrimEnd("/") }

# 服务端根据 NPU_HOST 做安全校验：对外监听必须有管理令牌，否则拒绝启动。
$env:NPU_HOST = $BindHost
$env:NPU_PORT = "$Port"
$env:PYTHONUTF8 = "1"

Write-Host ""
Write-Host "== NPU-Assistant ==" -ForegroundColor Cyan
Write-Host "本机访问: http://127.0.0.1:$Port" -ForegroundColor Green

if (-not $isLoopback) {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object IPAddress, InterfaceAlias -Unique

    Write-Host ""
    if ($ips) {
        Write-Host "手机（同一 Wi-Fi）访问（选择 WLAN/以太网 对应的地址）：" -ForegroundColor Green
        foreach ($ip in $ips) {
            $alias = if ($ip.InterfaceAlias) { $ip.InterfaceAlias } else { "网络" }
            Write-Host ("    {0}: http://{1}:{2}" -f $alias, $ip.IPAddress, $Port) -ForegroundColor Green
        }
    } else {
        Write-Host "未找到局域网 IPv4 地址，请检查网络连接。" -ForegroundColor Yellow
    }

    if (-not $env:NPU_ADMIN_TOKEN) {
        $env:NPU_ADMIN_TOKEN = [guid]::NewGuid().ToString("N")
        Write-Host ""
        Write-Host "已自动生成管理令牌（本次启动有效）：" -ForegroundColor Green
        Write-Host "    $env:NPU_ADMIN_TOKEN" -ForegroundColor Cyan
        Write-Host "手机首次管理操作前，请在「设置 → 移动端与管理令牌」填入上面的令牌。" -ForegroundColor DarkGray
    } else {
        Write-Host ""
        Write-Host "已使用环境变量 NPU_ADMIN_TOKEN 中的管理令牌。" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "首次手机打不开时，请用管理员 PowerShell 放行防火墙：" -ForegroundColor Yellow
    Write-Host "    New-NetFirewallRule -DisplayName `"NPU-Assistant $Port`" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow" -ForegroundColor DarkYellow
    Write-Host "启动后电脑打开 http://127.0.0.1:$Port，点右上角「手机扫码」让手机扫码（v0.1.1 默认方式）。" -ForegroundColor Green
    Write-Host "需要 4G/5G 或 PWA 安装时，在扫码弹窗里点「一键开启公网访问」。" -ForegroundColor DarkGray
    Write-Host "PWA 安装/离线能力需要 HTTPS：局域网 HTTP 只能网页访问；公网建议 Cloudflare Tunnel/Nginx 反代。" -ForegroundColor DarkGray
}
Write-Host ""

$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--app-dir", (Join-Path $root "backend"),
    "--host", $BindHost,
    "--port", "$Port"
)
if ($Reload) { $uvicornArgs += "--reload" }

& $python @uvicornArgs

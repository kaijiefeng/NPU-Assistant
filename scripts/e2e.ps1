# E2E 交互验收（沙箱隔离）
#
# 本地示例：
#   .\scripts\e2e.ps1                       # 全量沙箱运行（headless）
#   .\scripts\e2e.ps1 -Headed               # 显示浏览器，观察人类化操作
#   .\scripts\e2e.ps1 -Scenario "knowledge.*" -Keep
#   .\scripts\e2e.ps1 -SelfTest             # 引擎自检（不启浏览器）
param(
    [string]$Scenario = "",
    [string]$Tag = "",
    [switch]$Headed,
    [switch]$Keep,
    [string]$Junit = "",
    [double]$MinCoverage = 0,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "未找到虚拟环境：$python" }

$env:PYTHONUTF8 = "1"
$args = @("scripts\e2e_verify.py")
if ($SelfTest) { $args += "--self-test" }
if ($Scenario) { $args += @("--scenario", $Scenario) }
if ($Tag) { $args += @("--tag", $Tag) }
if ($Headed) { $args += "--headed" } else { $args += "--headless" }
if ($Keep) { $args += "--keep" }
if ($Junit) { $args += @("--junit", $Junit) }
if ($MinCoverage -gt 0) { $args += @("--min-coverage", "$MinCoverage") }

Push-Location $root
try {
    & $python @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

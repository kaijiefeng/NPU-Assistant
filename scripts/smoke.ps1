# 端到端 API 冒烟测试（需服务已启动）
# 用法: .\scripts\smoke.ps1 [-BaseUrl http://127.0.0.1:8000]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)
$ErrorActionPreference = "Stop"
$ok = 0; $fail = 0

function Step($name, [scriptblock]$action) {
    try {
        & $action | Out-Null
        Write-Host "  [PASS] $name" -ForegroundColor Green
        $script:ok++
    } catch {
        Write-Host "  [FAIL] $name -> $($_.Exception.Message)" -ForegroundColor Red
        $script:fail++
    }
}

function Invoke-Json($method, $path, $body) {
    $params = @{ Uri = "$BaseUrl$path"; Method = $method; TimeoutSec = 120 }
    if ($null -ne $body) {
        $params.ContentType = "application/json; charset=utf-8"
        $params.Body = ($body | ConvertTo-Json -Depth 8 -Compress)
    }
    Invoke-RestMethod @params
}

Write-Host "== NPU-Assistant 冒烟测试 ($BaseUrl) ==" -ForegroundColor Cyan

Step "健康检查 /api/health" {
    $r = Invoke-Json GET "/api/health" $null
    if ($r.status -ne "ok") { throw "status != ok" }
}
Step "运行统计 /api/system/stats" {
    $r = Invoke-Json GET "/api/system/stats" $null
    if ($null -eq $r.documents) { throw "缺少 documents" }
}
Step "导入示例知识库 /api/knowledge/seed" {
    $r = Invoke-Json POST "/api/knowledge/seed" @{}
    if ($r.total_documents -lt 1) { throw "种子文档为空" }
}
Step "知识库检索 /api/knowledge/search" {
    $r = Invoke-Json POST "/api/knowledge/search" @{ query = "国家奖学金怎么申请"; top_k = 3 }
    if ($r.items.Count -lt 1) { throw "无检索结果" }
}
Step "对话问答 /api/chat" {
    $r = Invoke-Json POST "/api/chat" @{ message = "国家奖学金怎么申请？需要哪些材料？"; session_key = "web:smoke" }
    if (-not $r.reply) { throw "无回复" }
    if ($r.sources_used -lt 1 -and $r.fallback -eq $false) { throw "非降级模式却无引用" }
    Write-Host ("     -> 引用 " + $r.citations.Count + " 条 | 模型 " + $r.model + " | " + $r.latency_ms + "ms") -ForegroundColor DarkGray
}
Step "模型列表 /api/models" {
    $r = Invoke-Json GET "/api/models" $null
    if ($r.items.Count -lt 1) { throw "无模型" }
}
Step "机器人通道 /api/bots/channels" {
    $r = Invoke-Json GET "/api/bots/channels" $null
    if ($r.items.Count -lt 1) { throw "无通道" }
}
Step "机器人模拟对话 /api/bots/channels/{id}/test" {
    $channels = Invoke-Json GET "/api/bots/channels" $null
    $console = $channels.items | Where-Object { $_.type -eq "console" } | Select-Object -First 1
    if (-not $console) { throw "缺少 console 通道" }
    $r = Invoke-Json POST "/api/bots/channels/$($console.id)/test" @{ message = "请假流程是什么？"; user_name = "冒烟同学" }
    if (-not $r.reply) { throw "无机器人回复" }
}
Step "前端首页 /" {
    $r = Invoke-WebRequest -Uri "$BaseUrl/" -UseBasicParsing -TimeoutSec 30
    if ($r.StatusCode -ne 200) { throw "HTTP $($r.StatusCode)" }
}

Write-Host ""
Write-Host "结果: 通过 $ok / 失败 $fail" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Red" })
if ($fail -gt 0) { exit 1 }

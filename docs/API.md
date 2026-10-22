# API 速览

> 本文件是接口的快速上手版本；**字段与行为的唯一事实源是 [CONTRACT.md](CONTRACT.md) §5**。启动服务后可访问交互式文档：
>
> - Swagger UI：<http://127.0.0.1:8000/api/docs>
> - OpenAPI JSON：<http://127.0.0.1:8000/api/openapi.json>

## 1. 通用约定

| 项目 | 说明 |
|------|------|
| 前缀 | 所有接口位于 `/api` 下，JSON 响应（上传为 multipart，公众号为 XML） |
| 错误格式 | `HTTPException(status_code, detail="中文可读信息")`，前端按 `detail` 展示 |
| 鉴权 | 默认无鉴权；设置环境变量 `NPU_ADMIN_TOKEN` 后，**写操作**需请求头 `X-Admin-Token: <token>` |
| 时间格式 | ISO 字符串（本地时间），ID 为 32 位 hex |
| 上传限制 | 单文件 ≤ 20MB（`NPU_MAX_UPLOAD_MB`），支持 `.md/.markdown/.txt/.pdf/.docx/.xlsx/.csv/.json/.html/.htm` |

## 2. 接口分组

| 分组 | 主要接口 | 说明 |
|------|----------|------|
| 系统 | `GET /health`、`GET /system/info`、`GET /system/stats`、`GET/PUT /settings` | 健康检查、运行目录、统计、可编辑设置 |
| 对话 | `POST /chat`、`GET /chat/conversations`、`GET /chat/conversations/{id}/messages`、`DELETE /chat/conversations/{id}` | 问答（含引用）、历史会话与消息 |
| 知识库 | `POST/GET /knowledge/documents`、`GET/DELETE /knowledge/documents/{id}`、`POST /knowledge/documents/{id}/reindex`、`GET .../chunks`、`POST /knowledge/search`、`POST /knowledge/seed`、`GET /knowledge/stats` | 上传解析、切片、检索、示例导入 |
| 模型 | `GET/POST /models/providers`、`PUT/DELETE /models/providers/{id}`、`POST /models/providers/{id}/test`、`GET/POST /models`、`PUT/DELETE /models/{id}`、`POST /models/{id}/default`、`POST /models/{id}/test` | Provider / 模型 CRUD、默认模型、连通性测试 |
| 机器人 | `GET/POST /bots/channels`、`PUT/DELETE /bots/channels/{id}`、`POST /bots/channels/{id}/test`、`GET /bots/logs`、`WS /bots/onebot/{id}/ws`、`POST /bots/onebot/{id}/event`、`GET/POST /bots/wechat/{id}` | 通道管理、控制台模拟、OneBot / 公众号回调 |

## 3. 常用调用示例

以下命令均针对本地已启动的服务（`.\scripts\run.ps1`）。PowerShell 已设置 `$env:PYTHONUTF8=1` 可避免中文乱码。

### 3.1 健康检查与统计

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/system/stats
```

```jsonc
// GET /api/health
{
  "status": "ok", "app": "NPU-Assistant", "version": "0.1.0",
  "time": "2026-10-22T16:30:00",
  "default_model": { "id": "…", "display_name": "本地知识库问答（演示）",
                     "model_name": "local-rag", "provider_type": "mock" }
}
```

### 3.2 导入示例知识库与检索

```powershell
# 幂等导入 backend/data/seed_docs（已存在的同名文件会跳过）
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/seed

# 检索测试
$body = @{ query = "国家奖学金怎么申请"; top_k = 4 } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/search `
  -ContentType "application/json; charset=utf-8" -Body $body
```

```jsonc
// POST /api/knowledge/search
{
  "query": "国家奖学金怎么申请", "latency_ms": 12,
  "items": [{
    "chunk_id": "…", "document_id": "…",
    "document_title": "国家奖学金与奖助学金评审办法（示例）",
    "section": "五、申请材料清单", "snippet": "……", "content": "……", "score": 9.1
  }]
}
```

### 3.3 对话问答

```powershell
$body = @{
  message = "国家奖学金怎么申请？需要哪些材料？"
  channel = "web"
  session_key = "web:demo"
  conversation_id = $null
} | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/chat `
  -ContentType "application/json; charset=utf-8" -Body $body
```

```jsonc
// POST /api/chat（未配置真实模型时 fallback=true，引用来自真实检索）
{
  "conversation_id": "…", "reply": "…… [1][2]",
  "citations": [
    { "index": 1, "chunk_id": "…", "document_id": "…",
      "document_title": "国家奖学金与奖助学金评审办法（示例）",
      "section": "五、申请材料清单", "snippet": "……", "score": 8.31 }
  ],
  "model": "本地知识库问答（演示）", "provider_type": "mock",
  "latency_ms": 126, "fallback": true, "sources_used": 2
}
```

多轮对话：把上一轮响应中的 `conversation_id` 原样传回即可。

### 3.4 上传文档

```powershell
# 上传 Markdown（title / tags 可选）
$form = @{
  file  = Get-Item .\docs\DEMO.md
  title = "演示文档"
  tags  = "演示,测试"
}
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/documents -Form $form
```

响应为 doc 对象（`status` 为 `ready` / `failed`，含 `chunk_count`、`char_count`、`tags` 等）。

### 3.5 模型管理

```powershell
# 新增 OpenAI 兼容 Provider；API Key 只在服务端保存，返回时脱敏
$provider = @{
  name = "DeepSeek"; type = "openai"
  base_url = "https://api.deepseek.com/v1"; api_key = "sk-你的Key"
} | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models/providers `
  -ContentType "application/json; charset=utf-8" -Body $provider

# 在 Provider 下新增模型并设为默认
$model = @{ provider_id = "<provider_id>"; model_name = "deepseek-chat"; is_default = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models `
  -ContentType "application/json; charset=utf-8" -Body $model

# 测试 Provider 连通性（失败也返回 200 + ok=false）
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models/providers/<provider_id>/test
```

### 3.6 机器人调试

```powershell
# 控制台通道模拟一条学生消息
$channels = Invoke-RestMethod http://127.0.0.1:8000/api/bots/channels
$console = $channels.items | Where-Object { $_.type -eq "console" } | Select-Object -First 1
$body = @{ message = "心理中心怎么预约？"; user_name = "演示同学" } | ConvertTo-Json
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/bots/channels/$($console.id)/test" `
  -ContentType "application/json; charset=utf-8" -Body $body
```

公众号 / OneBot 的回调地址、签名与鉴权规则见 [BOT-INTEGRATION.md](BOT-INTEGRATION.md)。

## 4. 与 LLM / 机器人联调时的注意点

- 模型调用失败、超时或没有可用模型时，`POST /chat` **不会返回 500**，而是降级为抽取式回答（`fallback=true`）。
- 知识库无命中时返回礼貌说明与咨询渠道，`citations=[]`。
- 写操作失败返回中文 `detail`；`api_key` 在任何响应中都只以 `api_key_masked` 形式出现。
- OneBot WS 支持 `Authorization: Bearer <token>` 或 `?access_token=`；反向 HTTP 事件支持 query token 或 `X-Signature`（HMAC-SHA1）。
- 微信公众号仅明文模式：GET 返回 `echostr` 纯文本校验，POST 返回 `text/xml; charset=utf-8` 的被动回复 XML。

# API 速览（LLM Wiki · v0.2）

> 本文件是接口的快速上手版本；**字段与行为的唯一事实源是 [WIKI-DESIGN.md](WIKI-DESIGN.md) §4 与 [CONTRACT.md](CONTRACT.md) §5**。启动服务后可访问交互式文档：
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
| 知识模型 | `knowledge_mode: "llm-wiki"`；无 embedding / 向量库 / rerank / chunk 检索 |
| LLM 依赖 | 上传编译、高质量回答需要真实 LLM；mock 模型下编译会被拒绝、问答降级（`fallback=true`） |

## 2. 接口分组

| 分组 | 主要接口 | 说明 |
|------|----------|------|
| 系统 | `GET /health`、`GET /system/info`、`GET /system/stats`、`GET/PUT /settings` | 健康检查（含 `knowledge_mode`、`wiki_pages`）、运行信息、统计、可编辑设置 |
| 对话 | `POST /chat`、`GET /chat/conversations`、`GET /chat/conversations/{id}/messages`、`DELETE /chat/conversations/{id}` | Wiki 导航问答 + 双层引用 + `navigation` 元信息 |
| 源文件 | `POST/GET /knowledge/documents`、`GET/DELETE /knowledge/documents/{id}`、`POST .../reindex`、`POST /knowledge/seed`、`GET /knowledge/stats` | 上传并**同步编译**、列表/详情/删除/重建、导入预编译快照、统计 |
| Wiki | `GET /wiki/tree`、`GET /wiki/pages`、`GET/PUT/DELETE /wiki/pages/{id}`、`POST /wiki/search`、`GET /wiki/stats`、`POST /wiki/rebuild`、`GET /wiki/builds`、`GET /wiki/builds/{id}`、`POST /wiki/seed` | 目录、浏览、搜索、编辑、重建与构建记录 |
| 模型 | `GET/POST /models/providers`、`PUT/DELETE /models/providers/{id}`、`POST .../test`、`GET/POST /models`、`PUT/DELETE /models/{id}`、`POST /models/{id}/default`、`POST /models/{id}/test` | Provider / 模型 CRUD、默认模型、连通性测试 |
| 机器人 | `GET/POST /bots/channels`、`PUT/DELETE /bots/channels/{id}`、`POST .../test`、`GET /bots/logs`、`WS /bots/onebot/{id}/ws`、`POST /bots/onebot/{id}/event`、`GET/POST /bots/wechat/{id}` | 通道管理、控制台模拟、OneBot / 公众号回调；回答复用 counselor |

> **已移除**：`GET /knowledge/documents/{id}/chunks`、`POST /knowledge/search`（经典 RAG 遗留）。

## 3. 系统

### 3.1 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

```jsonc
// GET /api/health
{
  "status": "ok", "app": "NPU-Assistant", "version": "0.2.0",
  "time": "2026-10-22T17:30:00",
  "knowledge_mode": "llm-wiki",
  "wiki_pages": 20,
  "default_model": { "id": "…", "display_name": "DeepSeek Chat",
                     "model_name": "deepseek-chat", "provider_type": "openai" }
}
```

- `GET /system/stats` 返回 `documents / ready_documents / wiki_pages / wiki_sources / messages / conversations / providers / models / bot_channels / db_size_bytes`。
- `GET /system/info` 额外返回 `wiki_seed_path`、`wiki_seed_available`、`knowledge_mode` 等。

### 3.2 设置

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/settings
```

可编辑项：`counselor_name`、`system_prompt`、`welcome_message`、`quick_questions`、`wiki_max_pages`（1–5，默认 3）、`wiki_answer_max_chars`（2000–20000，默认 9000）、`citations_enabled`、`bot_reply_footer`。

```powershell
$body = @{ wiki_max_pages = 4; wiki_answer_max_chars = 8000 } | ConvertTo-Json
Invoke-RestMethod -Method Put http://127.0.0.1:8000/api/settings `
  -ContentType "application/json; charset=utf-8" -Body $body
```

## 4. Wiki（词条）

### 4.1 目录树

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/wiki/tree
```

```jsonc
// GET /api/wiki/tree
{
  "total": 20,
  "groups": [
    { "tag": "奖助", "count": 4, "pages": [
      { "id": "…", "title": "国家奖学金申请", "summary": "……",
        "tags": ["奖助", "奖学金"], "source_count": 1, "updated_at": "…" }
    ]}
  ]
}
```

无 tag 的词条归入「其他」分组；按 tag 排序。

### 4.2 词条列表 / 详情 / 编辑 / 删除

```powershell
# 列表（支持 q / tag / limit / offset）
Invoke-RestMethod "http://127.0.0.1:8000/api/wiki/pages?q=奖学金&limit=20"

# 详情：含 body_html、来源锚点与相关词条
Invoke-RestMethod http://127.0.0.1:8000/api/wiki/pages/<page_id>

# 编辑（局部更新，version+1，status 置回 ready）
$body = @{ summary = "更新后的摘要"; body_md = "## 申请条件`n……"; tags = @("奖助") } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Put http://127.0.0.1:8000/api/wiki/pages/<page_id> `
  -ContentType "application/json; charset=utf-8" -Body $body

# 删除
Invoke-RestMethod -Method Delete http://127.0.0.1:8000/api/wiki/pages/<page_id>
```

```jsonc
// GET /api/wiki/pages/{id}
{
  "id": "…", "slug": "国家奖学金申请", "title": "国家奖学金申请",
  "summary": "……", "body_md": "## 申请条件\n……", "body_html": "<h2>申请条件</h2>…",
  "aliases": ["国奖"], "tags": ["奖助"], "status": "ready", "version": 2,
  "source_count": 1, "created_at": "…", "updated_at": "…",
  "sources": [
    { "document_id": "…", "document_title": "国家奖学金与奖助学金评审办法（示例）",
      "source_file": "国家奖学金与奖助学金评审办法.md",
      "section": "五、申请材料清单", "quote": "……" }
  ],
  "links": [{ "page_id": "…", "title": "家庭经济困难认定", "relation": "related" }]
}
```

### 4.3 导航搜索（可观测 `method`）

```powershell
$body = @{ query = "国家奖学金怎么申请"; limit = 5 } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wiki/search `
  -ContentType "application/json; charset=utf-8" -Body $body
```

```jsonc
// POST /api/wiki/search
{
  "query": "国家奖学金怎么申请",
  "method": "keyword",              // exact | keyword | llm | none
  "candidates": [{ "page_id": "…", "title": "国家奖学金申请", "summary": "…",
                   "score": 0.62, "reason": "标题/别名/关键词重合" }],
  "selected":   [{ "page_id": "…", "title": "国家奖学金申请", "summary": "…",
                   "score": 0.62, "reason": "keyword" }],
  "latency_ms": 18
}
```

- `method=exact`：标题/别名精确命中；
- `method=keyword`：关键词覆盖率得分 ≥ 0.34；
- `method=llm`：词法信号不足时由 LLM 读目录选页；
- `method=none`：无候选（`selected=[]`）。

### 4.4 统计与重建

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/wiki/stats
# → {"pages":20,"sources":23,"documents":10,"by_tag":{...},"last_build":{...}}

# 全部重建（后台任务，202）
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wiki/rebuild `
  -ContentType "application/json" -Body "{}"
# → {"build_id":"…","status":"running"}

# 单篇同步重建
$body = @{ document_id = "<doc_id>" } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/wiki/rebuild `
  -ContentType "application/json; charset=utf-8" -Body $body

# 构建状态 / 最近记录
Invoke-RestMethod http://127.0.0.1:8000/api/wiki/builds/<build_id>
Invoke-RestMethod "http://127.0.0.1:8000/api/wiki/builds?limit=10"
```

> 全部重建会按编译单元多次调用 LLM，消耗 Token；无真实模型时构建会 `failed` 并写入中文 `error`。

## 5. 源文件（上传并编译）

### 5.1 上传

```powershell
$form = @{
  file  = Get-Item .\docs\DEMO.md
  title = "演示文件"
  tags  = "演示,测试"
}
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/documents -Form $form
```

```jsonc
// 201 Created（同步完成解析 + 编译；编译需要真实 LLM）
{
  "id": "…", "title": "演示文件", "filename": "DEMO.md", "file_ext": ".md",
  "size_bytes": 8123, "status": "ready", "error": null,
  "tags": ["演示", "测试"], "char_count": 3000, "page_count": 2,
  "created_at": "…", "updated_at": "…"
}
```

- LLM 不可用/编译失败：仍返回 doc，但 `status="failed"` 且 `error` 为中文原因，可重试；
- 超大/不支持类型：413 / 400。

### 5.2 列表 / 详情 / 删除 / 重建

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/documents        # {items,total}，doc 含 page_count
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/documents/<id>   # doc + wiki_pages:[{id,slug,title,status}]

# 删除：同时删除独占词条、移除共享词条来源、删除物理文件
Invoke-RestMethod -Method Delete http://127.0.0.1:8000/api/knowledge/documents/<id>
# → {"ok":true,"pages_removed":1}

# 重新编译单篇
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/documents/<id>/reindex
# → {"document":{...},"build":{...}}
```

### 5.3 预编译快照与统计

```powershell
# 导入 backend/data/wiki_seed.json（幂等）
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/seed
# → {"imported":true,"documents":10,"pages":20,"sources":23,"skipped":0}

Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/stats
# → {"documents":10,"ready_documents":10,"pages":20,"sources":23}
```

## 6. 对话（双层引用）

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
// POST /api/chat（真实 LLM 成功时 fallback=false；mock/失败时 true 并返回词条摘录）
{
  "conversation_id": "…",
  "reply": "…… [1][2]",
  "citations": [
    {
      "index": 1,
      "page_id": "…",
      "page_title": "国家奖学金申请",
      "summary": "……",
      "snippet": "词条正文摘录 ≤200 字",
      "sources": [
        { "document_id": "…", "document_title": "国家奖学金与奖助学金评审办法（示例）",
          "source_file": "国家奖学金与奖助学金评审办法.md",
          "section": "五、申请材料清单", "quote": "……" }
      ]
    }
  ],
  "model": "DeepSeek Chat", "provider_type": "openai",
  "latency_ms": 3200, "fallback": false, "sources_used": 2,
  "navigation": { "method": "keyword", "pages": [{ "id": "…", "title": "国家奖学金申请" }] }
}
```

- 无命中：`reply` 为「校园 Wiki 暂未收录」+ 咨询渠道，`citations=[]`，`navigation.method="none"`；
- 多轮对话：把上一轮 `conversation_id` 传回即可；历史消息中的 `citations` 反序列化为同一结构；
- 机器人渠道（OneBot / 微信 / console）复用同一回答，仅取 `reply` 发送。

## 7. 模型管理（不变）

```powershell
# 新增 OpenAI 兼容 Provider（DeepSeek）
$provider = @{
  name = "DeepSeek"; type = "openai"
  base_url = "https://api.deepseek.com/v1"; api_key = "sk-你的Key"
} | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models/providers `
  -ContentType "application/json; charset=utf-8" -Body $provider

# 新增模型并设为默认
$model = @{ provider_id = "<provider_id>"; model_name = "deepseek-chat"; is_default = $true } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models `
  -ContentType "application/json; charset=utf-8" -Body $model

# 连通性测试（失败也返回 200 + ok=false）
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/models/providers/<provider_id>/test
```

`api_key` 只在服务端保存，任何响应只返回 `api_key_masked`（长度 ≤8 显示 `****`，否则 `前4****后4`）。

## 8. 机器人调试（不变）

```powershell
# 控制台通道模拟学生消息（走完整 Wiki 问答链路）
$channels = Invoke-RestMethod http://127.0.0.1:8000/api/bots/channels
$console = $channels.items | Where-Object { $_.type -eq "console" } | Select-Object -First 1
$body = @{ message = "心理中心怎么预约？"; user_name = "演示同学" } | ConvertTo-Json
Invoke-RestMethod -Method Post ("http://127.0.0.1:8000/api/bots/channels/" + $console.id + "/test") `
  -ContentType "application/json; charset=utf-8" -Body $body
```

公众号 / OneBot 的回调地址、签名与鉴权规则见 [BOT-INTEGRATION.md](BOT-INTEGRATION.md)。

# API 速览（LLM Wiki · v0.1.1）

> 本文件是接口的快速上手版本；**字段与行为的唯一事实源是 [WIKI-DESIGN.md](WIKI-DESIGN.md) §4 与 [CONTRACT.md](CONTRACT.md) §5**。启动服务后可访问交互式文档：
>
> - Swagger UI：<http://127.0.0.1:8000/api/docs>
> - OpenAPI JSON：<http://127.0.0.1:8000/api/openapi.json>

## 1. 通用约定

| 项目 | 说明 |
|------|------|
| 前缀 | 所有接口位于 `/api` 下，JSON 响应（上传为 multipart，公众号为 XML） |
| 错误格式 | `HTTPException(status_code, detail="中文可读信息")`，前端按 `detail` 展示 |
| 鉴权 | 默认无鉴权（本地演示）；设置环境变量 `NPU_ADMIN_TOKEN` 后，除 `POST /api/chat`、`GET/HEAD/OPTIONS` 与机器人 webhook（自带通道 token/签名）外的**写操作**以及**历史会话接口**需请求头 `X-Admin-Token: <token>`；缺失/错误 → 401 |
| 时间格式 | ISO 字符串（本地时间），ID 为 32 位 hex |
| 上传限制 | 单文件 ≤ 20MB（`NPU_MAX_UPLOAD_MB`），支持 `.md/.markdown/.txt/.pdf/.docx/.doc/.xlsx/.csv/.json/.html/.htm` |
| 知识模型 | `knowledge_mode: "llm-wiki"`；无 embedding / 向量库 / rerank / chunk 检索 |
| LLM 依赖 | 上传编译、高质量回答需要真实 LLM；mock 模型下编译会被拒绝、问答降级（`fallback=true`） |
| 安全响应头 | 所有响应默认带 `Content-Security-Policy`、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: same-origin`；CORS 默认关闭（仅 `NPU_CORS_ORIGINS` 白名单） |
| 部署约束 | 监听非 `127.0.0.1` 时必须设置 `NPU_ADMIN_TOKEN`，否则拒绝启动（仅可信内网临时演示可 `NPU_ALLOW_INSECURE_LAN=1`）；`GET /system/info` 不返回任何绝对路径 |

## 2. 接口分组

| 分组 | 主要接口 | 说明 |
|------|----------|------|
| 系统 | `GET /health`、`GET /system/info`、`GET /system/stats`、`GET/PUT /settings` | 健康检查（含 `knowledge_mode`、`wiki_pages`）、运行信息、统计、可编辑设置 |
| 对话 | `POST /chat`、`GET /chat/conversations`、`GET /chat/conversations/{id}/messages`、`DELETE /chat/conversations/{id}` | Wiki 导航问答 + 双层引用 + `navigation` 元信息 |
| 源文件 | `POST/GET /knowledge/documents`、`GET/DELETE /knowledge/documents/{id}`、`GET .../download`、`POST .../reindex`、`POST /knowledge/seed`、`GET /knowledge/stats` | 上传并**同步编译**、列表/详情/下载/删除/重建、导入预编译快照、统计 |
| 快速收录 | `POST /knowledge/import/url`、`POST /knowledge/import/text`、`POST /knowledge/import/batch` | 移动端：URL 抓取正文、粘贴文本、批量链接（≤5）→ 生成源文件并编译词条（见 [MOBILE-OPS.md](MOBILE-OPS.md)） |
| Wiki | `GET /wiki/tree`、`GET /wiki/pages`、`GET/PUT/DELETE /wiki/pages/{id}`、`POST /wiki/search`、`GET /wiki/stats`、`POST /wiki/rebuild`、`GET /wiki/builds`、`GET /wiki/builds/{id}`、`POST /wiki/seed` | 目录、浏览、搜索、编辑、重建与构建记录 |
| 模型 | `GET/POST /models/providers`、`PUT/DELETE /models/providers/{id}`、`POST .../test`、`GET/POST /models`、`PUT/DELETE /models/{id}`、`POST /models/{id}/default`、`POST /models/{id}/test` | Provider / 模型 CRUD、默认模型、连通性测试 |
| 机器人 | `GET/POST /bots/channels`、`PUT/DELETE /bots/channels/{id}`、`POST .../test`、`GET /bots/logs`、`GET/POST /bots/wechat/{id}` | 通道管理、控制台模拟、公众号回调；回答复用 counselor |

> **已移除**：`GET /knowledge/documents/{id}/chunks`、`POST /knowledge/search`（经典 RAG 遗留）。

## 3. 系统

### 3.1 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

```jsonc
// GET /api/health
{
  "status": "ok", "app": "NPU-Assistant", "version": "0.1.1",
  "time": "2026-10-22T17:30:00",
  "knowledge_mode": "llm-wiki",
  "wiki_pages": 20,
  "default_model": { "id": "…", "display_name": "DeepSeek Chat",
                     "model_name": "deepseek-chat", "provider_type": "openai" }
}
```

- `GET /system/stats` 返回 `documents / ready_documents / wiki_pages / wiki_sources / messages / conversations / providers / models / bot_channels / db_size_bytes`。
- `GET /system/info` 返回公开的运行配置：`app / version / knowledge_mode / wiki_seed_available / frontend_ready / supported_exts / max_upload_mb / url_import_max_mb`（默认 `8.0`）/ `url_import_max_urls`（`5`）/ `admin_token_required` / `auto_seed` / `cors_enabled` / `insecure_bind`；**不返回 `data_dir`/`db_path` 等绝对路径**（防止泄露部署布局）。

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

# body_html 由后端 html.escape + markdown 渲染，并做链接协议白名单清洗：
# javascript:/data:/vbscript:/file: 等危险链接移除 href；外链补 target + rel="noopener noreferrer"。

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
  "summary": "……", "body_md": "## 申请条件\n……", "body_html": "<h2>申请条件</h2>…",  "aliases": ["国奖"], "tags": ["奖助"], "status": "ready", "version": 2,
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
  "source_type": "file", "source_url": "",
  "created_at": "…", "updated_at": "…"
}
```

- LLM 不可用/编译失败：仍返回 doc，但 `status="failed"` 且 `error` 为中文原因，可重试；
- 超大/不支持类型：413 / 400。

### 5.2 列表 / 详情 / 下载 / 删除 / 重建

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/documents        # {items,total}，doc 含 page_count
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/documents/<id>   # doc + wiki_pages:[{id,slug,title,status}]

# 下载原始源文件（附件形式，文件名取上传时的原始文件名）
Invoke-WebRequest http://127.0.0.1:8000/api/knowledge/documents/<id>/download -OutFile 原文.pdf

# 删除：同时删除独占词条、移除共享词条来源、删除物理文件
Invoke-RestMethod -Method Delete http://127.0.0.1:8000/api/knowledge/documents/<id>
# → {"ok":true,"pages_removed":1}

# 重新编译单篇
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/documents/<id>/reindex
# → {"document":{...},"build":{...}}
```

> 对话引用的 `citations[].sources[]` 新增 `source_type`、`source_url`、`download_url`：
> 收录自网页的文档给原网页地址，本地上传文件给 `/api/knowledge/documents/{id}/download`；
> 机器人（QQ/微信/控制台）会在回答末尾追加「相关文件」绝对链接。

### 5.3 预编译快照与统计

```powershell
# 导入 backend/data/wiki_seed.json（幂等）
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/seed
# → {"imported":true,"documents":10,"pages":20,"sources":23,"skipped":0}

Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/stats
# → {"documents":10,"ready_documents":10,"pages":20,"sources":23}
```

### 5.4 快速收录（移动端 / 机器人管理员）

> 完整行为见 [MOBILE-OPS.md](MOBILE-OPS.md) §2。三个接口均属管理类写操作：设置 `NPU_ADMIN_TOKEN` 后必须带 `X-Admin-Token`，否则 401。

**收录网页 / 公众号链接** `POST /knowledge/import/url`

```powershell
$body = @{ url = "https://mp.weixin.qq.com/s/xxxx"; tags = @("公众号", "奖助") } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/import/url `
  -ContentType "application/json; charset=utf-8" -Body $body
```

```jsonc
// 201 Created（新链接；同一 URL 再次收录返回 200 + duplicated=true，不重复编译）
{
  "duplicated": false,
  "document": {
    "id": "…", "title": "国家奖学金申请指南（公众号）", "status": "ready",
    "page_count": 3, "source_type": "url", "source_url": "https://mp.weixin.qq.com/s/xxxx",
    "error": null
  },
  "pages": [{ "id": "…", "title": "国家奖学金申请", "summary": "……" }]
}
```

- 仅支持 `http/https`；手动跟随 ≤5 次重定向、超时 30s、响应 ≤8MB（`NPU_URL_IMPORT_MAX_BYTES` 可调，超限提示会用「文本收录」或调大该变量）、仅接受 HTML/纯文本；
- 默认**拒绝私网/回环地址**（内网部署可设 `NPU_URL_IMPORT_ALLOW_PRIVATE=1`）；`file://`、带账号密码 URL 一律 400；
- 正文提取优先 `#js_content` / `.rich_media_content` / `article|main`，去脚本导航页脚；提取不到正文 → 400 中文原因。

**收录文本** `POST /knowledge/import/text`

```powershell
$body = @{ title = "请假政策（转自班级群）"; text = "……"; tags = @("微信", "请假") } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/import/text `
  -ContentType "application/json; charset=utf-8" -Body $body
```

- `text` 30–200000 字；`title` 为空时取首行（≤40 字）或「未命名资料-时间」；
- 返回结构与 URL 收录一致，`document.source_type="text"`。

**批量收录链接** `POST /knowledge/import/batch`

```powershell
$body = @{ urls = @("https://a.example/1", "https://b.example/2"); tags = @("待整理") } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/import/batch `
  -ContentType "application/json; charset=utf-8" -Body $body
```

```jsonc
// 200 OK：单次 ≤5 条，顺序处理，单条失败不影响其它
{
  "items": [
    { "url": "https://a.example/1", "ok": true, "duplicated": false,
      "document_id": "…", "pages": [{ "id": "…", "title": "…" }], "error": null },
    { "url": "https://b.example/2", "ok": false, "duplicated": false,
      "document_id": null, "pages": [], "error": "网页抓取失败：…" }
  ]
}
```

> 文档对象新增 `source_type`（`file|url|text`）与 `source_url`，在 `/knowledge/documents` 列表与详情中返回。

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
- 匿名会话：未传 `session_key` / `conversation_id` 时，服务端生成随机 `session_key`（`web:anonymous-<随机>`），两次提问不会串到同一会话；
- **历史会话接口**（含学生隐私）：`GET /chat/conversations`、`GET /chat/conversations/{id}/messages` 在配置 `NPU_ADMIN_TOKEN` 后需 `X-Admin-Token`（缺失/错误 → 401）；未配置时公开；`POST /chat` 始终公开；
- 机器人渠道（QQ 官方 / 微信 / console）复用同一回答，仅取 `reply` 发送。

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

公众号的回调地址、签名与鉴权规则见 [BOT-INTEGRATION.md](BOT-INTEGRATION.md)。

> **通道安全**：`/bots/channels` 返回的 `config` 中凭据（QQ 官方 `app_secret` / 微信 `token`）脱敏为 `****`+后 4 位，`admin_user_ids` 只显示 `***（N 个管理员）`，并以布尔 `has_access_token` 表示是否已配置；PUT 传空串或脱敏占位表示「不修改」。微信 token 不得为空或 `npu-assistant`，回调校验 ±300 秒时间窗 + nonce 防重放（重复 403，进程内缓存），webhook 请求体上限 2MB（413）。配置 `NPU_ADMIN_TOKEN` 后 `GET /bots/logs` 需 `X-Admin-Token`。

## 9. 手机扫码访问与一键公网（v0.1.1 新增）

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/mobile/access` | 公开 | 返回 `preferred_url`、`urls[]`（`kind=public/tunnel/lan/current`）、`qr`（PNG data URL）、`token_required`、`tunnel` 状态与 `hint` |
| `GET /api/mobile/qr.png?url=<http(s)>` | 公开 | 生成指定地址的二维码 PNG；非 http/https 返回 400 |
| `POST /api/mobile/tunnel/start` | 管理令牌；未配置令牌时仅允许本机 | 启动 `cloudflared` 临时隧道，返回最新 access 信息；未安装时 400 + 安装提示 |
| `POST /api/mobile/tunnel/stop` | 同上 | 关闭隧道 |

**首次启用 / 轮换服务端令牌** `POST /system/admin-token/init`

```jsonc
// 未启用：仅本机可初始化；已启用：本机或携带当前 X-Admin-Token 的请求可轮换
{"token": "至少16位随机字符串"}
// 200（首次启用 rotated=false；替换旧令牌 rotated=true）
{"ok": true, "admin_token_required": true, "rotated": true, "persisted": "admin_token.txt"}
```

启用后立即对所有写接口生效，并持久化到 `backend/data/admin_token.txt`（已 gitignore；显式环境变量 `NPU_ADMIN_TOKEN` 优先）。已启用时可在设置页「替换令牌」，服务端会校验当前令牌后原子替换，旧令牌立即失效；携带错误令牌返回 401，非本机且未带当前令牌返回 401，令牌过短返回 400。若通过环境变量配置令牌，轮换请求返回 409，请在部署配置中修改并重启。设置页在服务端未启用令牌时自动显示「启用服务端令牌」按钮。

```jsonc
// GET /api/mobile/access（节选）
{
  "app_version": "0.1.1",
  "token_required": true,
  "preferred_url": "https://xxx.trycloudflare.com",
  "urls": [
    {"url": "https://xxx.trycloudflare.com", "kind": "tunnel", "label": "公网 HTTPS（临时隧道）", "secure": true, "pwa": true},
    {"url": "http://192.168.1.8:8000", "kind": "lan", "label": "局域网", "secure": false, "pwa": false}
  ],
  "qr": "data:image/png;base64,...",
  "tunnel": {"available": true, "running": true, "public_url": "https://xxx.trycloudflare.com", "started_at": 1760000000, "error": ""}
}
```

- 二维码只编码 URL，不含管理令牌；手机扫码后仍需在「设置 → 移动访问与安全」登录（若服务端启用了令牌）。
- 临时隧道地址每次启动都会变化，仅用于演示/临时访问；生产请使用命名 Cloudflare Tunnel / Nginx 反代，并设置 `NPU_PUBLIC_URL` 固定地址。
- 可用环境变量：`NPU_PUBLIC_URL`、`NPU_CLOUDFLARED`、`NPU_TUNNEL_TIMEOUT`（默认 30s）。

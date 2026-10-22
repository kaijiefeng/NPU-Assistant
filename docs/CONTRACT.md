# NPU-Assistant 开发契约（Lead 维护）

> 本文件是团队并行开发的**唯一接口事实源**。所有成员开工前必须完整阅读；任何需要修改接口的情况，先 `send_message` 通知 Lead，由 Lead 更新本文件后再实现。
> 目标：在互不踩踏写作用域的前提下，并行产出一个可运行、可演示、可测试的 MVP。

---

## 1. 产品与 MVP 范围

**NPU-Assistant（校园助手）**：辅导员的数字分身。四项核心能力：

| # | 能力 | MVP 交付 |
|---|------|----------|
| 1 | 辅导员数字分身问答 | Web 对话页 + 基于知识库的 RAG 回答 + 引用来源卡片 + 多轮会话 |
| 2 | LLM Wiki 知识库 | 上传 md/txt/pdf/docx/xlsx/csv/json/html → 解析 → 切片 → 建立索引 → 检索 → 问答引用真实文件内容 |
| 3 | LLM 接入与模型管理 | OpenAI 兼容 / Ollama / 内置演示模型三类 Provider；模型增删改查、设默认、连通性测试 |
| 4 | 微信 / QQ 机器人 | OneBot 11（QQ，反向 WS + 反向 HTTP 事件 + HTTP API 回复）、微信公众号（明文模式回调 + XML 回复）、控制台调试通道 |

**非目标（MVP 不做）**：多租户与权限体系、流式输出、向量数据库/在线 Embedding、语音、复杂插件系统、微信公众号加密模式（只做明文模式并在文档中说明）。

---

## 2. 技术栈与运行方式

- 后端：Python 3.11 + FastAPI + SQLite（标准库 `sqlite3`，无 ORM）+ httpx + jieba + pypdf/python-docx/openpyxl/bs4
- 前端：**纯静态** HTML + CSS + 原生 ES Module（无 Node 构建、无框架），由后端直接托管
- 虚拟环境：仓库根目录 `.venv`（已创建并装好依赖）。**所有 Python 命令统一使用**：
  ```powershell
  .\.venv\Scripts\python.exe ...
  ```
- 启动：`.\scripts\run.ps1`（默认 http://127.0.0.1:8000 ）
- 测试：`.\scripts\check.ps1`（等价于 `pytest backend/tests -q`，工作目录 `backend`）

> 端口 8000 被占用时用 `.\scripts\run.ps1 -Port 8010`。不要自行启动第二个常驻服务；联调需要时用后台任务并说明端口。

---

## 3. 目录与写作用域（重要）

| 归属 | 写作用域（只能改这些） |
|------|------------------------|
| **Lead** | `docs/CONTRACT.md`、`backend/app/{main,config,db}.py`、`backend/app/routers/system.py`、`backend/app/services/retrieval_core.py`、`scripts/`、`.gitignore`、`.env.example` |
| **backend-core** | `backend/app/{security.py}`、`backend/app/services/{llm,counselor}.py`、`backend/app/routers/{chat,models}.py` |
| **kb-rag** | `backend/app/services/{ingest,parsing,retrieval,rag}.py`、`backend/app/routers/knowledge.py` |
| **web-ui** | `frontend/**`（`index.html`、`assets/` 下 css/js 等） |
| **bot-gateway** | `backend/app/adapters/**`、`backend/app/services/bot_pipeline.py`、`backend/app/routers/bots.py` |
| **qa-docs** | `README.md`、`docs/{ARCHITECTURE,BOT-INTEGRATION,DEMO,API}.md`、`backend/tests/**`、`backend/data/seed_docs/**`、`scripts/`（只读，不改） |

规则：
1. **绝不修改他人作用域内的文件**；需要改接口/改他人文件时，发消息给 Lead 或在共享任务评论中说明。
2. 每个文件只有一个 owner；发现文件内容与自己预期不符（他人正在写）时，不要覆盖，先沟通。
3. 不新增第三方依赖（`requirements.txt` 由 Lead 维护）；确实需要时先申请。已装依赖见 `backend/requirements.txt`。
4. 代码注释/文档用中文，标识符用英文；API 字段用 `snake_case`。
5. 所有时间戳为 ISO 字符串（`db.now()`）；ID 为 `db.new_id()` 生成的 32 位 hex。
6. 错误统一 `HTTPException(status_code, detail="中文可读信息")`；前端按 `detail` 展示。
7. 数据库读写一律走 `app/db.py` 的 `query/query_one/execute/executemany`（每次调用新连接，线程安全）。
8. 同步函数优先（FastAPI 会放线程池）；WebSocket 处理器里调用同步服务时用 `await asyncio.to_thread(...)`。
9. 不要 `print` 调试；用 `logging.getLogger("npu-assistant")`。
10. **验收前必须自测**：至少用 `TestClient` 或 curl 走通自己负责的接口，并保证 `.\scripts\check.ps1` 中与自己相关的测试通过。

---

## 4. 数据库（已实现，见 `backend/app/db.py`）

表：`settings`、`documents`、`chunks`、`providers`、`models_registry`、`conversations`、`messages`、`bot_channels`。
首启自动写入默认设置、内置演示 Provider（type=`mock`）+ 默认模型、控制台 Bot 通道。

关键字段：
- `documents.status`: `pending|processing|ready|failed`；失败原因写 `error`；`chunk_count/char_count` 由入库逻辑维护。
- `chunks`: `id, document_id, seq, section, content, tokens`；检索时 `JOIN documents` 取 `title`。
- `providers.type`: `openai|ollama|mock`；`api_key` 只在服务端使用，API 返回时必须脱敏。
- `models_registry.is_default`: 全表至多一个为 1；`enabled=0` 不参与默认模型选择。
- `conversations.channel`: `web|onebot11|wechat_mp|console`；`session_key` 是渠道内会话标识（见 §7）。
- `messages.role`: `user|assistant`；`citations` 为 JSON 数组。
- 设置项（`settings` 表，通过 `GET/PUT /api/settings` 读写）：

| key | 默认 | 说明 |
|-----|------|------|
| `counselor_name` | 小南 | 分身名字 |
| `system_prompt` | 见 config.py | 系统提示词，可被管理员覆盖 |
| `welcome_message` | 见 config.py | 欢迎语 |
| `quick_questions` | 4 条 JSON 数组 | 首页快捷问题 |
| `retrieval_top_k` | 4 | 检索条数 |
| `retrieval_min_score` | 0.05 | 相关性阈值（低于则视为无命中） |
| `citations_enabled` | true | 是否返回/展示引用 |
| `bot_reply_footer` | 见 config.py | 机器人回复尾部提示语 |

---

## 5. API 契约（前缀 `/api`，响应 `application/json`，除非注明）

### 5.1 系统（Lead 已实现）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | `{status, app, version, time, default_model\|null}` |
| GET | `/system/info` | 运行目录、支持扩展名、上传上限、前端是否就绪 |
| GET | `/system/stats` | 文档/切片/消息/会话/模型/通道计数 + `db_size_bytes` |
| GET | `/settings` | 全部设置（`quick_questions` 反序列化为数组） |
| PUT | `/settings` | 局部更新，body 为可编辑设置项 JSON |

### 5.2 对话（backend-core）

**`POST /api/chat`**
```jsonc
// request
{ "message": "国家奖学金怎么申请？", "conversation_id": null, "channel": "web", "session_key": "web:demo" }
// response
{
  "conversation_id": "…", "reply": "…… [1][2]",
  "citations": [
    { "index": 1, "chunk_id": "…", "document_id": "…", "document_title": "国家奖学金评审办法",
      "section": "申请条件", "snippet": "……", "score": 8.31 }
  ],
  "model": "本地知识库问答（演示）", "provider_type": "mock",
  "latency_ms": 412, "fallback": true, "sources_used": 2
}
```
- `message` 为空或 >2000 字 → 400。
- 无可用模型 / 模型调用失败 → **必须降级**为抽取式回答（`fallback=true`），不能 500。
- 知识库无命中 → 礼貌说明“知识库暂未收录”，给出咨询渠道，`citations=[]`。
- 命中内容只允许来自检索结果；citations 的 `index` 与回答中 `[n]` 标注、上下文编号一致。
- 服务端持久化 user + assistant 两条消息，并维护 `conversations.updated_at`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/chat/conversations?channel=&limit=20` | `{items:[{id,channel,session_key,user_label,last_message,message_count,updated_at}]}` |
| GET | `/chat/conversations/{id}/messages` | `{conversation:{…}, items:[{id,role,content,citations,model,latency_ms,created_at}]}`；404 不存在 |
| DELETE | `/chat/conversations/{id}` | `{ok:true}`（级联删消息） |

### 5.3 知识库（kb-rag）

| 方法 | 路径 | 请求/响应 |
|------|------|-----------|
| POST | `/knowledge/documents` | multipart：`file`（必填）、`title`（可选）、`tags`（可选，逗号分隔字符串）。同步完成解析与入库，返回 201 + doc 对象。超 20MB→413；扩展名不支持→400；解析失败→返回 doc 且 `status="failed"` + `error` |
| GET | `/knowledge/documents` | `{items:[doc…], total}`（按 `created_at` 倒序） |
| GET | `/knowledge/documents/{id}` | doc + `chunks_preview`（前 5 条，`content` 截断 300 字） |
| DELETE | `/knowledge/documents/{id}` | `{ok:true}`，同时删除物理文件与切片 |
| POST | `/knowledge/documents/{id}/reindex` | 重新解析切片，返回 doc |
| GET | `/knowledge/documents/{id}/chunks?offset=0&limit=50` | `{items:[{id,seq,section,content,tokens}], total}` |
| POST | `/knowledge/search` | `{query, top_k?}` → `{query, latency_ms, items:[{chunk_id,document_id,document_title,section,snippet,content,score}]}`；空库返回空数组 |
| POST | `/knowledge/seed` | 导入 `backend/data/seed_docs/` 下示例文档（幂等：同名文件已存在则跳过）→ `{ingested:[…], skipped:[…], total_documents}` |
| GET | `/knowledge/stats` | `{documents, chunks, chars, by_file_type:{".md":2,…}}` |

doc 对象字段：`id,title,filename,file_ext,size_bytes,status,error,tags(char 数组),char_count,chunk_count,created_at,updated_at`。

解析要求：pdf→pypdf；docx→python-docx（含表格文本）；xlsx→openpyxl（sheet 名 + 行单元格）；csv→按行；json→格式化文本；html→bs4 去标签；md/txt→原文。切片：按标题/段落切，目标 300–600 字符、重叠约 60 字符，保留 `section`（最近标题）与 `seq`。禁止空切片。入库后 `chunk_count` 必须准确。

### 5.4 模型管理（backend-core）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/models/providers` | `{items:[{id,name,type,base_url,api_key_masked,embedding_model,model_count,created_at}]}` |
| POST | `/models/providers` | `{name,type,base_url?,api_key?,embedding_model?}` → 201 provider；`type` 非法→400；`openai` 必填 base_url+api_key+`name` |
| PUT | `/models/providers/{id}` | 局部更新；`api_key` 传空串表示不修改 |
| DELETE | `/models/providers/{id}` | `{ok:true}`，级联删模型；不允许删除最后一个 mock provider? 允许 |
| POST | `/models/providers/{id}/test` | `{ok,message,latency_ms,models?:[str]}`；openai 调 `{base_url}/models`（失败也返回 ok=false 的对象，HTTP 200） |
| GET | `/models` | `{items:[{id,provider_id,provider_name,provider_type,model_name,display_name,temperature,max_tokens,is_default,enabled,created_at}]}` |
| POST | `/models` | `{provider_id,model_name,display_name?,temperature?,max_tokens?,is_default?}` → 201；provider 不存在→400 |
| PUT | `/models/{id}` | 局部更新 |
| DELETE | `/models/{id}` | `{ok:true}`；删除默认模型后自动把最早创建的启用模型设为默认 |
| POST | `/models/{id}/default` | 设为默认（事务内清除其它默认），返回该模型 |
| POST | `/models/{id}/test` | 真实发一条 `你好` chat 请求 → `{ok,message,latency_ms,sample}` |

`api_key_masked` 规则：长度≤8 显示 `****`，否则 `前4****后4`。
Provider 调用约定：`openai` 走 `{base_url}/chat/completions`（`base_url` 形如 `https://api.openai.com/v1`）；`ollama` 走 `{base_url}/api/chat`（形如 `http://127.0.0.1:11434`，请求 `{model,messages,stream:false}`）；`mock` 不联网。超时 60s。所有调用不得因网络异常抛 500，应返回可读错误。

### 5.5 机器人（bot-gateway）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/bots/channels` | `{items:[{id,type,name,config,enabled,status,last_seen_at,created_at,webhook_urls:{…}}]}`；`webhook_urls` 给出 OneBot WS/HTTP、微信回调的完整相对路径 |
| POST | `/bots/channels` | `{type,name,config}` → 201；`type` ∈ `onebot11|wechat_mp|console` |
| PUT | `/bots/channels/{id}` | `{name?,config?,enabled?}` |
| DELETE | `/bots/channels/{id}` | `{ok:true}`；`console` 通道不可删（400） |
| POST | `/bots/channels/{id}/test` | `{message,user_id?,user_name?}` → `{channel,conversation_id,reply,citations,latency_ms,outbound}`；`outbound` 为将要发送的报文（用于前端展示） |
| GET | `/bots/logs?limit=50` | `{items:[{id,channel,session_key,user_label,message,reply,created_at}]}`（从 `conversations/messages` 聚合非 web 渠道） |
| WS | `/bots/onebot/{channel_id}/ws?access_token=` | OneBot 11 反向 WebSocket；收到 `post_type=message` 事件→生成回复→通过配置的 HTTP API 发回 |
| POST | `/bots/onebot/{channel_id}/event?access_token=` | OneBot 11 反向 HTTP 事件入口，返回 `{"status":"ok"}`（事件处理异步/快速返回） |
| GET | `/bots/wechat/{channel_id}?signature=&timestamp=&nonce=&echostr=` | 公众号接入校验：sha1(sort(token,timestamp,nonce)) 匹配则返回 echostr 明文，否则 403 |
| POST | `/bots/wechat/{channel_id}?signature=&timestamp=&nonce=` | 接收 XML 文本消息，回复明文 XML（`ToUserName/FromUserName/CreateTime/MsgType/Content`）；签名错误 403；非文本消息回复引导语 |

通道 `config`：
- `onebot11`: `{ "api_base": "http://127.0.0.1:3000", "access_token": "", "self_id": "", "group_mention_only": "true" }`
- `wechat_mp`: `{ "token": "npu-assistant", "app_id": "", "welcome": "" }`
- `console`: `{}`

OneBot 鉴权（实现已加固）：反向 WS 同时接受 `Authorization: Bearer <access_token>` 与 `?access_token=`（两者都带时必须一致，错误 401）；反向 HTTP 事件接受 query token 或 `X-Signature: sha1=<HMAC-SHA1(body, token)>`，未配置 token 时放行。群聊默认仅在被 @ 时回复（`group_mention_only=false` 可放开），`user_id==self_id` 的消息一律丢弃。WS 与 HTTP 事件同时开启时按 `channel_id+message_id` 进程内去重。
微信公众号：被动回复 `Content-Type: text/xml; charset=utf-8`；问答链路超过约 4.5s 时先回复兜底提示（MVP 无异步客服消息推送，用户重发即可）。

会话标识：
- web: `web:{conversation_id 或 anonymous}`
- QQ 私聊: `onebot11:{self_id}:private:{user_id}`；群聊: `onebot11:{self_id}:group:{group_id}:{user_id}`
- 微信: `wechat_mp:{channel_id}:{FromUserName}`

回复发送（OneBot）：`POST {api_base}/send_private_msg` 或 `/send_group_msg`，body `{user_id|group_id, message}`；有 `access_token` 时带 `Authorization: Bearer <token>`。发送失败只记日志（status 可置 `error`），不影响 HTTP 响应。

### 5.6 前端（web-ui）

- 单页应用，hash 路由，5 个视图：`#/chat` 对话、`#/knowledge` 知识库、`#/models` 模型、`#/bots` 机器人、`#/settings` 设置。
- 直接请求 `location.origin + '/api/...'`；所有错误 toast 展示 `detail`。
- 设计：西工大深蓝（`#0b3d91` 系）+ 科技青点缀；中文字体；卡片式；响应式（≥1024 桌面两栏、移动端单栏）；空状态/加载态/失败态齐全；不使用 CDN 资源（离线可用），图标用内联 SVG。
- 对话页：欢迎语 + 快捷问题 + 消息流 + 引用卡片（标题/片段/可展开）+ 输入框（Enter 发送、Shift+Enter 换行）+ 输入中状态 + 新建会话/历史会话。
- 知识库：拖拽上传、上传进度/结果、文档表格（状态徽标、大小、切片数、时间）、详情抽屉（切片预览）、检索测试框、删除确认、一键导入示例资料。
- 模型：Provider 卡片（类型徽标、脱敏 Key、测试连接）、添加/编辑 Provider 弹窗、模型列表（设默认、启停、测试、删除）、默认模型高亮。
- 机器人：三个通道卡片（QQ OneBot / 微信公众号 / 控制台），配置表单保存、Webhook 地址一键复制、控制台模拟对话（调用 `/bots/channels/{id}/test`）、消息日志。
- 设置：分身名称、系统提示词、欢迎语、快捷问题、检索 top_k/阈值、引用开关、回复尾注，保存后生效。
- 无控制台报错；`frontend/index.html` 必须能通过 `http://127.0.0.1:8000/` 直接打开。

---

## 6. 内部服务接口（跨模块 Import 契约）

```python
# backend/app/services/llm.py                          (backend-core)
def chat(messages: list[dict], *, model_id: str | None = None,
         temperature: float | None = None, max_tokens: int | None = None) -> dict:
    """返回 {content, model, provider_name, provider_type, fallback, usage, latency_ms}。
    mock provider 也要返回可读的 content（由 counselor 覆盖为抽取式回答）。"""

def default_model() -> dict | None: ...   # db.get_default_model() 的薄封装，含 provider 字段
def test_provider(provider_id: str) -> dict: ...
def test_model(model_id: str) -> dict: ...

# backend/app/services/rag.py                          (kb-rag)
def search(query: str, top_k: int | None = None) -> list[dict]:
    """返回 [{chunk_id, document_id, document_title, section, content, snippet, score}]，
    无命中返回 []，绝不抛异常。"""

def format_context(chunks: list[dict], max_chars: int = 7000) -> str:
    """把检索结果格式化为带编号的【知识库资料】文本。"""

# backend/app/services/ingest.py                       (kb-rag)
def ingest_upload(filename: str, data: bytes, title: str | None = None,
                  tags: list[str] | None = None) -> dict: ...
def reindex_document(doc_id: str) -> dict: ...
def delete_document(doc_id: str) -> bool: ...
def seed_documents() -> dict: ...

# backend/app/services/counselor.py                    (backend-core)
def answer(question: str, *, channel: str = "web", session_key: str = "web:default",
           conversation_id: str | None = None, user_label: str | None = None) -> dict:
    """完整问答链路（检索→提示词→LLM/降级→落库），供 /api/chat 与 bot-gateway 共用。
    返回 {conversation_id, reply, citations, model, provider_type, latency_ms,
          fallback, sources_used}。"""

# backend/app/services/retrieval_core.py               (Lead + codex)
def rank_chunks(query: str, chunks: list[dict], top_k: int = 5) -> list[tuple[dict, float]]: ...
```

bot-gateway 只通过 `counselor.answer(...)` 获取回复，**不要**自己拼提示词或访问 LLM/知识库内部实现。

---

## 7. 验收标准（Definition of Done）

- [ ] `.\scripts\check.ps1` 全绿（pytest：检索核心、知识库 CRUD、对话降级/引用、模型管理、机器人签名与模拟对话）
- [ ] 冷启动（空 `backend/data/app.db`）后 `GET /api/health` 返回 ok；`POST /api/knowledge/seed` 导入 ≥6 篇示例文档成功
- [ ] 上传 `.md/.txt/.pdf/.docx` 均能 ready，`chunk_count>0`；`POST /api/knowledge/search` 能命中相关切片
- [ ] `POST /api/chat` 针对示例问题返回带 citation 的回答（无 API Key 时走演示降级，`fallback=true`；配置真实 OpenAI 兼容模型后 `fallback=false`）
- [ ] 模型管理可新增 Provider/模型、测试连接、设为默认；默认模型会在 `/api/health` 中体现
- [ ] `POST /api/bots/channels/{console}/test` 返回完整回复；OneBot WS/HTTP 入口可接收事件；微信公众号签名校验正确（可单测）
- [ ] 浏览器打开 `http://127.0.0.1:8000/`，5 个页面均可用、无控制台报错
- [ ] `README.md` 5 分钟上手（安装→启动→演示路径）；`docs/BOT-INTEGRATION.md` 覆盖 NapCat/LLM/公众号配置

---

## 8. 协作流程

1. 读本契约 → 在共享任务板 `team_task_get` 领取自己的任务 → `team_task_update(action=claim)`。
2. 先实现**接口骨架 + 主路径**，尽快让服务能启动；再补错误处理与边界。
3. 自测通过后 `team_task_update(action=complete)`，并在完成消息里写：改了哪些文件、接口是否与契约一致、如何自测、遗留问题。
4. 遇到契约未覆盖的复杂业务问题：先按契约实现最小可用版本，同时 `send_message` 给 Lead，Lead 会调度 codex 咨询。
5. 完成前确认没有改动他人作用域文件（`git status` 自查）。

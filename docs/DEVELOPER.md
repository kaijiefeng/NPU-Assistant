# NPU-Assistant 开发手册

> 适用版本：v0.2.0（2026-11-09 发布）  
> 读者：接手开发、二次开发、代码评审、交接同学  
> 目标：读完能本地跑起来、看懂核心链路、知道改哪里、不踩已知坑。  
> 先读：`docs/ARCHITECTURE.md`（架构）、`docs/WIKI-DESIGN.md`（知识层设计）、`docs/CONTRACT.md`（接口契约）、`docs/API.md`（接口速览）。

---

## 1. 项目定位与技术栈

**一句话**：把校园文件用 LLM 编译成结构化 Wiki 词条，学生提问时导航到词条，再由 LLM 阅读词条作答，并给出「词条 + 源文件 · 章节 · 原文摘录」双层引用。

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+、FastAPI、SQLite（stdlib `sqlite3`）、httpx、jieba |
| 解析 | pypdf、python-docx、legacy-doc、openpyxl、beautifulsoup4、markdown |
| 机器人 | websockets、QQ 官方机器人、微信公众号明文 XML |
| 前端 | 纯静态 HTML/CSS/原生 ES Module，无 Node 构建、无 CDN |
| 测试 | pytest + TestClient + fake LLM；Chrome CDP UI 冒烟脚本 |
| 部署 | 单进程 Uvicorn；无外部数据库/队列/向量库 |

**明确非目标**：embedding、向量数据库、rerank、chunk 检索、流式输出（v0.1.x）、多租户、语音、插件系统。

---

## 2. 快速开始

```powershell
# 1) 建环境 + 装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 2) 启动（默认 127.0.0.1:8000）
.\scripts\run.ps1

# 3) 测试
.\scripts\check.ps1

# 4) 冒烟（需服务已启动）
.\scripts\smoke.ps1

# 5) 备份
.\scripts\backup.ps1
```

- 前端：<http://127.0.0.1:8000>
- Swagger：<http://127.0.0.1:8000/api/docs>
- OpenAPI：<http://127.0.0.1:8000/api/openapi.json>
- 手册：`/manual/index.html`、`/manual/product.html`

环境变量见 `.env.example`；项目**不会自动加载** `.env`，需在 PowerShell/部署配置中注入。

---

## 3. 目录与模块职责

```text
backend/app/
├─ main.py            # FastAPI 装配、中间件、静态托管
├─ config.py          # 路径/端口/上传限制/编译与导航参数/默认设置
├─ db.py              # SQLite 薄封装、建表、迁移、默认数据
├─ security.py        # 账号会话/管理令牌、权限与 CSRF 校验
├─ routers/
│  ├─ system.py       # health/info/stats/settings/admin-token
│  ├─ auth.py         # 登录/初始化/登出/改密、成员 CRUD、审计查询
│  ├─ chat.py         # 问答、会话历史、删除
│  ├─ insights.py     # 反馈、知识缺口、概览、时效看板
│  ├─ handoff.py      # 转人工工单、学生查询、危机事件
│  ├─ jobs.py         # 后台任务列表/重试/取消、系统告警
│  ├─ knowledge.py    # 上传/URL/文本/批量收录、列表、下载、删除、重建、seed
│  ├─ wiki.py         # 目录/列表/详情/编辑/核对/删除/搜索/统计/重建/构建记录
│  ├─ models.py       # Provider/模型 CRUD、默认、测试
│  ├─ bots.py         # 通道 CRUD、日志、QQ 官方/微信回调、控制台
│  └─ mobile.py       # 手机访问信息、二维码、隧道启停
├─ services/
│  ├─ auth.py         # PBKDF2、会话、角色权限、审计
│  ├─ freshness.py    # 日期解析、学年、fresh/aging/stale/expired 派生
│  ├─ insights.py     # 反馈/缺口聚类、概览、重算
│  ├─ handoff.py      # 工单状态机、危机规则、Webhook 通知
│  ├─ jobs.py         # 持久化任务队列、单线程 worker、重试/取消
│  ├─ alerts.py       # 系统告警创建/去重/确认/解决
│  ├─ counselor.py    # 问答编排：危机→导航→上下文→LLM/降级→完整性→远程兜底→落库
│  ├─ wiki_nav.py     # 精确/关键词导航、LLM 选页、context、citations
│  ├─ wiki_builder.py # 源文件→编译单元→LLM 词条→校验→去重/合并→来源锚点
│  ├─ ingest.py       # 上传落盘、解析入口、reindex、删除级联
│  ├─ importer.py     # URL/文本/批量收录（抓取+正文提取+编译）
│  ├─ parsing.py      # 多格式解析（pdf/docx/doc/xlsx/csv/json/html/md/txt）
│  ├─ llm.py          # OpenAI 兼容 / Ollama / mock 适配
│  ├─ remote_search.py# Wiki 无命中时的官网/公众号临时检索
│  ├─ bot_pipeline.py # 机器人统一入站处理（复用 counselor）
│  ├─ qq_gateway.py   # QQ 官方 WebSocket 网关
│  ├─ campus_sync.py  # 受控校园栏目同步
│  ├─ file_links.py   # 引用文件链接拼装
│  ├─ mobile_access.py# 局域网地址/二维码
│  ├─ tunnel.py       # cloudflared 临时隧道
│  └─ text_utils.py   # 分词/标题归一化/slugify
└─ adapters/          # qq_official / wechat_mp / base

frontend/
├─ index.html         # SPA 壳
├─ assets/js/
│  ├─ api.js          # fetch 封装、X-Admin-Token、错误处理
│  ├─ app.js          # hash 路由、视图挂载
│  ├─ ui.js           # 通用 UI/图标/toast/弹窗
│  └─ views/          # chat/knowledge/models/bots/settings
├─ assets/styles.css
├─ manual/index.html  # 使用手册（客户）
├─ manual/product.html# 产品能力总览（客户）
└─ manual/manual.css  # 两份手册共用样式
```

**写作用域**（来自 CONTRACT，历史协作约定）：`main/config/db/system` 归 Lead；`wiki_builder/wiki_nav/counselor/ingest/wiki/knowledge` 归 wiki-core；`frontend/**` 归 web-ui；`backend/tests/**`、README、docs 归 qa-docs。新接手时可按功能模块划分，不必严格沿用。

---

## 4. 核心数据模型

### 4.1 表

| 表 | 作用 | 关键字段 |
|----|------|----------|
| `settings` | 键值设置 | `key/value` |
| `documents` | 源文件 | `status(pending/processing/ready/failed)`、`page_count`、`source_type(file/url/text)`、`source_url` |
| `wiki_pages` | 编译后的词条 | `slug UNIQUE`、`title`、`summary`、`body_md`、`aliases/tags JSON`、`status(ready/needs_review)`、`version` |
| `wiki_sources` | 来源锚点 | `page_id`、`document_id`、`section`、`quote` |
| `wiki_links` | 词条关系 | `from/to/relation` |
| `wiki_builds` | 编译审计 | `status`、`pages_created/updated/removed`、`model`、`error` |
| `providers` | 模型 Provider | `type(openai/ollama/mock)`、`base_url`、`api_key`、`extra` |
| `models_registry` | 模型 | `provider_id`、`model_name`、`temperature`、`max_tokens`、`is_default`、`enabled` |
| `conversations` | 会话 | `channel`、`session_key`、`user_label` |
| `messages` | 消息 | `role`、`content`、`citations JSON`、`model`、`latency_ms`、`retrieval JSON`、`fallback`、`navigation_method` |
| `bot_channels` | 机器人通道 | `type`、`config JSON`、`enabled`、`status`、`last_seen_at` |
| `answer_feedback` | 回答反馈 | `message_id`、`voter_key`、`rating(±1)`、`reason_code`、`status`、UNIQUE(message_id,voter_key) |
| `knowledge_gaps` | 知识缺口 | `question`、`cluster_key`、`count`、`reasons JSON`、`status(open/linked/resolved/ignored)`、`priority` |
| `handoff_requests` | 人工工单 | `ticket_no`、`ticket_token`、`priority(normal/urgent)`、`status(pending/claimed/replied/resolved/closed)`、`sla` 相关时间 |
| `crisis_events` | 危机事件 | `handoff_id`、`category`、`matched_terms`、`status(open/acknowledged/resolved/false_positive)`、`notify_result` |
| `notification_log` | 通知重试留痕 | `channel`、`event_type`、`status`、`attempts`、`last_error` |
| `admin_users` | 管理账号 | `username UNIQUE`、`role(super_admin/admin/counselor/viewer)`、`password_hash`、`active`、`must_change_password` |
| `admin_sessions` | 登录会话 | `token_hash UNIQUE`、`csrf_token`、`expires_at`、`revoked_at` |
| `audit_log` | 操作审计 | `username/role`、`action`、`target_type/target_id`、`detail`、`ip`、`status_code` |
| `jobs` | 后台任务 | `type(ingest_upload/reindex/rebuild)`、`status`、`payload/result JSON`、`progress`、`attempts/max_attempts`、`cancel_requested` |
| `system_alerts` | 系统告警 | `level`、`category(job/llm/...)`、`title/detail`、`status(open/acknowledged/resolved)`、`resource_id` |

`documents` / `wiki_pages` 另有 `published_at`、`effective_at`、`expires_at`、`verified_at`、`academic_year`；时效状态在读取时由 `services/freshness.py` 派生，不落库。

### 4.2 状态机

```text
documents:  pending → processing → ready
                          └──────→ failed（可 reindex 重试）

wiki_pages: ready（参与问答）
            needs_review（展示、不参与问答，需人工编辑转 ready）

wiki_builds: running → ready / failed

knowledge_gaps: open → linked → resolved / ignored
handoff:      pending → claimed → replied → resolved / closed
crisis:       open → acknowledged → resolved / false_positive
jobs:         queued → running → succeeded / failed / cancelled
```

### 4.3 迁移

- `db.init_db()` 用 `_column_names()` + `ALTER TABLE ADD COLUMN` 做增量迁移；旧 RAG `chunks` 表会被 DROP。
- 新表用 `CREATE TABLE IF NOT EXISTS`。
- 迁移必须幂等、只加不删；不要写不可逆操作。
- 迁移后补充索引（如 `documents.source_url` 唯一索引）。

---

## 5. 两条核心链路

### 5.1 上传编译链路

```text
POST /api/knowledge/documents
  → ingest.ingest_upload（校验扩展名/大小、落盘、INSERT documents processing）
  → parsing.parse_path（文本 + 章节）
  → wiki_builder.compile_document
       → 按标题聚合编译单元（≤3500 字）
       → 每个单元 1 次 LLM（JSON 数组，1-3 词条）
       → 校验 JSON / 标题归一化 / source_quote 逐字比对
       → 命中已有词条 → LLM merge；否则 INSERT
       → 写 wiki_sources / wiki_links
  → documents.status=ready/failed、page_count
  → wiki_builds 审计
```

关键文件：
- `services/parsing.py`：格式解析，返回文本与 sections。
- `services/wiki_builder.py`：编译提示词、单元切分、校验、去重/合并、来源锚点。
- `services/ingest.py`：上传/重建/删除编排。

**硬约束**：
- 来源摘录必须能在原文中逐字找到（去空白比对）；否则替换为原文前 120 字。
- `source_section` 必须来自解析出的章节。
- 编译失败不抛 500，写入 `documents.error` 与 `wiki_builds.error`。
- mock/无真实模型时 `can_compile()` 返回 False，上传标记 failed。

### 5.2 问答导航链路

```text
POST /api/chat
  → counselor.answer
       → 读取设置/默认模型/会话历史
       → 寒暄/非校园话题分流
       → wiki_nav.select_pages_with_method
            ├─ 精确：标题/别名
            ├─ 关键词：覆盖率 + IDF + 标签
            └─ LLM 读目录选页（必要时）
       → 加载 1-3 个词条 → build_context
       → LLM（或抽取式降级）
       → attach_citations：按 [n] 对齐词条/来源
       → 完整性判断（规则 + LLM 裁判）
       → 不完整/无命中 → remote_search 官网/公众号临时检索
       → 落库 messages/conversations
```

关键文件：
- `services/wiki_nav.py`：`search_pages`、`_exact_candidates`、`_keyword_candidates`、`_llm_select`、`build_context`、`attach_citations`。
- `services/counselor.py`：`answer` 编排、`_answer_from_pages`、`_extractive_reply`、`_judge_answer_completeness`、`_remote_reply`。
- `services/remote_search.py`：站点/公众号检索（只做临时引用，不写库）。

**返回字段**：`conversation_id/reply/citations/model/provider_type/latency_ms/fallback/sources_used/navigation/retrieval`。

`citations` 结构：

```jsonc
{
  "index": 1,
  "page_id": "…",
  "page_title": "国家奖学金申请",
  "summary": "…",
  "snippet": "…",
  "sources": [{
    "document_id": "…",
    "document_title": "…",
    "source_file": "…",
    "section": "…",
    "quote": "…",
    "source_type": "file|url|text",
    "source_url": "…",
    "download_url": "…"
  }]
}
```

### 5.3 机器人链路

```text
QQ/微信/控制台事件
  → adapters 归一化（IncomingMessage）
  → bot_pipeline.handle_incoming
       ├─ 管理员命令/链接入库（#入库/#收录/#状态/#帮助）
       └─ 普通问题 → counselor.answer
  → 适配器发送回复（QQ 官方 API / 公众号 XML / 控制台）
```

- 会话标识规则见 `adapters/base.py`；群聊按 `group_id + user_id` 隔离。
- 管理员身份按 `admin_user_ids` 精确匹配，不靠昵称。
- 回答末尾通过 `file_links` 追加相关文件链接。
- 微信公众号 5 秒被动回复限制：先回“已开始收录”，后台线程完成。
- QQ 官方 1800 字限制：发送前裁剪正文。

---

## 6. 前端架构

- `app.js`：hash 路由（`#/chat`、`#/knowledge`、`#/ops`、`#/settings`），挂载视图并兼容旧路由重定向。
- `api.js`：统一 `fetch`，自动带 `X-Admin-Token`；401 提示；上传用 XHR 进度。
- `ui.js`：图标、toast、确认弹窗、空态、loading。
- `views/chat.js`：会话、消息、引用折叠、历史抽屉、重试。
- `views/knowledge.js`：知识库浏览 + 内容管理（收录/源文件/词条维护，`?manage=1` 进入管理）。
- `views/ops-hub.js`：运营聚合（服务台 / 任务与告警 / 审计日志）。
- `views/settings-hub.js`：设置聚合（助手设置 / 模型 / 机器人 / 成员与权限 / 我的账号）。
- `views/models.js`：Provider/模型双 Tab。
- `views/bots.js`：通道/调试/日志。
- `views/settings.js`：助手设置（基础/移动访问与安全/回复与引用），由设置聚合页挂载。
- `frontend/manual/*`：客户手册，共用 `manual.css`。

**无构建**：新增 JS 文件用原生 `import`；样式加到 `assets/styles.css`。改完刷新即可，注意 Service Worker 缓存（`service-worker.js`）版本号。

---

## 7. API 概览

完整字段见 `docs/API.md` / `/api/docs`。路由清单：

| 分组 | 接口 |
|------|------|
| 系统 | `GET /health`、`GET /system/info`、`GET /system/stats`、`GET/PUT /settings`、`POST /system/admin-token/init` |
| 账号 | `GET /auth/me`、`POST /auth/setup|login|logout|password`、`/admin/users` CRUD、`POST /admin/users/{id}/reset-password`、`GET /admin/audit` |
| 对话 | `POST /chat`、`POST /chat/stream`（SSE）、`POST /chat/feedback`、`GET /chat/feedback/{message_id}`、`GET /chat/conversations`、`GET /chat/conversations/{id}/messages`、`DELETE /chat/conversations/{id}` |
| 服务台 | `GET /insights/overview|gaps|feedback|freshness`、`POST /insights/gaps/{id}/link|resolve|ignore|assign`、`POST /insights/recompute` |
| 转人工 | `POST /handoff`、`GET /handoff/ticket/{ticket_no}`、`GET /handoff`、`POST /handoff/{id}/claim|reply|resolve`、`GET /crisis/events`、`POST /crisis/events/{id}/status` |
| 任务/告警 | `GET /jobs[/{id}]`、`POST /jobs/{id}/retry|cancel`、`GET /alerts`、`POST /alerts/{id}/ack|resolve` |
| 源文件 | `POST/GET /knowledge/documents`、`GET/DELETE /knowledge/documents/{id}`、`GET .../download`、`POST .../reindex`、`POST /knowledge/seed`、`GET /knowledge/stats` |
| 快速收录 | `POST /knowledge/import/url|text|batch` |
| Wiki | `GET /wiki/tree`、`GET /wiki/pages`、`GET/PUT/DELETE /wiki/pages/{id}`、`POST /wiki/search`、`GET /wiki/stats`、`POST /wiki/rebuild`、`GET /wiki/builds[/{id}]`、`POST /wiki/seed` |
| 模型 | `/models/providers`、`/models` CRUD、`/{id}/default`、`/{id}/test`、`/providers/{id}/test` |
| 机器人 | `/bots/channels` CRUD、`/bots/channels/{id}/test`、`/bots/logs`、`GET/POST /bots/wechat/{id}` |
| 移动 | `GET /mobile/access`、`GET /mobile/qr.png`、`POST /mobile/tunnel/start|stop` |

约定：
- 错误 `{"detail":"中文"}`。
- 时间 ISO-8601；ID 32 位 hex。
- 列表 `{items,total}`。
- 写操作在配置账号或令牌后需登录；Cookie 会话写操作另需 `X-CSRF-Token`；`POST /api/chat`、反馈、工单与机器人 webhook 例外。

---

## 8. 关键算法与调参

### 8.1 Wiki 导航

1. **精确**：标题/别名归一化后完全匹配，得分高。
2. **关键词**：分词 + IDF 归一化 + 标签加权；有强命中阈值 `KEYWORD_STRONG_THRESHOLD`；弱命中不返回，避免无据引用。
3. **LLM 选页**：精确/关键词不够时，把目录（标题/摘要/标签）给模型选 1-3 页。
4. **选择策略**：精确与关键词同时命中时，比较分数，具体主题优先。
5. **调参**：`WIKI_MAX_PAGES`、`WIKI_CONTEXT_MAX_CHARS`、`WIKI_TOC_MAX_CHARS`、`KEYWORD_*` 阈值、`wiki_max_pages/wiki_answer_max_chars` 设置。

### 8.2 回答完整性

- `_rule_completeness`：按时间/联系方式/公式/名单/条件/数值等槽位判断。
- `_llm_judge_completeness`：规则不确定时让模型输出结构化 JSON 裁判。
- 不完整：保留 Wiki 回答，追加远程检索结果（不硬替换）。
- 无命中：先远程检索；无结果再礼貌拒答。
- 开关：`NPU_LLM_JUDGE`、`NPU_REMOTE_FALLBACK`。

### 8.3 抽取式降级

- 无模型/模型失败：从词条正文抽取与问题最相关的句子，`fallback=true`。
- 仍返回词条级引用；不编造。
- 适合离线演示，但表达不如 LLM。

### 8.4 寒暄/非校园分流

- `_smalltalk_kind`：问候/致谢/告别/自我介绍。
- `_looks_non_campus`：明显非校园话题 → 返回服务范围说明，不调用 Wiki/模型。
- 目的：避免开放域闲聊与政策编造；边界问题可调关键词。

---

## 9. 编译管线细节

### 9.1 编译单元

- 按标题聚合，单元 ≤ `WIKI_UNIT_MAX_CHARS`（3500 字），最小 `WIKI_UNIT_MIN_CHARS`（200）。
- 每文档最多 `WIKI_MAX_UNITS_PER_DOC`（16）个单元。
- 单元太少/太大都会影响词条质量；解析器的 sections 质量很关键。

### 9.2 编译提示词

- `COMPILE_SYSTEM_PROMPT`：要求只输出 JSON 数组，字段 `title/aliases/summary/tags/body_md/source_section/source_quote/related_titles`。
- `MERGE_SYSTEM_PROMPT`：合并新旧来源，冲突时用引用块提示“以最新官方通知为准”。
- 改提示词会影响所有词条；改完需回归 `docs/npu-handbook` 评测集，并考虑全部重建。

### 9.3 校验与兜底

- JSON 解析失败/字段缺失 → `needs_review`，保留原文兜底。
- `source_quote` 必须逐字出现；否则替换为原文前 120 字。
- `source_section` 必须来自可用章节。
- 标题归一化去重（`text_utils.slugify`），同标题走 merge。

### 9.4 预编译快照

- `backend/data/wiki_seed.json` 由 `scripts/build_wiki_seed.py` 用真实 LLM 生成。
- 冷启动 `wiki_pages` 为空时自动导入；也支持 `POST /knowledge/seed`。
- 快照与源文件/提示词版本相关；源文件更新后需重新生成。

---

## 10. 测试与评测

### 10.1 pytest

```powershell
.\scripts\check.ps1
# 等价于
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q
```

- `conftest.py`：临时数据目录 + fake LLM，避免真实 API。
- 测试文件按模块：chat/wiki/knowledge/import/bots/models/system/mobile/parsing/retrieval_control/qq_official/campus_sync。
- 新增功能必须补测试；接口变更先改测试/契约。

### 10.2 冒烟

```powershell
.\scripts\smoke.ps1
```

覆盖：health、seed、tree、search、chat 双层引用、models、bots、console 对话、首页。

### 10.3 UI 冒烟

```powershell
.\scripts\ui_check.py
```

- 需要本机 Chrome/Edge + 服务已启动。
- 生成六页截图到 `docs/screenshots/`，检查 console 错误、1440/390 无横向滚动、引用折叠行。

### 10.4 评测集

- `docs/npu-handbook/release/v2026.21/`：479 可答 + 10 不可答。
- 指标：检索 100% / 引用 100% / 拒答 100%。
- 改导航/提示词/编译逻辑后必须回归；不要为了新功能牺牲拒答率。

---

## 11. 安全实现

| 机制 | 位置 | 说明 |
|------|------|------|
| 管理令牌 | `security.py` + `main.py admin_guard` | 写操作校验 `X-Admin-Token`，映射超级管理员；非回环 Host 无账号/令牌 fail-closed |
| 账号会话 | `services/auth.py` + `routers/auth.py` | PBKDF2-HMAC-SHA256（26 万次）、HttpOnly/SameSite=Lax Cookie、仅存 token SHA-256、默认 7 天 |
| CSRF | `security.csrf_valid` | Cookie 会话写操作必须回传登录返回的 `X-CSRF-Token` |
| 角色权限 | `services/auth.ROLE_PERMISSIONS` | 超级管理员/管理员/辅导员/只读；中间件按路径映射权限 |
| 审计 | `main.py admin_guard` + `auth.record_audit` | 所有已认证写请求 + 登录/登出/改密/成员管理 |
| 登录限流 | `main.py _login_rate_limited` | 10 次/分/IP，失败写入审计 |
| 启动安全 | `config.py _check_bind_security` | 非 loopback 且无账号/令牌拒绝启动 |
| CSP/X-Frame/nosniff | `main.py SECURITY_HEADERS` | 默认响应头 |
| CORS | `config.CORS_ORIGINS` | 默认关闭；禁止 `*` |
| 请求体限制 | `main.py BODY_LIMITS` | 上传/机器人/聊天分别限制 |
| 聊天限流 | `main.py _chat_rate_limited` | 30 次/分/IP |
| SSRF 防护 | `importer.py` | 逐跳 DNS + peer IP 校验，默认拒绝私网/回环 |
| HTML 清洗 | `wiki._page_detail` | 链接协议白名单 + `rel=noopener` |
| 机器人鉴权 | adapters/bots | 微信签名/时间窗/nonce、QQ 官方凭据 |
| 敏感信息 | 引用/日志 | 路径过滤、Key 脱敏、机器人 token 脱敏 |

---

## 12. 开发约定

1. **不新增第三方依赖**（除非评审通过）；现有依赖见 `backend/requirements.txt`。
2. 中文注释/文档；英文标识符；API 字段 `snake_case`。
3. 数据库读写走 `db.query/query_one/execute/executemany`；时间用 `db.now()`，ID 用 `db.new_id()`。
4. 同步函数优先；WS 中调同步服务用 `asyncio.to_thread`。
5. 不 `print` 调试；用 `logging.getLogger("npu-assistant")`。
6. 不在路由里写复杂 SQL；放 `services/` 或 `db.py`。
7. 改接口先更新 `docs/CONTRACT.md` / `docs/API.md`，保持前后端一致。
8. 完成前跑 `.\scripts\check.ps1` + `git status` 自查。
9. 不提交密钥、`backend/data/app.db`、`uploads/`、备份文件（见 `.gitignore`）。
10. 版本号在 `config.APP_VERSION`，发布时同步 `CHANGELOG.md` / README。

---

## 13. 常见开发任务 Recipes

### 13.1 新增一个设置项

1. `config.DEFAULT_SETTINGS` 加默认值。
2. `routers/system.py` 的 `EDITABLE_SETTINGS` / `INT_SETTINGS` 加键，必要时加校验。
3. 前端 `views/settings.js` 加表单 + 保存。
4. 读取处用 `db.get_setting` / `db.get_bool_setting`。
5. 加测试：默认值、非法值、持久化。

### 13.2 新增一个后端接口

1. 在对应 router 加函数，写清 docstring 与错误。
2. 写操作加 `dependencies=[Depends(require_admin_token)]`（或依赖中间件）。
3. 业务逻辑放 `services/`，不要在路由里堆。
4. 更新 `docs/API.md` / `CONTRACT.md`。
5. 加 pytest（TestClient）。

### 13.3 新增一种文件解析

1. `config.SUPPORTED_EXTS` 加扩展名。
2. `services/parsing.py` 加解析分支，返回统一结构（文本 + sections）。
3. `ingest` 的 MIME/扩展名映射同步。
4. 加真实文件 fixture + 解析测试。
5. 前端上传提示文案（`knowledge.js` / 手册）同步。
6. 注意：加密/扫描 PDF 需要 OCR；表格尽量保留结构。

### 13.4 新增一种机器人通道

1. `adapters/base.py` 加通道类型、默认配置、会话标识规则。
2. 新建 adapter（归一化事件、发送回复、鉴权）。
3. `bot_pipeline` 复用 `counselor.answer`；管理员命令按需支持。
4. `routers/bots.py` 加 CRUD/回调/测试端点。
5. 前端 `bots.js` 加快捷添加 + 配置表单。
6. 文档：`docs/BOT-INTEGRATION.md` + 管理员手册。
7. 测试：鉴权、会话隔离、长文本截断、失败重试。

### 13.5 新增数据库字段

1. `db.SCHEMA` 加列（新库）。
2. `db.init_db()` 用 `_column_names()` + `ALTER TABLE` 兼容旧库。
3. 默认值要有意义；不要 NOT NULL 无默认。
4. 更新 `document_to_dict` / `_page_dict` 等序列化。
5. 加迁移测试（旧库升级）。

### 13.6 改导航/编译逻辑

1. 先跑基线评测（`docs/npu-handbook`）。
2. 改 `wiki_nav` / `wiki_builder` / 提示词。
3. 跑 pytest + 评测集；对比检索/引用/拒答指标。
4. 需要时重建 `wiki_seed.json`。
5. 记录到 CHANGELOG。

---

## 14. 当前进度与特性矩阵

| 模块 | 状态 | 说明 |
|------|------|------|
| Web 问答 + 历史 | ✅ v0.1.0 | 多轮、引用、历史抽屉（需令牌） |
| LLM Wiki 编译 | ✅ v0.1.0 | 多格式、词条、来源锚点、合并/重建 |
| 模型管理 | ✅ v0.1.0 | OpenAI 兼容/Ollama/mock、CRUD/测试/默认 |
| 机器人 | ✅ v0.1.0+ | QQ 官方机器人、微信公众号、控制台（OneBot 11 已于 v0.2.0 移除） |
| 手机扫码/隧道/PWA | ✅ v0.1.1 | 局域网、Cloudflare 临时隧道 |
| 校园栏目同步 | ✅ v0.1.2 | 受控源、脚本定时；多数源因 WAF 默认关闭 |
| 真实数据/评测 | ✅ v0.1.3 | 75 篇/203 词条/241 锚点；v2026.21 数据集 |
| 远程兜底 | ✅ v0.1.4 | Wiki 无命中转官网/公众号，不写库 |
| 完整性判断 | ✅ v0.1.5 | 规则 + LLM 裁判；不完整追加检索 |
| QQ 官方机器人 | ✅ v0.1.6 | WebSocket、无 Webhook、心跳/重连 |
| 源文件下载/文件链接 | ✅ v0.1.7 | 引用卡下载/原文；机器人附链接 |
| 反馈/缺口工作台 | ✅ v0.2.0 M1 | 反馈、聚类、服务台、概览 |
| 转人工/危机升级 | ✅ v0.2.0 M2 | 工单、SLA、危机规则/卡片/Webhook |
| 账号/角色/审计 | ✅ v0.2.0 M3 | 会话登录、4 角色、CSRF、审计 |
| 时效性 | ✅ v0.2.0 M1 | 日期字段/学年/当前日期注入/过期降权 |
| 异步/流式/告警 | ✅ v0.2.0 M4-M5 | 任务队列、异步上传、SSE、重试/熔断/告警 |
| embedding/向量库 | ❌ 非目标 | 设计上不做 |
| 语音/图片 | ❌ 当前不做 | 路线图 v0.4.0 评估 |

---

## 15. 已知坑与注意事项

1. **单进程 SQLite**：不要用多 worker；写并发有限。生产扩展需评估 Postgres/外部队列。
2. **上传同步编译**：大文件会阻塞请求；v0.2.0 异步化前，前端要提示等待，避免超时。
3. **mock 不能编译**：`can_compile()` 为 False；测试用 fake LLM，不要依赖 mock 生成词条。
4. **`needs_review` 不参与问答**：只展示；需人工编辑转 ready。
5. **弱命中不返回**：为了避免无据引用；关键词阈值调低会增加误命中。
6. **远程兜底不写库**：引用是临时的；不要把它当永久知识。
7. **seed 路径修复**：`repair_seed_document_paths()` 会把快照路径迁到 uploads，避免删除仓库文件；改动要小心。
8. **微信 5 秒限制**：被动回复有硬限制；不要在里面做长编译。
9. **QQ 官方 1800 字**：发送前裁剪；文件链接可能被截断，注意顺序。
10. **WAF 源**：多数校园栏目对脚本 403；`campus_sources.json` 默认关闭，改人工/官方接口。
11. **Service Worker 缓存**：改前端后如果页面不更新，升级 `service-worker.js` 版本或硬刷新。
12. **管理令牌**：`run.ps1 -Lan` 每次启动会生成新令牌（除非环境变量）；手机需重填。
13. **种子资料删除保护**：删除文档会级联词条；种子路径迁移逻辑不要绕过。
14. **评测回归**：改导航/提示词不做评测，容易把拒答率/引用率弄坏。
15. **账号启用后的锁定**：一旦存在账号，写操作必须登录；不要直接清空 `admin_users` 来“解锁”，先保留一名超级管理员。
16. **CSRF**：新增写接口时，前端必须走 `api.js` 的请求封装（自动带 `X-CSRF-Token`）；直接用 `fetch` 会被 403。
17. **角色与路径**：新增管理路由时同步更新 `auth.permission_for_request()`，否则默认要求 `content:write`。
18. **任务队列是单进程单线程**：不要多 worker 启动；任务进程内线程随服务退出，重启会把 running 标记失败并提示重试。
19. **流式不可重放**：`llm.stream_chat` 只在「尚未输出任何 delta」时重试；否则会重复文本。
20. **熔断是进程内状态**：重启清零；阈值/冷却可用 `NPU_LLM_BREAKER_*` 调整，熔断会写 `system_alerts`。
21. **异步上传临时文件**：`backend/data/job_staging/` 在成功/取消后清理；失败保留以支持重试。

---

## 16. 交接 Checklist

### 16.1 环境

- [ ] 代码仓库/分支/最新 commit。
- [ ] Python 版本、venv 路径、依赖是否装好。
- [ ] 数据目录 `backend/data`、DB 大小、uploads 大小。
- [ ] 环境变量/令牌/模型 Key 的保管方式（不要写进文档）。
- [ ] 机器人通道（AppID/Token/管理员 ID）与公网地址。

### 16.2 运行

- [ ] `run.ps1` 能启动；`/api/health` 正常。
- [ ] `check.ps1` 全绿。
- [ ] `smoke.ps1` 通过。
- [ ] 首页/手册/管理页能打开。
- [ ] 手机扫码/公网地址可用（如启用）。

### 16.3 内容

- [ ] 当前文档/词条/来源数量。
- [ ] 最近一次备份时间与位置。
- [ ] 哪些源是自动同步、哪些是人工；WAF 限制。
- [ ] 待校对/失败文档列表。
- [ ] 评测基线（v2026.21 指标）。

### 16.4 待办

- [ ] 当前分支未合并改动。
- [ ] 已知 bug/临时 workaround。
- [ ] 下一步路线图（`docs/ROADMAP.md`）。
- [ ] 需要产品/学校确认的问题（见 PRD 开放问题）。

---

## 17. 版本与发布流程

1. 更新 `config.APP_VERSION`。
2. 更新 `CHANGELOG.md`（版本、日期、主题、变更点）。
3. 更新 README/手册/路线图状态。
4. 跑 `check.ps1` + `smoke.ps1` + 评测回归；UI 改动跑 `ui_check.py`。
5. 备份数据。
6. 合并到发布分支/打 tag。
7. 部署后验证 `/api/health`、首页、一次问答、一次上传。

---

## 18. 参考文档

| 文档 | 用途 |
|------|------|
| `docs/ARCHITECTURE.md` | 架构图、模块职责、部署建议 |
| `docs/WIKI-DESIGN.md` | 知识层设计、提示词、导航算法、验收 |
| `docs/CONTRACT.md` | 开发契约、API 概览、写作用域 |
| `docs/API.md` | 接口速查与示例 |
| `docs/BOT-INTEGRATION.md` | QQ/微信接入细节 |
| `docs/MOBILE-OPS.md` | 手机访问、快速收录、移动运维 |
| `docs/RETRIEVAL-CONTROL.md` | 完整性判断与远程兜底 |
| `docs/PRD-v0.2.0.md` | 下一版本需求与设计 |
| `docs/ROADMAP.md` | 特性排期与状态 |
| `CHANGELOG.md` | 版本变更 |

---

*本手册随代码演进维护；新增模块/接口/坑请同步更新对应章节。*

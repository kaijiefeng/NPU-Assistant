# NPU-Assistant 开发契约（v0.2 · LLM Wiki）

> **v0.2（2026-10-22）重大变更**：知识层从「切片 + BM25 的经典 RAG」**彻底迁移为 LLM Wiki**。
> 详细设计、提示词、数据流、导航算法、编译规则与验收标准见 **`docs/WIKI-DESIGN.md`（必须完整阅读）**。
> 本文件负责：范围、目录/写作用域、API 概览、内部接口、协作纪律。两者冲突时以 WIKI-DESIGN 为准，并立即通知 Lead。

---

## 1. 产品与 MVP 范围

**NPU-Assistant（校园助手）**：辅导员的数字分身。MVP 四项能力：

| # | 能力 | v0.2 交付 |
|---|------|-----------|
| 1 | 辅导员数字分身问答 | Web 对话 + **Wiki 词条导航**回答 + 词条级/来源级双层引用 + 多轮会话 |
| 2 | LLM Wiki 知识库 | 上传源文件 → LLM 编译为结构化词条（带来源锚点）→ 浏览/搜索/编辑词条；**不使用 embedding、向量库、rerank、切片检索** |
| 3 | LLM 接入与模型管理 | OpenAI 兼容（当前实测 DeepSeek）/ Ollama / mock；Provider/模型 CRUD、默认模型、连通性测试、Key 脱敏 |
| 4 | 微信 / QQ 机器人 | OneBot 11（反向 WS + 反向 HTTP + HTTP API）、微信公众号明文回调、控制台通道（复用 counselor 问答） |

**非目标**：多租户/权限体系、流式输出、embedding/向量检索、语音、插件系统、公众号加密模式。

---

## 2. 技术栈与运行

- 后端 Python 3.11 + FastAPI + SQLite（标准库 sqlite3）+ httpx + jieba（仅用于 Wiki 导航分词）+ pypdf/python-docx/openpyxl/bs4 + markdown（`body_html` 渲染）
- 前端纯静态 HTML/CSS/原生 ES Module，无构建、无 CDN
- 虚拟环境 `.venv`（已就绪）。统一用 `.\.venv\Scripts\python.exe`
- 启动 `.\scripts\run.ps1`（默认 <http://127.0.0.1:8000>）；测试 `.\scripts\check.ps1`
- **真实 LLM 是 Wiki 编译与高质量回答的前置条件**；当前开发实例已配置 DeepSeek（key 仅存于运行库，不入仓库）

---

## 3. 目录与写作用域

| 归属 | 写作用域 |
|------|----------|
| **Lead** | `docs/CONTRACT.md`、`docs/WIKI-DESIGN.md`、`backend/app/{main,config,db}.py`、`backend/app/routers/system.py`、`scripts/`、`.gitignore`、`.env.example`、`backend/data/wiki_seed.json`（生成物） |
| **wiki-core** | `backend/app/services/{wiki_builder,wiki_nav,text_utils}.py`、`backend/app/services/{counselor,ingest}.py`、`backend/app/routers/{wiki,knowledge}.py` |
| **web-ui** | `frontend/**` |
| **qa-docs** | `backend/tests/**`、`README.md`、`docs/{ARCHITECTURE,API,DEMO,BOT-INTEGRATION}.md`、`backend/data/seed_docs/**` |
| **backend-core / kb-rag / bot-gateway（已完成，暂停）** | 其既有文件不再改动；如确需改动，先通知 Lead |

纪律：
1. 绝不改他人作用域；需要改接口 → 先通知 Lead 更新 WIKI-DESIGN/CONTRACT。
2. 不新增第三方依赖（`markdown` 已加入 requirements，Lead 维护）。
3. 中文注释/文档、英文标识符、API 字段 `snake_case`；时间用 `db.now()`，ID 用 `db.new_id()`。
4. 数据库读写走 `app/db.py` 的 `query/query_one/execute/executemany`。
5. 同步函数优先；WS 中调同步服务用 `asyncio.to_thread`。
6. 不 `print` 调试；用 `logging.getLogger("npu-assistant")`。
7. 不 `git commit`（Lead 统一提交）；完成前 `git status` 自查。
8. 自己负责的接口必须用 TestClient/curl 自测；完成后 `team_task_update(action="complete")` 并向 Lead 报告：改动文件、接口一致性、自测结果、遗留风险。

---

## 4. 数据库（Lead 已实现，见 `backend/app/db.py`）

表：`settings`、`documents`、`wiki_pages`、`wiki_sources`、`wiki_links`、`wiki_builds`、`providers`、`models_registry`、`conversations`、`messages`、`bot_channels`。
**`chunks` 表已删除**（迁移时 DROP）；`documents` 用 `page_count`，旧 `chunk_count` 已删除。
详细字段与语义见 WIKI-DESIGN §2。`documents.status`: `pending|processing|ready|failed`；`wiki_pages.status`: `ready|needs_review`。

---

## 5. API（前缀 `/api`，错误 `{"detail":"中文"}`）

> 完整字段见 WIKI-DESIGN §4。此处列路由清单与要点。

### 5.1 系统（已实现）
`GET /health`、`GET /system/info`、`GET /system/stats`、`GET /settings`、`PUT /settings`。
`/health` 新增 `knowledge_mode:"llm-wiki"`、`wiki_pages` 字段；`/system/stats` 用 `wiki_pages/wiki_sources` 替代 `chunks`。

### 5.2 对话（wiki-core）
`POST /chat`、`GET /chat/conversations`、`GET /chat/conversations/{id}/messages`、`DELETE /chat/conversations/{id}`。
响应 citations 改为**词条级 + 来源级**：

```jsonc
{"index":1,"page_id":"…","page_title":"国家奖学金申请","summary":"…","snippet":"…",
 "sources":[{"document_id":"…","document_title":"…","source_file":"…","section":"…","quote":"…"}]}
```

另返回 `navigation:{method,pages:[{id,title}]}`；`fallback` 语义不变（真实 LLM 成功=false，降级=true）。

### 5.3 源文件（wiki-core）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/knowledge/documents` | multipart `file`/`title`/`tags`；保存+解析+**同步编译**；返回 doc（status/page_count/error） |
| GET | `/knowledge/documents` | 列表 |
| GET | `/knowledge/documents/{id}` | doc + `wiki_pages` |
| DELETE | `/knowledge/documents/{id}` | 删文档/独占词条/文件 |
| POST | `/knowledge/documents/{id}/reindex` | 重新编译 |
| POST | `/knowledge/seed` | 导入 `wiki_seed.json`（幂等） |
| GET | `/knowledge/stats` | 兼容返回文档/词条计数 |

**已删除**：`GET /knowledge/documents/{id}/chunks`、`POST /knowledge/search`、旧 chunks 契约。

### 5.4 Wiki（wiki-core）
`GET /wiki/tree`、`GET /wiki/pages`、`GET /wiki/pages/{id}`、`PUT /wiki/pages/{id}`、`DELETE /wiki/pages/{id}`、`POST /wiki/search`、`GET /wiki/stats`、`POST /wiki/rebuild`、`GET /wiki/builds/{id}`、`GET /wiki/builds`、`POST /wiki/seed`。
字段与行为见 WIKI-DESIGN §4.2。

### 5.5 模型管理（已完成，不变）
`/models/providers`、`/models` 全部 CRUD/测试/默认切换；`api_key_masked` 规则不变。

### 5.6 机器人（已完成，不变）
OneBot 11 反向 WS/HTTP、群 @ 门控、HMAC、微信公众号明文 XML、console 通道；问答统一走 `counselor.answer()`。citation 结构变化对机器人透明（bot 只取 reply）。

### 5.7 前端（web-ui）
知识库页改为「词条 / 源文件」双 Tab（浏览、搜索、来源锚点、编辑、重建、导入快照）；对话引用卡片展示词条+来源文件·章节并可跳转词条详情；设置页用 `wiki_max_pages`/`wiki_answer_max_chars` 替换旧检索参数。详见 WIKI-DESIGN §6。

---

## 6. 内部服务接口

完整签名见 **WIKI-DESIGN §5**。要点：

```python
# wiki_builder
load_seed_wiki() -> dict
compile_document(document_id, *, force=False) -> dict
rebuild_all() -> dict
remove_pages_for_document(document_id) -> int
can_compile() -> tuple[bool, str]
# wiki_nav
search_pages(query, *, limit=5) -> dict
select_pages(question, *, max_pages=None) -> list[dict]
build_context(pages, max_chars=9000) -> str
attach_citations(reply, pages, enabled=True) -> tuple[str, list[dict]]
# counselor.answer(...) 签名不变，内部改为 Wiki 导航 + 词条阅读 + 降级
```

`wiki_builder` 只能通过 `services.llm.chat()` 调模型；`counselor` 禁止 import 旧 RAG 模块（`retrieval`/`rag`/`retrieval_core` 将被删除）。

---

## 7. 验收标准

- [ ] 冷启动无 Key 自动导入预编译 Wiki（≥10 源文件 / ≥15 词条），`/api/wiki/tree` 可浏览
- [ ] 配 DeepSeek 后上传 md/docx/pdf → 编译出词条 + 来源锚点，`status=ready`、`page_count>0`
- [ ] `/api/wiki/search` 命中正确词条（method=exact/keyword/llm），无命中 `method=none`
- [ ] `/api/chat` 真实模型 `fallback=false`，citations 同时含 `page_id` 与 `sources[].section`；断网/mock 时 `fallback=true` 有词条摘录
- [ ] 删除/重建文档不产生重复词条，独占词条被清理
- [ ] 前端词条浏览/搜索/来源/编辑、对话引用跳转可用；`ui_check.py` 通过
- [ ] `pytest` 全绿（删除 `test_retrieval_core.py`）；`smoke.ps1` 更新后通过
- [ ] 旧 RAG 文件与接口全部删除（`services/{retrieval,retrieval_core,rag}.py`、`/knowledge/search`、chunks）

---

## 8. 协作流程

1. 读 `docs/WIKI-DESIGN.md` + 本契约 → 领任务 → `team_task_update(claim)`。
2. 先主路径后边界；接口严格按契约字段名；自测后 `complete` 并报告。
3. 复杂问题先按契约最小实现，同时通知 Lead；Lead 视情况调度 codex/专家咨询（注：本会话 codex 子代理两次失败，当前以团队自查 + 真实 LLM 验收为准）。
4. 所有改动最终由 Lead 统一 review、跑验收、git commit。

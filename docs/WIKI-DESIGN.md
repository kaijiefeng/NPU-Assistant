# NPU-Assistant · LLM Wiki 设计与契约（v0.1.0，Lead 维护）

> 本文件是 **LLM Wiki 改造**的唯一设计事实源。目标：**彻底移除经典 RAG**（切片 + 向量/BM25 检索），
> 改为：源文件 → LLM 编译为结构化 **Wiki 词条** → 查询时通过「目录/别名/关键词/LLM 选页」导航到 1–3 个词条 → LLM 阅读词条作答，并给出「词条 + 原始文件章节」双层引用。
>
> 规模假设：几十~几百篇校园文件、<1000 个词条、单机 SQLite + FastAPI。**不使用 embedding、向量库、rerank。**

---

## 1. 概念与边界

| 项目 | 约定 |
|------|------|
| 源文件（documents） | 上传的 md/txt/pdf/docx/xlsx/csv/json/html，是唯一事实来源 |
| Wiki 词条（wiki_pages） | 由 LLM 从源文件编译出的结构化 Markdown 页面，包含概述/流程/材料/时间/注意事项等 |
| 来源锚点（wiki_sources） | 词条 ← 源文件 + 章节标题 + 原文摘录（逐字），用于双层引用与人工核对 |
| 导航（wiki_nav） | 定位词条，**不检索切片**：标题/别名精确匹配 → 元数据关键词打分 → LLM 读目录选页 |
| 回答（counselor） | 读取选中的词条全文（截断）→ LLM 生成回答并标 `[n]` → 附加词条与来源引用 |
| 无 LLM 时 | 编译不可用（上传会 failed）；已有词条可浏览；问答降级为「词条摘录」并标注 fallback=true |
| 明确不做 | embedding、向量数据库、rerank 模型、chunk 检索、流式输出、多租户 |

**硬约束**：编译与回答都必须依赖真实 LLM（当前默认 DeepSeek `deepseek-chat`）。mock 模型只用于链路测试，编译会被拒绝并提示配置模型。

---

## 2. 数据模型（SQLite，见 `backend/app/db.py`）

```sql
documents(id, title, filename, stored_path, file_ext, size_bytes, status, error,
          tags, char_count, page_count, created_at, updated_at)
          -- status: pending|processing|ready|failed；page_count=该源文件贡献的词条数

wiki_pages(id, slug UNIQUE, title, summary, body_md, aliases JSON, tags JSON,
           status, version, source_count, created_at, updated_at)
           -- status: ready|needs_review（needs_review 只展示，不参与问答）

wiki_sources(id, page_id FK, document_id, document_title, source_file,
             section, quote, created_at)

wiki_links(id, from_page_id FK, to_page_id FK, relation, UNIQUE(from,to))

wiki_builds(id, document_id, status, model, pages_created, pages_updated,
            pages_removed, error, started_at, finished_at)
```

新库直接建；旧库迁移：`DROP TABLE chunks`、documents 增加 `page_count`（旧 `chunk_count` 删除）、清理 `retrieval_top_k/retrieval_min_score` 设置。

**设置项**（经 `/api/settings`）：`counselor_name`、`system_prompt`、`welcome_message`、`quick_questions`、`wiki_max_pages`(默认 3)、`wiki_answer_max_chars`(默认 9000)、`citations_enabled`、`bot_reply_footer`。

---

## 3. 流程

### 3.1 编译（上传 / reindex / rebuild）

```
文件 → parsing.parse_path() → 按标题聚合「编译单元」(≤3500 字, 保留 section)
     → 每个单元 1 次 LLM 调用（JSON 数组，1–3 个词条）
     → 校验/脱敏/来源摘录校验 → 归一化标题去重
        ├─ 命中已有词条 → LLM merge（已有 + 新）→ 更新 body/summary/aliases/tags，追加来源
        └─ 新词条 → INSERT wiki_pages + wiki_sources
     → 建 related 链接 → documents.status=ready, page_count=count
     → wiki_builds 记录 running→ready/failed
```

规则：
1. 单元内相邻标题合并，标题过长的章节按段落二次切分；纯目录/空内容单元跳过。
2. LLM 输出必须为 JSON（允许 ```json 包裹）；解析失败重试 1 次；仍失败则该单元生成 `needs_review` 兜底词条（title=章节名，body=该单元原文 Markdown，来源=原文首 120 字），**不阻塞整篇文档**。
3. 来源锚点：`source_section` 必须属于该单元的标题路径；`source_quote` 必须能在单元原文中逐字找到（去空白后比对），否则用单元首 120 字替换。
4. 去重键：`slug = 归一化(title)`；另用 aliases/标题归一化匹配已有词条。合并时若来源冲突，在 body 中用 `> 注：不同来源说法不一致…` 标注。
5. 重新编译单个文档：`remove_pages_for_document`（仅删除来源全属该文档的词条）→ 重新编译。
6. 删除文档：删除其独占词条；共享词条只移除该文档来源并更新 `source_count`；清理孤儿链接。
7. 所有 LLM 调用失败要写清中文 error；`documents.status=failed`，可重试。

### 3.2 导航（查询路由，不依赖 embedding）

`wiki_nav.search_pages(query, limit)` 步骤与 `method`：

1. **exact**：词条 title/alias（长度≥2）出现在问题中（或问题包含标题），得分最高。
2. **keyword**：`text_utils.tokenize(query)` 去停用词后，与 title(×3)/aliases(×3)/tags(×2)/summary(×1) 的 token 重合度打分，覆盖率归一化。
3. 若最佳候选归一化得分 ≥ 0.34 → 直接取前 `wiki_max_pages` 个，`method=keyword|exact`。
4. 否则（无候选或太弱）→ **LLM 选页**：把紧凑目录（`[id] 标题 | 别名 | 标签 | 摘要`）与问题给 LLM，要求输出 `{"page_ids":[...]}`；目录超过 12000 字时先按标签分组，让 LLM 先选标签组再选页（两次调用）。返回值必须在目录 id 集合内，1–`wiki_max_pages` 个。
5. LLM 不可用/超时/输出非法 → 回退 keyword 候选（若有），否则 `method=none` 返回 []。
6. 仅 `status='ready'` 的词条参与问答；`needs_review` 只在管理页展示。

### 3.3 回答

```
问题 → wiki_nav 选页(1–3) → build_context（[n] 标题 + 摘要 + body_md + 来源行）
     → system prompt（人设 + 只依据词条 + [n] 引用规则 + 不编造）
     → llm.chat → 成功: fallback=false；失败/mock: 词条摘录 fallback=true
     → attach_citations（[n] ↔ 词条；每个 citation 附 sources 锚点）
     → 落库 messages/citations
```

- 无选中词条：礼貌说明「校园 Wiki 暂未收录」，给咨询渠道，`citations=[]`，不调用 LLM。
- 任何异常不得 500；LLM 失败必须降级。
- 引用编号 `[n]` 必须与 citations 的 index 一致；模型未标编号时追加「参考词条」清单。
- 回答最大读取 `wiki_answer_max_chars`（默认 9000），单页读取上限 2500 字。

### 3.4 预编译快照（离线可用）

- `backend/data/wiki_seed.json` 是用真实 LLM 对 10 篇种子文档编译后导出的快照（含 documents/pages/sources/links），随仓库提交。
- 冷启动且 `wiki_pages` 为空时自动导入（`NPU_AUTO_SEED=1`），**不需要 API Key** 即可浏览 Wiki 和降级问答。
- `POST /api/knowledge/seed` 手动触发，幂等（按源文件名/词条 slug 跳过）。

---

## 4. API 契约

前缀 `/api`，错误统一 `{"detail": "中文"}`。

### 4.1 源文件（knowledge，wiki-core）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/knowledge/documents` | multipart：`file`(必填)、`title?`、`tags?`。保存→解析→**同步编译词条**。201 返回 doc；LLM 不可用/编译失败时 `status=failed`+`error`；扩展名/大小校验同旧版 |
| GET | `/knowledge/documents` | `{items:[doc…], total}`，doc 含 `page_count`（不再有 chunk 字段） |
| GET | `/knowledge/documents/{id}` | doc + `wiki_pages:[{id,slug,title,status}]` |
| DELETE | `/knowledge/documents/{id}` | 删除文档、独占词条、物理文件 → `{ok:true, pages_removed:n}` |
| POST | `/knowledge/documents/{id}/reindex` | 重新解析+编译 → `{document, build}` |
| POST | `/knowledge/seed` | 导入 wiki_seed.json → `{imported, documents, pages, sources, skipped}` |
| GET | `/knowledge/stats` | 兼容保留，返回 `{documents, ready_documents, pages, sources}` |

**移除**（RAG 遗留，必须删除）：`GET /knowledge/documents/{id}/chunks`、`POST /knowledge/search`。

### 4.2 Wiki（wiki，wiki-core）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/wiki/tree` | `{total, groups:[{tag,count,pages:[{id,title,summary,tags,source_count,updated_at}]}]}`；按 tag 排序，无 tag 归「其他」 |
| GET | `/wiki/pages?q=&tag=&limit=50&offset=0` | `{items:[{id,slug,title,summary,tags,status,source_count,updated_at}], total}` |
| GET | `/wiki/pages/{id}` | 页面详情：page 字段 + `body_html`（转义后 Markdown 渲染）+ `sources[]` + `links:[{page_id,title,relation}]` |
| PUT | `/wiki/pages/{id}` | 手工编辑 `{title?,summary?,body_md?,aliases?,tags?}` → `version+1`、`status=ready` |
| DELETE | `/wiki/pages/{id}` | `{ok:true}` |
| POST | `/wiki/search` | `{query, limit?}` → `{query, method, candidates:[{page_id,title,summary,score,reason}], selected:[同结构], latency_ms}` |
| GET | `/wiki/stats` | `{pages, sources, documents, by_tag, last_build}` |
| POST | `/wiki/rebuild` | 后台任务重建全部词条 → 202 `{build_id,status:"running"}`；可选 `{document_id}` 同步重建单篇 |
| GET | `/wiki/builds/{id}` | 构建状态；`GET /wiki/builds?limit=10` 最近记录 |
| POST | `/wiki/seed` | 同 `/knowledge/seed` |

### 4.3 对话（counselor 返回结构变化）

`POST /chat`、会话列表/消息/删除接口路径与请求不变。响应：

```jsonc
{
  "conversation_id": "…",
  "reply": "……[1][2]",
  "citations": [
    {
      "index": 1,
      "page_id": "…",
      "page_title": "国家奖学金申请",
      "summary": "…",
      "snippet": "词条正文摘录 ≤200 字",
      "sources": [
        {"document_id": "…", "document_title": "国家奖学金与奖助学金评审办法（示例）",
         "source_file": "国家奖学金与奖助学金评审办法.md",
         "section": "五、申请材料清单", "quote": "…"}
      ]
    }
  ],
  "model": "DeepSeek Chat", "provider_type": "openai",
  "latency_ms": 3200, "fallback": false, "sources_used": 2,
  "navigation": {"method": "keyword", "pages": [{"id": "…", "title": "…"}]}
}
```

消息历史中的 `citations` 反序列化为同一结构。

### 4.4 设置

`GET/PUT /api/settings` 可编辑项见 §2；`wiki_max_pages` 范围 1–5，`wiki_answer_max_chars` 范围 2000–20000。

---

## 5. 内部服务接口（跨模块 import 契约）

```python
# services/text_utils.py
def tokenize(text: str) -> list[str]          # jieba 分词 + 停用词 + 小写 + 中文 bigram
def normalize_title(text: str) -> str         # 去空白/标点/全半角，用于标题匹配
def slugify(title: str) -> str

# services/wiki_builder.py
def compile_document(document_id: str, *, force: bool = False) -> dict
    # → {document, pages_created, pages_updated, pages_removed, model, status}
def rebuild_all() -> dict
def rebuild_document(document_id: str) -> dict
def remove_pages_for_document(document_id: str) -> int
def load_seed_wiki() -> dict                  # → {imported, documents, pages, sources, skipped}
def export_seed_wiki(path: str | Path) -> dict # 供 scripts/build_wiki_seed.py
def can_compile() -> tuple[bool, str]          # (是否可用真实模型, 原因)

# services/wiki_nav.py
def search_pages(query: str, *, limit: int = 5) -> dict
def select_pages(question: str, *, max_pages: int | None = None) -> list[dict]
def build_context(pages: list[dict], max_chars: int = 9000) -> str
def attach_citations(reply: str, pages: list[dict], enabled: bool = True) -> tuple[str, list[dict]]

# services/counselor.py（签名不变）
def answer(question, *, channel="web", session_key="web:default",
           conversation_id=None, user_label=None) -> dict
```

`wiki_builder` 只能通过 `services.llm.chat()` 调模型；不得直接访问 httpx。`counselor` 不得 import 任何旧 RAG 模块。

---

## 6. 前端要求（web-ui）

1. **知识库页**：顶部统计（Wiki 词条/来源/源文件/最近构建）；两个 Tab：
   - **词条**（默认）：搜索框（调 `/wiki/search`，展示 method 与候选分数）、标签筛选、词条卡片列表；点击打开详情抽屉：`body_html` 渲染、别名/标签、来源锚点（文件+章节+原文摘录）、相关词条、编辑/删除；
   - **源文件**：拖拽上传（显示「编译中」）、文档表格（状态/页数/大小/时间）、单篇重建、删除、导入预编译快照、全部重建（轮询 build 状态）。
2. **对话页**：引用卡片改为「词条标题 + 来源文件·章节 + 原文摘录」，点击可跳转 `#/knowledge?page=<id>` 打开词条详情；保留原有引用展开交互。
3. **设置页**：移除 `retrieval_top_k / retrieval_min_score`，新增 `wiki_max_pages`（1–5）与 `wiki_answer_max_chars`。
4. 其余页面（模型/机器人）交互不变；`api.js` 补 wiki 接口；Markdown 内容由后端 `body_html` 提供，前端直接渲染（无需引入 Markdown 库）。

---

## 7. 验收标准

- [ ] 冷启动（空库、无 API Key）自动导入 `wiki_seed.json`：≥10 篇源文件、≥15 个词条；`/api/wiki/tree` 可浏览
- [ ] 配置 DeepSeek 后：上传一篇 md → 自动编译出 1–3 个词条 + 来源锚点；`documents.status=ready`、`page_count>0`
- [ ] `POST /api/wiki/search` 对「国家奖学金怎么申请」返回 `method=keyword|exact|llm` 且命中正确词条；无命中返回 `method=none`
- [ ] `/api/chat` 配置真实模型时 `fallback=false`、`citations[].page_id` 与 `sources[].section` 可追溯；mock/断网时 `fallback=true` 且仍返回词条摘录
- [ ] 删除源文件后其独占词条消失，共享词条保留；`reindex` 不产生重复词条
- [ ] 前端知识库页可浏览/搜索/查看来源/编辑词条；对话引用可跳转词条
- [ ] `pytest` 全绿（wiki/search/chat/models/bots/system，无 retrieval 测试）；`smoke.ps1`、`ui_check.py` 通过
- [ ] 旧 RAG 代码与接口删除：`services/{retrieval,retrieval_core,rag}.py`、`/knowledge/search`、chunks 表/接口、`test_retrieval_core.py`

---

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| 编译幻觉 | 提示词只允许原文事实；`source_quote` 逐字校验；数字/时间保留原文；低质量进 needs_review |
| 词条重复/膨胀 | 标题+别名归一化去重；合并走 LLM；每文档页数上限提示 |
| 导航选错页 | 精确/关键词优先；LLM 只从目录 id 选；无把握返回 none；`/wiki/search` 可观测 method |
| 大目录超上下文 | 标签分组两级选页；目录紧凑格式 |
| 更新过期 | 按文档 reindex/重建；页面显示 updated_at 与来源 |
| 成本 | 编译一次 ~1–3 次调用/文档；问答 1 次选页（可能省）+1 次回答；种子快照避免重复编译 |
| LLM 不可用 | 上传 failed 可重试；已有词条可浏览；问答降级词条摘录 |

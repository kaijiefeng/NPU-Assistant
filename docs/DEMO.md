# NPU-Assistant 5 分钟演示脚本（LLM Wiki）

> 目标：在 5 分钟内完整展示「LLM Wiki 编译 → 词条导航 → 双层引用回答 → 多渠道机器人」四类核心能力。
> 演示环境：Windows + 已安装依赖的 `.venv`，默认地址 <http://127.0.0.1:8000>；配置真实 DeepSeek 时问答质量最佳。
> 本文所有示例词条内容来自 `backend/data/seed_docs/` 与预编译快照 `backend/data/wiki_seed.json`，其中的时间、金额、电话均为示例。
> 五个页面的实际界面截图见 `docs/screenshots/`（由 `scripts/ui_check.py` 自动生成），可先整体浏览再按下述脚本演示。

## 0. 演示前检查（30 秒）

```powershell
cd D:\Development\NPU-Assistant
.\scripts\run.ps1          # 若 8000 被占用：.\scripts\run.ps1 -Port 8010
```

浏览器打开 <http://127.0.0.1:8000>，确认：

- 顶部/侧栏显示分身名称「小南」，5 个导航入口（对话 / 知识库 / 模型 / 机器人 / 设置）均能打开；
- 浏览器开发者工具 Console 无红色报错；
- 时间紧张时可直接用控制台命令快速确认后端：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/wiki/stats
```

预期：`health.status=ok`、`health.knowledge_mode="llm-wiki"`、`health.wiki_pages ≥ 15`；`wiki/stats` 返回 `pages ≥ 15`、`sources > 0`、`documents ≥ 10`（首次启动自动导入预编译快照，无需 API Key）。

> 若 `wiki_pages=0`：在「知识库 → 源文件」点击「导入预编译快照」，或 `POST /api/knowledge/seed`。

## 1. 冷启动浏览预编译 Wiki（约 60 秒）— 离线可用的知识底座

**操作**：

1. 进入「知识库」页（默认「词条」Tab），展示顶部统计：Wiki 词条 / 来源锚点 / 源文件 / 最近构建；
2. 展示按标签分组的词条目录（如「奖助」「学工」「生活」「就业」…），点击任一词条打开详情抽屉；
3. 在搜索框输入「国家奖学金怎么申请」，观察返回的导航 `method`、候选分数与命中词条；
4. 打开《国家奖学金申请》词条，展开「来源锚点」：显示来源文件、原文章节与逐字原文摘录；底部展示「相关词条」。

**预期**：

- 无需任何 API Key，词条可正常浏览（预编译快照已随仓库提供）；
- 搜索返回 `method=exact` 或 `method=keyword`，命中「国家奖学金」相关词条；
- 词条详情包含 `body_html` 渲染后的正文、别名/标签、来源锚点与相关词条；
- 切换到「源文件」Tab，可看到 10 篇示例源文件与各自编译出的 `page_count`。

## 2. 用 DeepSeek 问政策问题（约 90 秒）— 双层引用核心亮点

**前置**：确认「模型」页默认模型为真实 DeepSeek（`deepseek-chat`），点「测试连接」返回 `ok=true`；若尚未配置，按 [README §七](../README.md) 添加 Provider 与模型。

**操作**：进入「对话」页，点击快捷问题 **「国家奖学金怎么申请？需要哪些材料？」**（或手动输入后按 Enter）。

**预期**：

- 回答由真实 LLM 基于 Wiki 词条生成（接口 `fallback=false`），按「条件 → 材料 → 流程 → 咨询」分点组织；
- 句末带 `[1]`、`[2]` 等编号，与下方**词条引用卡片**一一对应；
- 引用卡片为**双层结构**：上层是词条标题 / 摘要 / 正文摘录（点击可跳转词条详情），下层是来源锚点「原始文件 · 章节 · 原文摘录」，可直接核对原文；
- 回答尾部带免责提示「请以学校最新官方通知为准」；
- 继续追问「那同一学年可以同时拿国家奖学金和国家励志奖学金吗？」——沿用同一会话，基于词条回答「不可兼得」，体现多轮上下文。

**兜底演示**（可选）：临时把默认模型切回 mock（或断网），再问一个问题——预期 `fallback=true`，回答变为「词条摘录」但仍保留双层引用。切回 DeepSeek 后恢复正常。

## 3. 上传新文件，看自动编译（约 60 秒）— LLM Wiki 的“编译”能力

**操作**：

1. 「知识库 → 源文件」Tab，拖拽上传一个本地 `.md` 文件（可现场新建「测试通知.md」，写两段课程调整通知）；
2. 观察状态从「编译中」变为「就绪」，并显示页数（`page_count`）；
3. 切回「词条」Tab，搜索新文件中的关键词（如「课程调整」），打开自动生成的新词条，查看其来源锚点（应指向刚上传的文件与章节）；
4. 回到对话页提问新文件里的内容，验证回答引用新词条。

**预期**：

- 上传返回 `status=ready`、`page_count>0`，并产生 1–3 个新词条与来源锚点；
- 词条正文由 LLM 归纳（概述 / 流程 / 注意事项），`source_quote` 能在原文中逐字找到；
- 未配置真实 LLM 时上传会 `status=failed` 并提示配置模型（这是设计行为，不是缺陷）。

## 4. 词条编辑与重建（约 40 秒）

**操作**：

1. 打开第 3 步生成的新词条，点击「编辑」，修改摘要或正文中的一句话 → 保存；
2. 观察词条 `version` 增加、状态保持 `ready`；
3. 回到「源文件」Tab，对该文件点「重新编译」，观察构建记录（`/api/wiki/builds`）；
4. 再次搜索该词条，确认没有出现重复词条（按标题归一化去重 / 合并）。

**预期**：

- 编辑立即生效，词条详情与对话引用同步更新；
- 重建不产生重复词条；共享词条只更新来源，独占词条随源文件删除而清理；
- 构建记录可在「源文件」Tab 或 `GET /api/wiki/builds` 查看状态与页数统计。

## 5. 控制台机器人（约 30 秒）— 多渠道复用同一问答内核

**操作**：

1. 进入「机器人」页，找到「控制台调试通道」，在「模拟对话」输入「心理中心怎么预约？」；
2. 预期返回与 Web 一致的回复与双层引用，`outbound` 区域展示将要发送的报文；
3. 下方「消息日志」出现该条记录（渠道 `console`）。

## 6. （可选）QQ / 微信公众号（约 30 秒）

- 展示 QQ 通道卡片上的反向 WebSocket 地址 `ws://127.0.0.1:8000/api/bots/onebot/{id}/ws` 与 NapCat 配置方式；
- 展示微信通道的回调地址与签名校验说明（`sha1(sort(token,timestamp,nonce))`）；
- 若已提前配置好，现场用手机 QQ / 微信发一条消息，展示真机回复；
- 完整配置步骤见 [BOT-INTEGRATION.md](BOT-INTEGRATION.md)。

## 7. 验收对照表

| 验收项 | 操作位置 | 通过标准 |
|--------|----------|----------|
| 冷启动 Wiki 快照 | `/api/health`、知识库页 | `knowledge_mode=llm-wiki`，`wiki_pages ≥ 15`，无 Key 可浏览 |
| 目录与搜索 | `/api/wiki/tree`、`/api/wiki/search` | 按标签分组；`method` 为 exact/keyword/llm，无命中为 none |
| 词条详情与来源 | 知识库 → 词条详情 | `body_html` 渲染、`sources[]` 含文件/章节/原文摘录、`links[]` |
| 真实问答双层引用 | 对话页 | `fallback=false`，`citations[].page_id` + `sources[].section/quote` 可追溯 |
| 降级可用 | 切 mock / 断网 | `fallback=true`，仍有词条摘录与引用，无 500 |
| 上传编译 | 知识库 → 源文件上传 | `status=ready`、`page_count>0`，生成新词条与来源锚点 |
| 编辑与重建 | 词条编辑 / 重新编译 | `version+1`，无重复词条，build 记录可查 |
| 机器人复用 | 机器人页控制台通道 | 返回一致回复与 `outbound`，日志可见 |
| 页面可用 | 5 个视图 | 无 console 报错，移动端窄屏布局正常 |

## 8. 演示应急方案

| 突发情况 | 处理 |
|----------|------|
| 8000 端口被占用 | `.\scripts\run.ps1 -Port 8010`，改用 <http://127.0.0.1:8010> |
| 数据库状态被之前演示改乱 | 停止服务 → 删除 `backend/data/app.db` → 重新启动（自动重建并导入预编译 Wiki 快照） |
| 没有网络 / 没有 API Key | 展示预编译 Wiki 浏览 + 降级问答，强调“离线可读的知识底座”是设计目标，`fallback=true` 是预期行为 |
| DeepSeek 响应慢 | 提前把设置页 `wiki_max_pages` 调到 2、`wiki_answer_max_chars` 调到 6000；演示时先展示已缓存的词条详情 |
| 上传编译失败 | 检查默认模型是否真实可用（mock 不能编译）；把 `documents.error` 的中文原因展示出来，说明可重试 |
| 导航选错词条 | 打开 `/api/wiki/search` 看 `method` 与候选分数，展示如何通过编辑别名/标签修正 |
| 页面白屏 | 确认通过 `http://127.0.0.1:8000/` 访问（不要双击 `index.html`），检查后端是否已启动 |
| 演示机中文乱码 | `chcp 65001`，或直接使用浏览器界面（不受终端编码影响） |

## 9. 已验证（真实服务端到端验收实测）

> 实测日期：**2026-10-22**；环境：Windows 11 + Python 3.11 + 仓库 `.venv`；真实服务：`.\scripts\run.ps1`（uvicorn <http://127.0.0.1:8000>），默认模型 **DeepSeek Chat**，已导入 `backend/data/wiki_seed.json` 预编译快照。
> 自动化测试使用临时数据目录 + fake LLM（不调用真实 API）；真实 E2E 少量调用 DeepSeek（问答 / 上传编译 / reindex）。

### 9.1 自动化测试（fake LLM，无真实额度消耗）

| 验证项 | 命令 | 实测结果 |
|--------|------|----------|
| pytest 全量 | `.\scripts\check.ps1` | **62 passed**，1 warning（`StarletteDeprecationWarning`，依赖提示，无影响），耗时约 22s |
| 用例分布 | — | test_wiki 18 / test_bots 17 / test_chat 15 / test_system 7 / test_models 5 |
| 测试隔离 | `conftest.py` | 临时 `NPU_DATA_DIR`/`NPU_DB_PATH`/`NPU_WIKI_SEED_PATH`；每用例重置数据库与上传目录；不污染 `backend/data/app.db` |
| fake LLM | `conftest.py` | 按提示词分流：编译返回固定 JSON（含逐字 `source_quote`）、合并返回固定结果、导航返回 `page_ids`、回答返回带 `[1]` 文本、寒暄返回自然回复；另有失败模式验证降级路径 |

### 9.2 真实服务端到端（8000 + DeepSeek，30 项检查全部通过，总耗时约 16s）

| 验证项 | 实测结果 |
|--------|----------|
| 健康检查 | `knowledge_mode=llm-wiki`、`wiki_pages=28`、默认模型 `DeepSeek Chat`（openai） |
| 预编译快照基线 | **10 篇源文件 / 28 个词条 / 30 条来源锚点**；`/api/wiki/tree` 9 个标签分组 |
| 目录 / 详情 | 按标签分组正常；词条详情含 `body_html`、来源锚点（文件·章节·原文）、相关词条 |
| 导航搜索 | 「国家奖学金怎么申请」→ `method=exact`、命中正确词条；无命中问题 → `method=none` |
| 真实问答（双层引用） | `fallback=false`，DeepSeek 回答耗时约 **1973ms**；citations 含 `page_id`/`page_title`/`snippet` 与 `sources[].section/quote`；`[1]` 与引用编号一致；`navigation.method=exact` |
| 无命中兜底 | 返回「校园 Wiki 暂未收录」+ 咨询渠道，`citations=[]` |
| 寒暄 / 闲聊（真实 LLM） | 「你好」「谢谢」「你是谁」→ 自然回复（`navigation.method=chitchat`、`citations=[]`、不再误报暂未收录）；「今天天气怎么样」→ 友好服务范围说明（`method=none`、`citations=[]`）；政策问题仍走 Wiki 引用（回归通过） |
| 真实上传编译 | 上传 1 篇新 `.md` → `status=ready`、**3 个词条**、来源锚点指向新文件，编译耗时约 **3.22s**；新词条可被搜索命中 |
| 词条编辑 | `PUT /api/wiki/pages/{id}` 成功，`version 1→2`，摘要立即生效 |
| 单篇 reindex | `POST /knowledge/documents/{id}/reindex` → `build.status=ready`，耗时约 **4.24s**，不产生重复词条；`/api/wiki/builds` 有记录 |
| 控制台机器人 | console `/test` 返回回复 + 双层引用 + `outbound`，消息日志可见 |
| 微信公众号 | GET 签名校验返回 echostr；文本消息回复 `text/xml; charset=utf-8` 明文 XML |
| OneBot 11 | 反向 HTTP 事件返回 `{"status":"ok"}`；错误 token 403 |
| 设置 / 统计 | `wiki_max_pages` / `wiki_answer_max_chars` 更新生效；`/system/stats` 用 `wiki_pages/wiki_sources`（无 chunks 字段） |
| 前端资源 | `index.html` 与知识库视图 JS 均 200（由后端静态托管） |
| 数据清理 | E2E 自建文档 / 会话 / 通道已清理；演示库恢复 10 源文件 / 28 词条 / 30 来源 |

### 9.3 演示数据基线

- 源文件：10 篇中文 Markdown（`backend/data/seed_docs/`）；
- Wiki：28 个词条 / 30 条来源锚点 / 9 个标签分组（`backend/data/wiki_seed.json` 预编译快照，冷启动自动导入）；
- 模型：默认 `DeepSeek Chat`；内置 mock 仅用于链路/降级演示（不能编译）；
- 机器人：控制台调试通道默认可用；QQ / 微信通道按需创建；
- 设置：`counselor_name=小南`、`wiki_max_pages=3`、`wiki_answer_max_chars=9000`、引用开启。

### 9.4 已知限制（实测确认，不影响 MVP 验收）

1. **编译依赖真实 LLM 且按单元计费**：1 篇中等长度文件实测 3.22s / 3 个词条；长文件拆多单元后调用次数与耗时线性增加。
2. **导航有概率选错词条**：优先精确/关键词；弱相关时走 LLM 选页，可通过 `/api/wiki/search` 的 `method` 与候选分数定位，并用别名/标签人工修正。
3. **`needs_review` 词条只展示不参与问答**：LLM 输出非法或来源无法校验时生成兜底词条，需人工编辑后转为 `ready`。
4. **无语义检索**：设计上不使用 embedding / 向量库 / rerank，同义改写依赖别名与标签维护。
5. **快照与提示词版本相关**：源文件或编译提示词更新后需重新编译并导出 `wiki_seed.json`。
6. **Web 会话无用户体系**：历史会话全局可见，多租户/登录鉴权为契约非目标。
7. **公众号仅明文模式**：问答超过约 4.5s 先回兜底提示，需重发；加密模式为非目标。
8. **单进程 SQLite**：部署保持单实例，不要多 worker。
9. **问候 / 闲聊由 LLM 自然回应，不引用 Wiki**：寒暄、致谢、告别、自我介绍（`navigation.method=chitchat`）不检索词条、`citations=[]`；非校园话题仅做「服务范围」说明，不做开放域闲聊，也不编造政策。
10. 依赖库提示 `httpx` + `starlette.testclient` 的 `StarletteDeprecationWarning`，仅测试告警。

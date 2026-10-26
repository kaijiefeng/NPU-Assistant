# NPU-Assistant 5 分钟演示脚本（LLM Wiki）

> 目标：在 5 分钟内完整展示「LLM Wiki 编译 → 词条导航 → 双层引用回答 → 多渠道机器人」四类核心能力。
> 演示环境：Windows + 已安装依赖的 `.venv`，默认地址 <http://127.0.0.1:8000>；配置真实 DeepSeek 时问答质量最佳。
> 本文所有示例词条内容来自 `backend/data/seed_docs/` 与预编译快照 `backend/data/wiki_seed.json`，其中的时间、金额、电话均为示例。
> 六个页面的实际界面截图见 `docs/screenshots/`（由 `scripts/ui_check.py` 自动生成），可先整体浏览再按下述脚本演示。

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
| 页面可用 | 6 个视图 | 无 console 报错，移动端窄屏布局正常 |

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
| pytest 全量 | `.\scripts\check.ps1` | **120 passed**，1 warning（`StarletteDeprecationWarning`，依赖提示，无影响），耗时约 42s |
| 用例分布 | — | test_bots 38 / test_wiki 19 / test_chat 17 / test_import 16 / test_system 11 / test_models 5 / test_mobile 4 |
| 测试隔离 | `conftest.py` | 临时 `NPU_DATA_DIR`/`NPU_DB_PATH`/`NPU_WIKI_SEED_PATH`；每用例重置数据库与上传目录；不污染 `backend/data/app.db` |
| fake LLM / fake importer | `conftest.py` | fake LLM 按提示词分流（编译 / 合并 / 导航 / 回答 / 寒暄，含失败模式）；本地 `http.server` 提供公众号样式 HTML、4MB / 9MB / gzip 页面验证 URL 抓取上限；机器人入库用 fake importer（threading.Event 验证微信后台线程） |

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
| 数据清理 | E2E 自建文档 / 会话 / 通道已清理；最终演示基线由 Lead 统一恢复为 10 源文件 / 28 词条 / 30 来源 |

### 9.3 移动端运维 v0.1.1（扫码联网 / 8000 无令牌 / 8011 带令牌，实测 2026-10-22）

| 验证项 | 实测结果 |
|--------|----------|
| PWA 资源 | `manifest.webmanifest` 200（`display=standalone`、4 个 icon、start_url `/#/knowledge`）；`service-worker.js` 200（缓存静态壳、`/api/` network-only）；`/assets/icons/icon-192.png`、`icon-512.png` 200；index 已挂 manifest/theme-color |
| 文本收录（真实 LLM） | 8000：201、`source_type=text`、`ready`、`page_count=1`，耗时约 **0.83–1.14s**；8011 带令牌同样 201 |
| URL 收录（真实抓取 + 编译） | 8000：`https://www.nwpu.edu.cn/` → 201、`title=西北工业大学`、`source_type=url`、`source_url` 正确、`page_count=1`，耗时约 **1.1–1.6s**；8011 带令牌同样 201 |
| 重复 URL | 第二次返回 **200 + `duplicated=true`**，不重复建文档/词条 |
| 批量收录 | `[同一 URL, ftp://…]` → 200，`items=[(ok=true,duplicated=true),(ok=false,error=…)]`，单条失败不影响其它 |
| 鉴权（8011，`NPU_ADMIN_TOKEN`） | 无令牌 POST `import/text` / `import/url` / 上传 / PUT settings → **401**；带 `X-Admin-Token` → 导入 201、settings 200；GET `/health` 与 POST `/chat` 不受影响（200）；历史会话与机器人日志无令牌 401、带令牌 200 |
| 安全修复（8011 令牌侧真实，2026-10-22） | **19/19 PASS**：历史会话/日志 401/200；无令牌写 401；文本/URL/重复/批量收录；通道 token=`****oken`、`api_base` 脱敏、`outbound.url` 脱敏；微信 nonce 首次 200→重放 403→换 nonce 200、过期 timestamp 403、>2MB body 413 |
| 机器人管理员入库（8000） | onebot 通道 `admin_user_ids=10002`：管理员发链接 → `/test` 200 且回复「该链接已收录过」；`#状态` 返回运行状态；非管理员发链接不入库（200） |
| 通道 `/test` 回归 | onebot11 / wechat_mp 通道 `/test` 均 200（修复 `CHAT_PRIVATE` 后），console 无回归 |
| 390px UI（Chrome CDP，390×844） | 知识库页 `innerWidth=390` / `scrollWidth=390` **无横向滚动**；快速收录卡片首屏可见（链接/文本/文件）；设置页无横向滚动且含「管理令牌」入口 |
| 390px 触控目标（修复后复测） | web-ui 已加移动端媒体查询（≤720px）统一放大控件：知识库页 64 个可见控件 **0 个 <44px**（tab/搜索/刷新/图标按钮/标签 chip 均 ≥44）；内联正文链接按 WCAG 2.5.8 例外不强制 |
| 390px UI 交互 | 文本模式真实提交 → 生成 `ready` 文档（`page_count=1`，source_type=text）；8011 设置页保存令牌到 `localStorage`、测试连接「令牌有效」、带令牌导入快照写操作成功（UI 5/5 PASS） |
| 安全修复真实验收（8000 重启后，2026-10-22 23:29） | **17/17 PASS**：`system/info` 返回 `url_import_max_mb=8.0`/`url_import_max_urls=5`/`cors_enabled=false`/`insecure_bind=false` 且**无绝对路径**；CSP/`X-Frame-Options: DENY`/`nosniff`/`Referrer-Policy` 均在、默认无 CORS 头；通道响应 token=`****oken`、`api_base` 去 userinfo/query、`outbound.url` 脱敏；微信 nonce 首次 200 → 重放 403 → 换 nonce 200、过期 timestamp 403、>2MB body 413；无令牌模式历史会话/日志 200 |
| 390px 长文件名/长 URL（重启后复测） | **10/10 PASS**：源文件卡片容器 `scrollWidth=clientWidth=306`、长文件名 ellipsis 不超视口、快速收录长 URL 结果不溢出、五页无横向滚动、console 错误 0（截图 `%TEMP%\npu_mobile_overflow_shots\*.png`） |
| 端到端汇总 | 8000 安全 E2E：17/17 PASS（约 4.1s）；8011 令牌 E2E：19/19 PASS（约 4.2s）；390px UI：10/10 PASS（长文件名/长 URL/五页无溢出） |

### 9.4 演示数据基线

- 源文件：10 篇中文 Markdown（`backend/data/seed_docs/`）；
- Wiki：28 个词条 / 30 条来源锚点 / 9 个标签分组（`backend/data/wiki_seed.json` 预编译快照，冷启动自动导入）；
- 模型：默认 `DeepSeek Chat`；内置 mock 仅用于链路/降级演示（不能编译）；
- 机器人：控制台调试通道默认可用；QQ / 微信通道按需创建；
- 设置：`counselor_name=小南`、`wiki_max_pages=3`、`wiki_answer_max_chars=9000`、引用开启。

### 9.5 已知限制（实测确认，不影响 MVP 验收）

1. **编译依赖真实 LLM 且按单元计费**：1 篇中等长度文件实测 3.22s / 3 个词条；长文件拆多单元后调用次数与耗时线性增加。
2. **导航有概率选错词条**：优先精确/关键词；弱相关时走 LLM 选页，可通过 `/api/wiki/search` 的 `method` 与候选分数定位，并用别名/标签人工修正。
3. **`needs_review` 词条只展示不参与问答**：LLM 输出非法或来源无法校验时生成兜底词条，需人工编辑后转为 `ready`。
4. **无语义检索**：设计上不使用 embedding / 向量库 / rerank，同义改写依赖别名与标签维护。
5. **快照与提示词版本相关**：源文件或编译提示词更新后需重新编译并导出 `wiki_seed.json`。
6. **Web 会话无用户体系**：历史会话全局可见，多租户/登录鉴权为契约非目标。
7. **公众号仅明文模式**：问答超过约 4.5s 先回兜底提示，需重发；加密模式为非目标。
8. **单进程 SQLite**：部署保持单实例，不要多 worker。
9. **问候 / 闲聊由 LLM 自然回应，不引用 Wiki**：寒暄、致谢、告别、自我介绍（`navigation.method=chitchat`）不检索词条、`citations=[]`；非校园话题仅做「服务范围」说明，不做开放域闲聊，也不编造政策。
10. **URL 收录默认禁私网**：只接受 `http/https`、HTML/纯文本，超时 30s、响应 ≤8MB（`NPU_URL_IMPORT_MAX_BYTES` 可调）；内网资料需设 `NPU_URL_IMPORT_ALLOW_PRIVATE=1`，公众号文章抓取依赖外网可达；文章过大/反爬时改用「文本收录」粘贴正文。
11. **机器人入库需管理员配置**：`admin_user_ids` 为空时不触发入库；微信先回 ack、后台线程完成入库，无异步结果推送；入库同样消耗 LLM 编译额度。
12. **PWA 安装需 HTTPS**：局域网 HTTP 只能网页使用；Service Worker 只缓存静态壳，不缓存 API 响应。
13. **移动端触控目标已验证达标**：≤720px 媒体查询统一 ≥44px（390px 实测知识库页 0/64 个控件 <44px）；设置页内联正文链接按 WCAG 2.5.8 inline exception 不强制 44px。
14. 依赖库提示 `httpx` + `starlette.testclient` 的 `StarletteDeprecationWarning`，仅测试告警。

### 9.6 已修复问题（回归验证记录，2026-10-22）

| 问题 | 修复 | 回归验证 |
|------|------|----------|
| 移动端长文件名 / 长 URL 撑破卡片与结果区 | web-ui：grid `minmax(0,1fr)` + 容器 `min-width:0`，文件名/URL 文本 `overflow:hidden + text-overflow:ellipsis` | Chrome CDP 390×844 复测 **10/10 PASS**：源文件卡片容器 `scrollWidth=clientWidth=306`、长文件名 ellipsis 且不超出视口、快速收录长 URL 结果不溢出、五页无横向滚动、console 错误 0（截图 `%TEMP%\npu_mobile_overflow_shots\*.png`） |
| 链接 >3MB 被误拒 | 默认上限 3MB → **8MB**（`NPU_URL_IMPORT_MAX_BYTES`，可配置）；gzip 压缩页面按解压后大小校验并放宽 Content-Length 预检；超限文案含「文本收录 / 调大 `NPU_URL_IMPORT_MAX_BYTES`」建议 | pytest 3 条回归：4MB 可提取页面 201 成功（旧上限会拒绝）、gzip 压缩 4MB 页面 201 成功（不因压缩后 Content-Length 误拒）、9MB 页面 400 且 detail 同时含「过大」+`NPU_URL_IMPORT_MAX_BYTES`+「文本收录」 |
| 非 console 机器人通道 `/test` 500（`CHAT_PRIVATE` 未导入） | bot-gateway 补 import（并做静态检查） | test_bots 回归：onebot11 / wechat_mp 通道 `/test` 均 200，console 无回归 |
| 移动端触控目标 <44px | web-ui：≤720px 媒体查询统一 ≥44px | 390px 实测知识库页 64 个控件 **0 个 <44px**（内联正文链接按 WCAG 2.5.8 例外） |
| 机器人凭据回显 & webhook 裸奔风险（安全 P0） | bot-gateway：通道 config 凭据脱敏（`****`+后 4 位）、`has_access_token`、OneBot 创建自动生成随机 token（`token_generated`/`token_hint`）、PUT 空串/脱敏占位不覆盖、空 token webhook 403、微信拒绝公开默认 token `npu-assistant` 且时间戳 ±300s、self_id 不一致事件忽略、`NPU_BOT_ALLOW_INSECURE=1` 仅本地调试放行；发送不跟随重定向、`api_base` 仅 http(s) | test_bots 8 条安全用例通过（脱敏/回填不覆盖/自动随机 token/空 token 403/self_id 不一致忽略/微信默认 token 403/时间窗过期 403/insecure 开关），PUT 后原 token 仍可调用 webhook |
| 历史会话泄露 & 匿名会话串号 | chat.py：`GET /chat/conversations` 与 `/messages` 在配置 `NPU_ADMIN_TOKEN` 后需 `X-Admin-Token`；匿名 `session_key` 改为随机 `web:anonymous-<随机>` | test_chat 新增 2 条：401/带令牌 200、`POST /chat` 始终公开、两次匿名提问会话不同且 `session_key` 不同 |
| 词条正文存储型 XSS（`javascript:`/`data:` 链接） | wiki.py：`body_html` 渲染后 URL 协议白名单清洗，危险 `href/src` 移除，外链补 `target="_blank" rel="noopener noreferrer"` | test_wiki 新增 1 条：恶意协议全部移除、合法 https/mailto/站内链接保留且外链带 rel |
| 默认 CORS `*`、公网无令牌裸奔、`system/info` 泄露绝对路径、缺少安全响应头 | main/config：CORS 默认关闭（`NPU_CORS_ORIGINS` 白名单）；非回环监听未设 `NPU_ADMIN_TOKEN` 拒绝启动（`NPU_ALLOW_INSECURE_LAN=1` 例外）；下发 CSP/`X-Frame-Options`/`X-Content-Type-Options`/`Referrer-Policy`；`system/info` 移除绝对路径并新增 `url_import_max_mb/url_import_max_urls/cors_enabled/insecure_bind` | test_system：`system/info` 无 `data_dir/db_path` 等字段与盘符路径、`url_import_max_mb=8.0`、默认无 CORS 头、四项安全响应头存在 |
| URL 并发去重（多请求同时收录同一链接） | importer：同 URL 进程内串行锁 + `documents.source_url` 唯一部分索引 + `IntegrityError` 兜底返回既有文档 | test_import 新增 1 条：4 线程并发收录同一 URL → 仅一次 201、其余 200 duplicated，最终文档数 1 |
| 安全复审 4 项：机器人日志未鉴权、`api_base` 泄露凭据、微信 nonce 可重放、webhook 超大 body | bots.py：`GET /bots/logs` 加 `require_admin_token`；`api_base`/outbound `url` 去 userinfo+query+fragment 回显且 PUT 回填脱敏值不覆盖；微信 nonce 进程内防重放（TTL 330s/上限 1000，签名失败不消费）；OneBot/微信 webhook body 上限 2MB → 413 | test_bots 新增 5 条：日志 401/带令牌 200 且 webhook 不受影响、`api_base` 脱敏+PUT 保留原值+outbound url 脱敏、微信 GET/POST nonce 重放 403/换 nonce 200/坏签名不消费、空 nonce 登记放行、2MB 超限 413 |

> 安全修复后全量 **104 passed**（v0.1.0）；v0.1.1 新增手机扫码/隧道/首次启用令牌测试后 **110 passed**；v0.1.2 新增真实 .doc/.docx/.pdf 解析测试后 **120 passed**（test_parsing 5 + test_campus_sync 5）。

### 9.7 v0.1.1 扫码联网 / 一键公网（2026-10-23 实测）

- `GET /api/mobile/access` 返回局域网/公网地址与二维码 data URL；`GET /api/mobile/qr.png?url=…` 200。
- 使用真实 cloudflared 2026.8.2：`POST /api/mobile/tunnel/start` 约 4.7s 返回 `https://*.trycloudflare.com`；公网 HTTPS 访问 health、首页、手册均 200；无令牌写操作 401，带 `X-Admin-Token` 文本收录 201；`POST /tunnel/stop` 后公网返回 502。
- 前端 CDP：顶栏「手机扫码」弹窗打开、二维码加载、地址切换、一键开启后自动切换公网二维码并提示；机器人页 3 张快速添加卡、创建后 Webhook 指引正常；390/1440 无横向滚动、console 0 错误。

### 9.8 v0.1.2 UI / 信息架构重构（2026-10-24 实测）

- 六个页面：`#/chat`、`#/knowledge`、`#/manage`、`#/models`、`#/bots`、`#/settings`；侧栏分「使用 / 运维」两组。
- 知识库只读浏览；内容管理拆「收录资料 / 源文件 / 词条维护」三 Tab；模型「模型 / Provider」、机器人「通道 / 调试 / 日志」、设置「基础 / 移动访问与安全 / 回复与引用」。
- 移除全局顶栏扫码、版本号/API 文档/安装按钮、知识库统计卡与快速收录、设置实时预览/配置建议；扫码入口迁入设置。
- 验收：`scripts/ui_check.py` 六页渲染 + 真实问答/来源折叠行通过，console **0 错误**；1440×960 与 390×844 均无横向滚动；内容管理移动端统计 2×2、源文件列表首屏可见；pytest **120 passed**。
- 截图：`docs/screenshots/{chat,knowledge,manage,models,bots,settings}.png`；手册静态图与 2 个 GIF 已同步新界面。

### 9.9 真实校园数据 / 初始知识库（2026-10-24 实测）

- 通过公开页面导入 **12 篇真实数据**：HTML 5（教务部、民航学院、研究生院）、PDF 4（材料学院制度）、DOCX 2（知识产权声明、博士后审核表）、DOC 1（在校学习基本情况表）。
- 数据库达到 **22 篇源文件 / 58 个词条 / 60 个来源锚点**，全部 `ready`；`.doc` 使用纯 Python `legacy-doc` 解析，无需 Word/LibreOffice。
- 自动更新 MVP：`scripts/campus_sync.ps1` 可从研究生院公开通知列表增量导入（robots/限速/限条数/去重）；WAF 拦截的源默认关闭，转人工/官方接口。
- 抽查真实问答：研究生课程调课、博士后进站材料、知识产权声明，均返回真实 PDF/DOCX 来源与原文摘录。
- 负样本：扫描版 PDF 无文本，系统明确提示“扫描件/无文本”，不做 OCR；PPT/PPTX 按约定暂不实现。
- 测试：`backend/tests/test_parsing.py` 使用真实 `.doc/.docx/.pdf` 样本回归；全量 pytest **120 passed**。
- 清单与来源：[CAMPUS-SEED.md](CAMPUS-SEED.md)、[campus-seed/manifest.json](campus-seed/manifest.json)；文件本体在 `backend/data/campus_seed/files/`（gitignored）。

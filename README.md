# NPU-Assistant · 校园辅导员数字分身（LLM Wiki）

> 面向高校学生工作场景的本地可运行 AI 助手：把学生手册、奖助政策、办事指南交给 LLM **编译成结构化 Wiki 词条**，问答时通过「目录 / 别名 / 关键词 / LLM 选页」定位词条，再用 LLM 阅读词条作答，并给出**词条 + 原始文件章节**双层引用。

NPU-Assistant 是一个**可离线演示、可接入真实大模型**的校园问答系统。它把「辅导员数字分身问答、LLM Wiki 知识库、LLM 接入与模型管理、微信 / QQ 机器人」四件事做成一个单体应用：后端 FastAPI + SQLite，前端纯静态 HTML/CSS/ES Module，无 Node 构建、无 CDN 依赖。

> **v0.1.0 首个发布版本**：知识层改为 **LLM Wiki**——「源文件 → LLM 编译词条 → 导航选页 → 词条阅读回答 → 双层引用」，不再使用切片检索、embedding、向量库或 rerank。设计细节见 **[docs/WIKI-DESIGN.md](docs/WIKI-DESIGN.md)**。
>
> ⚠️ 本项目为教学演示 / MVP 原型。当前演示库仅保留真实校园公开资料；实际政策以学校官方发布为准。
>
> 📘 **普通用户（学生 / 辅导员）请直接看 [图文使用手册](frontend/manual/index.html)**：包含手机截图、短动图和常见问题；运行服务后也可访问 <http://127.0.0.1:8000/manual/index.html>。
>
> 🚀 **当前发布版本：v0.1.3**（2026-10-27）· 对应分支 `release`，变更记录见 **[CHANGELOG.md](CHANGELOG.md)**。
> 🎯 **v0.2.0 数据集已发布**：NPU Student Handbook QA **v2026.8**（441 可答 + 10 不可答；检索 100% / 引用 100% / 拒答 100%）；见 **[docs/npu-handbook/release/v2026.8/](docs/npu-handbook/release/v2026.8/)**。
> ➕ **v2026.8 补充**：新增集成电路学院历史/领导/国际合作、鸿蒙教学、联合实验室等官方文档与 QA；见 **[docs/npu-handbook/README.md](docs/npu-handbook/README.md)**。

---

## 一、核心功能

| # | 能力 | 说明 |
|---|------|------|
| 1 | **辅导员数字分身问答** | Web 对话页，基于「Wiki 词条导航」的回答，句末 `[n]` 对应引用卡片；每张引用卡片同时给出**词条**与**来源文件 · 章节 · 原文摘录**双层信息；支持多轮会话与历史会话 |
| 2 | **LLM Wiki 知识库** | 上传 `md / txt / pdf / docx / doc / xlsx / csv / json / html` → 解析 → LLM 编译为结构化词条（概述 / 流程 / 材料 / 时间 / 注意事项，带来源锚点）→ 浏览目录、标签筛选、关键词导航搜索、手工编辑、重建；支持导入**预编译快照**离线使用 |
| 3 | **LLM 接入与模型管理** | OpenAI 兼容（当前实测 **DeepSeek**）、Ollama 本地模型、内置 mock 模型；Provider / 模型 CRUD、设默认、启停、连通性测试；API Key 服务端脱敏。**编译与高质量回答需要真实 LLM** |
| 4 | **微信 / QQ 机器人** | QQ 走 NapCat / OneBot 11（反向 WS + 反向 HTTP + HTTP API 回复）；微信公众号明文模式 XML 回调；控制台调试通道。四个入口复用同一套 `counselor.answer()` Wiki 问答链路 |

**非目标（MVP 明确不做）**：多租户与权限体系、流式输出、**embedding / 向量数据库 / rerank / chunk 检索**、语音、复杂插件系统、公众号加密模式。

## 二、核心链路

```text
上传源文件
  └─ 解析（pdf/docx/doc/xlsx/html…）→ 按标题聚合「编译单元」（≤3500 字）
       └─ 每个单元 1 次 LLM 调用 → 1~3 个结构化词条（JSON）
            └─ 校验来源摘录 → 按标题归一化去重 → 新词条 INSERT / 已有词条 LLM 合并
                 └─ 写入 wiki_pages + wiki_sources（逐字来源锚点）+ wiki_links

学生提问
  └─ 导航（无 embedding）：标题/别名精确匹配 → 关键词覆盖率打分 → 分数不够时 LLM 读目录选页
       └─ 读取 1~3 个词条全文（截断）→ system prompt + 词条上下文 → LLM 回答
            └─ 回答标注 [n] → 引用卡片 = 词条（标题/摘要/摘录）+ 来源锚点（文件/章节/原文）
```

与经典 RAG 的区别：知识被「编译」成人类可读、可编辑的词条，引用可逐字回溯到源文件章节；导航靠目录与词法信号（必要时 LLM 选页），**不依赖向量相似度**。

## 三、界面预览

> 以下截图由 `.\scripts\ui_check.py`（Chrome DevTools Protocol，需要本机安装 Chrome/Edge 且服务已启动）自动生成，保存在 `docs/screenshots/`。

**辅导员对话**：Wiki 词条回答 + 句末 `[n]` + 可展开的「来源 N」行（词条 + 来源文件·章节 + 原文摘录）。

![辅导员对话](docs/screenshots/chat.png)

**知识库（只读浏览）**：搜索、分类筛选、词条卡片；点开即可看正文与来源锚点，不混入运维操作。

![知识库](docs/screenshots/knowledge.png)

**内容管理（运维）**：收录资料 / 源文件 / 词条维护三个独立 Tab；快速收录、上传、编译状态、重建与删除都在这里。

![内容管理](docs/screenshots/manage.png)

**模型管理**：模型 / Provider 双 Tab；默认模型、Provider 卡片（Key 脱敏）、模型列表。

![模型管理](docs/screenshots/models.png)

**机器人**：通道 / 调试 / 日志三个 Tab；QQ / 微信 / 控制台三通道配置、Webhook 地址、控制台模拟对话与消息日志。

![机器人](docs/screenshots/bots.png)

**设置**：基础 / 移动访问与安全 / 回复与引用三个 Tab；人设、系统提示词、管理令牌与扫码入口、Wiki 参数。

![设置](docs/screenshots/settings.png)

## 四、5 分钟快速开始

### 4.1 环境要求

- Windows 10 / 11（脚本为 PowerShell；macOS / Linux 可手动执行等价命令）
- Python 3.11+
- 无需 Node.js、无需数据库服务
- **离线体验**：冷启动自动导入仓库自带的预编译 Wiki 快照（`backend/data/wiki_seed.json`），无需 API Key 即可浏览词条与降级问答
- **上传编译 / 真实问答**：需要配置 OpenAI 兼容模型（推荐 DeepSeek）

### 4.2 安装与启动

```powershell
# 1) 进入项目根目录
cd D:\Development\NPU-Assistant

# 2) 创建虚拟环境（仓库已自带 .venv 可跳过创建）
python -m venv .venv

# 3) 安装后端依赖
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 4) 启动服务（默认 http://127.0.0.1:8000）
.\scripts\run.ps1
```

或者使用一键脚本完成「建环境 → 装依赖 → 启动」：

```powershell
.\scripts\bootstrap.ps1
```

启动后浏览器打开 **<http://127.0.0.1:8000>**：

1. 首次启动自动创建 `backend/data/app.db`、写入默认 mock 模型与控制台机器人通道，并在 `wiki_pages` 为空时**自动导入预编译 Wiki 快照**；
2. 进入「知识库」页可直接浏览词条目录、搜索词条、查看来源锚点；
3. 没有配置真实 LLM 时，问答降级为「词条摘录」（接口返回 `fallback=true`），仍会给出词条级引用，可完整体验导航与双层引用链路；配置 DeepSeek 后 `fallback=false`。

> 端口被占用时：`.\scripts\run.ps1 -Port 8010`，然后打开 <http://127.0.0.1:8010>。

### 4.3 手机扫码访问（v0.1.1 默认方式）

不需要记 IP，也不用单独启动“移动端服务”：启动时加 `-Lan`，然后在电脑浏览器点右上角 **「手机扫码」**，用手机相机/微信扫码即可打开。

```powershell
# 电脑和手机在同一 Wi-Fi
.\scripts\run.ps1 -Lan

# 启动后：
# 1) 电脑打开 http://127.0.0.1:8000
# 2) 点右上角「手机扫码」→ 手机扫码访问
# 3) 如果没安装 cloudflared，弹窗会显示安装命令；安装后可一键开启公网 HTTPS
```

扫码弹窗会显示二维码、访问地址和管理令牌提示；需要在外网（4G/5G、公众号回调、安装 PWA）时，点 **「一键开启公网访问」**（Cloudflare 临时隧道，需电脑已安装 `cloudflared`），二维码会自动切换为 `https://xxx.trycloudflare.com`。没有 `cloudflared` 时按弹窗提示安装：

```powershell
winget install Cloudflare.cloudflared
```

也可以使用固定公网地址：

```powershell
$env:NPU_PUBLIC_URL = "https://npu.example.edu.cn"
.\scripts\run.ps1 -Lan
```


## 五、5 分钟演示脚本

完整分步脚本与预期结果见 **[docs/DEMO.md](docs/DEMO.md)**，速览：

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 冷启动（无 Key）→ 知识库页浏览 Wiki 目录 | 自动导入 ≥10 篇源文件 / ≥15 个词条，按标签分组展示 |
| 2 | 词条搜索「国家奖学金怎么申请」 | 返回 `method=keyword`（或 `exact` / `llm`）与命中词条，可打开详情查看来源锚点 |
| 3 | 对话页提问「国家奖学金怎么申请？需要哪些材料？」 | 配置 DeepSeek 时 `fallback=false`，回答带 `[1][2]`，引用卡片含词条 + 来源文件·章节 + 原文摘录 |
| 4 | 知识库 → 源文件 Tab → 上传一篇新的 `.md` | 显示「编译中」→ `status=ready`、`page_count>0`，自动生成新词条与来源锚点 |
| 5 | 打开新词条 → 编辑正文/摘要 → 保存；源文件 Tab 点「重新编译」 | 词条 `version+1`；重建不产生重复词条 |
| 6 | 机器人页 → 控制台通道「模拟对话」 | 返回与 Web 一致的回答与双层引用，消息日志出现记录 |
| 7 | （可选）配置 QQ / 微信通道 | 复制 Webhook 地址，按 [docs/BOT-INTEGRATION.md](docs/BOT-INTEGRATION.md) 接入 NapCat 或公众号 |

## 六、目录结构

```text
NPU-Assistant/
├─ backend/
│  ├─ app/
│  │  ├─ main.py            # FastAPI 应用装配、静态前端托管、异常处理
│  │  ├─ config.py          # 路径 / 端口 / 上传限制 / Wiki 编译与导航参数
│  │  ├─ db.py              # SQLite 薄封装、建表与旧库迁移、首启默认数据
│  │  ├─ security.py        # 可选管理员令牌（NPU_ADMIN_TOKEN）
│  │  ├─ routers/           # system / chat / knowledge / wiki / models / bots
│  │  ├─ services/
│  │  │  ├─ counselor.py    # 问答编排：Wiki 导航 → 词条上下文 → LLM / 降级 → 双层引用
│  │  │  ├─ wiki_builder.py # 源文件 → 编译单元 → LLM 词条 → 去重 / 合并 / 来源锚点
│  │  │  ├─ wiki_nav.py     # 精确/关键词导航、LLM 选页、上下文构建、引用组装
│  │  │  ├─ text_utils.py   # 分词 / 标题归一化 / slugify
│  │  │  ├─ llm.py          # OpenAI 兼容 / Ollama / mock Provider 适配
│  │  │  ├─ ingest.py       # 上传落盘、解析入口、reindex、删除级联
│  │  │  ├─ importer.py     # URL / 文本快速收录（抓取 + 正文提取 + 复用编译）
│  │  │  ├─ parsing.py      # 多格式解析（pdf/docx/doc/xlsx/csv/json/html/md/txt）
│  │  │  └─ bot_pipeline.py # 机器人统一入站处理（复用 counselor；管理员入库命令）
│  │  └─ adapters/          # onebot（QQ）、wechat_mp（公众号）、base
│  ├─ data/
│  │  ├─ seed_docs/         # 演示源文件目录（示例已清理，仅保留 .gitkeep）
│  │  ├─ wiki_seed.json     # 真实 LLM 预编译的 Wiki 快照（冷启动离线可用）
│  │  └─ uploads/           # 上传源文件落盘目录
│  ├─ tests/                # pytest（conftest 临时数据目录 + fake LLM）
│  └─ requirements.txt
├─ frontend/
│  ├─ index.html            # 单页应用（hash 路由 6 个视图）
│  ├─ manifest.webmanifest  # PWA 清单（standalone / #0b3d91 / 192/512 图标）
│  ├─ service-worker.js     # 静态壳缓存（API 一律 network-only）
│  └─ assets/               # styles.css + 原生 ES Module（api.js / app.js / ui.js / views/）+ icons/
├─ scripts/
│  ├─ run.ps1               # 启动服务（默认仅本机；-Lan 同时开放手机访问）
│  ├─ serve-lan.ps1         # 兼容别名：等价于 run.ps1 -Lan
│  ├─ check.ps1             # 运行 pytest 全量测试
│  ├─ bootstrap.ps1         # 一键建环境 + 装依赖 + 启动
│  ├─ smoke.ps1             # 启动后 Wiki API 冒烟检查
│  ├─ build_wiki_seed.py    # 用真实 LLM 重新生成 wiki_seed.json
│  ├─ backup.ps1            # 备份 DB 快照 + uploads + wiki_seed
│  ├─ screenshot.ps1        # Edge 无头模式六页截图（备用）
│  ├─ campus_sync.ps1       # 受控同步公开校园栏目（建议任务计划每 6 小时）
│  ├─ manual_queue_fetch.py # 人工队列分流：抓取→下载→人工接管
│  ├─ remote_search_probe.py # 限定范围网站/公众号检索探针
│  ├─ ocr_manual_pdfs.py    # 扫描 PDF 本地 OCR（PyMuPDF + RapidOCR）
│  └─ ui_check.py           # Chrome CDP 交互冒烟 + 六页截图（推荐）
├─ docs/
│  ├─ CONTRACT.md           # 开发契约（接口事实源）
│  ├─ WIKI-DESIGN.md        # LLM Wiki 设计与契约（v0.1.1）
│  ├─ MOBILE-OPS.md         # 移动端运维与快速收录（v0.1.2）
│  ├─ API.md                # 接口速览与调用示例
│  ├─ ARCHITECTURE.md       # 架构、请求链路、表结构、部署建议
│  ├─ BOT-INTEGRATION.md    # NapCat / OneBot 11、公众号、控制台接入与排障
│  ├─ CAMPUS-SEED.md        # 真实校园数据测试集 / 初始知识库（网页/PDF/DOCX/DOC）
│  ├─ CAMPUS-CONTENT-PLAN.md # 内容领域清单、合法采集与自动更新方案
│  ├─ CAMPUS-MANUAL-QUEUE.md # 需人工浏览器下载/复制的条目（P0/P1/P2）
│  ├─ CAMPUS-REMOTE-SEARCH.md # 限定范围联网检索试验（四网站 + 四公众号）
│  ├─ DATA-PROVENANCE.md      # 真实/示例数据审计与源文件管理
│  ├─ EXTERNAL-DATA-POLICY.md # 外部数据边界与来源白名单审计
│  ├─ eval/                 # 混合检索/真实问题 TDD 测试集与报告
│  │  ├─ REALWORLD-TDD.md   # 42 道真实学生问题：红→补库→绿
│  │  ├─ COVERAGE-MATRIX.md # 参考开源数据集的校园问答覆盖矩阵
│  │  └─ HYBRID-TEST-PLAN.md # 本地+远程混合检索评测
│  ├─ NPU-STUDENT-HANDBOOK-PLAN.md # v0.2.0 学生手册数据集方案
│  ├─ npu-handbook/         # M2 来源清单、QA schema、M3 语料
│  ├─ DEMO.md               # 5 分钟演示脚本与已验证结果
│  └─ screenshots/          # 六页界面截图（ui_check.py 自动生成）
└─ README.md
```

## 七、LLM 配置（DeepSeek 推荐）

Wiki 编译与高质量回答都依赖真实 LLM；默认的 mock 模型只用于链路测试（**尝试编译源文件会被拒绝并提示配置模型**）。

### 7.1 方式 A：OpenAI 兼容接口（推荐 DeepSeek）

在「模型」页点击「添加 Provider」，填写：

| 字段 | 说明 | DeepSeek 示例 |
|------|------|---------------|
| 名称 | 自定义 | `DeepSeek` |
| 类型 | `openai` | — |
| Base URL | 到 `/v1` 为止 | `https://api.deepseek.com/v1` |
| API Key | 仅服务端保存，接口返回脱敏 | `sk-****` |

然后「添加模型」→ 选择该 Provider、模型名 `deepseek-chat` → 「设为默认」→ 点「测试连接」应返回 `ok=true`。兼容 OpenAI 协议的服务（DeepSeek、通义千问、Moonshot、OpenAI、自建 vLLM 等）均可接入。

### 7.2 方式 B：Ollama 本地模型

1. 本机安装 [Ollama](https://ollama.com/) 并拉取模型：`ollama pull qwen2.5:7b`；
2. 确认 `http://127.0.0.1:11434` 可访问；
3. 模型页添加 Provider：类型 `ollama`，Base URL `http://127.0.0.1:11434`，无需 API Key；
4. 添加模型（模型名与 `ollama list` 中一致）并设为默认。

### 7.3 方式 C：mock 模型（仅链路测试）

保持默认即可，不联网。可用于浏览预编译 Wiki、验证页面交互与 pytest（测试用 fake LLM）；**不能用于上传编译**，问答会降级为词条摘录（`fallback=true`）。

> LLM 调用失败或超时时会自动降级（响应 `fallback=true`），不会返回 500。

### 7.4 运行时环境变量（可选）

| 变量 | 默认 | 说明 |
|------|------|------|
| `NPU_HOST` / `NPU_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `NPU_DATA_DIR` | `backend/data` | 数据目录（数据库、上传文件） |
| `NPU_DB_PATH` | `backend/data/app.db` | SQLite 数据库路径 |
| `NPU_WIKI_SEED_PATH` | `backend/data/wiki_seed.json` | 预编译 Wiki 快照路径 |
| `NPU_MAX_UPLOAD_MB` | `20` | 单文件上传上限 |
| `NPU_AUTO_SEED` | `1` | 首启是否自动导入 Wiki 快照 |
| `NPU_ADMIN_TOKEN` | 空 | 设置后管理类写操作与历史会话接口需 `X-Admin-Token` 请求头（学生问答 `POST /api/chat`、GET、机器人 webhook 不受影响） |
| `NPU_CORS_ORIGINS` | 空 | CORS 白名单（逗号分隔）；默认关闭，同源 SPA 不需要 |
| `NPU_ALLOW_INSECURE_LAN` | `0` | 对外监听且未设管理令牌时，服务默认拒绝启动；仅可信内网临时演示可设 `1` |
| `NPU_BOT_ALLOW_INSECURE` | `0` | 机器人 webhook 空 token 逃生开关，仅限本机调试 |
| `NPU_URL_IMPORT_ALLOW_PRIVATE` | `0` | URL 收录是否允许私网/回环地址（内网资料才临时开启） |
| `NPU_URL_IMPORT_MAX_BYTES` | `8388608` | URL 抓取响应大小上限（默认 8MB，可调） |
| `NPU_URL_IMPORT_MAX_URLS` | `5` | 批量收录单次链接上限 |

## 八、预编译 Wiki 快照（离线演示）

- `backend/data/wiki_seed.json` 是当前 **75 篇真实来源**的词条快照（文档 / 词条 / 来源锚点 / 关联链接），冷启动可直接浏览；源文件与数据库运行时文件不入库。
- 冷启动且 `wiki_pages` 为空时自动导入；也可在「知识库 → 源文件」点「导入预编译快照」或调用 `POST /api/knowledge/seed`（幂等）。
- 重新生成快照（会消耗 LLM Token）：

```powershell
$env:NPU_SEED_API_KEY="sk-..."
$env:NPU_SEED_BASE_URL="https://api.deepseek.com/v1"   # 可选
$env:NPU_SEED_MODEL="deepseek-chat"                    # 可选
.\.venv\Scripts\python.exe scripts\build_wiki_seed.py
```

脚本使用临时数据目录编译，不会污染 `backend/data/app.db`。

## 九、微信 / QQ 机器人接入

- **QQ（NapCat / OneBot 11）**：NapCat 侧配置「HTTP 服务器」（默认 `127.0.0.1:3000`，Token 与通道 `access_token` 一致，供后端发送回复）与「WebSocket 客户端」（反向 WS，URL 为 `ws://127.0.0.1:8000/api/bots/onebot/{通道ID}/ws`，Token 字段填通道 `access_token`）；也可选用「HTTP 客户端」做反向 HTTP 事件。群聊默认仅 @ 机器人时回复。通道创建时留空 `access_token` 会自动生成随机值（API 只回显脱敏值），空 Token 的 webhook 一律 403。
- **微信公众号（明文模式）**：需要公网可达的 URL（可用 ngrok / cpolar 内网穿透），在公众号后台「开发 → 基本配置」填写回调 URL `https://<公网域名>/api/bots/wechat/{通道ID}` 与自选强随机 Token（与通道 config 一致，**不能为空或使用公开默认值 `npu-assistant`**），完成签名校验（含 ±300 秒时间窗）后即可收发 XML 文本消息。
- **控制台调试通道**：无需任何外部账号，在「机器人」页直接模拟学生消息，走完整 `counselor → Wiki 导航 → 词条回答` 链路。
- **安全**：通道接口的 token 脱敏、`has_access_token`、PUT「留空不修改」、`self_id` 一致性校验及 `NPU_BOT_ALLOW_INSECURE=1` 本地调试开关详见文档。

完整步骤、配置示例与故障排查见 **[docs/BOT-INTEGRATION.md](docs/BOT-INTEGRATION.md)**。

## 十、移动端运维（v0.1.1）

> 完整设计、部署与安全清单见 **[docs/MOBILE-OPS.md](docs/MOBILE-OPS.md)**。目标是「手机就能运维」：手机扫码打开，查看状态、上传文件、把网页/公众号文章链接或转发文字一键收录进 Wiki；QQ/微信机器人管理员转发链接即可自动入库。

### 10.1 手机访问方式（v0.1.1 默认扫码）

> **移动端不是单独的服务**：网页、手机端、PWA、API、机器人回调都由同一个 FastAPI 进程提供，没有额外端口。默认 `.\scripts\run.ps1` 只监听 `127.0.0.1`（本机）；要让手机访问，启动时加 `-Lan`：
>
> ```powershell
> .\scripts\run.ps1 -Lan
> # 电脑打开 http://127.0.0.1:8000，点右上角「手机扫码」，手机扫码即可
> # 需要 4G/5G 或 PWA 安装：扫码弹窗里点「一键开启公网访问」
> ```
>
> 扫码弹窗自带二维码、地址切换、管理令牌提示与 Cloudflare 临时隧道开关；同一条命令即同时启动「总服务 + 移动端」，无需再开第二个服务。

| 方式 | 适用 | HTTPS | 说明 |
|------|------|-------|------|
| **扫码（默认）** | 电脑启动后，手机扫码打开 | 看地址 | `run.ps1 -Lan` → 电脑右上角「手机扫码」；可在弹窗内一键开公网 HTTPS |
| 局域网 | 手机与电脑同一 Wi-Fi，先跑通 | 否 | `run.ps1 -Lan` 后手机访问打印出的 `http://<电脑IP>:8000`；HTTP 下 PWA 不能安装，网页可用 |
| 公网隧道（推荐试用） | 手机 4G/5G、公众号回调、PWA 安装 | 是 | 扫码弹窗内「一键开启公网访问」（Cloudflare 临时隧道），或 Cloudflare Tunnel / ngrok / frp |
| 云服务器 | 7×24 生产 | 是 | systemd + Caddy/Nginx 反代 + 定期备份 |

**管理令牌**：设置环境变量 `NPU_ADMIN_TOKEN`（建议 ≥24 位随机串）后，除学生问答 `POST /api/chat`、只读 GET 与机器人 webhook 外的写操作都要求请求头 `X-Admin-Token`。手机在「设置 → 移动访问与安全」登录一次，令牌保存在 `localStorage`，所有 API 自动携带；收到 401 时会引导回设置页。未设置令牌时完全放行（仅限本机演示）；`-Lan` 启动会在未设置时自动生成并打印一个令牌。**服务端未启用令牌时，也可以直接在设置页输入 ≥16 位随机令牌并保存，本机一键启用（持久化，无需重启），公网扫码按钮随后即可用。**

### 10.2 快速收录（手机 / 桌面通用）

「内容管理」→「收录资料」的「快速收录」卡片提供三种模式：

- **链接**：一行一个、最多 5 个，调用 `POST /api/knowledge/import/url`（单条）或 `/import/batch`（批量）→ 抓取正文 → 生成源文件 → LLM 编译词条；重复 URL 返回 `duplicated=true` 不重复编译；
- **文本**：粘贴微信/QQ 转发的文字 + 可选标题，调用 `POST /api/knowledge/import/text`（30–200000 字）；
- **文件**：原有上传入口（`POST /api/knowledge/documents`）。

URL 抓取默认拒绝私网/回环地址（内网部署可设 `NPU_URL_IMPORT_ALLOW_PRIVATE=1`），仅接受 HTML/纯文本，响应 ≤8MB（`NPU_URL_IMPORT_MAX_BYTES` 可调）；公众号文章过大或触发反爬时，改用「文本」粘贴正文收录。

### 10.3 机器人管理员入库

在机器人通道配置中填写 `admin_user_ids`（QQ 号 / 公众号 openid，逗号分隔或数组）后，管理员在 QQ / 微信中：

| 发送内容 | 行为 |
|----------|------|
| 纯链接或包含链接的文字、`#入库 <链接>`、`#收录 <链接>` | 抓取并入库，回复「已收录《标题》：生成 N 个词条…」 |
| `#状态` | 回复版本、Wiki 词条数、源文件数、默认模型、最近构建状态 |
| `#帮助` | 回复命令说明 |

- **QQ**：同步入库后直接回复结果；
- **微信公众号**：受 5 秒被动回复限制，先立即回复「收到，已开始收录，请稍后到 Wiki 查看」，后台线程完成入库（MVP 不做客服消息异步推送）；
- 非管理员发链接不触发入库，按普通问题回答；`import_enabled=false` 可关闭该通道入库。

### 10.4 PWA 与备份

- PWA：`frontend/manifest.webmanifest` + `service-worker.js` + 192/512 图标；`display=standalone`，主色 `#0b3d91`；Service Worker 只缓存静态壳、不缓存 API；安装需 HTTPS（隧道/反代）。
- 备份：`.\scripts\backup.ps1` 生成 `backups/npu-assistant-<时间>.zip`（DB 快照 + uploads + seed），恢复时停服覆盖 `backend/data`。

## 十一、运行测试

```powershell
# 全量测试（pytest，工作目录 backend）
.\scripts\check.ps1

# 或手动执行
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q
```

当前 **126 个用例全部通过**（约 42s）。测试使用 `tempfile` 临时数据目录（`NPU_DATA_DIR` / `NPU_DB_PATH` / `NPU_WIKI_SEED_PATH`）与 **fake LLM**（固定编译 JSON / 回答文本 / 寒暄回复，可切换失败模式），**不会调用真实 API，也不会污染** `backend/data/app.db`。覆盖：Wiki 快照导入 / 目录 / 详情 / 搜索 / 编辑 / 删除 / 上传编译 / 合并去重 / 级联删除 / rebuild / builds、body_html XSS 链接清洗、对话双层引用与降级、寒暄与闲聊意图（chitchat / 服务范围说明）、历史会话鉴权、移动端 URL/文本/批量收录与 SSRF/8MB/gzip/并发去重、手机扫码/二维码/一键公网隧道/首次启用服务端令牌/真实校园文件解析、管理员令牌、机器人安全（凭据脱敏、api_base 脱敏、webhook token、微信时间窗 + nonce 防重放、self_id、2MB body 上限、日志鉴权）、模型管理与系统设置。

服务启动后还可运行真实 HTTP 冒烟与前端交互检查：

```powershell
.\scripts\smoke.ps1                                          # Wiki API 冒烟（需服务已启动）
.\.venv\Scripts\python.exe scripts\ui_check.py               # Chrome CDP 交互冒烟 + 六页截图
```

接口调试：启动后访问 Swagger UI <http://127.0.0.1:8000/api/docs>，或 `GET /api/health`（含 `knowledge_mode`、`wiki_pages`）；常用示例见 [docs/API.md](docs/API.md)。

## 十二、常见问题

**Q1：启动报错「未找到虚拟环境」？**
先执行 `python -m venv .venv`，再 `.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt`。

**Q2：8000 端口被占用？**
用 `.\scripts\run.ps1 -Port 8010` 换端口；不要同时启动多个常驻服务。

**Q3：页面打不开或提示「前端资源尚未就绪」？**
确认 `frontend/index.html` 存在；前端由后端直接托管，不要直接双击 `index.html`（API 请求需要同源后端）。

**Q4：上传文件后 `status=failed`，提示编译失败？**
Wiki 编译需要真实 LLM。请到「模型」页检查默认模型是否为 DeepSeek / 其他真实 Provider，点「测试连接」确认可用后重新上传或点「重新编译」。mock 模型无法编译。

**Q5：回答总是 `fallback=true` / 没有真实 LLM 文案？**
当前默认模型是 mock，或 LLM 调用失败。配置真实模型并设为默认后应为 `fallback=false`；即使降级，仍会返回词条摘录与引用。

**Q6：想重新体验首次启动流程？**
停止服务后删除 `backend/data/app.db`（可选清空 `backend/data/uploads/`），重新启动即自动重建并导入预编译 Wiki 快照。

**Q7：中文乱码？**
项目文件统一 UTF-8。PowerShell 使用 `chcp 65001` 或保持 `$env:PYTHONUTF8=1`（脚本已设置）。

## 十三、已知限制

1. **编译依赖真实 LLM**：无 Key 不能上传编译；每次编译会产生 1–N 次 LLM 调用（按编译单元计数，成本与文件长度相关）。
2. **编译可能产生 `needs_review` 词条**：LLM 输出解析失败或来源无法校验时保留原文兜底，只展示、不参与问答，需人工编辑后转 `ready`。
3. **导航可能选错页**：精确/关键词匹配优先，弱相关时 LLM 选页；可在词条页手工修正别名/标签，或通过 `/api/wiki/search` 观察 `method` 排查。
4. **无语义检索**：本设计明确不使用 embedding / 向量库 / rerank；同义改写能力有限，依靠别名与标签维护弥补。
5. **快照会过期**：`wiki_seed.json` 与源文件/提示词版本相关，源文件更新后需重新编译并导出快照。
6. **单进程 SQLite**：部署保持单实例，不要多 worker（见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)）。
7. **公众号仅明文模式**，问答超过约 4.5 秒会先回兜底提示（无异步客服消息推送）。
8. **问候/闲聊由 LLM 自然回应，不引用 Wiki（`navigation.method=chitchat`）**；非校园话题仅做「服务范围」说明，不做开放域闲聊，也不编造政策。

## 十四、免责声明与安全提醒

- 本项目为**教学演示 / MVP**，不是学校的官方信息系统。Wiki 示例词条内容为虚构的示例政策，时间、金额、电话、地点均不可作为实际办事依据；请以学校官方通知、学生手册与负责部门答复为准。
- AI 编译与回答可能存在遗漏或偏差，涉及奖学金、学籍、毕业、贷款等关键事项，请务必核对引用来源并与辅导员或职能部门核实。
- 涉及心理危机、人身安全、紧急医疗时，请优先联系辅导员、校保卫处、心理中心，或拨打 **110 / 120**；不要依赖 AI 作答。
- 部署到公网前请务必设置 `NPU_ADMIN_TOKEN`、使用 HTTPS 反向代理、限制上传大小与来源 IP，并遵守《个人信息保护法》与学校数据管理规定；**上传真实文件与调用第三方 LLM 前请完成数据合规评估**。
- **安全默认**：CORS 默认关闭（按需 `NPU_CORS_ORIGINS` 白名单）；对外监听（非 `127.0.0.1`）未设管理令牌时服务拒绝启动；历史会话、设置、导入、词条写操作在配置令牌后均需 `X-Admin-Token`；默认下发 CSP / `X-Frame-Options: DENY` / `X-Content-Type-Options: nosniff` 等响应头。
- **机器人安全**：通道接口不返回 token 明文（脱敏 + `has_access_token`），空 token 的 OneBot webhook 与使用公开默认 token `npu-assistant` 的公众号回调一律 403；微信校验签名与 ±300 秒时间窗；`self_id` 不一致事件忽略；`NPU_BOT_ALLOW_INSECURE=1` 仅限本机调试。
- **内容安全**：URL 收录默认拒绝私网/回环地址（SSRF 防护）、仅 http(s) 与 HTML/纯文本、响应上限 8MB；Wiki 词条正文渲染前转义并对链接协议白名单清洗，外链带 `rel="noopener noreferrer"`。
- 接入 QQ / 微信公众号需遵守对应平台协议；公众号仅实现明文模式，生产环境建议改为加密模式并做消息验签加固。

## 十五、许可

本项目仅用于教学与内部演示。如需在真实院系试点，请先完成数据合规评估与内容审核流程。

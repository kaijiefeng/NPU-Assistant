# NPU-Assistant · 校园辅导员数字分身

> 面向高校学生工作场景的本地可运行 AI 助手：把学生手册、奖助政策、办事指南变成可检索、可引用、可对话的知识库，并通过 Web 与微信 / QQ 机器人多渠道服务学生。

NPU-Assistant 是一个**开箱即用、零 API Key 也能演示**的校园问答系统。它把「辅导员数字分身问答、LLM Wiki 知识库、LLM 接入与模型管理、微信 / QQ 机器人」四件事做成了一个单体应用：后端 FastAPI + SQLite，前端纯静态 HTML/CSS/ES Module，无 Node 构建、无 CDN 依赖，离线即可运行。

> ⚠️ 本项目为教学演示 / MVP 原型。示例知识库中的时间、金额、电话、地点均为**示例数据**，实际政策以学校官方发布为准。

---

## 一、核心功能

| # | 能力 | 说明 |
|---|------|------|
| 1 | **辅导员数字分身问答** | Web 对话页，基于知识库的 RAG 回答，句末 `[1][2]` 标注引用来源，侧边可展开引用原文片段；支持多轮会话与历史会话 |
| 2 | **LLM Wiki 知识库** | 上传 `md / txt / pdf / docx / xlsx / csv / json / html` → 解析 → 按标题切片（300–600 字，约 60 字重叠）→ BM25（jieba 分词）索引 → 检索 → 问答引用真实文件内容；支持一键导入示例资料 |
| 3 | **LLM 接入与模型管理** | 三类 Provider：OpenAI 兼容（DeepSeek / 通义 / Moonshot 等）、Ollama 本地模型、内置演示模型（mock，无需 Key）；模型增删改查、设默认、启停、连通性测试；API Key 服务端脱敏 |
| 4 | **微信 / QQ 机器人** | QQ 走 NapCat / OneBot 11（反向 WebSocket + 反向 HTTP 事件 + HTTP API 回复）；微信公众号明文模式回调（XML 回复）；控制台调试通道无需任何外部账号即可体验完整机器人链路 |

**非目标（MVP 不做）**：多租户与权限体系、流式输出、向量数据库 / 在线 Embedding、语音、复杂插件系统、公众号加密模式。

## 二、界面预览

> 以下截图由 `.\scripts\ui_check.py`（Chrome DevTools Protocol，需要本机安装 Chrome/Edge 且服务已启动）自动生成，保存在 `docs/screenshots/`。

**辅导员对话**：知识库 RAG 回答 + 句末 `[n]` 引用 + 可展开的引用来源卡片。

![辅导员对话](docs/screenshots/chat.png)

**知识库**：多格式上传、文档状态与切片统计、检索测试。

![知识库](docs/screenshots/knowledge.png)

**模型管理**：Provider 卡片（Key 脱敏）、模型列表与默认模型高亮。

![模型管理](docs/screenshots/models.png)

**机器人**：QQ / 微信 / 控制台三通道配置、Webhook 地址、控制台模拟对话与消息日志。

![机器人](docs/screenshots/bots.png)

**设置**：分身人设、系统提示词、欢迎语、检索参数与引用开关。

![设置](docs/screenshots/settings.png)

## 三、5 分钟快速开始

### 3.1 环境要求

- Windows 10 / 11（脚本为 PowerShell；macOS / Linux 可手动执行等价命令）
- Python 3.11+
- 无需 Node.js、无需数据库服务、无需联网即可运行演示模型

### 3.2 安装与启动

```powershell
# 1) 进入项目根目录
cd D:\Development\NPU-Assistant

# 2) 创建并激活虚拟环境（仓库已自带 .venv 可跳过创建）
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

1. 首次启动会自动创建 `backend/data/app.db`、写入默认演示模型与控制台机器人通道，并自动导入 `backend/data/seed_docs/` 下的示例知识库；
2. 在「对话」页点击快捷问题（如 *国家奖学金怎么申请？*），即可看到带引用标注的回答；
3. 没有配置任何 API Key 时，回答由「内置演示模型 + 抽取式降级」生成（`fallback=true`），依然会返回真实检索到的知识库片段，可完整体验 RAG 链路。

> 端口被占用时：`.\scripts\run.ps1 -Port 8010`，然后打开 <http://127.0.0.1:8010>。

## 四、5 分钟演示脚本

完整分步脚本与预期结果见 **[docs/DEMO.md](docs/DEMO.md)**，速览：

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 打开首页 → 对话页，输入「国家奖学金怎么申请？需要哪些材料？」 | 返回分点回答，句末带 `[1][2]`，下方引用卡片显示《国家奖学金与奖助学金评审办法（示例）》原文片段 |
| 2 | 知识库页 → 上传一份自己的 `.md` / `.pdf` | 文档状态变为「就绪」，切片数 > 0；在检索测试框输入关键词可命中 |
| 3 | 知识库页 → 「导入示例资料」 | 10 篇示例文档导入完成（幂等，重复点击跳过已存在文件） |
| 4 | 模型页 → 新增 OpenAI 兼容 Provider（填 base_url 与 Key）→ 测试连接 → 添加模型 → 设为默认 | `/api/health` 的 `default_model` 变为新模型；问答 `fallback=false` |
| 5 | 机器人页 → 控制台通道「模拟对话」 | 返回与 Web 一致的回复与引用，消息日志出现记录 |
| 6 | 机器人页 → 配置 QQ / 微信通道 | 复制 Webhook 地址，按 [docs/BOT-INTEGRATION.md](docs/BOT-INTEGRATION.md) 接入 NapCat 或公众号 |

## 五、目录结构

```text
NPU-Assistant/
├─ backend/
│  ├─ app/
│  │  ├─ main.py            # FastAPI 应用装配、静态前端托管、异常处理
│  │  ├─ config.py          # 路径 / 端口 / 上传限制 / 默认设置
│  │  ├─ db.py              # SQLite 薄封装、建表、首启默认数据
│  │  ├─ security.py        # 可选管理员令牌（NPU_ADMIN_TOKEN）
│  │  ├─ routers/           # system / chat / knowledge / models / bots 五组 API
│  │  ├─ services/          # counselor 问答编排、llm、ingest、parsing、
│  │  │                     # retrieval / retrieval_core（BM25）、rag、bot_pipeline
│  │  └─ adapters/          # onebot（QQ）、wechat_mp（公众号）、base
│  ├─ data/
│  │  ├─ seed_docs/         # 示例知识库（10 篇中文 Markdown，首次启动自动导入）
│  │  └─ uploads/           # 上传文件落盘目录
│  ├─ tests/                # pytest 测试（conftest 使用临时数据目录）
│  └─ requirements.txt
├─ frontend/
│  ├─ index.html            # 单页应用（hash 路由 5 个视图）
│  └─ assets/               # styles.css + 原生 ES Module（api.js / app.js / ui.js）
├─ scripts/
│  ├─ run.ps1               # 启动开发服务器
│  ├─ check.ps1             # 运行 pytest 全量测试
│  ├─ bootstrap.ps1         # 一键建环境 + 装依赖 + 启动
│  ├─ smoke.ps1             # 启动后接口冒烟检查
│  ├─ screenshot.ps1        # Edge 无头模式五页截图（备用）
│  └─ ui_check.py           # Chrome CDP 交互冒烟 + 五页截图（推荐）
├─ docs/
│  ├─ CONTRACT.md           # 开发契约（接口事实源）
│  ├─ API.md                # 接口速览与调用示例
│  ├─ ARCHITECTURE.md       # 架构、请求链路、表结构、部署建议
│  ├─ BOT-INTEGRATION.md    # NapCat / OneBot 11、公众号、控制台接入与排障
│  ├─ DEMO.md               # 5 分钟演示脚本与已验证结果
│  └─ screenshots/          # 五页界面截图（ui_check.py 自动生成）
└─ README.md
```

## 六、LLM 配置

系统内置「本地知识库问答（演示）」mock 模型，**不联网即可运行**。要获得更自然的大模型回答，任选一种方式：

### 方式 A：OpenAI 兼容接口（推荐）

在「模型」页点击「添加 Provider」，填写：

| 字段 | 说明 | 示例 |
|------|------|------|
| 名称 | 自定义 | `DeepSeek` |
| 类型 | `openai` | — |
| Base URL | 到 `/v1` 为止 | `https://api.deepseek.com/v1` |
| API Key | 服务端保存，接口返回脱敏 | `sk-****` |
| 模型名 | 与 Provider 对应 | `deepseek-chat` |

然后「添加模型」→ 选择该 Provider、填写模型名 → 「设为默认」。保存后在「测试连接」中应返回 `ok=true`。兼容 OpenAI 协议的服务（DeepSeek、通义千问、Moonshot、OpenAI、自建 vLLM 等）均可接入。

### 方式 B：Ollama 本地模型

1. 本机安装 [Ollama](https://ollama.com/) 并拉取模型：`ollama pull qwen2.5:7b`；
2. 确认 `http://127.0.0.1:11434` 可访问；
3. 模型页添加 Provider：类型 `ollama`，Base URL `http://127.0.0.1:11434`，无需 API Key；
4. 添加模型（模型名与 `ollama list` 中一致）并设为默认。

### 方式 C：内置演示模型

保持默认即可。它不调用外部网络，由 `counselor.py` 使用检索结果做抽取式回答，适合离线演示与单元测试。

> AI 回答失败或超时时会自动降级为抽取式回答（响应中 `fallback=true`），不会返回 500。

### 运行时环境变量（可选）

复制 `.env.example` 为 `.env` 参考注释，或在启动前注入环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `NPU_HOST` / `NPU_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `NPU_DATA_DIR` | `backend/data` | 数据目录（数据库、上传文件） |
| `NPU_DB_PATH` | `backend/data/app.db` | SQLite 数据库路径 |
| `NPU_MAX_UPLOAD_MB` | `20` | 单文件上传上限 |
| `NPU_AUTO_SEED` | `1` | 首启是否自动导入示例知识库 |
| `NPU_ADMIN_TOKEN` | 空 | 设置后写操作需 `X-Admin-Token` 请求头 |

## 七、微信 / QQ 机器人接入

- **QQ（NapCat / OneBot 11）**：NapCat 侧配置「HTTP 服务器」（默认 `127.0.0.1:3000`，Token 与通道 `access_token` 一致，供后端发送回复）与「WebSocket 客户端」（反向 WS，URL 为 `ws://127.0.0.1:8000/api/bots/onebot/{通道ID}/ws`，Token 字段填通道 `access_token`）；也可选用「HTTP 客户端」做反向 HTTP 事件。群聊默认仅 @ 机器人时回复。
- **微信公众号（明文模式）**：需要公网可达的 URL（可用 ngrok / cpolar 内网穿透），在公众号后台「开发 → 基本配置」填写回调 URL `https://<公网域名>/api/bots/wechat/{通道ID}` 与 Token（与通道 config 一致），完成签名校验后即可收发 XML 文本消息。
- **控制台调试通道**：无需任何外部账号，在「机器人」页直接模拟学生消息，走完整 counselor → RAG → 回复链路。

完整步骤、配置示例与故障排查见 **[docs/BOT-INTEGRATION.md](docs/BOT-INTEGRATION.md)**。

## 八、运行测试

```powershell
# 全量测试（pytest，工作目录 backend）
.\scripts\check.ps1

# 或手动执行
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q
```

当前 **57 个用例全部通过**（约 20s），覆盖：检索核心（BM25 部分匹配 / 标题加权 / 空输入 / 排序 / 英文混排 / 确定性 / 性能）、知识库导入 / 检索 / CRUD、对话（mock 降级与引用、多轮、无命中兜底）、模型管理（脱敏 / 默认模型切换与回退）、机器人（公众号签名与 XML、OneBot 归一化 / HMAC 签名 / 去重 / 群聊 @ 门控、控制台模拟）。测试使用 `tempfile` 临时数据目录（`NPU_DATA_DIR`），**不会污染** `backend/data/app.db`。

服务启动后还可运行真实 HTTP 冒烟与前端交互检查：

```powershell
.\scripts\smoke.ps1                                          # 9 项 API 冒烟（需服务已启动）
.\.venv\Scripts\python.exe scripts\ui_check.py               # Chrome CDP 交互冒烟 + 五页截图
```

接口调试：启动后访问 Swagger UI <http://127.0.0.1:8000/api/docs>，或 `GET /api/health` 检查服务状态；常用 curl / PowerShell 示例见 [docs/API.md](docs/API.md)。

## 九、常见问题

**Q1：启动报错「未找到虚拟环境」？**
先执行 `python -m venv .venv`，再 `.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt`。

**Q2：8000 端口被占用？**
用 `.\scripts\run.ps1 -Port 8010` 换端口；不要同时启动多个常驻服务。

**Q3：页面打不开或提示「前端资源尚未就绪」？**
确认 `frontend/index.html` 存在；前端由后端直接托管，无需另起服务，也不要直接双击 `index.html`（API 请求需要同源后端）。

**Q4：回答总是 `fallback=true` / 没有引用？**
说明当前默认模型是内置演示模型或 LLM 调用失败。到「模型」页检查默认模型、Base URL 与 Key，并点「测试连接」；同时确认知识库已导入文档（`/api/knowledge/seed`）。

**Q5：想重新体验首次启动流程？**
停止服务后删除 `backend/data/app.db`（可选清空 `backend/data/uploads/`），重新启动即自动重建并导入示例文档。

**Q6：中文乱码？**
项目文件统一 UTF-8。PowerShell 使用 `chcp 65001` 或保持 `$env:PYTHONUTF8=1`（脚本已设置）。

## 十、免责声明与安全提醒

- 本项目为**教学演示 / MVP**，不是学校的官方信息系统。知识库示例内容为虚构的示例政策，时间、金额、电话、地点均不可作为实际办事依据；请以学校官方通知、学生手册与负责部门答复为准。
- AI 生成内容可能存在遗漏或偏差，涉及奖学金、学籍、毕业、贷款等关键事项，请务必与辅导员或职能部门核实。
- 涉及心理危机、人身安全、紧急医疗时，请优先联系辅导员、校保卫处、心理中心，或拨打 **110 / 120**；不要依赖 AI 作答。
- 部署到公网前请务必设置 `NPU_ADMIN_TOKEN`、使用 HTTPS 反向代理、限制上传大小与来源 IP，并遵守《个人信息保护法》与学校数据管理规定；不要把真实学生隐私数据上传到第三方 LLM。
- 接入 QQ / 微信公众号需遵守对应平台协议；公众号仅实现明文模式，生产环境建议改为加密模式并做消息验签加固。

## 十一、许可

本项目仅用于教学与内部演示。如需在真实院系试点，请先完成数据合规评估与内容审核流程。

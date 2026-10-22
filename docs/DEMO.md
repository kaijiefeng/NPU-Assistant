# NPU-Assistant 5 分钟演示脚本

> 目标：在 5 分钟内完整展示「辅导员数字分身 → 知识库 RAG → 引用来源 → 模型切换 → 机器人多渠道」四类核心能力。
> 演示环境：Windows + 已安装依赖的 `.venv`，默认地址 <http://127.0.0.1:8000>。
> 本文所有示例问题的回答内容来自 `backend/data/seed_docs/` 示例知识库，其中的时间、金额、电话均为示例。
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
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/stats
```

预期：`health.status=ok`，`default_model` 为「本地知识库问答（演示）」；`knowledge/stats.documents ≥ 6`（首次启动自动导入示例文档）。

## 1. 对话与引用（约 90 秒）— 核心亮点

**操作**：进入「对话」页，点击快捷问题 **「国家奖学金怎么申请？需要哪些材料？」**（或手动输入后按 Enter）。

**预期**：

- 数秒内出现回答，按「条件 → 材料 → 流程 → 咨询」分点组织；
- 句末带 `[1]`、`[2]` 等引用编号，编号与下方引用卡片一一对应；
- 引用卡片显示文档标题《国家奖学金与奖助学金评审办法（示例）》、章节名（如「申请材料清单」）与命中原文片段，可展开查看；
- 回答尾部带免责提示「请以学校最新官方通知为准」；
- 未配置真实 LLM 时，接口响应中 `fallback=true`（演示模型 + 抽取式降级），但引用是**真实检索结果**。

**补充演示多轮**：继续追问「那同一学年可以同时拿国家奖学金和国家励志奖学金吗？」——预期基于上文与知识库回答「不可兼得」，体现多轮上下文。

**兜底演示**（可选）：输入「学校有没有马术社团？」——预期回答「知识库暂未收录」并给出咨询渠道（辅导员 / 学院学工办），不编造信息。

## 2. 知识库上传与检索（约 60 秒）

**操作**：

1. 进入「知识库」页，展示文档列表（10 篇示例文档）、统计卡片（文档数 / 切片数 / 字符数 / 文件类型分布）；
2. 点击「导入示例资料」——预期提示已存在则跳过（幂等），不会重复导入；
3. 拖拽上传一个本地 `.md` 或 `.txt` 文件（可现场新建一个「测试通知.md」，内容写两行课程调整通知）；
4. 在「检索测试」输入框输入该文件中的关键词，如「课程调整」；
5. 点击文档行查看详情抽屉，展示切片预览与切片总数。

**预期**：

- 上传后文档状态从「处理中」变为「就绪」，切片数 > 0，大小与时间正确；
- 检索测试返回命中的切片，显示文档标题、章节、相关度分数与片段；
- 详情抽屉能看到前 5 条切片内容。

## 3. 模型接入与切换（约 60 秒）

**操作（有可用 API Key 时）**：

1. 进入「模型」页，点击「添加 Provider」，类型选 `openai`，填写名称、Base URL（如 `https://api.deepseek.com/v1`）、API Key；
2. 点击「测试连接」——预期返回 `ok=true` 与可用模型列表（网络异常时返回 `ok=false` 的可读信息，不报 500）；
3. 添加模型（模型名如 `deepseek-chat`），点击「设为默认」；
4. 回到对话页提问，预期 `fallback=false`，回答更自然，引用保持不变；
5. 刷新 `/api/health`，`default_model` 已变为新模型。

**无 API Key 时的替代演示**：

- 展示 Provider 卡片中的 Key 脱敏（`sk-1***abcd` 形式）与「内置演示模型（无需 API Key）」；
- 添加一个 `ollama` 类型 Provider（`http://127.0.0.1:11434`）演示「本地大模型」配置项，说明未启动 Ollama 时测试连接会返回可读失败信息而非崩溃。

> 演示结束建议把默认模型切回「本地知识库问答（演示）」，保证离线状态下也可复现。

## 4. 机器人多渠道（约 60 秒）

**控制台通道（必演，无需外部账号）**：

1. 进入「机器人」页，找到「控制台调试通道」，在「模拟对话」输入「心理中心怎么预约？」；
2. 预期返回与 Web 一致的回复与引用，`outbound` 区域展示将要发送的报文；
3. 下方「消息日志」出现该条记录（渠道 `console`）。

**QQ / 微信公众号（可选，视现场环境）**：

- 展示 QQ 通道卡片上的反向 WebSocket 地址 `ws://127.0.0.1:8000/api/bots/onebot/{id}/ws?access_token=...` 与 HTTP API 地址，说明 NapCat 配置方式；
- 展示微信通道的回调地址与签名校验说明（`sha1(sort(token,timestamp,nonce))`）；
- 若已提前配置好，现场用手机 QQ / 微信发一条消息，展示真机回复；
- 完整配置步骤见 [BOT-INTEGRATION.md](BOT-INTEGRATION.md)。

## 5. 设置与收尾（约 30 秒）

1. 进入「设置」页，修改分身名称为「小南 2 号」或补充系统提示词，保存后回到对话页，欢迎语立即生效；
2. 展示「检索 top_k / 相关性阈值 / 引用开关 / 回复尾注」等可调参数，说明同一问答内核可按院系定制；
3. 打开 <http://127.0.0.1:8000/api/docs> 展示 Swagger 接口文档；
4. 打开 <http://127.0.0.1:8000/api/system/stats> 展示文档 / 切片 / 消息 / 会话计数，说明数据全部落在本地 SQLite。

## 6. 验收对照表

| 验收项 | 操作位置 | 通过标准 |
|--------|----------|----------|
| 冷启动健康检查 | `GET /api/health` | `status=ok`，返回默认模型 |
| 示例知识库导入 | 知识库页「导入示例资料」/ `POST /api/knowledge/seed` | 导入 ≥ 6 篇，重复执行跳过 |
| 上传解析 | 知识库页上传 md/txt/pdf/docx | `status=ready`，`chunk_count>0` |
| 检索命中 | `POST /api/knowledge/search` | 返回相关切片，含标题 / 章节 / 分数 |
| 带引用问答 | 对话页 | `citations` 非空，编号与 `[n]` 一致 |
| 降级可用 | 无 Key / 断网 | `fallback=true`，仍然给出引用回答，无 500 |
| 模型管理 | 模型页 | 增删改查、脱敏、设默认、测试连接可用 |
| 机器人模拟 | 机器人页控制台通道 | 返回完整回复与 `outbound` 报文 |
| 页面可用 | 5 个视图 | 无控制台报错，移动端窄屏布局正常 |

## 7. 演示应急方案

| 突发情况 | 处理 |
|----------|------|
| 8000 端口被占用 | `.\scripts\run.ps1 -Port 8010`，改用 <http://127.0.0.1:8010> |
| 数据库状态被之前演示改乱 | 停止服务 → 删除 `backend/data/app.db` → 重新启动（自动重建并导入示例文档） |
| 没有网络 / 没有 API Key | 使用默认 mock 模型，强调「离线 RAG + 引用」是产品设计目标，`fallback=true` 是预期行为 |
| 真实模型响应慢 | 提前在设置页把 `retrieval_top_k` 调到 3，或换低延迟模型；演示时先展示已缓存的历史会话 |
| 页面白屏 | 确认是通过 `http://127.0.0.1:8000/` 访问（不要双击 `index.html`），检查后端是否已启动 |
| 演示机中文乱码 | `chcp 65001`，或直接使用浏览器界面（不受终端编码影响） |

## 8. 已验证（阶段二端到端验收实测）

> 实测日期：**2026-10-22**；环境：Windows 11 + Python 3.11 + 仓库 `.venv`；服务：`.\scripts\run.ps1`（uvicorn，<http://127.0.0.1:8000>）；浏览器：Chrome（`scripts/ui_check.py` CDP 截图）。
> 验收时使用临时数据目录跑自动化测试；真实服务验收产生的文档 / 会话 / 通道已在结束后清理，数据库恢复为 10 篇示例文档的演示状态。

### 8.1 自动化测试

| 验证项 | 命令 | 实测结果 |
|--------|------|----------|
| pytest 全量 | `.\scripts\check.ps1` | **57 passed**，1 warning（`StarletteDeprecationWarning`，依赖库提示，无影响），耗时约 20s |
| 用例分布 | — | system 6 / knowledge 8 / chat 6 / models 5 / bots 16 / retrieval_core 16（公众号签名与 XML、OneBot 归一化 / X-Signature / message_id 去重 / 群聊 @ 门控、console 全链路；BM25 部分匹配 / 标题加权 / 空输入 / top_k / 英文混排 / 确定性 / 300 切片性能） |
| 测试隔离 | conftest | 导入 app 前设置临时 `NPU_DATA_DIR` / `NPU_DB_PATH`，每用例重置数据库与上传目录；不污染 `backend/data/app.db` |

### 8.2 真实服务端到端（uvicorn 8000，40 项检查全部通过）

| 验证项 | 实测结果 |
|--------|----------|
| 健康检查 / 前端资源 | `/api/health` ok；`/` 与 `styles.css`、`app.js`、各视图 JS 均 200；SPA 路由回退 200；未知 `/api` 路径 404 |
| 首启自动 seed | 10 篇文档 / 78 切片（重复调用 `POST /knowledge/seed`：ingested=0，skipped=10，幂等） |
| 多格式上传 | md（2 切片）、txt（1）、pdf（1）、docx（1）均 201 + `ready` + `chunk_count>0` |
| 检索 | 4 个唯一标记词（md/txt/pdf/docx）全部命中对应文档 |
| 对话（mock 模型） | `fallback=true`，返回 3 条 citations，回答中 `[1][2][3]` 与引用编号一致，延迟约 126ms |
| 多轮会话 | 第二轮沿用同一 `conversation_id`，历史接口返回 4 条消息（user/assistant 交替） |
| 无命中兜底 | 返回「知识库暂未收录」+ 咨询渠道，`citations=[]` |
| 模型管理 | mock Provider 测试 ok；新增 OpenAI Provider Key 脱敏为 `sk-e****abcd`；新增模型设为默认后 `/api/health` 同步；删除默认模型自动回退到 mock |
| 控制台机器人 | 返回回复 + 3 条引用 + `outbound` 报文，消息日志可见 |
| 微信公众号 | 签名校验返回 echostr；文本消息回复 `Content-Type: text/xml; charset=utf-8` 且 `ToUserName` 互换；非文本消息回引导语；错误签名 403 |
| OneBot 11 | 反向 HTTP 事件返回 `{"status":"ok"}` 并落库；错误 token 403；私聊会话键 `onebot11:10001:private:10002` |
| 设置 | `PUT /api/settings` 局部更新生效，验收后恢复默认 |
| 机器人冒烟脚本 | `.\scripts\smoke.ps1`：**9/9 PASS**（health / stats / seed / search / chat 3 条引用 / models / channels / console test / 前端首页） |
| 前端交互冒烟 + 截图 | `scripts/ui_check.py`：5 个页面 0 console 错误，截图见 `docs/screenshots/` |

### 8.3 演示数据基线

- 文档：10 篇中文 Markdown（`backend/data/seed_docs/`），78 个切片，约 1.4 万字符；
- 模型：内置演示模型「本地知识库问答（演示）」为默认，无需 API Key；
- 机器人：控制台调试通道（console）默认可用，QQ / 微信通道按需创建；
- 设置：全部为默认值（分身名「小南」、`retrieval_top_k=4`、`retrieval_min_score=0.05`、引用开启）。

## 9. 已知限制与注意事项（不影响 MVP 验收）

1. **`fallback=true` 是默认演示模式的预期行为**：内置 mock 模型 + 抽取式回答；配置真实 OpenAI 兼容 / Ollama 模型后 `fallback=false`。
2. **Web 会话无用户体系**：`session_key` 由前端生成为 `web:anonymous-*`，历史会话全局可见；多租户 / 登录鉴权是契约明确的非目标。
3. **上传接口支持 `title` / `tags`，但当前前端上传 UI 未暴露自定义入口**（按文件名自动作为标题）；可直接调用 API 传入。
4. **微信公众号仅明文模式**，且问答超过约 4.5s 会先回兜底提示（MVP 无异步客服消息推送，重发即可）；加密模式为非目标。
5. **公众号 / OneBot 消息去重是进程内内存去重**（最近 512 条），服务重启后失效；事件缺少 `message_id` 时仍可能重复，建议只启用一种上报方式。
6. **SQLite 单进程设计**：多 worker 无收益且可能重复占用内存，部署保持单实例（见 `docs/ARCHITECTURE.md` §7）。
7. **检索为 BM25 词法检索**（jieba 分词 + 标题加权），不含语义向量；同义词 / 长句泛化能力有限，属于 MVP 取舍。检索核心已有 16 个单元测试覆盖（`backend/tests/test_retrieval_core.py`：部分匹配、标题加权、空输入、top_k / 排序、英文混排、确定性、300 切片性能）。
8. 依赖库提示 `httpx` + `starlette.testclient` 的 `StarletteDeprecationWarning` 仅影响测试告警，不影响功能。

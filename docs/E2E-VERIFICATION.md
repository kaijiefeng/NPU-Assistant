# E2E 交互验收（computer use + 沙箱隔离）

用真实浏览器输入事件（鼠标/键盘/滚动/触摸）驱动 SPA，按场景断言界面、提示、网络与后端状态；**默认在一次性沙箱内自举服务**，不接触生产数据。

## 1. 一条命令

```powershell
.\scripts\e2e.ps1                       # 全量沙箱运行（headless）
.\scripts\e2e.ps1 -Headed               # 显示浏览器，观察人类化操作
.\scripts\e2e.ps1 -Scenario "knowledge.*" -Keep
.\scripts\e2e.ps1 -SelfTest             # 引擎自检（不启浏览器）
```

等价 Python 入口：`python scripts/e2e_verify.py [--scenario ...] [--tag ...] [--headed] [--keep] [--junit PATH] [--min-coverage N] [--ci]`。

产物：`.artifacts/e2e/<run>/`（`report.json` / `report.html` / `junit.xml` / `coverage.json` / `sandbox.json` / `screenshots/` / 失败时 `dom/` + `console.log`）。全量约 1 分钟。

## 2. 沙箱隔离（与生产环境隔离）

每次运行创建 `%TEMP%\npu-e2e-<随机>`：

| 隔离项 | 做法 |
|---|---|
| 数据目录 | `NPU_DATA_DIR` / `NPU_DB_PATH` 指向沙箱；启动时若发现目录位于仓库 `backend/data` 之下直接拒绝 |
| 端口 | `socket.bind(127.0.0.1:0)` 随机端口，**从不占用 8000** |
| 浏览器 | 一次性 `--user-data-dir`，空 localStorage，不注入生产令牌；禁用缓存并注销 Service Worker |
| 模型 | 本地 mock LLM（随机端口，仅 127.0.0.1），聊天返回占位回答、编译提示词返回可被 `wiki_builder.normalize_entry` 接受的 JSON 词条 |
| 网络 | 不访问外网；URL 收录场景只指向引擎自起的本地静态页（沙箱进程内开启 `NPU_URL_IMPORT_ALLOW_PRIVATE`） |
| 生产指纹 | 运行前后校验 `backend/data/app.db`、`uploads/`、`admin_token.txt` 的哈希/清单，变化即 FAIL |
| 进程 | uvicorn / mock LLM / Chrome 均为子进程，`finally` 统一回收；失败自动保留现场，可 `--keep` 复现 |

## 3. 人类化操作

- 点击：滚动到可视区 → 元素框内随机点 → 3–6 段带抖动 `mouseMoved` → `mousePressed/Released`（不用 JS `.click()`）。
- 输入：ASCII 逐字符键事件；中文以 2–4 字 `insertText` 分片（模拟 IME），间隔 50–140ms。
- 等待：DOM 文本/URL 静默 ≥350ms，再断言；固定 seed 可复现。
- 视口：默认 1440×960；`mobile.layout` 场景切 390×844 + touch 模拟。
- 门禁：默认任何 console error 或 5xx 请求判失败。

## 4. 场景清单（25 个）

| 场景 | 覆盖 |
|---|---|
| `bootstrap.routes` | 四路由、侧栏切换、旧路由 `#/manage` 重定向、未知路由回落 |
| `chat.answer_and_citation` | 输入问题、流式回答、引用行出现、展开来源 |
| `chat.interactions` | 快捷问题、点赞反馈、历史会话开关、新建会话 |
| `knowledge.browse` | 搜索、结果列表、打开词条抽屉、Esc 关闭 |
| `knowledge.manage_upload` | 收录资料页、选择文件（真实 `DOM.setFileInputFiles`）、后台任务、文档入库 |
| `knowledge.scan_and_delete` | 扫描文件夹、任务结果、源文件刷新、删除确认框、文档数减少 |
| `ops.tabs` | 服务台/任务/审计切换、审计刷新按钮 |
| `settings.assistant_and_models` | 设置页、模型标签、沙箱模型展示 |
| `auth.login_logout` | 登录、会话建立、退出回登录页（account 档） |
| `token.settings_redaction` | 匿名设置白名单、带令牌完整设置（token 档） |
| `mobile.layout` | 390×844 四页无横向滚动、侧栏开合 |
| `sandbox.isolation` | 数据目录在沙箱、端口非 8000、生产指纹未变 |
| `knowledge.more` | 快速收录展开/收起、文本收录、词条列表刷新、标签筛选、导入快照、文档详情、重编译 |
| `chat.more` | 点踩+原因+提交、历史会话打开/删除、转人工弹窗 |
| `models.crud` | Provider/模型增改测、设默认、停用、刷新、删除（含确认框） |
| `team.users` | 成员新增、取消弹窗、角色变更、停用、重置密码（account 档） |
| `knowledge.extra` | 编辑词条、链接收录、下载源文件、全部重建、清空上传队列、复制收录链接、关闭构建提示 |
| `chat.extra` | 引用定位、发送失败重试/关闭错误、危机提示确认 |
| `bots.console` | 公众号通道增改、启停、刷新、控制台模拟对话、日志、清空管理员绑定、删除通道 |
| `settings.quick` | 快捷问题增删、手机扫码弹窗、令牌显示/保存/清除/测试 |
| `settings.account` | 账号页、退出登录、匿名历史「去登录」 |
| `jobs.actions` | 失败任务重试、排队任务取消、告警确认/解决 |
| `insights.tabs` | 服务台标签切换与刷新 |
| `insights.actions` | 工单认领/复制/回复/解决、危机状态、缺口/反馈处理 |
| `errors.retry` | 词条详情加载失败重试、设置加载失败重试 |


覆盖度量：`coverage.json` 静态盘点各 view 的 `data-action`（当前 114 个），按场景 `covers` 统计，且只计入未失败场景。**当前动作覆盖率 91.2%（104/114）**，缺口按 view 列在 `coverage.json`；截图证据与逐场景覆盖表见 [E2E-COVERAGE.md](E2E-COVERAGE.md)（由 `--publish` 生成，截图在 `docs/screenshots/e2e/`）。

## 5. 确定性模型

`scripts/e2e/mock_llm.py` 实现 OpenAI 兼容接口：
- 编译提示词（含 `source_quote`/`body_md` 等）→ 返回词条 JSON，正文与逐字摘录取自用户消息中的真实段落；
- 其它请求 → 可读占位回答；
- 仅监听 `127.0.0.1`，随沙箱回收。

聊天引用不依赖模型文本，由后端检索生成，因此 mock 也能验证双层引用链路。

## 6. CI

`.github/workflows/e2e.yml`：ubuntu + setup-chrome，`python scripts/e2e_verify.py --headless --ci --junit .artifacts/e2e/junit.xml`，上传产物；触发 push（develop/master/release）、PR（frontend/backend/e2e 路径）与手动。CI runner 本身是一次性环境，不挂载生产数据。

## 7. 已知边界

- v1 只支持**自举沙箱**，不支持连接已有实例（避免误连生产）。
- 未做视觉像素基线；截图用于人工核对与失败取证。
- 编译链路使用 mock JSON，验证的是状态流转与 UI，不是真实模型质量。
- 文件上传通过 `DOM.setFileInputFiles`（浏览器限制无法模拟系统文件选择器），其余交互均为真实输入事件。

## 8. 截图证据

```powershell
.\scripts\e2e.ps1 -Publish
```

运行后把每一步截图复制到 `docs/screenshots/e2e/<场景>/`，并生成 [E2E-COVERAGE.md](E2E-COVERAGE.md)：场景状态、覆盖动作、可点击截图链接。

## 9. 引擎单测

```powershell
.\.venv\Scripts\python.exe -m pytest scripts\e2e\tests -q
```

覆盖：随机端口、生产指纹字段、沙箱创建/清理、动作盘点、覆盖率结构、报告与 JUnit 输出。

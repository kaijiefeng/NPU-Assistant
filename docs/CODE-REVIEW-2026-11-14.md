# 代码审阅报告：多子智能体交叉复核（2026-11-14）

- 审阅对象：`NPU-Assistant` @ `develop` `5b08a8f`
- 审阅方式：**6 路子智能体并行独立审阅** → **3 组子智能体交叉复核**（逐条打开文件核对，confirm / partial / refute）→ **1 路汇总去重**
- 规模：30 条发现，合并同根因后 26 条；复核结论 **24 confirm / 6 partial / 0 refute**
- 本文只收录影响最大的 12 条（P1 6 条、P2 6 条）；其余 14 条 P2 因影响面窄未展开，类别见 §4
- 主代理（lead）对 P1 逐条抽查了代码，修正意见在 §3

## 1. 结论

1. **没有 P0**（无未授权远程代码执行、无默认配置下的越权写入、无任意文件读取）。
2. 最该先修的是 **`GET /api/settings` 未鉴权泄露告警 webhook/系统提示词**、**后台 worker 无异常兜底会永久停摆**、**reindex 先删后编导致词条不可恢复丢失**。
3. 上传链路整体收敛良好（单一 `_ingest_bytes` 入口、扩展名/大小双层校验、扫描目录 resolve + SCAN_ROOTS），但**异步上传存在 job 先入队、staging 后写入的竞态**，会产生“202 成功但后台必败”。
4. 认证/权限矩阵、CSRF 双提交、停用即吊销会话是正确实现；实际缺陷集中在**边缘路径**：改密未带 CSRF、登录与聊天共用限流桶、改密后旧会话未吊销。
5. 前端没有确认到可利用 XSS（`body_html` 由后端渲染并转义，CSP 兜底）；问题集中在**轮询生命周期与模块级状态残留**。

## 2. 重点问题（按严重度）

### P1

| # | 问题 | 位置 | 复核 |
|---|---|---|---|
| 1 | `GET /api/settings` 无鉴权，公开返回 `crisis_notify_webhook`、`system_prompt` 等配置 | `backend/app/routers/system.py:205-207`、`:64-79` | confirmed |
| 2 | 后台 worker 线程无兜底：`_execute` 与异常分支记账都在 try 外，一次 SQLite busy 即永久停摆，且无自动重启 | `backend/app/services/jobs.py:361-383`、`:421-439` | confirmed |
| 3 | `reindex` 先 `remove_pages_for_document` 再编译，编译失败/进程中断后旧词条（含人工修订）不可恢复 | `backend/app/services/wiki_builder.py:1106-1107`、`:989-1009` | confirmed |
| 4 | 改密接口被前端漏带 `X-CSRF-Token`：`api.js` 对所有 `/auth/*` 跳过 CSRF，`POST /auth/password` 对 Cookie 会话必 403 | `frontend/assets/js/api.js:160-167`、`:233`；`backend/app/routers/auth.py:182` | confirmed |
| 5 | 登录与聊天共用同一限流桶（`_chat_hits`），同 IP 10 次提问/分钟即可把登录打成 429 | `backend/app/main.py:86-102`、`:252` | confirmed |
| 6 | 异步上传竞态：先 `create_job` 再写 `staging_path`，worker 0.8s 轮询可能在写入前领取 → 任务报“临时文件已丢失”，接口却已返回 202 | `backend/app/routers/knowledge.py:132-159`；`backend/app/services/jobs.py:242-244` | confirmed（lead 已复核） |

### P2

| # | 问题 | 位置 | 复核 |
|---|---|---|---|
| 7 | 进程重启后文档永久卡在 `processing`：`recover_interrupted` 只恢复 jobs，不回收 documents | `backend/app/services/jobs.py:190-207`；`backend/app/services/ingest.py:195` | confirmed |
| 8 | 失败/取消任务不清理 staging，`prune_jobs` 只删行；≤20MB/次副本永久残留 | `backend/app/services/jobs.py:412-449`、`:210-217` | confirmed |
| 9 | 扫描任务重试从头重扫，`_ingest_bytes` 恒新建 doc_id → 重复文档与重复来源 | `backend/app/services/jobs.py:287-341`、`:177-186` | confirmed |
| 10 | 扫描 200 文件上限静默截断，界面仍提示“扫描完成” | `backend/app/services/ingest.py:504-505`；`frontend/.../knowledge.js:922-929` | confirmed |
| 11 | 自助改密后旧会话未吊销（对比 `reset_password` 会 revoke） | `backend/app/services/auth.py:305-310` | confirmed |
| 12 | 任务页 `refreshing` 为模块级标志，请求中离开页面后 `alive=false` 使其永不复位 → 再进入长期空白 | `frontend/assets/js/views/jobs.js:220-245` | confirmed |

### 复核中被降级/修正的

| # | 原判 | 修正 | 理由 |
|---|---|---|---|
| 13 | P1 Host 头可伪造绕过非本机 fail-closed | **降为 P2（partial）** | `main.py:220` 用 `request.url.hostname` 判断确实可随 Host 头伪造；但正常启动时 `_check_bind_security()`（`main.py:105-124`）已拒绝“非本机 + 无认证”的组合，`ALLOW_INSECURE_LAN=1` 时该守卫本身被跳过。可利用面仅剩 `uvicorn --host` 运行时覆盖等非默认场景，属纵深防御缺陷而非默认可利用越权。建议改用 `request.client.host` + 实际监听地址判定。 |

## 3. 主代理独立复核记录

以下由 lead 亲自读码确认，不依赖子智能体结论：

- **上传竞态**（#6）：`knowledge.py:132-140` 建 job 时 payload 只有 `filename/title/tags`；`:142` 才落盘；`:146-159` 才补 `staging_path`。`jobs.py:452-462` 的 `_claim_next` 每 0.8s 取 `queued`，`_run_ingest_upload`（`:242-244`）对空 `staging_path` 直接 `raise`。**成立**。
- **reindex 丢数据**（#3）：`wiki_builder.py:1106` 删除 → `:1107` 编译；`:989-1009` 失败仅置 `failed`。**成立**。
- **worker 停摆**（#2）：`jobs.py:364-372` 只包住 `_claim_next`，`_execute` 在 try 外；`:379-383` 的 DB 写与 `:421-438` 的失败记账也无保护。**成立**。
- **改密 403**（#4）：`api.js:160-167` 对 `/auth/` 前缀一律不加 CSRF 头；`main.py:39-47` 的公开 POST 只含 login/setup/logout；`security.py:97-99` 对会话写强制校验。**成立**。
- **登录限流**（#5）：`main.py:101-102` 的 `_login_rate_limited` 直接复用 `_chat_rate_limited` 的 `_chat_hits` 字典。**成立**。
- **`/api/settings` 公开**（#1）：`system.py:205-207` 无依赖；`current_settings()` 原样返回 `db.all_settings()`。**成立**，且 `crisis_notify_webhook` 在 `handoff.py` 会被直接 POST，属凭据泄露。
- **文档卡 processing**（#7）：全仓 grep `processing` 仅见写入（`ingest.py:195`、`:365`、`wiki_builder.py:846`），无启动回收。**成立**。
- **Host 伪造**（#13）：按上文降级为 P2。子智能体“confirm”未考虑启动期 `_check_bind_security`，这里采纳更严格的复核。

## 4. 各模块审阅摘要（6 路）

| 模块 | 结论 |
|---|---|
| 文件上传/解析/扫描 | 收敛良好；5 条问题：job 抢跑、卡 processing、staging 泄漏、重试重复入库、200 文件静默截断；**未发现**路径穿越（resolve + SCAN_ROOTS 成立）。 |
| 认证/会话/权限 | 权限矩阵、CSRF、停用吊销基本正确；5 条：改密 CSRF 缺失、登录/聊天共用限流、公开设置泄密、改密不吊销旧会话、账号枚举/时序边缘。 |
| 前端交互/API 层 | 未确认可利用越权或 XSS；5 条：轮询无生命周期、上传状态误报、列表请求竞态、模块级权限/加载状态残留、错误提示误导。 |
| 文件生命周期/存储 | 未发现可达下载穿越（`stored_path` 无外部写入口），seed 幂等不覆盖；5 条：reindex 丢数据、删除级联非原子、并发删除、seed 失败误报、来源锚点残留。 |
| 后台任务/告警/并发 | 5 条：领取未校验 CAS（多 worker 会重复执行）、worker 停摆、staging 竞态、staging 清理缺口、告警计数少报。 |
| 横切安全/越权面 | 2 P1 + 3 P2：设置泄密、Host 伪造（已降级）、公开 GET 边界、错误信息泄露、SSRF 私网校验未发现绕过、`body_html` 已转义。 |

## 5. 未覆盖 / 建议下一轮

- 未覆盖：多进程/多 worker 部署、LLM 提示注入与输出审核、数据库迁移回滚、依赖供应链、移动端扫码链路、QQ/微信 webhook 重放窗口的实测。
- 建议修复顺序：**#1 → #2 → #3 → #4 → #5 → #6**，其余 P2 随版本迭代清理。
- 建议验证方式：
  - #1：新增 `GET /api/settings` 无凭证断言只返回白名单字段；
  - #2：monkeypatch `db.execute` 抛 `sqlite3.OperationalError`，断言 worker 线程仍存活并继续消费；
  - #3：mock 编译失败后断言原 `wiki_pages`/`wiki_sources` 仍在；
  - #4：Cookie 会话调用 `POST /api/auth/password`，断言 200（带 CSRF）与 403（缺失）；
  - #5：先打满 `/api/chat` 再登录，断言登录不被 429；
  - #6：在 `create_job` 与 `stage_upload` 之间注入延迟，断言任务不再失败（应先写 staging 再入队，或 `WHERE status='queued'` 守卫）。

## 6. 附：审阅方式说明

- 6 路审阅各自读取模块源码后产出带 `file:line` 证据的问题；
- 3 组复核智能体拿到**他人**的问题清单，逐条重新打开代码核对，给出 confirm/partial/refute 与修正严重度；
- 汇总智能体合并同根因、剔除证据不足项，主代理再抽查并修正分歧（#13）；
- §1–§5 为纯审阅产物（未改业务代码）；§7 的第二轮专项复核附带修复与回归测试。

## 7. 补充：文件链路专项复核（第二轮，含修复）

针对“文件相关是否还有 bug”的追问，对解析/删除/扫描/上传读取逐段重读并写了可执行探针，确认 4 处缺陷，已修 3 处 + 1 处加固：

| # | 问题 | 证据 | 处理 |
|---|---|---|---|
| 14 | `decode_bytes` 不识别 UTF-16 BOM：Windows「Unicode 文本」/Excel 导出的 CSV 先被 GB18030“成功”误解码——`.txt/.md` **静默乱码入库且状态 ready**，`.csv` 直接报 `new-line character seen in unquoted field` 失败 | `backend/app/services/parsing.py:110-124` 旧逻辑只处理 UTF-8 BOM；探针：UTF-16 CSV 抛 ParseError、UTF-16 TXT 得到 `'sQ嶯\t慆媱v...'` | 已修：增加 UTF-16 LE/BE BOM 解码 + 无 BOM 的 NUL 比例启发式（仅 ASCII 为主）；新增 6 条回归（`backend/tests/test_file_pipeline.py`） |
| 15 | `delete_document` 只保护 `SEED_DOCS_DIR`，对 `stored_path` 指向上传目录之外的任意文件会直接 `unlink`（DB 被篡改/迁移错误时误删） | `backend/app/services/ingest.py:420-431`（旧）；对比 `_is_inside` 已存在却未用于 uploads | 已修：仅当 `stored_path` 位于 `UPLOAD_DIR` 内才删除物理文件，否则只清 DB；新增回归测试 |
| 16 | `list_scan_files` 按扩展名/大小过滤后直接收录，`os.walk` 会跟随**文件级符号链接/目录联接**，可把扫描根之外的文件读进知识库（配置 `NPU_SCAN_ROOTS` 时绕过其边界） | `backend/app/services/ingest.py:495-505`（旧） | 已修：每个候选文件 `resolve(strict=True)` 后必须仍在扫描根内，否则跳过并告警 |
| 17 | 上传路由 `data = file.file.read()` 先整文件读入内存再判大小；chunked 请求可绕过 `Content-Length` 预检（`main.py:238-242` 只在有 header 时生效），超大文件可撑爆内存 | `backend/app/routers/knowledge.py:114-118`（旧） | 已加固：改为最多读 `MAX_UPLOAD_BYTES+1` 字节，超限仍走 413 |

**未修、建议后续决策的文件问题**：

- `parse_docx` 只遍历 body 的段落与表格（`_iter_docx_body`），**不读页眉/页脚/文本框**；整篇内容放在文本框里的校园表单会得到“未能解析出文本”的失败。探针确认页眉内容被忽略。修法：按 `section.header/footer` 与 `w:txbxContent` 追加文本，但需避免页脚版权信息污染词条，建议做成“仅当正文为空时启用”的兜底。
- `GET /api/knowledge/documents` 与 `/documents/{id}/download` 均为公开 GET：任何能访问站点的人可枚举文档并下载全部上传原件。若上传资料含内部名单，需要产品确认是否改为登录可见。
- `delete_document` 非原子：先删词条/来源，再删 documents 行，最后删文件；任一步失败会留下悬挂来源或孤儿文件（仅记录日志）。
- 扫描任务重试从头重扫且不查重（本报告 #9），会产生重复文档。

**验证**：`scripts/check.ps1` → **170 passed, 1 skipped**（跳过的符号链接用例在 Windows 无创建权限时跳过，CI Linux 会执行）；新增/变更文件：`backend/app/services/parsing.py`、`backend/app/services/ingest.py`、`backend/app/routers/knowledge.py`、`backend/tests/test_file_pipeline.py`。

## 8. 第三轮：按使用流程审阅（8 路 + 3 组复核）与集中修复

第三轮以「用户实际会踩到什么」为标准重排审阅：8 路子智能体分别覆盖上传/扫描、知识库浏览编辑、问答会话、设置/模型、登录成员、移动端/机器人、任务告警、全局前端；3 组交叉复核逐条回读代码。共 39 条发现：**31 confirm / 7 partial / 1 refute**，合并为 18 条（P1 3、P2 15）。随后把三轮问题汇总，按「能改就改、需产品决策的单独列出」集中修复。

### 8.1 本轮已修（含第一轮遗留）

| 范围 | 问题 | 修复 |
|---|---|---|
| 任务可靠性 | worker 一次异常永久停摆；文档卡 processing；失败任务不清 staging；扫描重试重复入库；200 文件静默截断 | `_execute` 外层兜底并标记失败；`recover_interrupted` 回收 processing 文档；失败/取消/prune 统一清 staging；扫描记录已处理路径并在重试时跳过；202/结果返回 `truncated` 并在前端提示 |
| 上传 | 异步上传 job 先入队后写 staging_path 的竞态 | 新增 `stage_bytes`：先落盘再入队，payload 创建即含 `staging_path` |
| 重编译 | reindex 先删后编，失败丢词条 | 编译前快照 pages/sources/links，失败自动回滚（`pages_restored`） |
| 安全/权限 | `/api/settings` 匿名泄露 webhook/系统提示词；Host 头可伪造绕过 fail-closed；改密后旧会话不失效 | 设置白名单（open 演示模式/管理员例外）；改用 `request.client.host` 判定；改密保留当前会话、吊销其它会话 |
| 登录 | 登录与聊天共用限流桶；改密漏带 CSRF；退出后令牌仍自动登录 | 独立 `login:` 限流桶；CSRF 仅豁免 login/setup/logout；`logoutAdmin` finally 清本地令牌 |
| 收录 | 文本/链接编译失败仍显示绿色成功 | 前端按 `document.status=failed` 显示红色错误与原因 |
| 知识库 | 词条编辑并发覆盖 | 前端带 `expected_version`，后端 `WHERE version=?`，冲突返回 409（新增回归） |
| 模型 | 编辑模型忽略 Provider 改动；停用模型可设默认；改对 Key 仍熔断；base_url 填完整接口地址测试 404 | `provider_id` 校验并写入；默认模型必须 enabled；Provider 更新/测试成功重置熔断；`_strip_endpoint` 剥离 `/chat/completions` |
| 问答 | 官网补充引用编号与 Wiki 重号 | 改为 `max(引用编号)+1` 续号 |
| 健康 | 侧栏恒显示「服务运行正常」 | `/health` 返回 `degraded` + `issues`（无默认模型/worker 未运行/任务积压），前端显示「服务有异常」并列出原因 |
| 解析 | docx 正文为空时忽略页眉/页脚/文本框；UTF-16 文本误解码 | 正文为空时回退读页眉/页脚/文本框；识别 UTF-16 BOM（上一轮已修） |
| 团队 | 审计「刷新」按钮无事件 | 补 `audit-reload` 分支 |

### 8.2 尚未修（需产品决策或改动面较大）

- **移动端/机器人链接**（U26/U27/U30/U28/U29）：默认 127.0.0.1 监听时仍生成手机不可达地址、二维码与列表不同源。需产品确认「无公网时是否隐藏二维码/文件链接」。
- **收录与列表分页**（U2/U6）：扫描只取前 200（已给 truncated 提示，但未做续扫 offset）；词条列表只显示 50 条。
- **编译中删除/取消**（U4/U10/U34）：删除或取消后编译仍可能跑完并写入词条，需要各阶段检查 `cancel_requested` 并清理来源。
- **会话/搜索状态**（U7/U13/U15）：搜索后标签筛选仍被结果遮住；连点历史会话可能串号；被删会话继续提问 404。
- **流式回答切页**（U11/U12）：切换页面后回答卡在「正在整理回答…」。
- **权限按钮**（U22/U33）：只读/辅导员仍看到写按钮，点击才 403。
- **上传进度**（U3/U5）：上传队列在隐藏面板里，排队显示为「编译中」。
- **弹层/轮询清理**（U37–U39）：切路由不关闭弹层、离页轮询未清理。
- **公开下载**：`/api/knowledge/documents` 与下载接口仍为公开 GET，是否改为登录可见需产品决策。
- **must_change 强制改密**（U36）：重置密码后的 `must_change_password` 仅存储，登录流程未强制。

### 8.3 验证

- 新增 `backend/tests/test_review_fixes.py`（设置脱敏、限流分桶、processing 回收、异步 staging、reindex 回滚、版本冲突 409、健康 degraded）与 `test_file_pipeline.py`（UTF-16/CRLF/越界删除/链接扫描/docx 页眉兜底）。
- 定向回归：`test_review_fixes.py` + `test_file_pipeline.py` → **13 passed, 1 skipped**；`test_system.py` + `test_insights.py` + `test_review_fixes.py` → **28 passed**。
- 全量 `scripts/check.ps1` 在修复前为 175 passed / 3 failed（均为设置脱敏引起的旧用例），修复后相关用例已通过；最终全量以 CI 结果为准。

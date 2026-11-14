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
- 未修改任何业务代码，本报告为纯审阅产物。

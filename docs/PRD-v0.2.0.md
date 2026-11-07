# NPU-Assistant PRD v0.2.0 · 服务闭环与内容治理

> 版本：v0.2.0 草案  
> 日期：2026-11-05  
> 范围：Top 5 需求详细设计 + Top 20 优先级排期  
> 关联文档：`docs/CONTRACT.md`、`docs/WIKI-DESIGN.md`、`docs/ARCHITECTURE.md`、`docs/MOBILE-OPS.md`

---

## 0. 摘要

v0.1.7 已经完成「学生问答闭环 + 单人内容运维」：学生能提问、拿到带双层引用的回答；辅导员能上传资料、编译词条、维护 Wiki；QQ / 微信 / Web 多入口复用同一链路。

但真实上生产还缺两件事：

1. **服务闭环**：学生答不好时没有反馈、没有转人工、没有危机升级；辅导员不知道学生到底卡在哪里。
2. **内容治理**：没有账号/角色/审计、没有时效性、没有发布审核、没有异步任务与告警，靠一个共享令牌和同步接口撑不住多人协作。

v0.2.0 的主题是 **「让回答可反馈、问题可转交、内容可治理、操作可追溯」**，交付 Top 5：

| # | 需求 | 一句话目标 |
|---|------|------------|
| 1 | 反馈与知识缺口闭环 | 每条回答可评价；答不好的问题自动进入缺口池，辅导员能补资料并关闭 |
| 2 | 转人工与危机升级 | 学生能一键找辅导员；危机信号立即升级、通知值班人、留痕 |
| 3 | 账号、角色与审计 | 从单一共享令牌升级为账号/角色/权限/审计日志 |
| 4 | 时效性与当前时间 | 答案和引用显示来源日期/有效期；过期内容不误导；「今年」按真实日期回答 |
| 5 | 异步编译、流式回答与告警 | 上传不再阻塞；回答逐字输出；失败可重试、可告警 |

---

## 1. 背景与问题

### 1.1 现状

- 问答链路：`counselor.answer()` → Wiki 导航 → LLM/抽取式降级 → 双层引用。
- 知识链路：上传/URL/文本 → 解析 → LLM 编译 → 词条 + 来源锚点 → 浏览/编辑/重建。
- 渠道：Web/PWA、QQ 官方、微信公众号、控制台。
- 运维：单管理员令牌、同步编译、无任务队列、无反馈表、无审计表。

### 1.2 真实用户问题

| 角色 | 当前卡点 |
|------|----------|
| 学生 | 答错了只能自己猜；找不到人问；不知道答案是不是过期；「今年」问题可能答错年份 |
| 辅导员 | 不知道哪些问题没答好；无法把「学生问倒」转成补资料任务；多人共用一个令牌 |
| 学院管理员 | 无法分权、无法追责、无法看学院维度的数据 |
| 值班/心理老师 | 危机信号没有产品化升级，依赖学生主动打电话 |

### 1.3 机会

把「问答工具」升级为「辅导员服务台」：学生问 → 反馈/转人工 → 缺口池 → 补资料 → 时效校验 → 回答变好。这个飞轮是产品真正的壁垒。

---

## 2. 目标与非目标

### 2.1 业务目标（建议基线，上线后用真实数据校准）

| 指标 | 目标 |
|------|------|
| 学生一次解决率（无负反馈） | ≥ 70% |
| 回答反馈覆盖率 | ≥ 15% |
| 知识缺口 7 日关闭率 | ≥ 50% |
| 危机信号升级率 | 100%（规则命中 + 人工确认） |
| 危机升级平均时延 | ≤ 1 分钟 |
| 转人工首次响应 P90（工作时间） | ≤ 30 分钟 |
| 管理写操作审计覆盖率 | 100% |
| 带日期的词条占比 | ≥ 80% |
| 过期内容参与问答比例 | < 2% |
| 上传接口 P95 返回 | ≤ 2 秒（异步模式） |
| 流式首字 P95 | ≤ 2.5 秒（模型依赖） |
| 编译任务 P95 | ≤ 3 分钟 / 1 万字 |

### 2.2 用户目标

- 学生：2 次点击内完成评价/转人工；危机场景 1 秒内看到求助卡片。
- 辅导员：打开服务台就能看到「今天最该补的 10 个问题」；每条 AI 回答可追溯、可修正。
- 管理员：能创建账号、分配角色、查操作日志，不再共享令牌。
- 值班老师：危机事件有明确的通知、认领、处理、关闭流程。

### 2.3 非目标（v0.2.0 不做）

- embedding / 向量库 / rerank（维持 LLM Wiki 路线）。
- 完整多租户、SSO、企业微信/钉钉/飞书原生集成。
- 电话外呼、自动排班、复杂多级工单流转。
- 教务/学工系统数据集成（课表、成绩、请假、奖助进度）。
- 全渠道富媒体主动推送（v0.2.0 只做 Web 内通知 + 可配置 Webhook）。
- 学生端跨设备历史（依赖账号体系，放到 v0.3.0）。

---

## 3. 用户与核心场景

| 角色 | 核心场景 |
|------|----------|
| 学生（匿名/登录） | 提问 → 看引用 → 点「没解决」→ 转人工 → 收到回复 |
| 辅导员 | 打开服务台 → 看待处理/缺口 → 补资料/关联词条 → 关闭 |
| 学院管理员 | 管理本学院成员与内容 → 看学院数据 → 审计 |
| 系统管理员 | 账号/角色/模型/通道/备份/告警 |
| 值班老师 | 收到危机通知 → 认领 → 联系学生 → 记录处理结果 |
| 教师/内容负责人 | 提交本业务域资料 → 校对词条 → 发布（v0.3.0 起） |

---

## 4. 总体方案

### 4.1 新增能力地图

```text
学生端
  ├─ 回答反馈（👍/👎 + 原因 + 备注）
  ├─ 转人工（工单号 + 预计响应时间 + 状态）
  └─ 危机卡片（热线 + 值班通知 + 一键转人工）
        │
        ▼
服务台（新视图，管理员/辅导员可见）
  ├─ 概览：一次解决率、缺口、转人工、危机
  ├─ 未解决问题：聚类、样本、补资料、关联词条、关闭
  ├─ 反馈：负反馈列表、原因分布、处理状态
  ├─ 待人工：认领/分配/回复/解决
  └─ 危机：事件列表、认领、处理记录
        │
        ▼
内容治理
  ├─ 账号/角色/权限/审计
  ├─ 时效性字段 + 过期提醒 + 当前日期注入
  └─ 异步任务队列 + 重试 + 告警
```

### 4.2 架构原则

- **不引入新依赖**：密码哈希用 `hashlib.pbkdf2_hmac`；任务队列用 `threading` + `queue.Queue`；SSE 用 FastAPI `StreamingResponse`。
- **增量迁移**：只加表/加列，不破坏现有 DB；旧 `X-Admin-Token` 继续可用。
- **权限服务端强制**：前端隐藏只是体验，所有写接口必须服务端校验。
- **敏感数据最小化**：危机/转人工记录仅管理员可见；列表页默认脱敏。
- **失败可恢复**：任务可重试、可取消；流式中断不落半条消息；告警可确认。

---

## 5. 需求 1：反馈与知识缺口闭环

### 5.1 目标

让每一条 AI 回答都可评价，让每一次「没答好」都变成可跟进的内容任务。

### 5.2 用户故事

- 作为学生，我可以在回答下方点 👍/👎，并选择「没解决 / 不完整 / 已过期 / 引用不对」，可选填备注。
- 作为学生，我评价后能看到「已记录，辅导员会看到」，而不是石沉大海。
- 作为辅导员，我能在服务台看到「最近 7 天被问得最多但答不好的问题」，并一键去补资料。
- 作为辅导员，我能把缺口关联到某个词条/源文件，标记「已解决」，并看到关闭率。
- 作为管理员，我能看到负反馈列表、原因分布和趋势。

### 5.3 功能清单

1. 对话页每条 assistant 消息下方增加反馈条：
   - 👍「有帮助」；👎「没帮助」。
   - 👎 展开原因 chips：`没找到 / 回答不完整 / 内容已过期 / 引用不对 / 其他`。
   - 可选备注（≤200 字）。
   - 提交后按钮变为已选状态；同一浏览器可修改。
2. 反馈写入 `answer_feedback`，并按规则同步/异步进入 `knowledge_gaps`。
3. 服务台「未解决问题」：
   - 按问题聚类，展示出现次数、渠道、首次/最近时间、样本问题、关联词条/文件、负责人。
   - 操作：`去补资料`（跳转上传/URL 收录并预填标题）、`关联词条`、`标记已解决`、`忽略`、`备注`。
4. 服务台「反馈」：
   - 列表：评分、原因、问题、回答摘要、引用、渠道、时间、处理状态。
   - 操作：标记处理中/已解决/忽略；跳转原会话。
5. 服务台「概览」：
   - KPI：回答数、反馈数、有帮助率、未解决数、缺口新增/关闭、转人工、危机。
   - Top 10 缺口、7/30 天趋势（纯 CSS 柱状图，不引图表库）。
6. 缺口自动生成规则（见 5.7）。
7. 历史数据回填：首次上线时从 `messages` 扫描最近 90 天，生成初始缺口池。

### 5.4 交互

**学生端**

```text
[回答正文 ...]
[1] 国家奖学金申请   [2] 奖学金评审办法
────────────────────────────
这条回答有帮助吗？  👍 有帮助   👎 没帮助
（点 👎 后）
哪里不满意？ [没找到] [不完整] [已过期] [引用不对] [其他]
备注（选填）________________________  [提交]
```

**服务台**

```text
概览 | 未解决问题(12) | 反馈(8) | 待人工(3) | 危机(0)

未解决问题
┌──────────────────────────────────────────────┐
│ 研究生国家奖学金什么时候截止？  12 次 · 3 渠道 │
│ 最近：11-04 20:12 · 来源：web/qq · 未关联      │
│ [去补资料] [关联词条] [已解决] [忽略]          │
└──────────────────────────────────────────────┘
```

### 5.5 数据模型

```sql
-- 回答反馈
CREATE TABLE IF NOT EXISTS answer_feedback (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'web',
    rating          INTEGER NOT NULL,              -- 1=有帮助, -1=没帮助
    reason_code     TEXT NOT NULL DEFAULT '',      -- not_found|incomplete|expired|bad_citation|other
    comment         TEXT NOT NULL DEFAULT '',
    voter_key       TEXT NOT NULL DEFAULT '',      -- 浏览器随机标识，防重复
    status          TEXT NOT NULL DEFAULT 'open',  -- open|triaged|resolved|ignored
    handled_by      TEXT,
    handled_at      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(message_id, voter_key)
);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON answer_feedback(status, created_at);

-- 知识缺口
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id                TEXT PRIMARY KEY,
    question          TEXT NOT NULL,
    normalized_key    TEXT NOT NULL,
    sample_message_id TEXT,
    channel           TEXT NOT NULL DEFAULT 'web',
    count             INTEGER NOT NULL DEFAULT 1,
    reasons           TEXT NOT NULL DEFAULT '[]',  -- JSON: ["no_hit","incomplete","negative"]
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open',-- open|linked|resolved|ignored
    priority          INTEGER NOT NULL DEFAULT 0,  -- 0 普通, 1 高（多次/多渠/负反馈）
    linked_page_id    TEXT,
    linked_document_id TEXT,
    assignee_id       TEXT,
    note              TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gaps_status ON knowledge_gaps(status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_gaps_key ON knowledge_gaps(normalized_key);

-- messages 增补：把本次回答的检索状态持久化，供缺口/看板使用
ALTER TABLE messages ADD COLUMN retrieval TEXT NOT NULL DEFAULT '{}';
ALTER TABLE messages ADD COLUMN fallback INTEGER NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN navigation_method TEXT NOT NULL DEFAULT '';
```

> 不新增 `feedback_rating` 冗余列；列表查询用 `LEFT JOIN answer_feedback` 取最新一条。

### 5.6 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/chat/feedback` | 公开（限流） | 提交/更新反馈；body `{message_id,rating,reason_code,comment,voter_key}` |
| GET | `/api/chat/feedback/{message_id}` | 公开 | 查询当前 `voter_key` 的反馈状态（可选） |
| GET | `/api/insights/gaps` | 服务台读 | 筛选 `status/days/channel/q/limit/offset` |
| GET | `/api/insights/gaps/{id}` | 服务台读 | 缺口详情 + 样本消息 + 关联内容 |
| POST | `/api/insights/gaps/{id}/link` | 服务台写 | `{page_id?,document_id?,note?}` → `status=linked` |
| POST | `/api/insights/gaps/{id}/resolve` | 服务台写 | `{note}` → `status=resolved` |
| POST | `/api/insights/gaps/{id}/ignore` | 服务台写 | `{note}` → `status=ignored` |
| GET | `/api/insights/feedback` | 服务台读 | 反馈列表 |
| POST | `/api/insights/feedback/{id}/status` | 服务台写 | `{status,note?}` |
| GET | `/api/insights/overview` | 服务台读 | `days=7/30` KPI + top gaps + daily |
| POST | `/api/insights/recompute` | 超级管理员 | 重建缺口池（回填/修复） |

响应约定：错误统一 `{"detail":"中文"}`；列表返回 `{items,total}`；时间 ISO-8601。

### 5.7 后端逻辑

1. **反馈写入**
   - 校验 `message_id` 存在且 `role='assistant'`；`conversation_id` 必须匹配。
   - `rating` 只允许 `1/-1`；`reason_code` 在枚举内；`comment` ≤200 字。
   - `UNIQUE(message_id,voter_key)`：重复提交走 `UPSERT` 更新评分/原因。
   - 公开接口限流：复用聊天限流或单独 20 次/分/IP。

2. **缺口生成**
   - 触发条件（满足任一）：
     - `retrieval.wiki_used=false`（无命中）；
     - `retrieval.complete=false`（回答不完整）；
     - `fallback=true`（降级回答）；
     - 负反馈 `rating=-1`；
     - `retrieval.remote_used=true`（远程兜底，说明 Wiki 缺口）。
   - 聚类：用 `text_utils.tokenize()` 取词，去停用词/标点，生成 token 集合；与最近 30 天 `open/linked` 缺口比较 Jaccard ≥ 0.6 且至少 2 个实词重叠 → 合并，`count+1`，`last_seen_at` 更新；否则新建。
   - `normalized_key` = 排序后的 token 前 12 个，便于索引。
   - `priority=1` 条件：`count>=5` 或渠道数 ≥2 或存在负反馈。
   - 同一 `message_id` 只计一次；反馈改为 👍 时不移除已生成缺口，但可把原因标记为 `resolved_feedback` 并在详情展示。

3. **回填**
   - `POST /api/insights/recompute` 扫描最近 90 天 assistant 消息，按上述规则重建；幂等：先清空 `status=open` 且 `sample_message_id` 指向的历史缺口，再重建。
   - 启动时若 `knowledge_gaps` 为空且 `messages` 非空，可异步执行一次轻量回填（不阻塞启动）。

4. **性能**
   - 缺口聚类在写反馈/问答结束后同步执行（单条，成本低）；回填走后台线程。
   - 列表分页；概览查询按 `created_at` 索引。

### 5.8 权限与安全

- 学生只能提交反馈，不能读取他人反馈。
- 服务台 API 仅 `feedback.read/write` 权限；v0.2.0 先用管理员令牌，v0.2.0 后期接入账号角色。
- 反馈备注可能含个人信息：列表默认截断，详情需 `feedback.read`；导出需 `audit` 记录。
- 防止刷反馈：按 IP + `voter_key` 限流；同一消息同一 voter 只能一条。

### 5.9 验收标准

- [ ] 学生在 Web 端 2 次点击内完成 👎 + 原因提交；刷新后状态保留。
- [ ] 同一浏览器重复提交会更新而非新增；不同浏览器可各自提交。
- [ ] `wiki_used=false` 的问答在 1 分钟内出现在缺口池（或回填后出现）。
- [ ] 缺口列表支持按状态/时间/渠道过滤，分页正确。
- [ ] 「去补资料」能跳转到内容管理并预填标题/问题描述。
- [ ] 关联词条后缺口状态变为 `linked`；解决后变为 `resolved`，概览关闭率更新。
- [ ] 未授权用户访问 `/api/insights/*` 返回 401/403。
- [ ] 现有问答接口响应字段不破坏；新增 `feedback` 不影响旧前端。
- [ ] pytest 覆盖：反馈校验、去重、聚类合并、缺口状态流转、权限。

### 5.10 风险

- 反馈率低 → 在回答末尾轻量提示，不强制；对负反馈用户二次提示转人工。
- 聚类误合并（「国奖」vs「国励」）→ Jaccard 阈值 + 保留样本；提供「拆分」操作（v0.3.0）。
- 缺口池膨胀 → 自动归档 90 天无新增且低优先级的缺口。

---

## 6. 需求 2：转人工与危机升级

### 6.1 目标

AI 不是终点：学生能一键找到人；危机信号必须立即升级、通知值班人、全程留痕。

### 6.2 用户故事

- 作为学生，我可以在回答下方点「联系辅导员」，选择原因、留联系方式、补充说明，拿到工单号和预计响应时间。
- 作为学生，我提交后能在页面上看到「待处理 / 已认领 / 已回复」。
- 作为辅导员，我在服务台看到待人工列表，能认领、查看完整对话、回复、标记解决。
- 作为值班老师，危机事件会以高优先级出现在列表，并通过 Webhook 通知我。
- 作为管理员，我能配置危机关键词、热线、通知 Webhook、SLA。

### 6.3 功能清单

1. 学生端「转人工」入口：
   - 位置：回答下方、输入框上方常驻「联系辅导员」。
   - 表单：原因（没解决/需要人工确认/涉及个人情况/投诉建议/其他）、联系方式（手机/微信/QQ，选填）、补充说明（≤500 字）。
   - 提交后返回 `ticket_no`、`ticket_token`、SLA 文案（如「工作时间 24 小时内回复」）。
   - 同一会话 24 小时内重复提交合并到同一工单。
2. 服务台「待人工」：
   - 列表：工单号、优先级、原因、渠道、学生标识、等待时长、状态、负责人。
   - 详情：完整对话、引用、反馈、联系方式（脱敏/展开）、处理记录。
   - 操作：认领、分配、回复（Web 会话内可见）、标记解决、关闭、备注。
   - 超 SLA 高亮 + 概览计数。
3. 危机识别与升级：
   - 规则库（可配置）：自杀/自伤、暴力、性侵、诈骗、急救、霸凌、走失。
   - 命中后：
     - 立即返回固定危机卡片（不等待 LLM）；卡片含：`先保证安全`、校内保卫处、心理中心、120/110、辅导员电话（设置项）。
     - 创建 `crisis_events` + 高优先级 `handoff_requests`（`source='crisis'`）。
     - 触发 Webhook 通知值班人（企业微信/钉钉/飞书/自定义 JSON，可配置）。
     - 在服务台顶部显示红色横幅，需认领后才消失。
   - 明确规则（如「我想自杀」）直接升级；模糊表达（如「活着没意思」）走规则 + 可选 LLM 复核，复核失败也升级（高召回优先）。
   - 危机回复不得由 LLM 自由发挥；模板固定，可追加一句安抚。
4. 通知：
   - v0.2.0 支持：Web 服务台站内提醒 + 通用 Webhook（POST JSON）。
   - Webhook payload 只含工单号、类别、时间、渠道、优先级，不含完整对话（保护隐私）；管理员凭工单号查看详情。
   - 失败重试 3 次，记录 `notification_log`（可复用 `audit_log` 或独立表）。
5. SLA：
   - 设置项：`handoff_sla_hours`（普通，默认 24）、`crisis_sla_minutes`（默认 5）。
   - 超时在列表标红；概览显示超时数。

### 6.4 交互

**学生端**

```text
[回答正文]
[联系辅导员]  [没帮助]
        ↓
联系辅导员
原因：[没解决▼]  联系方式：________（选填）
补充说明：________________________________
[提交]
        ↓
已提交，工单号 NPU-20261105-0012
工作时间 24 小时内回复；紧急情况请直接拨打 120/110 或联系辅导员。
```

**危机卡片**

```text
⚠️ 你现在的安全最重要
请立即联系：辅导员 138xxxx / 心理中心 029-xxxx / 保卫处 029-xxxx
紧急情况请拨打 120 / 110
[我已联系] [联系辅导员]
```

**服务台**

```text
待人工(3) | 危机(1)
🔴 危机 · 11-05 21:03 · web · 自杀/自伤 · 未认领  [认领]
```

### 6.5 数据模型

```sql
CREATE TABLE IF NOT EXISTS handoff_requests (
    id               TEXT PRIMARY KEY,
    ticket_no        TEXT NOT NULL UNIQUE,          -- NPU-YYYYMMDD-XXXX
    ticket_token     TEXT NOT NULL,                 -- 学生查询用随机串
    conversation_id  TEXT NOT NULL,
    message_id       TEXT,
    channel          TEXT NOT NULL DEFAULT 'web',
    session_key      TEXT NOT NULL DEFAULT '',
    user_label       TEXT,
    contact          TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL,                 -- not_solved|confirm|personal|complaint|other|crisis
    priority         TEXT NOT NULL DEFAULT 'normal',-- normal|urgent
    status           TEXT NOT NULL DEFAULT 'pending',-- pending|claimed|replied|resolved|closed
    summary          TEXT NOT NULL DEFAULT '',
    assignee_id      TEXT,
    resolution       TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT 'student',-- student|system|crisis
    created_at       TEXT NOT NULL,
    claimed_at       TEXT,
    first_response_at TEXT,
    resolved_at      TEXT,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoff_status ON handoff_requests(status, priority, created_at);

CREATE TABLE IF NOT EXISTS crisis_events (
    id               TEXT PRIMARY KEY,
    handoff_id       TEXT,
    conversation_id  TEXT NOT NULL,
    message_id       TEXT,
    category         TEXT NOT NULL,                 -- suicide|violence|sexual|fraud|medical|bullying|other
    matched_terms    TEXT NOT NULL DEFAULT '[]',
    severity         TEXT NOT NULL DEFAULT 'high',  -- high|medium
    status           TEXT NOT NULL DEFAULT 'open',  -- open|acknowledged|resolved|false_positive
    notified_at      TEXT,
    notify_result    TEXT NOT NULL DEFAULT '',
    handled_by       TEXT,
    handled_at       TEXT,
    note             TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crisis_status ON crisis_events(status, created_at);

CREATE TABLE IF NOT EXISTS notification_log (
    id           TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,                     -- webhook|email|inapp
    target       TEXT NOT NULL DEFAULT '',
    event_type   TEXT NOT NULL,                     -- crisis|handoff|alert
    resource_id  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending|sent|failed
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    sent_at      TEXT
);
```

### 6.6 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/handoff` | 公开（限流） | 学生创建工单 |
| GET | `/api/handoff/ticket/{ticket_no}?token=` | 公开（token） | 学生查询状态 |
| GET | `/api/handoff` | 服务台读 | 列表筛选 `status/priority/assignee/days` |
| GET | `/api/handoff/{id}` | 服务台读 | 详情 + 对话 + 危机事件 |
| POST | `/api/handoff/{id}/claim` | 服务台写 | 认领 |
| POST | `/api/handoff/{id}/assign` | 服务台写 | `{user_id}` |
| POST | `/api/handoff/{id}/reply` | 服务台写 | `{content}`，写入 Web 会话；机器人渠道按能力矩阵 |
| POST | `/api/handoff/{id}/resolve` | 服务台写 | `{resolution, link_page_id?, link_document_id?}` |
| GET | `/api/crisis/events` | 危机读 | 列表 |
| POST | `/api/crisis/events/{id}/ack` | 危机写 | 认领/确认 |
| POST | `/api/crisis/events/{id}/resolve` | 危机写 | 处理结果 + 误报标记 |
| GET/PUT | `/api/settings/handoff` | 设置写 | SLA、开关、Webhook、热线、关键词 |

### 6.7 后端逻辑

1. **工单创建**
   - 校验会话存在；`reason` 枚举；`contact` ≤120 字；`summary` ≤500 字。
   - 同一 `conversation_id` + `status in (pending,claimed,replied)` + 24 小时内 → 返回已有工单（幂等）。
   - `ticket_no` 格式 `NPU-YYYYMMDD-XXXX`（4 位当日序号）；并发生成用事务/唯一索引重试。
   - 学生查询只允许 `ticket_no + ticket_token`，不暴露对话内容。

2. **危机识别**
   - 在 `counselor.answer()` 之前执行规则匹配（避免 LLM 延迟）。
   - 规则结构：`{category, patterns:[regex], negative_patterns:[regex], severity}`；默认内置，设置项可追加。
   - 明确命中 → 直接危机流程；模糊命中 → 可调用轻量 LLM 分类（复用默认模型，`max_tokens` 小），超时/失败按命中处理。
   - 危机流程返回固定模板 + 创建工单/事件 + 通知；不落普通 assistant 回答，或落一条带 `crisis=true` 的消息。
   - 同一会话 10 分钟内多次命中合并到一个事件，避免刷屏。

3. **通知**
   - Webhook POST JSON：`{event, ticket_no, category, priority, channel, created_at, url}`；`url` 为服务台工单地址（需公网可达，未配置则省略）。
   - 超时 5 秒；失败重试 3 次（1/5/15 分钟）；结果写 `notification_log`。
   - Web 端服务台轮询（10 秒）即可，不必 WebSocket。

4. **回复**
   - Web：向 `conversation_id` 追加一条 `role='assistant'`、`model='辅导员'` 的消息；学生下次拉取会话可见（现有前端刷新/轮询）。
   - QQ 官方：复用开放平台被动回复（带 `msg_id`）。
   - 微信公众号：需客服消息接口/用户 48 小时内互动；v0.2.0 仅记录「请通过公众号再次提问」或标记「需线下联系」。
   - QQ 官方：被动回复有 `msg_id` 时效，主动推送需平台权限；v0.2.0 标记「渠道暂不支持主动回复」，由辅导员线下面谈/电话。

### 6.8 权限与安全

- 学生只能看到自己的工单状态（token）；不能列出他人。
- 服务台列表按角色过滤：辅导员看自己/未分配；学院管理员看本学院；超级管理员看全部。
- 联系方式默认脱敏（中间 4 位），点击「展开」写审计。
- 危机事件仅 `crisis.read`；导出/查看详情写审计。
- 危机内容不进入普通日志；日志只记录类别/工单号。

### 6.9 验收标准

- [ ] 学生 3 次点击内提交转人工，拿到工单号/SLA；重复提交不产生重复工单。
- [ ] 服务台可认领/分配/回复/解决；Web 学生端能看到回复。
- [ ] 危机关键词（含中英文/变体）命中后 1 秒内返回危机卡片，生成 urgent 工单和危机事件。
- [ ] 配置 Webhook 后收到通知；失败重试并记录。
- [ ] 危机事件必须认领后才能关闭；可标记误报。
- [ ] 非授权用户无法访问工单/危机列表；联系方式展开写审计。
- [ ] 现有 `counselor.answer()` 正常问答不受影响；危机分支有开关。
- [ ] pytest 覆盖：工单幂等、token 查询、危机规则、通知重试、权限。

### 6.10 风险

- 危机误报打扰值班人 → 负向词表（讲座/宣传/预防/论文）+ 复核 + 误报标记，持续调优。
- 危机漏报 → 高召回优先；明确表达必升级；定期用测试集回归。
- 值班人不在线 → SLA + 升级到备用联系人（v0.3.0）；v0.2.0 至少 Webhook 多目标。
- 法律与伦理 → 危机记录最小化、访问审计、保留期限；上线前请学校心理中心/法务评审。
- 渠道主动回复限制 → 明确能力矩阵，避免承诺「机器人一定回复」。

---

## 7. 需求 3：账号、角色与审计

### 7.1 目标

用账号/角色/权限替代单一共享令牌，让多人协作可分权、可追责、可审计。

### 7.2 用户故事

- 作为超级管理员，我可以创建辅导员/学院管理员/内容编辑/只读账号。
- 作为学院管理员，我只能管理本学院成员和内容范围。
- 作为辅导员，我能处理反馈/工单/危机，但不能改模型和全局设置。
- 作为审计员，我能按人/时间/动作查操作日志。
- 作为现有部署，我升级后旧 `X-Admin-Token` 仍然能用。

### 7.3 功能清单

1. 登录/登出/当前用户/修改密码。
2. 首次启动设置向导：无账号时，仅本机可创建第一个超级管理员（或沿用 `NPU_ADMIN_TOKEN`）。
3. 用户管理：列表、创建、编辑、禁用、重置密码、角色分配、学院范围。
4. 角色与权限：
   - `super_admin`：全部。
   - `college_admin`：本学院内容/成员/反馈/工单/危机，不能改模型/全局设置。
   - `counselor`：反馈/工单/危机处理，内容只读。
   - `editor`：内容读写/发布，不能处理工单/危机。
   - `viewer`：只读。
5. 审计日志：登录、设置、用户、内容、模型、通道、工单、危机、导出。
6. 前端：登录页、用户菜单、成员管理页、审计页；按权限隐藏导航。
7. 兼容：旧 `X-Admin-Token` 映射为 `super_admin`（脚本/CLI 用）。

### 7.4 数据模型

```sql
CREATE TABLE IF NOT EXISTS admin_users (
    id                  TEXT PRIMARY KEY,
    username            TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    password_hash       TEXT NOT NULL,             -- pbkdf2_sha256$iterations$salt$hash
    role                TEXT NOT NULL,             -- super_admin|college_admin|counselor|editor|viewer
    college             TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active', -- active|disabled
    must_change_password INTEGER NOT NULL DEFAULT 0,
    last_login_at       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    csrf_token   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip           TEXT NOT NULL DEFAULT '',
    user_agent   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON admin_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id            TEXT PRIMARY KEY,
    actor_id      TEXT,
    actor_name    TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL,                   -- login|settings.update|wiki.update|...
    resource_type TEXT NOT NULL DEFAULT '',
    resource_id   TEXT NOT NULL DEFAULT '',
    before_json   TEXT NOT NULL DEFAULT '{}',
    after_json    TEXT NOT NULL DEFAULT '{}',
    ip            TEXT NOT NULL DEFAULT '',
    user_agent    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, created_at);
```

### 7.5 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/auth/login` | 公开（限流） | `{username,password,remember}` → 设置 HttpOnly cookie + 返回用户/CSRF |
| POST | `/api/auth/logout` | 登录 | 删除会话 |
| GET | `/api/auth/me` | 登录 | 当前用户 + 权限 |
| POST | `/api/auth/password` | 登录 | 修改密码 |
| GET | `/api/admin/users` | `users.manage` | 列表 |
| POST | `/api/admin/users` | `users.manage` | 创建（初始密码一次性展示） |
| PUT | `/api/admin/users/{id}` | `users.manage` | 角色/学院/状态 |
| POST | `/api/admin/users/{id}/reset-password` | `users.manage` | 重置 |
| GET | `/api/admin/audit` | `audit.read` | 筛选 `actor/action/from/to/limit` |

### 7.6 认证与权限实现

- 登录成功后生成 32 字节随机 token，仅存 `sha256(token)`；`HttpOnly; SameSite=Lax; Secure`（HTTPS）cookie。
- 所有写请求校验 `Origin` + CSRF header（双提交）；GET 不要求 CSRF。
- 权限依赖：`require_permission("feedback.write")`，从 session 用户角色映射。
- 密码：`pbkdf2_hmac('sha256', password, salt, 260_000)`；常量时间比较。
- 登录限流：5 次/分/IP；连续 10 次失败锁定 15 分钟（内存 + 可持久化）。
- 旧令牌：中间件优先 cookie；无 cookie 时校验 `X-Admin-Token`，通过则视为 `super_admin`（审计 actor=`token`）。
- 迁移：首次启动若无 `admin_users`：
  - 若设置了 `NPU_ADMIN_TOKEN`，不强制建号，保持旧行为；
  - 若未设置，前端引导 `/setup`（仅本机）创建超级管理员；
  - 已有部署升级后，设置页显示「启用账号体系」向导。

### 7.7 前端

- 新增 `views/login.js`、`views/team.js`、`views/audit.js`。
- `api.js`：`credentials:'same-origin'`，自动附带 CSRF header；401 跳转登录。
- 导航按权限渲染；无权限路由显示 403 空态。
- 设置页移除「管理令牌」主入口，保留「兼容令牌」折叠项。

### 7.8 验收标准

- [ ] 首次启动可创建超级管理员；密码强度校验；初始密码只展示一次。
- [ ] 五种角色权限矩阵服务端生效；越权返回 403。
- [ ] 登录/登出/会话过期/记住我 7 天正常；CSRF 缺失的写请求被拒绝。
- [ ] 旧 `X-Admin-Token` 仍可调用写接口（兼容）。
- [ ] 内容/模型/设置/用户/工单/危机写操作均写审计；审计可按人/动作/时间筛选。
- [ ] 禁用用户立即失效；重置密码后旧会话失效。
- [ ] 密码只存哈希；日志不出现明文密码/token。
- [ ] pytest 覆盖：权限矩阵、会话过期、CSRF、审计、旧令牌兼容。

### 7.9 风险

- 升级锁死 → 保留旧令牌通道 + 本机恢复入口；文档明确恢复步骤。
- 角色过细导致配置复杂 → v0.2.0 固定 5 角色，不做自定义角色。
- Cookie 跨域/隧道场景 → 同源 SPA 为主；Cloudflare 隧道下 Secure cookie 正常；如跨域，回退 Bearer token（配置开关）。
- 审计日志膨胀 → 保留 180 天，定时归档/清理。

---

## 8. 需求 4：时效性与当前时间

### 8.1 目标

让用户知道「这条信息是什么时候的、还有效吗」，并让模型按真实日期理解「今年/明年/本学期」。

### 8.2 用户故事

- 作为学生，我在引用卡片上看到来源发布日期/最后核对日期/适用学年。
- 作为学生，如果内容已过期，回答会明确提示「该政策可能已失效，请以最新通知为准」。
- 作为辅导员，我能设置文档/词条的有效期，并在到期前收到提醒。
- 作为辅导员，我能一键「标记已核对」，延长 freshness。
- 作为学生，我问「今年国家奖学金什么时候截止」时，模型知道今年是哪一年。

### 8.3 功能清单

1. 文档/词条新增时效字段：`published_at`、`effective_at`、`expires_at`、`verified_at`、`academic_year`、`doc_type`。
2. 编译时从原文抽取日期：
   - LLM 编译 JSON 增加 `published_at/effective_at/expires_at/academic_year` 字段；
   - 只接受原文中真实出现的日期（正则 + 原文包含校验）；否则留空，标记 `date_confidence=low`。
3. 词条新鲜度（派生，不存库）：
   - `expired`：`expires_at` < 今天；
   - `stale`：`verified_at` 超过 `freshness_stale_days`（默认 180）或 `academic_year` 早于当前学年；
   - `aging`：`verified_at` 超过 90 天；
   - `fresh`：其余；
   - `unknown`：无日期。
4. 导航与回答：
   - 默认排除 `expired` 词条；若无其他候选，允许选中但强制加过期提示。
   - `stale/aging` 轻微降权；`fresh` 轻微加权。
   - 通知类（`doc_type=notice`）按 `published_at` 降序优先，同主题保留最新。
   - 引用卡展示日期 + 徽标（最新/待核对/可能过期）。
   - 所有选中词条都过期时，回答开头加固定提示，并触发远程兜底（若开启）。
5. 当前日期注入：
   - 在回答 system prompt 前加：「当前日期：YYYY-MM-DD；当前学年：YYYY-YYYY；时区：Asia/Shanghai。判断今年/明年/本学期时以此为准。」
   - 远程检索 query 也注入学年/日期，提升「2026 通知」命中。
   - 设置项：`academic_year`（默认按 8 月切换计算）、`timezone`（默认 Asia/Shanghai）。
6. 管理端：
   - 文档/词条编辑表单增加日期字段。
   - 内容管理列表显示新鲜度徽标 + 到期日。
   - 「时效看板」：30 天内到期、已过期、超过 180 天未核对。
   - 批量操作：标记已核对、设置有效期、按学年筛选。
7. 提醒：
   - 服务台/内容管理顶部提示「N 条内容将在 30 天内过期」。
   - 可选 Webhook 每周摘要（复用通知通道）。

### 8.4 数据模型

```sql
ALTER TABLE documents ADD COLUMN published_at TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN effective_at TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN expires_at TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN verified_at TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN academic_year TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'general'; -- general|notice|policy|faq

ALTER TABLE wiki_pages ADD COLUMN published_at TEXT NOT NULL DEFAULT '';
ALTER TABLE wiki_pages ADD COLUMN effective_at TEXT NOT NULL DEFAULT '';
ALTER TABLE wiki_pages ADD COLUMN expires_at TEXT NOT NULL DEFAULT '';
ALTER TABLE wiki_pages ADD COLUMN verified_at TEXT NOT NULL DEFAULT '';
ALTER TABLE wiki_pages ADD COLUMN academic_year TEXT NOT NULL DEFAULT '';
ALTER TABLE wiki_pages ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'general';
ALTER TABLE wiki_pages ADD COLUMN verified_by TEXT NOT NULL DEFAULT '';
```

> `freshness` 在读取时按设置计算，避免存储值与当前时间漂移；列表筛选在 Python 层过滤（词条量级 < 1 万可接受），必要时再加物化列。

### 8.5 API

- `PUT /api/wiki/pages/{id}` 扩展允许字段：`published_at/effective_at/expires_at/verified_at/academic_year/doc_type`。
- `POST /api/wiki/pages/{id}/verify`：`{verified_at?}`，写 `verified_by` + 审计。
- `POST /api/knowledge/documents/{id}/verify`：同上。
- `GET /api/insights/freshness?window=30`：`{expiring:[], expired:[], stale:[]}`。
- `GET /api/settings` 增加 `academic_year/freshness_stale_days/freshness_expiry_warn_days`。
- 引用结构扩展：`sources[]` 增加 `published_at/effective_at/expires_at/verified_at/academic_year/freshness`。

### 8.6 后端改动点

- `wiki_builder.COMPILE_SYSTEM_PROMPT` 增加日期字段与「无日期输出空字符串」约束。
- `wiki_builder` 校验日期：`YYYY-MM-DD`，且原文包含该日期或年份；不接受推断。
- `wiki_nav._page_dict()` 计算 freshness；`_exact_candidates/_keyword_candidates` 加权重；`build_context` 注入日期/新鲜度；`_citation_sources` 透出日期。
- `counselor._system_prompt()` 注入当前日期/学年；`_answer_from_pages()` 在过期时加提示。
- 设置页/内容管理前端展示。

### 8.7 验收标准

- [ ] 当前日期/学年出现在 system prompt；问「今年」时回答按服务器日期理解（用固定问题回归）。
- [ ] 编译能从原文抽取日期；原文没有的日期不会被写入。
- [ ] 过期词条默认不参与导航；无替代时参与但回答带过期提示。
- [ ] 引用卡显示来源日期/核对日期/学年/新鲜度徽标。
- [ ] 管理端可设置/批量核对/查看 30 天内到期列表。
- [ ] 现有问答检索指标不下降（用 v2026.21 数据集回归）。
- [ ] pytest 覆盖：日期抽取/校验、freshness 计算、权重、过期提示、prompt 注入。

### 8.8 风险

- LLM 抽取日期错误 → 原文校验 + 人工可改；低置信标记。
- 过期内容被完全排除导致答不上 → 保留「无替代则带警告回答」。
- 学年计算规则因学校而异 → 设置项可覆盖，默认 8 月 1 日切换。
- 时区 → 统一 Asia/Shanghai，避免服务器 UTC 偏差。

---

## 9. 需求 5：异步编译、流式回答与告警

### 9.1 目标

上传/编译不再阻塞请求；回答逐字输出；失败自动重试、可取消、可告警。

### 9.2 用户故事

- 作为辅导员，我上传 20MB PDF 后立即看到「已排队」，可以关页面，稍后回来看进度。
- 作为辅导员，编译失败时我能看到原因并一键重试；多次失败会出现在告警里。
- 作为学生，我提问后 1-2 秒内看到第一个字，回答逐字出现，可随时停止。
- 作为管理员，我能看到任务队列、失败任务、告警，并确认/重试。

### 9.3 功能清单

1. 异步任务队列：
   - 类型：`compile_document`、`reindex_document`、`import_url`、`import_text`、`rebuild_all`。
   - 状态：`queued|running|succeeded|failed|canceled`。
   - 进度：0-100 + 阶段文案（解析/编译第 N/M 单元/合并/完成）。
   - 并发：默认 2 个 worker（可配 `NPU_JOB_WORKERS`），避免打爆 LLM 限流。
   - 启动恢复：`running` 且超过 10 分钟无心跳 → `queued` 重试；超过 3 次 → `failed`。
   - 幂等：同一文档同一时间只允许一个编译任务；文件内容哈希去重（见 Top20 #7）。
2. 上传/导入异步化：
   - `POST /api/knowledge/documents` 默认返回 `202 {document, job_id}`，`status='pending'`。
   - 兼容：`?sync=1` 保持原同步行为（测试/旧客户端）。
   - URL/文本/批量导入同样入队；批量内部逐条任务。
3. 任务 UI：
   - 内容管理 → 「任务」Tab：排队/运行/失败/完成，进度条、错误、重试、取消。
   - 页面刷新/重开保留进度（轮询 `/api/jobs`）。
   - 上传卡片显示状态，不阻塞其他操作。
4. 流式回答：
   - `POST /api/chat/stream`（SSE），事件：`meta`、`delta`、`supplement`、`citations`、`done`、`error`。
   - 前端用 `fetch` + `ReadableStream`；逐字渲染 Markdown；停止按钮 abort。
   - 非流式 `/api/chat` 保留；前端能力检测失败时回退。
   - 机器人渠道保持非流式。
   - 远程兜底/完整性补充：主回答流完后，若需要远程检索，发 `supplement` 事件追加「补充检索」段落；最终统一落库。
5. LLM 重试与熔断：
   - 429/5xx/超时自动重试 2 次（指数退避 1s/3s）。
   - Provider 连续 5 次失败 → 熔断 60 秒，期间直接降级/换备用模型（v0.3.0 完整 failover）。
   - 超时：默认 60 秒（可配）。
6. 告警：
   - `system_alerts`：任务最终失败、模型不可用、源同步失败、备份失败。
   - 服务台/设置顶部横幅；可确认/解决；可选 Webhook。
   - `/health` 增加 `queue_depth`、`last_job_failure`、`model_status`。
7. 成本/耗时埋点（为 v0.3.0 看板打基础）：
   - `jobs` 记录 `tokens_in/tokens_out/llm_calls/duration_ms`；
   - `messages` 记录已有 `latency_ms`，补充 `tokens` 字段（可选）。

### 9.4 数据模型

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,                    -- compile_document|reindex_document|import_url|import_text|rebuild_all
    status        TEXT NOT NULL DEFAULT 'queued',   -- queued|running|succeeded|failed|canceled
    resource_type TEXT NOT NULL DEFAULT '',
    resource_id   TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '{}',
    progress      INTEGER NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    last_error    TEXT NOT NULL DEFAULT '',
    created_by    TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    next_retry_at TEXT,
    heartbeat_at  TEXT,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    llm_calls     INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_resource ON jobs(resource_type, resource_id);

CREATE TABLE IF NOT EXISTS system_alerts (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,                    -- job_failed|model_down|sync_failed|backup_failed
    severity      TEXT NOT NULL DEFAULT 'warning',  -- info|warning|critical
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    resource_type TEXT NOT NULL DEFAULT '',
    resource_id   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',     -- open|acknowledged|resolved
    created_at    TEXT NOT NULL,
    ack_by        TEXT,
    ack_at        TEXT,
    resolved_at   TEXT
);

ALTER TABLE messages ADD COLUMN tokens_in INTEGER NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN tokens_out INTEGER NOT NULL DEFAULT 0;
```

### 9.5 API

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/knowledge/documents?sync=0` | 内容写 | 默认 202 + `job_id` |
| POST | `/api/knowledge/documents/{id}/reindex` | 内容写 | 返回 `job_id`（202） |
| GET | `/api/jobs?type=&status=&limit=` | 内容/运维读 | 任务列表 |
| GET | `/api/jobs/{id}` | 内容/运维读 | 任务详情/进度 |
| POST | `/api/jobs/{id}/retry` | 内容写 | 重新入队 |
| POST | `/api/jobs/{id}/cancel` | 内容写 | 取消（运行中置标记） |
| POST | `/api/chat/stream` | 公开（限流） | SSE |
| GET | `/api/alerts?status=` | 运维读 | 告警列表 |
| POST | `/api/alerts/{id}/ack` | 运维写 | 确认 |
| POST | `/api/alerts/{id}/resolve` | 运维写 | 解决 |

SSE 示例：

```text
event: meta
data: {"conversation_id":"...","navigation":{"method":"keyword","pages":[...]},"stream_id":"..."}

event: delta
data: {"text":"国家奖学金"}

event: supplement
data: {"text":"\n\n补充检索：..."}

event: citations
data: {"citations":[...]}

event: done
data: {"latency_ms":1830,"fallback":false,"retrieval":{...}}
```

### 9.6 后端实现要点

- **队列**：`services/jobs.py`，模块级 `queue.Queue` + N 个 daemon `threading.Thread`；`lifespan` 启动/停止；不引入 Celery/Redis。
- **任务执行**：复用 `ingest.reindex_document` / `importer.import_*`；把现有同步函数包一层 `job_runner`，更新 progress/message。
- **进度**：`wiki_builder` 增加 `progress_cb` 回调（编译单元计数）；或通过 `jobs.update_progress` 从单元循环调用（注意解耦，可用可选参数）。
- **取消**：`cancel_requested` 列或在 payload/内存事件；worker 在每个编译单元前检查。
- **重试**：捕获 `LLMUnavailable/Timeout/HTTP 429/5xx` → 指数退避；业务错误（文件损坏/格式不支持）不重试。
- **流式**：`services/llm.py` 增加 `stream_chat(messages, ...)`：
  - OpenAI 兼容：`httpx.stream` 解析 `data:` SSE；
  - Ollama：`/api/chat` `stream=true` 逐行 JSON；
  - mock：按字符/句子 yield。
  - 统一 yield `{"delta": str}`，结束时 `{"usage": ...}`。
- **流式 + 引用**：先导航得到 pages，发 `meta`；LLM 边流边累积；结束后 `attach_citations` 过滤 `[n]`，发 `citations`；落库最终 reply。
- **断连**：`request.is_disconnected()` 检查；停止 LLM；已产生的部分文本可选择落库并标记 `aborted=true`（建议不落库，避免半条回答；保留用户消息）。
- **降级**：模型不支持 stream（mock/部分 Ollama）→ 一次性 `delta`；远程兜底在主回答后执行，发 `supplement`。
- **限流**：流式接口同样 30 次/分/IP；单 IP 并发流 ≤ 2。
- **超时**：整体 90 秒；LLM 单次 60 秒。

### 9.7 前端实现要点

- `views/chat.js`：发送时优先 `streamChat()`；用 `TextDecoder` 增量解析 SSE；`AbortController` 停止；失败回退 `/api/chat`。
- 渲染节流：每 50-80ms 或每 20 字更新一次 DOM，避免频繁重排。
- 停止后保留已输出内容 + 「已停止」标记；可重新生成（v0.3.0）。
- 任务 Tab：轮询 2 秒（运行中），空闲 10 秒；离开页面停止轮询。
- 上传 UI：不再转圈等待编译，显示「已排队 12%」。

### 9.8 验收标准

- [ ] 上传 20MB PDF 在 2 秒内返回 202 + job_id；页面可关闭；重开后进度可见。
- [ ] 任务队列并发 2；同一文档重复上传不会并发编译（幂等）。
- [ ] 失败任务可重试；3 次失败生成告警；告警可确认/解决。
- [ ] 服务重启后 running 任务恢复为 queued 并继续；不会重复生成词条。
- [ ] 流式回答首字 P95 ≤ 2.5 秒；停止按钮 1 秒内生效；最终引用与落库一致。
- [ ] 模型不支持流式时自动回退，不报错。
- [ ] 机器人渠道仍走非流式，行为不变。
- [ ] 现有 pytest 全绿；新增任务/SSE 测试。
- [ ] `/health` 返回队列深度/最近失败。

### 9.9 风险

- 单进程线程队列在重启时丢任务 → 启动恢复 + 状态机；生产多实例需外部队列（v0.5.0）。
- SQLite 并发写 → 单 worker 写、WAL、短事务；避免长事务。
- 流式与远程兜底组合复杂 → 先保证主回答稳定，supplement 作为可选事件；失败不影响主回答。
- 前端 Markdown 增量渲染性能 → 节流 + 纯文本先渲染，结束后统一格式化。
- 用户中途停止导致消息不完整 → 不落库半条；UI 明确「已停止」。

---

## 10. 数据模型汇总

| 表 | 类型 | 用途 |
|----|------|------|
| `answer_feedback` | 新增 | 回答反馈 |
| `knowledge_gaps` | 新增 | 知识缺口池 |
| `handoff_requests` | 新增 | 转人工工单 |
| `crisis_events` | 新增 | 危机事件 |
| `notification_log` | 新增 | 通知投递记录 |
| `admin_users` | 新增 | 管理员账号 |
| `admin_sessions` | 新增 | 登录会话 |
| `audit_log` | 新增 | 操作审计 |
| `jobs` | 新增 | 异步任务 |
| `system_alerts` | 新增 | 系统告警 |
| `messages` | 加列 | `retrieval/fallback/navigation_method/tokens_in/tokens_out` |
| `documents` | 加列 | `published_at/effective_at/expires_at/verified_at/academic_year/doc_type` |
| `wiki_pages` | 加列 | 同上 + `verified_by` |

迁移策略：`db.init_db()` 内用 `_column_names()` + `ALTER TABLE ADD COLUMN` 增量迁移；新表 `CREATE TABLE IF NOT EXISTS`；不删旧列；旧数据默认空值。

---

## 11. 兼容与迁移

1. **API 兼容**
   - `/api/chat` 响应只加字段（`retrieval` 已有，新增 `feedback` 可选）。
   - 上传默认异步是行为变更：保留 `?sync=1`；前端同步升级；文档标注。
   - 旧 `X-Admin-Token` 保留，映射超级管理员；账号体系上线后仍可用。
2. **前端兼容**
   - 新视图按权限懒加载；旧 localStorage token 在登录页可迁入。
   - 无账号体系时，保持现有令牌输入方式。
3. **数据迁移**
   - 首次启动创建缺失表/列；不阻塞。
   - 缺口回填、时效字段为空时 `freshness=unknown`，不参与降权。
4. **回滚**
   - 新功能用设置开关：`FEATURE_FEEDBACK/HANDOFF/CRISIS/AUTH/STREAM/ASYNC_COMPILE`。
   - 关闭开关不影响旧链路；新表保留不删。
5. **测试基线**
   - 现有 pytest 126+ 全绿；v2026.21 检索 100% / 引用 100% / 拒答 100% 不回归。

---

## 12. 安全、隐私与合规

- **最小权限**：服务台/危机/审计分权限；学生只能看自己的反馈/工单。
- **敏感信息**：联系方式脱敏；危机记录单独表 + 访问审计；日志不写完整对话。
- **密码/会话**：PBKDF2 + HttpOnly cookie + CSRF；登录限流/锁定。
- **数据保留**：反馈/工单/危机/审计默认保留 180 天，可配置；提供删除接口（v0.4.0 完整合规）。
- **AI 告知**：聊天页/首次使用提示「回答由 AI 生成，可能有误；危机请直接联系人工」。
- **合规评审**：危机识别与心理相关功能上线前需学校心理中心/法务评审；不做自动诊断，只做求助引导。
- **内容安全**：危机模板固定，不让 LLM 自由生成求助信息；远程检索内容仍按现有引用标注。

---

## 13. 测试计划

| 层级 | 覆盖 |
|------|------|
| 单元 | 反馈校验/去重、缺口聚类、freshness 计算、日期抽取、危机规则、权限矩阵、密码哈希、SSE 解析 |
| 集成 | 反馈→缺口、危机→工单→通知、角色越权、任务重试/取消、流式落库 |
| 端到端 | Chrome CDP：反馈提交、转人工、服务台认领、危机横幅、流式停止、上传异步进度 |
| 回归 | 现有 126+ pytest；v2026.21 数据集；机器人四通道冒烟 |
| 安全 | 401/403、CSRF、限流、SSRF、XSS（反馈备注/工单内容）、日志脱敏 |
| 可用性 | 390px 移动端；键盘操作；对比度；错误文案 |

---

## 14. 里程碑（Top 5）

> 估算假设：2 名工程师（1 后端 + 1 前端）+ 0.5 QA；不含评审/外部依赖等待。

| 里程碑 | 周期 | 交付 |
|--------|------|------|
| M1 | W1-2 | 时效性字段/当前日期注入 + 反馈采集（表/API/学生端 UI） |
| M2 | W3-4 | 知识缺口工作台 + 反馈列表 + 概览；转人工工单（Web） |
| M3 | W5-6 | 危机规则/卡片/通知；账号/角色/登录（后端 + 登录页） |
| M4 | W7-8 | 审计/权限迁移；异步任务队列 + 上传异步化 |
| M5 | W9-10 | 流式回答 + 失败重试/告警 + 回归/灰度发布 |

**退出标准**：Top 5 验收清单全过；现有测试与评测不回归；完成一次真实辅导员试用（≥5 人）并收集反馈。

---

## 15. Top 20 优先级排期

> 优先级：P0 = v0.2.0 必须；P1 = v0.3.0；P2 = v0.4.0；P3 = v0.5.0+。  
> 估算单位：人日（PD），按 2 人并行估算。

| # | 需求 | 优先级 | 版本/里程碑 | PD | 依赖 | 验收要点 |
|---|------|--------|-------------|----|------|----------|
| 1 | 反馈 + 知识缺口工作台 | P0 | v0.2.0 M1-M2 | 14 | messages.retrieval 持久化 | 反馈→缺口→补资料→关闭闭环 |
| 2 | 转人工 + 危机升级 | P0 | v0.2.0 M2-M3 | 12 | 通知 Webhook；权限 | 工单/危机卡片/通知/留痕 |
| 3 | 账号、角色与审计 | P0 | v0.2.0 M3-M4 | 18 | 所有管理 API | 5 角色服务端生效 + 审计覆盖 |
| 4 | 时效性与当前时间 | P0 | v0.2.0 M1 | 9 | 编译提示词/导航 | 日期展示/过期提示/今年正确 |
| 5 | 异步编译 + 流式 + 告警 | P0 | v0.2.0 M4-M5 | 18 | 任务队列/LLM stream | 202/进度/停止/重试/告警 |
| 6 | 审核发布 + 版本回滚 + needs_review 队列 | P1 | v0.3.0 M6 | 12 | #3 权限 | 草稿→审核→发布；可回滚 |
| 7 | FAQ/Excel/PPT/OCR 批量导入 + 哈希查重 + 批量词条操作 | P1 | v0.3.0 M6-M7 | 15 | #5 任务队列 | 批量导入/去重/合并/打标 |
| 8 | 主动推送 + 订阅 + 学期专题 | P1 | v0.3.0 M7 | 12 | #2 通知；渠道能力 | 新通知触达群/公众号/订阅 |
| 9 | 学生身份卡 + 材料清单/日历 | P1 | v0.3.0 M7-M8 | 10 | #4 时效 | 本科/研究生/年级差异化 |
| 10 | 运营看板 + 成本/token + per-purpose model/failover | P1 | v0.3.0 M8 | 15 | #1/#5 数据；模型路由 | 成本可见、模型可切换/降级 |
| 11 | 发布前回归评测 | P1 | v0.3.0 M8 | 8 | 评测数据集 | 新词条/提示词上线前跑分 |
| 12 | 收藏/分享/跨设备历史 | P1 | v0.3.0 M9 | 8 | #3 账号 | 登录后历史同步；分享卡片 |
| 13 | 多模态输入（图/语音/PDF） | P2 | v0.4.0 M9-M10 | 15 | ASR/视觉模型 | 截图/语音/附件提问 |
| 14 | 拼音/模糊/别名推荐 | P2 | v0.4.0 M10 | 8 | #1 缺口/query 日志 | 拼音首字母、错别字、别名建议 |
| 15 | 内容冲突检测 + 多来源对比 | P2 | v0.4.0 M10-M11 | 8 | #6 版本；#4 日期 | 金额/截止/电话冲突提示 |
| 16 | 合规：保留期限/导出/删除/脱敏 | P2 | v0.4.0 M11 | 10 | #3 审计 | 数据可导出/删除/脱敏 |
| 17 | 内容分级/学院范围 | P2 | v0.4.0 M11-M12 | 10 | #3 角色 | 内部文件不进学生问答 |
| 18 | 备份恢复 UI + 健康监控 + 日志检索 | P2 | v0.4.0 M12 | 8 | #5 告警 | 一键备份/恢复/健康面板 |
| 19 | 多学院多租户 + SSO/企微/钉钉/小程序 | P3 | v0.5.0 | 30 | #3/#17 | 租户隔离、统一登录 |
| 20 | 教务/学工系统集成（课表/成绩/请假/奖助进度） | P3 | v0.5.0+ | 40+ | #19 SSO；校方接口 | 能查个人进度，不再只问答 |

### 15.1 阶段汇总

| 版本 | 周期 | 内容 | 累计 PD |
|------|------|------|---------|
| v0.2.0 | W1-10（10 周） | #1-#5 服务闭环 | 71 |
| v0.3.0 | W11-20（10 周） | #6-#12 内容治理与运营 | 80 |
| v0.4.0 | W21-30（10 周） | #13-#18 体验与合规 | 59 |
| v0.5.0+ | W31+ | #19-#20 规模化与系统集成 | 70+ |

### 15.2 关键路径

- **#3 账号/角色** 是 #12/#16/#17/#19 的前置；越早完成，后续返工越少。
- **#5 任务队列** 是 #7/#10/#18 的前置；异步化越早，批量导入越顺。
- **#1 反馈** 是 #10 数据看板的前置；没有反馈就没有质量指标。
- **#4 时效性** 是 #15 冲突检测的前置；先有日期，才谈「哪份更新」。
- **#2 通知** 是 #8 主动推送的前置；先打通 Webhook，再做订阅。

### 15.3 建议的实际顺序（避免依赖阻塞）

1. W1-2：#4 时效性 + #1 反馈采集（并行）。
2. W3-4：#1 工作台 + #2 转人工（Web）。
3. W5-6：#2 危机 + #3 账号/角色（后端并行）。
4. W7-8：#3 审计/迁移 + #5 异步任务。
5. W9-10：#5 流式 + 告警 + 回归。
6. 之后按上表推进。

---

## 16. 风险与开放问题

### 16.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 危机误报/漏报 | 信任与安全 | 规则+复核+误报标记；心理中心评审；定期回归 |
| 辅导员人力不足 | 转人工积压 | SLA/分级/自动回复；只对高价值问题转人工 |
| 账号迁移锁死 | 无法管理 | 保留旧令牌 + 本机恢复入口 + 文档 |
| 异步任务丢/重复 | 内容不一致 | 启动恢复 + 幂等锁 + 哈希去重 |
| 流式与远程兜底复杂 | 回答不完整 | 主回答优先；supplement 可选；失败降级 |
| 单进程 SQLite 瓶颈 | 并发差 | 控制 worker；v0.5.0 评估 Postgres/外部队列 |
| 隐私合规 | 法律风险 | 最小化/脱敏/保留期限/审计/评审 |
| 反馈率低 | 数据不足 | 轻量提示；负反馈二次引导；辅导员主动回访 |

### 16.2 开放问题（需要产品/学校确认）

1. 危机事件的通知对象与值班表由谁维护？是否需要短信/电话告警？
2. 转人工的 SLA 是否按工作时间计算？寒暑假/夜间如何处理？
3. 学生是否需要登录才能转人工/看历史？还是保持匿名 + 工单 token？
4. 账号体系是否对接学校统一身份认证？v0.2.0 本地账号是否可接受？
5. 内容分级：哪些文件属于「内部」，判定标准由谁定？
6. 数据保留期限：聊天/危机/审计分别保留多久？
7. 是否允许辅导员在 Web 端直接回复学生？回复是否显示「辅导员」身份？
8. 多学院部署是「一套系统多租户」还是「每学院独立部署」？

---

## 17. 附录：角色权限矩阵（建议）

| 能力 | super_admin | college_admin | counselor | editor | viewer |
|------|-------------|---------------|-----------|--------|--------|
| 内容读取 | ✓ | ✓（本院） | ✓ | ✓ | ✓ |
| 内容上传/编辑 | ✓ | ✓（本院） | — | ✓ | — |
| 内容发布/回滚 | ✓ | ✓（本院） | — | ✓ | — |
| 反馈查看/处理 | ✓ | ✓（本院） | ✓ | — | — |
| 转人工处理 | ✓ | ✓（本院） | ✓ | — | — |
| 危机查看/处理 | ✓ | ✓（本院） | ✓ | — | — |
| 设置修改 | ✓ | 部分 | — | — | — |
| 模型/通道管理 | ✓ | — | — | — | — |
| 成员管理 | ✓ | ✓（本院） | — | — | — |
| 审计查看 | ✓ | ✓（本院） | — | — | — |
| 数据导出 | ✓ | ✓（本院） | — | — | — |

> 「本院」依赖 `admin_users.college` 与内容 `college` 字段；内容分级/学院范围在 #17 落地，v0.2.0 先做全局角色。

---

## 18. 附录：现有接口兼容清单

| 现有接口 | v0.2.0 变化 | 兼容策略 |
|----------|-------------|----------|
| `POST /api/chat` | 响应增加 `feedback` 可选；内部持久化 retrieval | 只加字段 |
| `POST /api/chat`（同步） | 保留 | 流式走新 `/api/chat/stream` |
| `POST /api/knowledge/documents` | 默认 202 异步 | `?sync=1` 保留同步；前端升级 |
| `POST /api/knowledge/documents/{id}/reindex` | 202 + job_id | 旧客户端可轮询 document.status |
| `GET /api/wiki/pages/{id}` | 增加日期/freshness 字段 | 只加字段 |
| `PUT /api/wiki/pages/{id}` | 允许日期字段 | 旧请求不受影响 |
| `GET /api/settings` | 增加时效/危机/账号设置 | 前端容错 |
| `GET /api/health` | 增加队列/告警字段 | 只加字段 |
| `X-Admin-Token` | 保留为超级管理员 | 双通道认证 |

---

*本 PRD 为 v0.2.0 草案，需与学校学生工作部门、心理中心、信息化部门评审后冻结。*

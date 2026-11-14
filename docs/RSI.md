# RSI 自进化与端到端质量门禁

RSI（Recursive Self-Improvement）在本项目里是一条可审计的闭环：

```
学生反馈（没帮助 + 原因）
      ↓ 聚合
知识缺口（按频次排序）
      ↓ 辅导员补料 / 关联词条
LLM 重新编译（带来源锚点）
      ↓
时效治理（fresh / aging / stale / expired）
      ↓
下一次同类问题直接命中
```

自进化改动（改提示词、改编译策略、改导航/检索、改前端交互）都可能悄悄破坏已有能力，
所以 RSI **不是只靠“补资料”**，还必须有一道可复跑的端到端门禁。

## 1. 门禁做什么

`scripts/rsi_verify.py` 运行带 `rsi` 标签的关键场景（桌面 + 移动端），验证：

| 场景 | 保护的能力 |
|---|---|
| `bootstrap.routes` | 四个一级入口、旧路由兼容 |
| `chat.answer_and_citation` | 提问 → 回答 → 双层引用 |
| `knowledge.browse` | 搜索 → 词条抽屉 |
| `mobile.layout` | 390×844 五页无横向滚动、侧栏开合 |
| `mobile.chat` | 触摸发送、引用、点赞、历史会话 |
| `mobile.knowledge` | 触摸搜索、打开词条、标签筛选 |
| `sandbox.isolation` | 数据目录/端口/生产指纹隔离 |

通过标准：
- 上述场景 **全部 PASS**
- 全局动作覆盖率 ≥ **90%**（来自 E2E 静态盘点）
- 产出机器可读证据，供 RSI 循环读取

## 2. 怎么跑

```powershell
# 独立跑关键场景（自举沙箱，约 1 分钟）
.\scripts\rsi.ps1

# 复用最近一次全量 E2E 产物（CI 推荐）
.\scripts\rsi.ps1 -FromArtifacts

# 只看门禁覆盖哪些场景
.\.venv\Scripts\python.exe scripts\rsi_verify.py --list
```

产物：

```
docs/rsi/e2e-latest.json     # 提交进仓库：commit / 场景状态 / 覆盖率 / 截图索引 / passed
docs/rsi/latest/             # 本次证据：report.json、junit、逐场景截图
```

`docs/rsi/e2e-latest.json` 的 `passed` 是 RSI 循环的准入条件；不通过时脚本以非零码退出，
CI 与自进化脚本都应中止后续“自动接受改动”。

## 3. 与 CI 的关系

`.github/workflows/e2e.yml` 在跑完全量 E2E 后执行：

```bash
python scripts/rsi_verify.py --from-artifacts --min-coverage 90
```

失败即整个 E2E job 失败，并上传 `docs/rsi` 证据。这样每次 PR / push 都会验证
“自进化没有把已有的端到端能力弄坏”。

## 4. 移动端覆盖说明

移动端不是“缩窄窗口截个图”，而是真实触摸事件（`Input.dispatchTouchEvent`）驱动：

- 视口 390×844 + `mobile=true`，触摸菜单、导航、发送、搜索、标签、上传；
- 每步断言 `scrollWidth<=clientWidth`，防止长文件名/长 URL 撑破布局；
- 截图证据在 `docs/rsi/latest/screenshots/` 与 `docs/screenshots/e2e/mobile.*`。

当前覆盖：移动端布局、对话发送与引用、知识库搜索/抽屉/筛选、内容管理上传、设置与模型页。

## 5. 后续可扩展

- 把 `rsi` 标签扩展到“缺口 → 补料 → 重编译”的真实数据链路（当前用 mock 模型验证状态流转）；
- 增加离线/PWA 场景（Service Worker 缓存、断网提示）；
- 增加真机云测（BrowserStack/自建设备农场），本引擎保持同一套场景脚本。

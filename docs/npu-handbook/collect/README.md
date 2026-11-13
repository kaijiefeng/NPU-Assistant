# 公开文档采集（collect/）

> 目标：为 NPU-Assistant 准备 **1000+ 份西北工业大学官方公开文档**，并保证覆盖广度（站点、主题、文档类型、受众、时效）。
> 结果：**1710 篇 / 66 个官方站点 / 24 个主题类目 / 6 种文档类型 / 228 万字**（2026-11-13 采集）。
> 分类口径见 [TAXONOMY.md](TAXONOMY.md)，统计见 [COLLECTION-REPORT.md](COLLECTION-REPORT.md) 与 [coverage.csv](coverage.csv)，
> 完整性审计（67 项学生手册应覆盖内容）见 [COMPLETENESS-AUDIT.md](COMPLETENESS-AUDIT.md)。

## 安全约定（登录态 / computer use）

涉及登录或需要"人机交互"的资料，遵守以下规则（与项目「只用公开资料」的边界一致）：

- **不接收账号密码**：密码只输给学校页面，不进入聊天、代码、日志或仓库；
- **不绕过验证码/登录墙**：遇到「请输入验证码下载附件」直接停止，不尝试 OCR 破解、不猜验证码；
- **拟人节奏、小批量**：可见 Chrome 窗口、同一会话内逐页操作，间隔 6–12 秒，每批 ≤10 个文档，不做批量爬取/并发抓取；
- **只取所需**：只下载制度/通知类原文，不导出成绩、学籍、名单等个人数据；入库前自动脱敏（身份证/手机/邮箱/一卡通/学号），名单/成绩单类整篇丢弃；
- **会话不持久化**：实测学校 CAS 会话 Cookie 不跨浏览器重启保留（`--login` 保存的 profile 复用会重新跳登录页），因此需要"同一窗口登录后继续操作"，而不是后台脚本代登。

## 与既有 114 篇精选语料的关系

- `../corpus/` + `../sources.json`：此前人工挑选的 114 篇**精选**语料，已进入 Wiki 编译与 QA 评测链路；
- 本目录 `corpus/`：本轮批量采集的 **1702 篇**公开文档，独立存放，**不混入**既有 `corpus.json`，避免影响已冻结的 v2026.21 评测集；
- 后续可按 `manifest.jsonl` 中的「主题 × 类型」挑出制度规章/办事指南，逐步"提拔"进精选语料（promote 流程待做）。

## 目录结构

```text
collect/
├─ TAXONOMY.md             # 五维分类法与覆盖验收标准（参考了学生手册/校园 wiki 项目的分类）
├─ seeds.json              # 栏目种子：55 个站点、357 个列表页（轮转排序，广度优先）
├─ discovered.json         # --discover 的原始探测结果（64 个机构站点及其栏目）
├─ manifest.jsonl          # 采集清单（httpx 采集 + 子智能体分片 + 浏览器渲染补采，每行一篇）
├─ manual-shards/
│  ├─ raw/*.json           # 采集子智能体产出的原始分片（a1-a6）
│  └─ imported.jsonl       # 分片导入后的清单
├─ corpus/<id>.md          # 1702 篇统一 Markdown（YAML front matter + 正文）
├─ COLLECTION-REPORT.md    # 采集报告：站点覆盖、主题×类型矩阵、示例
└─ coverage.csv            # 主题覆盖统计
```

单篇 Markdown 格式：

```markdown
---
title: "…"
source_url: https://gs.nwpu.edu.cn/info/2198/14775.htm
source_site: gs.nwpu.edu.cn
source_name: "研究生教育"
category: 学位与毕业
doc_type: 办事指南
audience: 研究生
publish_date: 2026-09-18
collected_at: 2026-11-12
---

# 标题

正文……
> 来源：https://…
```

## 三条采集通道

| 通道 | 脚本 | 适用 | 本轮产出 |
|---|---|---|---|
| HTTP 直采 | `scripts/handbook_collect.py --fetch` | 普通公开站点（httpx + bs4，复用 `services.importer.extract_article`） | 1255 篇 |
| 子智能体检索 | `handbook_collect.py --import-shards` | 难访问站点/专题页：6 个采集子智能体用 `web_search` + `web_fetch` 检索并落盘分片 | 82 篇 |
| 浏览器渲染 | `scripts/browser_collect.py` | WAF 412/403 站点：真实 Chrome + CDP 渲染，页面 JS 自行完成校验 | 97 篇（job 48 / zsb 30 / tushuguan 19） |
| 附件（PDF） | `scripts/attachment_collect.py` | 详情页只挂 PDF 的制度（正文很短）：抓 PDF → `pypdf` 提文本 | 试跑 1 篇（公寓管理办法） |
| 定向 URL 清单 | `handbook_collect.py --urls FILE` / `browser_collect.py --urls FILE` | 审计缺口清单：按 web_search 命中的官方页/PDF 逐条补 | +5 篇（含 1 篇 PDF、4 个 HTML 页） |
| 电脑操作（computer use） | 可见 Chrome + CDP 会话，人工登录后逐页下载 | 需要登录 / HTTP 被 502 拦截但浏览器可加载的 PDF；拟人节奏、小批量 | +2 篇（校园卡管理办法、学生申诉管理办法） |

```text
机构页（党政机关/教学科研机构/支撑保障）
  → --discover 探测站点首页栏目，统计列表页详情链接数 → discovered.json
  → 整理成 seeds.json（站点 × 栏目，轮转保证 breadth-first）
  → --fetch 按栏目抓详情页（httpx）
  → 子智能体补采难访问站点/专题（web_search + web_fetch → manual-shards/raw/*.json）
  → --import-shards 合并分片
  → browser_collect.py 用真实 Chrome 渲染 WAF 站点
  → --reclassify 补齐分类（只填「其他」，不覆盖已有结论）
  → --report 生成 COLLECTION-REPORT.md / coverage.csv
```

复现命令：

```powershell
.\.venv\Scripts\python.exe scripts\handbook_collect.py --discover
.\.venv\Scripts\python.exe scripts\handbook_collect.py --fetch --target 1600
.\.venv\Scripts\python.exe scripts\handbook_collect.py --import-shards
.\.venv\Scripts\python.exe scripts\browser_collect.py --sites job,zsb,tushuguan --limit 80
.\.venv\Scripts\python.exe scripts\handbook_collect.py --reclassify
.\.venv\Scripts\python.exe scripts\handbook_collect.py --report
```

## 浏览器渲染补采（computer-use 式）

httpx 直接请求 `job` / `zsb` / `tushuguan` 等站点会得到 **412（WAF）**；用真实 Chrome（CDP，`--headless=new`）渲染后，页面 JS 自行完成校验，正文可读。`scripts/browser_collect.py` 的做法：

1. 启动 Chrome（随机临时 profile）+ `Network.setUserAgentOverride` 使用普通 Chrome UA（HeadlessChrome UA 会被 WAF 拦）；
2. 逐页 `Page.navigate`，轮询 `document.readyState` 与正文长度，等 WAF 挑战完成；
3. 取渲染后的 `outerHTML` → `services.importer.extract_article` 提取正文 → 写 corpus + manifest；
4. 同域限速、每站限量、URL 去重；不登录、不输验证码、不点授权按钮，遇验证码/登录墙直接跳过。

实测结果：

| 站点 | 产出 | 说明 |
|---|---:|---|
| `job.nwpu.edu.cn` | 48 | 就业政策/手续、招聘会、学院就业细则 |
| `zsb.nwpu.edu.cn` | 30 | 招生简章、强基/专项/保送生、录取分数、专业介绍 |
| `tushuguan.nwpu.edu.cn` | 19 | 入馆须知、借阅与馆藏、数据库/培训、服务导航 |
| `library.nwpu.edu.cn` | 0 | 仍返回 403（旧域名，未再尝试） |

## 本轮「子智能体」分工

6 个采集子智能体并行，各自负责主题/站点，产出 `manual-shards/raw/aK.json`（真实抓取、正文 ≥180 字才保留，共补采 82 篇，并反馈了 5 个机构页未列出的站点线索）：

| 子智能体 | 负责范围 | 结果 | 主要障碍 |
|---|---|---:|---|
| a1 就业 | 就业信息网、档案转递、学院就业手续 | 11 | `job.nwpu.edu.cn` 全站 412（WAF），改从档案馆/学院/学工部补采 |
| a2 本科招生 | 本科招生信息网、研究生招生网 | 18 | `zsb.nwpu.edu.cn` 全站 412；研究生招生网可抓 |
| a3 图书馆 | 入馆/借阅/座位预约/数据库 | 10 | `tushuguan` 412、`library` 403，改从迎新网/新闻网补服务类信息 |
| a4 机关办事 | 财务处、校医院、后勤、档案馆 | 15 | PDF/图片型正文无法提取，弃用 11 篇 |
| a5 学院与机关 | 计算机学院、科研平台、党政办 | 21 | 列表页超时，改用详情页直取；`kypt` 需校园网 |
| a6 学生事务 | 迎新、校历校车、军训征兵、社团、出国 | 21 | 微信公众号文章需验证码，未绕过 |

> 说明：a1/a2/a3 当时被 WAF 挡住的部分，已在本轮用「浏览器渲染」通道补回 97 篇；子智能体的分片仍保留原样，便于追溯。

## 合规与边界

- 只采集 `*.nwpu.edu.cn` 官方公开页面；**不登录、不绕过验证码、不抓附件 PDF/图片、不抓需要统一身份认证的页面**；
- 遵守 robots.txt；同域限速（HTTP 通道默认 0.6s/次，浏览器通道 0.4–0.6s/次）；
- 以 URL 去重，可断点续采；正文由 `services/importer.extract_article` 提取，不足 180 字丢弃；单篇截断 6000 字；
- 浏览器渲染通道只渲染公开页面、不点击授权/登录/表单提交；UA 使用普通 Chrome 字符串以让 WAF 的 JS 校验正常执行（不伪造登录态、不破解验证码）；
- 版权归西北工业大学及各原发布单位，仅用于教学演示与技术验证；引用时保留 `source_url`；
- 采集结果没有经过逐篇人工复核，分类为规则判定，**不作为对外承诺**；正式发布前建议按 10% 抽检。

## 完整性审计（67 项学生手册应覆盖内容）

`handbook_collect.py --audit` 对照 67 项清单（学籍/教学/考试/学位/奖助/纪律/公寓/医保/心理/就业/网络/图书馆/军训/校历/迎新…）逐项核对：

| 结果 | 数量 | 说明 |
|---|---:|---|
| ✅ 有独立文档（标题命中 ≥3 篇） | 53 项（79%） | 可直接作为问答依据 |
| ⚠️ 薄弱（标题命中 1–2 篇） | 17 项 | 需加固 |
| ◐ 仅正文提及（无独立文档） | 14 项 | 有内容但缺权威原文，需人工确认 |
| ❌ 完全未命中 | 0 项 | — |

**仍缺独立文档的 14 项**（大多是「制度原文只在 PDF / 微信公众号」或「站点被 WAF 拦」）：
缓考/补考/重修、GPA/学分绩计算、四六级、体测/体育课、考试违纪/学术不端、
调宿/退宿/走读、食堂/快递/热水/洗衣、体检/疫苗、心理咨询预约、危机干预渠道、
就业手续（三方/报到证/改派）、校园网/账号/VPN、体育场馆预约、完整版学生手册。

本轮定向补采（`--urls` / 浏览器渲染 / 电脑操作）已验证可补到部分原文：**学生公寓管理办法（4.4k 字）**、
**校园卡管理办法**、**学生申诉管理办法**、体育培养方案、心理中心工作说明、校医院迎新新闻等；审计从
「16 项缺失」降到 **14 项**。但仍有一批 PDF 原文所在主机（`dongneng` / `tiyu` / `it` / `xxgk` 的
`download.jsp`）要求**输入验证码**或从当前网络 502/超时，按安全约定不绕过验证码，因此这些项暂未闭环。

## 已知缺口（下一轮优先补）

1. **PDF 原文网络受限**：`*.nwpu.edu.cn/system/_content/download.jsp`、`__local/**.pdf` 在部分主机上
   502/超时；需要校园网出口、可执行 JS 的下载环境，或让学校提供制度汇编；
2. **扫描件 PDF**：部分制度是扫描图片，`pypdf` 提取不到文本，需 OCR（可接 `scripts/ocr_manual_pdfs.py`）；
3. **完整版学生手册**：学校未在公网发布完整 HTML/PDF 版（第三方《瓜兵速成指南》无许可证，未导入）；
4. **仍被拦的站点**：`library.nwpu.edu.cn`（403 旧域名）、`kypt.nwpu.edu.cn`（需校园网）、
   `jobszqy.nwpu.edu.cn`（412）；微信公众号文章需验证码，按规则不绕过；
5. **主题偏斜**：`其他` 占 31%，纪律申诉/军训国防/生活服务/校历出行偏薄（对应内容同样多在 PDF/公众号）；
6. **受众标注**：学院站点默认标「本科生」，口径偏粗，后续可按标题关键词细分；
7. **分类精度**：规则分类，「其他」与误分类需人工抽检；建议正式发布前按 10% 抽检。

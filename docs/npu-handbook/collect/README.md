# 公开文档采集（collect/）

> 目标：为 NPU-Assistant 准备 **1000+ 份西北工业大学官方公开文档**，并保证覆盖广度（站点、主题、文档类型、受众、时效）。
> 结果：**1605 篇 / 59 个官方站点 / 24 个主题类目 / 6 种文档类型 / 210 万字**（2026-11-12 采集）。
> 分类口径见 [TAXONOMY.md](TAXONOMY.md)，统计见 [COLLECTION-REPORT.md](COLLECTION-REPORT.md) 与 [coverage.csv](coverage.csv)。

## 与既有 114 篇精选语料的关系

- `../corpus/` + `../sources.json`：此前人工挑选的 114 篇**精选**语料，已进入 Wiki 编译与 QA 评测链路；
- 本目录 `corpus/`：本轮批量采集的 **1605 篇**公开文档，独立存放，**不混入**既有 `corpus.json`，避免影响已冻结的 v2026.21 评测集；
- 后续可按 `manifest.jsonl` 中的「主题 × 类型」挑出制度规章/办事指南，逐步"提拔"进精选语料（promote 流程待做）。

## 目录结构

```text
collect/
├─ TAXONOMY.md             # 五维分类法与覆盖验收标准（参考了学生手册/校园 wiki 项目的分类）
├─ seeds.json              # 栏目种子：55 个站点、357 个列表页（轮转排序，广度优先）
├─ discovered.json         # --discover 的原始探测结果（64 个机构站点及其栏目）
├─ manifest.jsonl          # 脚本采集清单（每行一篇：url/title/分类/字数/sha256/文件）
├─ manual-shards/
│  ├─ raw/*.json           # 采集子智能体产出的原始分片（a1-a6）
│  ├─ imported.jsonl       # 分片导入后的清单
│  └─ ...
├─ corpus/<id>.md          # 1605 篇统一 Markdown（YAML front matter + 正文）
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

## 采集管线

```text
机构页（党政机关/教学科研机构/支撑保障）
  → --discover 探测站点首页栏目，统计列表页详情链接数 → discovered.json
  → 整理成 seeds.json（站点 × 栏目，轮转保证 breadth-first）
  → --fetch 按栏目抓详情页（httpx + BeautifulSoup，正文复用 services.importer.extract_article）
  → 子智能体补采难访问站点/专题（web_search + web_fetch → manual-shards/raw/*.json）
  → --import-shards 合并分片
  → --reclassify 补齐分类（只填「其他」，不覆盖已有结论）
  → --report 生成 COLLECTION-REPORT.md / coverage.csv
```

复现命令：

```powershell
.\.venv\Scripts\python.exe scripts\handbook_collect.py --discover
.\.venv\Scripts\python.exe scripts\handbook_collect.py --fetch --target 1600
.\.venv\Scripts\python.exe scripts\handbook_collect.py --import-shards
.\.venv\Scripts\python.exe scripts\handbook_collect.py --reclassify
.\.venv\Scripts\python.exe scripts\handbook_collect.py --report
```

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

## 合规与边界

- 只采集 `*.nwpu.edu.cn` 官方公开页面；**不登录、不绕过验证码、不抓附件 PDF/图片、不抓需要统一身份认证的页面**；
- 遵守 robots.txt；同域限速（默认 0.6s/次），并发 6；以 URL 去重，可断点续采；
- 正文由 `services/importer.extract_article` 提取（与线上「快速收录」同一套逻辑），不足 180 字丢弃；单篇截断 6000 字；
- 版权归西北工业大学及各原发布单位，仅用于教学演示与技术验证；引用时保留 `source_url`；
- 采集结果没有经过逐篇人工复核，分类为规则判定，**不作为对外承诺**；正式发布前建议按 10% 抽检。

## 已知缺口（下一轮优先补）

1. **WAF 站点**：`job.nwpu.edu.cn`（就业）、`zsb.nwpu.edu.cn`（本科招生）、`tushuguan.nwpu.edu.cn` / `library.nwpu.edu.cn`（图书馆）对脚本返回 412/403，需要可执行 JS 的抓取环境（Playwright/Chrome CDP）才能补；
2. **PDF/图片型正文**：大量制度（校园卡管理办法、医保政策、公寓流程、学位细则）只有 PDF 附件或扫描流程图，当前按规则跳过；可走 `scripts/ocr_manual_pdfs.py` 的 OCR 路线补；
3. **主题偏斜**：`其他` 占 33%，`纪律与申诉`(1)、`军训与国防`(3)、`生活服务`(4)、`校历与出行`(7)、`校园概况与机构`(7) 偏薄；主要因为对应内容多在 PDF/公众号；
4. **受众标注**：学院站点默认标「本科生」，口径偏粗，后续可按标题关键词细分。

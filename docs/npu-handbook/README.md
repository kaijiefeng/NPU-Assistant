# NPU 学生手册数据集（v0.2.0）

状态：**v2026.21 已发布：479 可答 + 10 不可答；新增王淼等辅导员个人 QA 与导师名单**。

## 当前规模

- 已入库文档：114 篇
- Wiki 词条：287
- 来源锚点：334
- v2026.21：479 可答 + 10 不可答

## 公开文档批量采集（collect/，2026-11-12）

- 目标：为知识库准备 **1000+ 份**官方公开文档，并保证覆盖广度（站点、主题、文档类型、受众、时效）；
- 结果：**1921 篇 / 66 个 `nwpu.edu.cn` 站点 / 24 个主题类目 / 6 种文档类型**，正文约 334 万字，发布时间跨 2013–2026 年；
- 位置：独立目录 `collect/`，**不混入**上面 114 篇精选语料，也不影响已冻结的 v2026.21 QA 数据集；
- 分类法：`collect/TAXONOMY.md`（参考高校学生手册目录、《瓜兵速成指南》、USC Wiki 等相似项目的分类方式，采用「主题 × 文档类型 × 受众 × 发布单位 × 时效」五维模型）；
- 统计与完整性：`collect/COLLECTION-REPORT.md`、`collect/coverage.csv`、`collect/COMPLETENESS-AUDIT.md`（67 项清单，55 项已有独立文档）、`collect/README.md`；
- 采集器：`scripts/handbook_collect.py`（发现 → 采集 → 分片导入 → 重分类 → 报告 → 审计，支持 `--urls` 定向补采）、`scripts/list_docs_collect.py`（列表页 → 详情 → PDF）、`scripts/site_crawl.py`（信息公开网等公开站点整站采集）；被 WAF 拦截的站点由 `scripts/browser_collect.py`（真实 Chrome + CDP 渲染）补采；PDF 附件由 `scripts/attachment_collect.py` 提取；需要登录的资料按「安全约定」用人工参与的电脑操作方式小批量补齐。

## 文件

| 文件 | 作用 |
|---|---|
| `sources.json` | 108 篇已入库文档 + 27 个待补官方来源 |
| `coverage.csv` | 类别覆盖统计 |
| `INVENTORY.md` | M2 盘点摘要 |
| `schema.json` | QA 数据格式 |
| `corpus/` | 统一 Markdown 语料（带页码/来源） |
| `corpus.json` | 语料清单 |
| `qa-pilot.jsonl` | 50 条试点可答 QA |
| `qa-pilot-negative.jsonl` | 10 条试点不可答 QA |
| `QA-REVIEW.md` / `QA-REVIEW-RESULT.md` | 试点复核表与结果 |
| `qa-dataset.jsonl` | v2026.1：300 条冻结可答 QA |
| `qa-dataset-negative.jsonl` | v2026.1：10 条冻结不可答 QA |
| `DATASET.md` / `DATASET-CHECK.md` | v2026.1 统计与校验 |
| `M6-REPORT.md` / `M6-REPORT.json` | v2026.1 评测 |
| `qa-dataset-v2026.2.jsonl` | v2026.2：389 条可答 |
| `qa-dataset-negative-v2026.2.jsonl` | v2026.2：10 条不可答 |
| `DATASET-V2026.2.md` | v2026.2 统计 |
| `M6-REPORT-v2026.2.md` / `.json` | v2026.2 评测 |
| `qa-dataset-v2026.3.jsonl` | v2026.3：408 条可答 |
| `qa-dataset-negative-v2026.3.jsonl` | v2026.3：10 条不可答 |
| `DATASET-V2026.3.md` | v2026.3 统计 |
| `M6-REPORT-v2026.3.md` / `.json` | v2026.3 评测 |
| `qa-dataset-v2026.4.jsonl` | v2026.4：422 条可答 |
| `qa-dataset-negative-v2026.4.jsonl` | v2026.4：10 条不可答 |
| `DATASET-V2026.4.md` | v2026.4 统计 |
| `M6-REPORT-v2026.4.md` / `.json` | v2026.4 评测 |
| `qa-dataset-v2026.5.jsonl` | v2026.5：422 条可答 |
| `qa-dataset-negative-v2026.5.jsonl` | v2026.5：10 条不可答 |
| `M6-REPORT-v2026.5.md` / `.json` | v2026.5 评测（98%） |
| `qa-supplement-ic8.jsonl` | v2026.6-draft：联合实验室/校企合作 4 条补充 QA |
| `qa-dataset-v2026.6.jsonl` | v2026.6：426 条可答 |
| `qa-dataset-negative-v2026.6.jsonl` | v2026.6：10 条不可答 |
| `M6-REPORT-v2026.6.md` / `.json` | v2026.6 评测（100%） |
| `qa-dataset-v2026.7.jsonl` | v2026.7：429 条可答 |
| `qa-dataset-negative-v2026.7.jsonl` | v2026.7：10 条不可答 |
| `M6-REPORT-v2026.7.md` / `.json` | v2026.7 评测（100%） |
| `qa-dataset-v2026.8.jsonl` | v2026.8：441 条可答 |
| `qa-dataset-negative-v2026.8.jsonl` | v2026.8：10 条不可答 |
| `M6-REPORT-v2026.8.md` / `.json` | v2026.8 评测（100%） |
| `qa-dataset-v2026.9.jsonl` | v2026.9：443 条可答 |
| `qa-dataset-negative-v2026.9.jsonl` | v2026.9：10 条不可答 |
| `M6-REPORT-v2026.9.md` / `.json` | v2026.9 评测（98%） |
| `qa-dataset-v2026.10.jsonl` | v2026.10：447 条可答 |
| `qa-dataset-negative-v2026.10.jsonl` | v2026.10：10 条不可答 |
| `M6-REPORT-v2026.10.md` / `.json` | v2026.10 评测（100%） |
| `qa-dataset-v2026.11.jsonl` | v2026.11：451 条可答 |
| `qa-dataset-negative-v2026.11.jsonl` | v2026.11：10 条不可答 |
| `M6-REPORT-v2026.11.md` / `.json` | v2026.11 评测（100%） |
| `qa-dataset-v2026.12.jsonl` | v2026.12：457 条可答 |
| `qa-dataset-negative-v2026.12.jsonl` | v2026.12：10 条不可答 |
| `M6-REPORT-v2026.12.md` / `.json` | v2026.12 评测（100%） |
| `qa-dataset-v2026.13.jsonl` | v2026.13：460 条可答 |
| `qa-dataset-negative-v2026.13.jsonl` | v2026.13：10 条不可答 |
| `M6-REPORT-v2026.13.md` / `.json` | v2026.13 评测（100%） |
| `qa-dataset-v2026.14.jsonl` | v2026.14：463 条可答 |
| `qa-dataset-negative-v2026.14.jsonl` | v2026.14：10 条不可答 |
| `M6-REPORT-v2026.14.md` / `.json` | v2026.14 评测（100%） |
| `qa-dataset-v2026.15.jsonl` | v2026.15：466 条可答 |
| `qa-dataset-negative-v2026.15.jsonl` | v2026.15：10 条不可答 |
| `M6-REPORT-v2026.15.md` / `.json` | v2026.15 评测（100%） |
| `qa-dataset-v2026.16.jsonl` | v2026.16：469 条可答 |
| `qa-dataset-negative-v2026.16.jsonl` | v2026.16：10 条不可答 |
| `M6-REPORT-v2026.16.md` / `.json` | v2026.16 评测（100%） |
| `qa-dataset-v2026.17.jsonl` | v2026.17：478 条可答 |
| `qa-dataset-negative-v2026.17.jsonl` | v2026.17：10 条不可答 |
| `M6-REPORT-v2026.17.md` / `.json` | v2026.17 评测（100%） |
| `qa-dataset-v2026.19.jsonl` | v2026.19：467 条可答（精简鸿蒙内容） |
| `qa-dataset-negative-v2026.19.jsonl` | v2026.19：10 条不可答 |
| `M6-REPORT-v2026.19.md` / `.json` | v2026.19 评测（100%） |
| `qa-dataset-v2026.20.jsonl` | v2026.20：473 条可答（含教师/辅导员/行政人员） |
| `qa-dataset-negative-v2026.20.jsonl` | v2026.20：10 条不可答 |
| `M6-REPORT-v2026.20.md` / `.json` | v2026.20 评测（100%） |
| `qa-dataset-v2026.21.jsonl` | v2026.21：479 条可答（含王淼等辅导员个人 QA） |
| `qa-dataset-negative-v2026.21.jsonl` | v2026.21：10 条不可答 |
| `M6-REPORT-v2026.21.md` / `.json` | v2026.21 评测（100%） |
| `release/` | v2026.1 / v2026.3 / v2026.4 / v2026.5 / v2026.6 / v2026.7 / v2026.8 / v2026.9 / v2026.10 / v2026.11 / v2026.12 / v2026.13 / v2026.14 / v2026.15 / v2026.16 / v2026.17 / v2026.19 / v2026.20 / v2026.21 发布文件与 dataset card |
| `OPEN-SOURCE-SOURCES.md` | NWPU 相关开源资料调研（仅覆盖参考） |
| `qa-supplement*.jsonl` | 补充 QA 草案与已复核记录 |
| `SUPPLEMENT*.md` | 补充说明 |

## 数据来源

只使用西北工业大学官方公开资料：

- `*.nwpu.edu.cn` 官网页面；
- 西工大校内官方公众号；
- 官网附件/学院公开 PDF、DOC、DOCX。

重点补充：

- 集成电路学院（微电子学院）：学院简介、本科专业、学科、科研平台、科研团队、导师、竞赛、就业、铸剑班、学生会、社团；
- 鸿蒙/手机系统：鸿蒙 OS 菁英班、鸿蒙 PC 实验局、软件学院鸿蒙宣讲；
- 本科生制度：GPA、选课、体测评分标准。

参考的开源项目只用于分类法、评测方法和字段设计，**未导入任何外校内容**。

## 构建流程

```text
114 篇官方来源
  → 统一 Markdown 语料（来源、sha256、页码）
  → Wiki 词条页面级 QA
  → 自动校验 + 引用命中复核
  → v2026.21：479 可答 + 10 不可答
  → 检索/引用/拒答评测
```

## 评测结果（v2026.21）

| 指标 | 结果 |
|---|---:|
| 检索文档召回 | **100%**（479/479） |
| 端到端引用文档召回 | **100%**（50/50 抽样） |
| 不可答拒答率 | **100%**（10/10） |
| 检索平均耗时 | 128 ms |
| 回答平均耗时 | 2273 ms |

## 已知局限

- 答案来自 Wiki 摘要，不是逐字法条；
- 引用召回为 50 条抽样，不是 479 条全量；
- 完整本科生/研究生学生手册未在公开网页找到；
- 发布前建议人工抽查 10%。

## 生成与校验

```powershell
.\.venv\Scripts\python.exe scripts\handbook_inventory.py
.\.venv\Scripts\python.exe scripts\handbook_extract.py
.\.venv\Scripts\python.exe scripts\handbook_page_dataset.py
.\.venv\Scripts\python.exe scripts\handbook_dataset_check.py
.\.venv\Scripts\python.exe scripts\eval_handbook_v2026_4.py --chat-limit 50
```

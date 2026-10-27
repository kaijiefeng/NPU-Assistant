# NPU 学生手册数据集（v0.2.0）

状态：**v2026.1 已发布；v2026.2-draft 已补充 23 篇官方文档 + 100 条 QA 草案（含集成电路学院/鸿蒙专题）**。

- 已入库文档：98 篇
- Wiki 词条：252
- 来源锚点：297
- v2026.1：300 可答 + 10 不可答
- 检索文档召回：100%
- 引用文档召回：98%
- 拒答率：100%
- 集成电路/鸿蒙补充 QA 引用命中：25/26（96.2%）

## 文件

| 文件 | 作用 |
|---|---|
| `sources.json` | 84 篇已入库文档 + 27 个待补官方来源 |
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
| `DATASET.md` / `DATASET-CHECK.md` | 冻结数据集统计与校验 |
| `M6-REPORT.md` / `M6-REPORT.json` | 检索/引用/拒答评测 |
| `release/` | v2026.1 发布文件与 dataset card |
| `OPEN-SOURCE-SOURCES.md` | NWPU 相关开源资料调研（仅覆盖参考） |
| `qa-supplement.jsonl` | v2026.2-draft：GPA/选课/体测 16 条补充 QA |
| `qa-supplement-ic.jsonl` | v2026.2-draft：集成电路/鸿蒙 26 条补充 QA |
| `SUPPLEMENT.md` / `SUPPLEMENT-IC.md` | 补充说明 |

## 数据来源

只使用西北工业大学官方公开资料：

- `*.nwpu.edu.cn` 官网页面；
- 西工大校内官方公众号；
- 官网附件/学院公开 PDF、DOC、DOCX。

参考的开源数据集/项目（HUST-Student-Handbook2、CampusIQ、Campus-AI-RAG、university-information）只用于分类法、评测方法和字段设计，**未导入任何外校内容**。

## 构建流程

```text
98 篇官方来源
  → 统一 Markdown 语料（来源、sha256、页码）
  → Wiki 词条页面级 QA（每页 2–3 个问法）
  → 答案取词条摘要，quote 取来源片段
  → 自动校验 + 页面级筛选
  → 300 可答 + 10 不可答冻结
  → 检索/引用/拒答评测
  → v2026.1 发布
  → v2026.2-draft：补充 23 篇官方文档 + 100 条 QA
```

## 评测结果（M6）

| 指标 | 结果 |
|---|---:|
| 检索文档召回 | **100%**（300/300） |
| 端到端引用文档召回 | **98%**（49/50 抽样） |
| 不可答拒答率 | **100%**（10/10） |
| 检索平均耗时 | 82 ms |
| 回答平均耗时 | 2326 ms |

## 已知局限

- 答案来自 Wiki 摘要，不是逐字法条；
- 问题围绕 Wiki 词条，细粒度条款问答覆盖仍有限；
- 完整本科生/研究生学生手册未在公开网页找到；
- 发布前建议人工抽查 10%。

## 生成与校验

```powershell
.\.venv\Scripts\python.exe scripts\handbook_inventory.py
.\.venv\Scripts\python.exe scripts\handbook_extract.py
.\.venv\Scripts\python.exe scripts\handbook_qa_pilot.py
.\.venv\Scripts\python.exe scripts\handbook_qa_check.py
.\.venv\Scripts\python.exe scripts\handbook_page_dataset.py
.\.venv\Scripts\python.exe scripts\handbook_dataset_check.py
.\.venv\Scripts\python.exe scripts\eval_handbook_dataset.py --chat-limit 50
```

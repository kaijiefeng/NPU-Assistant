# NPU 学生手册数据集（v0.2.0）

状态：**M2 来源盘点完成，M3 语料抽取完成，M4 试点 QA 草案已生成（50 条，待人工复核）**。

## 文件

| 文件 | 作用 |
|---|---|
| `sources.json` | 当前 75 篇已入库文档 + 27 个待补官方来源 |
| `coverage.csv` | 类别覆盖统计 |
| `INVENTORY.md` | M2 盘点摘要 |
| `schema.json` | QA 数据格式 |
| `corpus/` | 统一 Markdown 语料（带页码/来源） |
| `corpus.json` | 语料清单 |
| `qa-pilot.jsonl` | 50 条试点 QA 草案 |
| `QA-PILOT.md` | 试点 QA 统计 |
| `QA-CHECK.md` | 引用/来源自动校验（当前 PASS） |

## M2 结论

- 当前 75 篇真实文档，分类后仍有明显缺口；
- P0 待补：本科生学生手册、研究生手册、学位授予细则、最新违纪处分办法、校园卡最新版；
- P1 待补：转专业/考试办法、申诉、学术不端、勤工助学、公寓/消防、医保、就业/档案、网络、图书、安全户籍、迎新；
- 所有已入库来源均为西工大官网/校内公众号，无外校数据。

## 生成

```powershell
.\.venv\Scripts\python.exe scripts\handbook_inventory.py
.\.venv\Scripts\python.exe scripts\handbook_extract.py
```

## 下一步

1. 人工复核 50 条试点 QA（重点核对答案是否完全由 quote 支撑）；
2. 按 `sources.json` 的 P0/P1 目标抓取官方文件；
3. 复核通过后扩到 300–500 条；
4. 冻结评测集并接入现有 LLM Wiki。

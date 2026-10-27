# 数据来源与源文件管理

## 一、当前数据审计（2026-10-27）

演示库：

| 类别 | 数量 |
|---|---:|
| 文档总数 | **75** |
| 真实来源文档 | **75** |
| 示例/合成文档 | **0** |
| Wiki 词条 | **203** |
| 来源锚点 | **241** |

### 真实来源 75 篇

| 类型 | 数量 | 说明 |
|---|---:|---|
| 网页导入 | **59** | `source_url` 指向学校官网/信息公开/学工/心理/教务/就业/后勤/体育/保卫等页面 |
| 本地文件 | **9** | PDF / DOCX / DOC，来自学院资料下载或预置文件 |
| 文本收录 | **7** | 从官方页面清洗摘录的短文档（请假、考试、转专业、心理、三方、食堂、快递） |

59 篇网页/链接来源覆盖学工部、研究生院、心理中心、信息公开、教务部、民航学院、校园公告、就业网，以及本轮 TDD 补齐新增的信息化处、迎新网、校医院、网络空间安全学院、电子信息学院、计划财务部、新闻网、体育部、保卫处等；具体来源见 [eval/REALWORLD-TDD.md](eval/REALWORLD-TDD.md)。

9 篇本地真实文件：

- 西北工业大学知识产权声明页（材料学院资料下载）
- 西北工业大学博士后进站审核表（非定向）
- 西北工业大学在校学习基本情况表（材料学院资料下载）
- 西北工业大学学位四会关于学位论文评阅意见的处理办法（修订版）
- 材料学院教授、副教授为本科生授课管理办法
- 西北工业大学材料学院关于研究生培养和招生增加国际化要求的规定
- 西北工业大学研究生课程教学管理规定
- 西北工业大学校园卡管理办法（2026 修订，计划财务部 PDF）
- 西北工业大学本科生学籍管理办法（2024 修订，信息公开 PDF）

另有 7 篇“文本收录”短文档：请假销假、补考缓考重修、转专业、心理咨询预约、三方协议、食堂、快递等，均从上述官方来源清洗摘录。

### 示例已清理

2026-10-27 已删除全部 10 篇示例文档、示例词条、示例源文件与 `backend/data/seed_docs/` 下的示例种子；当前库 **只保留 75 篇真实来源**。

清理前的备份在 `backups/pre-example-cleanup-20261027-092143/`（数据库、seed、uploads、seed_docs），如需回滚可使用。

**结论：当前演示库所有文档都来自真实校园公开资料。**

## 二、源文件怎么管理

### 1. 数据库元数据

`backend/data/app.db` 的 `documents` 表保存：

- `title` / `filename` / `file_ext` / `size_bytes`
- `stored_path`：运行时的物理文件路径
- `source_type`：`url` / `file`
- `source_url`：网页导入的原始 URL
- `tags`：如“真实数据”“示例”
- `status`：`ready` / `needs_review` / `failed`

Wiki 词条通过 `wiki_sources` 关联回文档，引用中能看到 `document_title`、`section`、`quote`。

### 2. 物理文件目录

| 目录 | 内容 | 是否入库 Git |
|---|---|---|
| `backend/data/uploads/` | 上传/导入后的运行时文件副本 | 否，gitignored |
| `backend/data/campus_seed/files/` | 预置真实源文件（PDF/DOC/DOCX） | 否，gitignored |
| `backend/data/campus_manual/files/` | 人工/自动下载的 PDF/XLSX 原件 | 否，gitignored |
| `backend/data/campus_manual/ocr/` | 扫描 PDF 的 OCR 文本 | 否，gitignored |
| `backend/data/seed_docs/` | 演示源文件目录（示例已清理，仅保留 `.gitkeep`） | 否，gitignored |

### 3. 仓库里保留的“来源清单”

| 文件 | 作用 |
|---|---|
| `docs/campus-seed/manifest.json` | 真实文件清单：标题、下载页、下载 URL、sha256、抓取时间 |
| `docs/campus-seed/manual-queue.json` | 待人工处理的链接队列 |
| `docs/campus-seed/manual-queue-results.json` | 队列处理结果 |
| `docs/CAMPUS-SEED.md` | 真实数据来源与导入说明 |
| `backend/tests/fixtures/campus/` | 测试用真实格式样本，不进入生产库 |

这样做的原则：**仓库只提交来源清单和文档，不提交运行时的原始文件/数据库**。

### 4. 删除与重建

- UI「源文件 → 删除」会删除文档记录、独占词条和物理文件；
- 共享词条只移除该文件的来源锚点，不删除其他文件的贡献；
- 删除后可通过「重新编译」从保留的源文件重建。

## 三、网站上看不到内容的原因

当前“内容管理”页默认在「收录资料」Tab，这个 Tab 只显示上传/链接输入框，下面没有内容列表；**源文件列表在「源文件」Tab**，词条在「词条维护」Tab。

已做的改进：

- 「源文件」表格新增「来源」列：
  - 网页：显示 `网页` + 原始域名链接，可点开原文；
  - 本地真实文件：显示 `本地`；
  - 示例：已全部清理，不再出现；
- 静态壳缓存版本升到 `v7`，刷新后会加载新表格；
- UI 冒烟检查通过，六个页面 console 0 错误。

如果仍然看不到：

1. 点「源文件」Tab；
2. 强制刷新 `Ctrl+F5`；
3. 或在浏览器 DevTools → Application → Service Workers 里 Unregister 后刷新；
4. API 侧可用 `GET /api/knowledge/documents` 确认返回 75 条。

## 四、已知内容缺口

- 《西北工业大学校历、校车时刻表》：Wiki 词条 `needs_review`，正文只有导航；
- 《西北工业大学本科生学籍管理办法》：Wiki 词条 `needs_review`，正文只有导航/附件入口。

这两篇源文件是真实的，但**编译出的词条不可回答**，已标记为评测 `known_gap`，需要人工复核/OCR/附件补全。

## 五、建议

1. 当前已清理 10 篇示例，只保留 75 篇真实来源；后续导入不要引入示例；
2. 对 `needs_review` 的两篇做人工复核；
3. 需要“下载原始文件”时，再增加受管理员鉴权的下载接口；
4. 所有网页导入保留 `source_url`，引用展示原始链接与抓取时间；
5. 定期跑 `docs/eval/hybrid_golden.json` 与 `scripts/eval_hybrid.py` 回归。

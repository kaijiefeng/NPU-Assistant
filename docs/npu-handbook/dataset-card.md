# Dataset Card — NPU Student Handbook QA（草案）

## 基本信息

- 名称：NPU Student Handbook QA
- 版本：2026.1-draft
- 语言：中文
- 任务：校园制度/学生手册问答、引用回溯、拒答
- 状态：M2/M3 进行中，尚未冻结

## 范围

只覆盖西北工业大学官方公开资料：

- 本科生/研究生学生手册与规章制度；
- 学工部、教务部、研究生院、校医院、保卫处、后勤、体育部等公开制度；
- 生活服务指南（宿舍、洗浴、洗衣、餐饮、快递、校车、校园卡、网络等）。

不包含：其他高校数据、学生个人信息、未公开内部文件。

## 数据结构

每条记录见 `schema.json`，包含：

- question / answer / category / answerable；
- source：document_title、source_url、page、section、quote、sha256、effective_date；
- review：draft/approved/rejected；
- version。

## 统计（草案）

| 指标 | 当前 |
|---|---:|
| 已入库来源文档 | 75 |
| 抽取语料 | 75 篇 / 约 13.5 万字 |
| QA 对 | 50 条试点草案（自动引用校验 PASS，待人工复核） |
| 类别 | 13 类（试点覆盖） |

## 质量门禁

- 每条答案 100% 有 quote；
- 引用准确率 ≥ 95%；
- 无依据断言 = 0；
- 不可答题必须拒答；
- P0 人工复核 100%，整体 ≥ 30%。

## 许可与来源

- 仅使用西工大公开资料，保留原始 URL、sha256、抓取时间；
- 数据集发布前需确认学校对公开资料再分发的许可；
- 不复制 HUST/CampusIQ/university-information 等外部数据集内容。

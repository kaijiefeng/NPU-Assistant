# 外部数据使用边界与审计

## 一、结论

**没有把其他学校的数据集/内容录入本地知识库。**

本轮参考的开源项目只用于“覆盖清单”和测试设计：

- HUST-Student-Handbook2：只参考其学生手册问答的**领域分类**；
- CampusIQ：只参考其“可答/不可答”评测设计；
- university-information：只参考其大学生活质量 FAQ 的**问题条目**；
- Campus-AI-RAG：只参考校园 RAG 的工程结构。

这些项目的内容、问答对、文档均未写入 `documents`、`wiki_pages` 或 `wiki_seed.json`。

## 二、本地库允许的来源

| 类型 | 允许范围 |
|---|---|
| 官网页面 | `*.nwpu.edu.cn`（学工部、研究生院、教务部、心理中心、信息公开、后勤、校医院、体育部、保卫处等） |
| 公众号 | 仅西工大官方/校内单位公众号文章（当前 2 篇：电子信息学院、西工大微后勤） |
| 本地文件 | 西工大官网附件、学院资料下载、预置真实文件 |
| 文本摘录 | 从上述西工大官方来源清洗出的短文档，正文保留“来源”说明 |

## 三、审计结果（2026-10-27）

运行：

```powershell
.\.venv\Scripts\python.exe scripts\audit_sources.py
```

结果：

```json
{
  "documents": 75,
  "non_nwpu_url_docs": 0,
  "issues": [],
  "seed_documents": 75,
  "seed_issues": []
}
```

- 75 篇文档中，所有带 URL 的来源均为 `*.nwpu.edu.cn` 或西工大校内公众号；
- 冷启动快照 `wiki_seed.json` 的 75 篇文档同样无外校 URL；
- 未发现“华中科技/HUST/CampusIQ/ModelScope/GitHub”等外部数据集内容进入正文。

## 四、持续约束

1. 新增 URL 只接受 `*.nwpu.edu.cn`；
2. 公众号只接受西工大官方/校内单位账号，且保留原文链接；
3. 外部数据集只能用于出题/覆盖检查，不能直接导入；
4. 每次补库后运行 `scripts/audit_sources.py`，有非白名单来源即失败；
5. 文本摘录必须写明“来源：西北工业大学……”，便于人工复核。

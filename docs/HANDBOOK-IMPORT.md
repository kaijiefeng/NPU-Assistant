# 采集语料导入知识库报告

- 生成时间：2026-11-15 19:05:33
- 数据库：`backend\data\app.db`
- 词条：**3686**　文档：**2618**
- 已提交采集文件：**2482**（去重后）

## 任务队列

| 状态 | 数量 |
|---|---:|
| succeeded | 2504 |

## 编译构建

| 状态 | 数量 | 新建词条 | 更新词条 |
|---|---:|---:|---:|
| failed | 8 | 41 | 9 |
| ready | 2740 | 4204 | 1582 |
| running | 1 | 0 | 0 |

## 最近失败构建

| 文件 | 开始时间 | 错误 |
|---|---|---|
| 75D3D163C8B8CCD3B950E02AA12_3BF25C39_13B9D8.pdf | 2026-10-27T09:24:43 | FOREIGN KEY constraint failed |
| FA1980259CCFCEB793A7B2C36FD_54DB6AA1_DF187.pdf | 2026-10-27T09:24:01 | FOREIGN KEY constraint failed |
| xgb-zdgk-10610.md | 2026-10-27T09:22:59 | FOREIGN KEY constraint failed |
| xgb-zdgk-13418.md | 2026-10-27T09:22:20 | FOREIGN KEY constraint failed |
| 99636A5BCE1773BD7430977A235_A60C1D8B_48658.pdf | 2026-10-27T09:21:39 | FOREIGN KEY constraint failed |
| 西工大-研究生课程教学管理规定.pdf | 2026-10-27T09:20:17 | FOREIGN KEY constraint failed |
| — | 2026-10-27T09:18:44 | FOREIGN KEY constraint failed |
| — | 2026-10-27T09:18:10 | FOREIGN KEY constraint failed |

## 复跑方式

```powershell
.\scripts\run.ps1
.\\.venv\\Scripts\\python.exe scripts\\import_handbook_collect.py --apply --min-chars 0 --skip-type __none__
.\\.venv\\Scripts\\python.exe scripts\\handbook_import_report.py
```

导入脚本按文件去重、跳过已入库；进度记录在 `docs/npu-handbook/collect/imported-app.jsonl`。

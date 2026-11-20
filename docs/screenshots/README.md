# 界面截图

`v0.2.2/` 为当前发布使用的界面截图（隔离演示数据，2026-11-20）：

| 文件 | 页面 | 内容 |
|------|------|------|
| `chat.png` | 对话 | 两级导航、居中阅读列、双层引用；右侧管理助手常驻 |
| `knowledge.png` | 知识库 | 搜索、标签筛选、词条卡片 |
| `ops-tasks.png` | 运维管理 → 任务 | 紧凑任务列表、状态筛选、进度与失败原因 |
| `settings.png` | 设置 | 助手 / 模型服务 / 机器人 / 账号分组导航 |
| `chat-mobile.png` | 手机端对话 | 390×844 移动布局与快捷问题 |

`e2e/` 为 RSI 端到端证据，可通过 `scripts/e2e_verify.py --publish` 重新生成；历次改版的临时验收截图不随发布包保留。

## 重新生成

`scripts/ui_check.py` 通过 Chrome / Edge DevTools Protocol 做前端交互冒烟并生成页面截图：

```powershell
.\scripts\run.ps1
.\.venv\Scripts\python.exe scripts\ui_check.py
```

截图仅用于文档展示；请勿包含真实 API Key、学生个人信息、内网地址或个人目录。发布前应使用隔离演示数据，而不是正式运营库。

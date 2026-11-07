# 界面截图

本目录随仓库提供八个页面的界面截图（1440×960），由 `scripts/ui_check.py` 通过 Chrome DevTools Protocol 自动生成：

| 文件 | 对应页面 | 内容 |
|------|----------|------|
| `chat.png` | `#/chat` 对话 | 带 `[1]` 引用的回答 + 可展开的「来源 N」行（词条 + 来源文件·章节 + 原文摘录） |
| `knowledge.png` | `#/knowledge` 知识库 | 浏览视图：搜索、分类筛选、词条卡片 |
| `manage.png` | `#/knowledge?manage=1` 内容管理 | 收录资料 / 源文件 / 词条维护三个 Tab；快速收录与编译状态 |
| `models.png` | `#/settings?tab=models` 设置 → 模型 | 模型 / Provider 双 Tab；Provider 卡片（Key 脱敏）+ 模型列表与默认模型高亮 |
| `bots.png` | `#/settings?tab=bots` 设置 → 机器人 | 通道 / 调试 / 日志三个 Tab；通道卡片 + Webhook 地址 + 控制台模拟对话 |
| `settings.png` | `#/settings` 设置 | 助手设置：基础 / 移动访问与安全 / 回复与引用；人设、令牌与 Wiki 参数 |
| `jobs.png` | `#/ops?tab=jobs` 运营 → 任务与告警 | 后台任务进度与告警处理 |
| `audit.png` | `#/ops?tab=audit` 运营 → 审计日志 | 账号、操作、对象、结果与 IP 追溯 |

截图由 `scripts/ui_check.py` 在 **LLM Wiki 版**界面下生成；如界面有更新，按下方命令重跑即可覆盖。

## 重新生成

前置条件：NPU-Assistant 已在 <http://127.0.0.1:8000> 启动，本机安装 Chrome（或 Edge）。

```powershell
# 1) 启动服务（另开一个终端）
.\scripts\run.ps1

# 2) 生成截图 + 前端交互冒烟（会检查 8 个页面的控制台错误）
.\.venv\Scripts\python.exe scripts\ui_check.py
```

脚本会输出各页面交互检查结果，并把截图写入本目录；如需换地址，请修改 `scripts/ui_check.py` 顶部的 `BASE` 常量。

注意：截图仅用于文档展示，请勿包含真实 API Key、学生个人信息或内网地址。

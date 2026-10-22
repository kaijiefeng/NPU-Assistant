# 界面截图

本目录随仓库提供五个页面的界面截图（1440×960），由 `scripts/ui_check.py` 通过 Chrome DevTools Protocol 自动生成：

| 文件 | 对应页面 | 内容 |
|------|----------|------|
| `chat.png` | `#/chat` 对话 | 带 `[1][2][3]` 引用的回答 + 展开的**双层引用卡片**（词条 + 来源文件·章节 + 原文摘录）+ 历史会话侧栏 |
| `knowledge.png` | `#/knowledge` 知识库 | 「词条 / 源文件」双 Tab：Wiki 目录与搜索、来源锚点、源文件编译状态 |
| `models.png` | `#/models` 模型 | Provider 卡片（Key 脱敏）+ 模型列表与默认模型高亮 |
| `bots.png` | `#/bots` 机器人 | 三个通道卡片 + Webhook 地址 + 控制台模拟对话 |
| `settings.png` | `#/settings` 设置 | 分身名称、系统提示词、Wiki 导航页数（`wiki_max_pages`）、回答上下文字数（`wiki_answer_max_chars`）与引用开关 |

截图由 Lead 在 **LLM Wiki 版**界面下用 `scripts/ui_check.py` 重新生成；如界面有更新，按下方命令重跑即可覆盖。

## 重新生成

前置条件：NPU-Assistant 已在 <http://127.0.0.1:8000> 启动，本机安装 Chrome（或 Edge）。

```powershell
# 1) 启动服务（另开一个终端）
.\scripts\run.ps1

# 2) 生成截图 + 前端交互冒烟（会检查 5 个页面的控制台错误）
.\.venv\Scripts\python.exe scripts\ui_check.py
```

脚本会输出各页面交互检查结果，并把截图写入本目录；如需换地址，请修改 `scripts/ui_check.py` 顶部的 `BASE` 常量。

注意：截图仅用于文档展示，请勿包含真实 API Key、学生个人信息或内网地址。

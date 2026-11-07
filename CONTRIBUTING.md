# 贡献指南

感谢参与 NPU-Assistant。提交代码前请阅读以下约定。

## 开发环境

- Python 3.11+
- 无需 Node.js / 前端构建步骤；前端是原生 HTML、CSS、ES Module
- Windows 推荐 PowerShell，Linux/macOS 使用等效命令

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\scripts\run.ps1
```

服务默认监听 `http://127.0.0.1:8000`。前端静态文件由 FastAPI 直接托管，修改后刷新页面即可。

## 提交前检查

```powershell
# 后端全量测试（工作目录 backend）
cd backend
..\.venv\Scripts\python.exe -m pytest -q

# 前端语法检查（可选，需 Node.js）
node --check ..\frontend\assets\js\app.js
```

涉及界面的改动，建议在服务运行时执行：

```powershell
.\.venv\Scripts\python.exe scripts\ui_check.py
```

## 代码约定

- 后端使用标准库 `sqlite3` 与薄封装，不引入 ORM；新表/字段需兼容旧数据库迁移。
- 前端保持无构建、无 CDN；新增视图使用 ES Module，复用 `assets/js/ui.js` 与现有样式变量。
- API Key、AppSecret、管理令牌只存服务端；接口返回必须脱敏。
- 不在仓库提交 `.env`、`app.db`、上传文件、日志或真实凭据。
- 校园公开资料需注明来源；不要提交个人隐私或未公开数据。

## Pull Request

1. 从 `develop` 切出分支，保持改动聚焦。
2. 说明变更目的、用户影响与验证方式。
3. 行为变更需补测试；界面变更附截图。
4. 提交信息建议使用 `feat:`、`fix:`、`docs:`、`chore:` 前缀。

## 安全问题

不要用公开 Issue 报告漏洞，请按 [SECURITY.md](SECURITY.md) 私下联系维护者。

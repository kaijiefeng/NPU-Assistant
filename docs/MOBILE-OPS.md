# NPU-Assistant 移动端运维与快速收录（v0.1.2 设计/操作手册）

> 面向学生/辅导员的**图文使用手册（含短动图）**：[frontend/manual/index.html](../frontend/manual/index.html)（运行服务后访问 `/manual/index.html`）。本文档偏部署/实现说明。
>
> v0.1.1 目标：**手机就能运维**——查看状态、上传文件、把网页/公众号文章链接、微信/QQ 里转发的文字，一键收录进 LLM Wiki；QQ/微信机器人管理员转发链接即可自动入库。
> 本文档同时是**实现契约**：API 字段、机器人命令、PWA、安全边界。接口实现后以本文档为准。

---

## 1. 总体方案

```
手机浏览器 / PWA ──HTTPS──┐
微信/QQ 机器人（管理员）──┴──> NPU-Assistant FastAPI（单机）
                                  ├─ 快速收录 API：URL 抓取 → 正文提取 → 源文件 → LLM 编译词条
                                  ├─ 机器人入库：管理员转发链接/文字 → 入库 → 回复结果（微信即时 ack）
                                  ├─ LLM Wiki / 模型 / 机器人 / 设置 管理页（响应式）
                                  └─ SQLite + uploads（文档副本）+ wiki_seed 快照
```

访问方式（按场景选一个）：
> **同一个服务**：移动端、桌面 Web、PWA、API、机器人回调都由同一个 FastAPI 进程提供，没有单独的移动端服务/端口。默认启动只监听本机 `127.0.0.1`；需要手机访问时加 `-Lan`（自动绑定 `0.0.0.0` 并生成令牌），即「总服务与移动端一起启动」。

| 方式 | 适用 | HTTPS | 成本 | 说明 |
|---|---|---|---|---|
| **扫码（v0.1.1 默认）** | 电脑启动服务后，手机扫码打开 | 看地址 | 0 | `.\scripts\run.ps1 -Lan` → 电脑点右上角「手机扫码」→ 手机扫码；可一键开公网 HTTPS（临时隧道） |
| 局域网 | 手机与电脑同一 Wi-Fi，先跑通 | 否 | 0 | `.\scripts\run.ps1 -Lan`，手机访问 `http://<电脑IP>:8000`；PWA 不能安装（非 HTTPS），但网页可用 |
| 公网隧道（推荐试用） | 手机 4G/5G、公众号回调、PWA 安装 | 是 | 0 | 扫码弹窗内「一键开启公网访问」（Cloudflare 临时隧道），或 Cloudflare Tunnel / ngrok / frp |
| 云服务器/VPS | 7×24 生产 | 是 | 低 | systemd + Caddy/Nginx 反代 + 备份；手机随时访问 |

### 1.1 扫码联网（默认方式，v0.1.1）

电脑浏览器点右上角 **「手机扫码」** 打开弹窗：

- 二维码默认编码「公网 HTTPS > 局域网」中优先级最高的地址；可在弹窗里切换不同网卡地址，二维码同步刷新。
- 手机扫码 → 浏览器打开 → 需要管理操作时到「设置 → 移动访问与安全」填入令牌；HTTPS 地址可安装 PWA。
- **服务端未启用令牌时**：设置页输入 ≥16 位随机令牌并保存，会调用 `POST /api/system/admin-token/init` 在本机立即启用并持久化（无需重启），之后公网按钮才可用；仅在浏览器保存令牌不会改变服务端。
- **一键公网访问**：弹窗内点「一键开启公网访问」，后端启动 `cloudflared` 临时隧道并自动把二维码切换为 `https://*.trycloudflare.com`；弹窗显示公网地址、可直接关闭。
- 未安装 `cloudflared` 时弹窗给出安装命令（Windows：`winget install Cloudflare.cloudflared`）；也可设置固定公网地址 `NPU_PUBLIC_URL`（命名隧道/反代）。

相关 API：

| 接口 | 说明 |
|---|---|
| `GET /api/mobile/access` | 返回 `preferred_url`、`urls[]`（public/tunnel/lan/current）、二维码 data URL、`token_required`、隧道状态 |
| `GET /api/mobile/qr.png?url=…` | 生成指定地址的二维码 PNG（仅 http/https） |
| `POST /api/mobile/tunnel/start` / `stop` | 一键开启/关闭 Cloudflare 临时隧道；开启前需设置 `NPU_ADMIN_TOKEN`（`-Lan` 会自动生成），关闭可由本机操作 |

**安全提示**：临时隧道地址是公网可访问的；开启前请确认已设置 `NPU_ADMIN_TOKEN`，使用完及时关闭。生产请使用命名隧道/反向代理 + HTTPS。

> **真实隧道验收（2026-10-23）**：cloudflared 2026.8.2（自动检测 `%LOCALAPPDATA%\cloudflared\cloudflared.exe`）一键启动约 4.7s；公网 HTTPS 下 health/首页/使用手册 200，无令牌写操作 401、带令牌收录 201；停止后公网 502。未安装时弹窗会给出 `winget install Cloudflare.cloudflared` 安装提示。


**鉴权**：设置 `NPU_ADMIN_TOKEN` 后，除「学生问答 `POST /api/chat`、只读 GET、机器人 webhook」外的所有写操作都要求请求头 `X-Admin-Token`。移动端在「设置 → 管理令牌」登录一次，令牌存 localStorage，所有 API 自动带上。未设置令牌时完全放行（仅限本机演示）。

---

## 2. 快速收录 API（新增）

### 2.1 收录网页 / 公众号文章链接

`POST /api/knowledge/import/url`（需管理令牌）

```jsonc
// request
{"url": "https://mp.weixin.qq.com/s/xxxx", "title": "可选标题", "tags": ["公众号", "奖助"]}
// 201 response
{
  "duplicated": false,
  "document": { "id": "...", "title": "...", "status": "ready", "page_count": 3,
                "source_type": "url", "source_url": "https://...", "error": null },
  "pages": [{"id": "...", "title": "国家奖学金", "summary": "..."}]
}
```
- 已收录过的同一 URL（`documents.source_url` 相同）→ `200 {"duplicated": true, "document": ..., "pages": [...]}`，不重复编译。
- 抓取限制：仅 `http/https`；手动跟随 ≤5 次重定向；超时 30s；响应 ≤8MB（`NPU_URL_IMPORT_MAX_BYTES` 可调）；仅接受 HTML/纯文本；默认**禁止私网/回环 IP**（内网部署可设 `NPU_URL_IMPORT_ALLOW_PRIVATE=1`）。文章过大或触发微信反爬时，改用「文本收录」粘贴正文。
- 正文提取：优先 `#js_content` / `.rich_media_content`（微信公众号）、`article/main/[role=main]`，否则取 body 最大文本块；去掉 script/style/nav/footer；标题取 `og:title`/`<title>`/`h1`。提取不到正文 → 400。
- 图片/附件不下载（MVP）；需要图片内容时用「文本收录」粘贴或上传文件。

`POST /api/knowledge/import/text`（需管理令牌）

```jsonc
{"title": "请假政策（转自班级群）", "text": "……", "tags": ["微信", "请假"]}
```
- `text` 30–200000 字；`title` 为空时取首行（≤40 字）或「未命名资料-时间」；生成源文件并编译词条，返回结构同上（`source_type="text"`）。

`POST /api/knowledge/import/batch`（需管理令牌）

```jsonc
{"urls": ["https://a", "https://b"], "tags": ["待整理"]}
// 200
{"items": [{"url": "https://a", "ok": true, "duplicated": false, "document_id": "...",
            "pages": [{"id": "...", "title": "..."}], "error": null}, ...]}
```
- 单次 ≤5 条，顺序处理；单条失败不影响其它。

文档对象新增字段：`source_type`（`file|url|text`）、`source_url`。列表/详情接口返回。

### 2.2 机器人管理员入库（QQ / 微信）

通道配置（`POST/PUT /api/bots/channels` 的 `config`）新增：
- `admin_user_ids`: `"QQ号或openid，逗号分隔"`（也接受数组）。只有这些用户发来的「收录指令」才会入库，防止资料被污染。
- 可选 `import_enabled`: `"true"|"false"`（默认 true）。

管理员在 QQ/微信里发送：
| 消息 | 行为 |
|---|---|
| 纯链接（或包含一个链接的文字，如“帮我收录 https://…”） | 抓取该链接并入库 |
| `#入库 <链接>` | 同上 |
| `#收录 <链接>` | 同上 |
| `#状态` | 回复：版本、Wiki 词条数、源文件数、默认模型、最近构建状态 |
| `#帮助` | 回复命令说明 |

回复策略：
- **QQ（OneBot）**：同步入库（通常 2–8 秒），直接回复「已收录《标题》：生成 N 个词条：…」或失败原因。
- **微信公众号**：受 5 秒被动回复限制，**先立即回复**「收到，已开始收录，请稍后到 Wiki 查看」，后台线程完成入库；MVP 不做客服消息异步推送（再次发送链接会提示已收录）。
- 非管理员发链接：不触发入库，按普通问题回答（避免滥用）。
- 机器人入库的文档 `source_type="url"`，`tags` 自动追加 `bot`/`QQ`/`微信`。

---

## 3. 移动端 Web / PWA

1. **快速收录卡片**（内容管理 → 收录资料，移动端优先）：
   - 三个模式 Tab：`链接`（支持一行一个、最多 5 个，批量收录 + 逐条结果）、`文本`（标题 + 正文粘贴）、`文件`（原有上传）。
   - 收录中显示进度（抓取 → 编译词条）；成功后展示生成的词条标题，可直接点开查看；失败逐条显示原因。
   - 卡片内提示：微信/QQ 可把链接直接发给机器人（需管理员），或在手机上复制链接到此。
2. **管理令牌登录**：设置页新增「管理令牌」卡片（输入 + 保存 + 测试连接）；保存到 `localStorage["npu_admin_token"]`；`api.js` 所有请求自动附加 `X-Admin-Token`；收到 401 时 toast 引导去设置页。
3. **PWA**：`manifest.webmanifest` + `service-worker.js` + 192/512 图标；`display: standalone`，主色 `#0b3d91`；安装后桌面图标打开直达内容管理 → 收录资料。Service Worker 只缓存静态壳（HTML/CSS/JS/图标），**不缓存 API 响应**。
4. **移动端适配**：390px 宽度无横向滚动；内容管理 → 收录资料首屏可见；上传/编辑抽屉全屏化；触控目标 ≥44px。

---

## 4. 部署与运维

### 4.1 局域网（最快，和总服务一起启动）
```powershell
.\scripts\run.ps1 -Lan             # 绑定 0.0.0.0，自动生成并打印管理令牌 + 手机访问地址
# 兼容旧命令：.\scripts\serve-lan.ps1  （等价于 run.ps1 -Lan）
# 启动后电脑打开 http://127.0.0.1:8000，点右上角「手机扫码」让手机扫码（推荐）
# 首次需放行防火墙（管理员 PowerShell）：
New-NetFirewallRule -DisplayName "NPU-Assistant 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```
手机浏览器打开脚本打印的 `http://<电脑局域网IP>:8000`，或直接扫电脑端「手机扫码」弹窗中的二维码。移动端与 Web/API 是**同一个进程**，无需也不会额外启动端口；`-Lan` 只是改变监听地址。注意：HTTP 下 PWA 不能安装；不要在不可信 Wi-Fi 下开 `0.0.0.0` 且不设令牌（`-Lan` 会自动生成一个，手机在设置页填入）。

### 4.2 公网 HTTPS（推荐：Cloudflare Tunnel）
```powershell
winget install Cloudflare.cloudflared
$env:NPU_ADMIN_TOKEN = "<长随机串>"; .\scripts\run.ps1
# 另开终端：
cloudflared tunnel --url http://127.0.0.1:8000
# 得到 https://xxxx.trycloudflare.com → 手机访问 / PWA 安装 / 公众号回调
```
长期使用建议命名隧道 + 自有域名；ngrok/frp 同理。**公网必须设置 `NPU_ADMIN_TOKEN`，不要把 8000 端口直接映射到公网**。

### 4.3 云服务器（7×24）
- `uvicorn app.main:app --host 127.0.0.1 --port 8000` + Caddy/Nginx 反代 HTTPS；
- systemd 服务示例、备份 cron 见附录；
- 数据目录 `backend/data`（`app.db` + `uploads` + `wiki_seed.json`）定期备份。

### 4.4 备份 / 恢复
```powershell
.\scripts\backup.ps1                 # 生成 backups/npu-assistant-<时间>.zip（DB 快照 + uploads + seed）
```
恢复：停止服务 → 解压覆盖 `backend/data` → 启动。**不要**直接复制运行中的 `app.db-wal` 而不停服务。

### 4.5 安全清单
- [ ] 设置强随机 `NPU_ADMIN_TOKEN`（≥24 字符），手机设置页保存；`.\scripts\run.ps1 -Lan` / `.\scripts\serve-lan.ps1` 在未设置时会**自动生成并打印**一次；
- [ ] 对外监听（非 `127.0.0.1`）必须设置 `NPU_ADMIN_TOKEN`，否则服务拒绝启动；仅可信内网临时演示才设 `NPU_ALLOW_INSECURE_LAN=1`；
- [ ] CORS 默认关闭（同源 SPA 不需要）；确需跨域时用 `NPU_CORS_ORIGINS` 逗号白名单，禁止 `*`；
- [ ] 公网只走 HTTPS 隧道/反代；管理接口不直接暴露裸端口；
- [ ] 机器人 `admin_user_ids` 只填本人/值班老师账号；通道接口只回显脱敏 token + `has_access_token`，PUT 留空/脱敏占位表示不修改；
- [ ] 机器人 webhook 安全：OneBot 空 token 403（创建时自动生成随机 token）、微信 token 不能为空或公开默认值 `npu-assistant`、回调校验 ±300 秒时间窗（服务器开启 NTP）与 nonce 防重放（重复 403；缓存为进程内，多 worker 需共享存储）、webhook 请求体 ≤2MB；`NPU_BOT_ALLOW_INSECURE=1` 仅本地调试；
- [ ] 机器人日志/配置：设置 `NPU_ADMIN_TOKEN` 后 `GET /api/bots/logs` 需 `X-Admin-Token`；通道 `api_base` 与 outbound `url` 回显脱敏（去 userinfo/query），编辑回填脱敏值=不修改；
- [ ] URL 抓取默认禁私网（`NPU_URL_IMPORT_ALLOW_PRIVATE=0`）、默认上限 8MB；内网资料才临时开启；
- [ ] Wiki 词条正文链接协议已白名单清洗（`javascript:`/`data:` 等移除）；后端默认下发 CSP、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff` 等安全响应头；
- [ ] 反向代理加限流/请求体上限（例如 5MB）与访问日志；
- [ ] 定期备份 `backend/data`，升级前先备份；
- [ ] 公众号仅明文模式 + Token 校验；QQ 通道 access_token 强随机，发送不跟随重定向、`api_base` 仅允许 http(s)。

---

## 5. 验收标准（v0.1.1）

- [ ] `POST /api/knowledge/import/text` 收录文字 → `documents.source_type=text`、`page_count>0`、词条可在 `/api/wiki/tree` 看到
- [ ] `POST /api/knowledge/import/url` 抓取本地测试网页/公众号样式 HTML → 正确提取正文与标题、生成词条；重复 URL 返回 `duplicated=true` 且不重复建词条
- [ ] URL 抓取拒绝 `file://`、私网 IP、超大响应、非 HTML；失败返回 400 中文原因
- [ ] 批量链接 ≤5 条，逐条返回 ok/error
- [ ] 设置 `NPU_ADMIN_TOKEN` 后：POST 导入/上传/编辑/重建无令牌 → 401；带 `X-Admin-Token` → 200；`POST /api/chat` 与 GET 不受影响；机器人 webhook 不受影响
- [ ] QQ 管理员发链接 → 入库并回复结果；非管理员发链接 → 不入库；`#状态` 返回统计
- [ ] 微信管理员发链接 → 5 秒内先 ack，后台完成入库
- [ ] 手机 390px 下内容管理 → 收录资料可完成「链接/文本/文件」三种收录；无横向滚动；管理令牌登录后管理操作成功
- [ ] PWA：manifest、service worker、192/512 图标可访问；Chrome 可安装；离线打开显示静态壳与错误提示
- [ ] `pytest` 新增导入/鉴权/机器人入库用例全绿；smoke/真实 E2E 通过；文档更新

> **验收记录（2026-10-22，QA 实测）**：pytest **104 passed**（v0.1.0）；v0.1.1 新增扫码/隧道/首次启用令牌用例后 **110 passed**；v0.1.2 新增真实文件解析用例后 **126 passed**；真实服务 E2E：8000 安全/移动端 17/17、8011（`NPU_ADMIN_TOKEN`）令牌侧 19/19、390px UI/长文件名 10/10 全部通过；触控目标经 web-ui 修复后复测 **0/64 个控件 <44px**，无遗留阻塞项。

---

## 6. 附录

### 6.1 机器人通道配置示例
```jsonc
// QQ OneBot
{"api_base":"http://127.0.0.1:3000","access_token":"强随机","self_id":"123456789",
 "group_mention_only":"true","admin_user_ids":"123456789,987654321"}
// 微信公众号
{"token":"公众号后台Token","app_id":"","welcome":"","admin_user_ids":"oXxxOpenId1,oXxxOpenId2"}
```

### 6.2 systemd 示例
```ini
[Unit]
Description=NPU-Assistant
After=network.target

[Service]
WorkingDirectory=/opt/NPU-Assistant
Environment=NPU_HOST=127.0.0.1 NPU_PORT=8000 NPU_ADMIN_TOKEN=replace-me
ExecStart=/opt/NPU-Assistant/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### 6.3 常见问题
| 现象 | 处理 |
|---|---|
| 手机打不开局域网地址 | 同一 Wi-Fi？防火墙放行 8000？电脑 IP 正确（`ipconfig`）？ |
| 手机管理操作提示 401/需要令牌 | 设置页填 `NPU_ADMIN_TOKEN` 同样的值并保存；刷新重试 |
| 公众号回调校验失败 | URL 必须公网 80/443、Token 与通道 config 完全一致、明文模式；检查隧道是否在线 |
| 微信发链接只回“已开始收录”没有结果 | 设计如此（5 秒限制）；稍后到 Wiki 页刷新查看，或再发一次会提示已收录 |
| URL 收录报“无法访问私网地址” | 内网资料：设 `NPU_URL_IMPORT_ALLOW_PRIVATE=1` 后重启（仅限可信网络） |
| 公众号文章过大 / 抓取失败（反爬、需要验证） | 改用「文本收录」粘贴正文；确需抓取时由管理员调大 `NPU_URL_IMPORT_MAX_BYTES`（默认 8MB） |
| 收录成功但词条质量差 | 到知识库编辑词条/补别名，或改进源文档结构（标题清晰） |
| PWA 装不上 | 需要 HTTPS（Cloudflare Tunnel / 反代）；LAN HTTP 不支持安装 |

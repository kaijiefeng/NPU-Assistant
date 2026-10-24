# 机器人接入指南（QQ / 微信公众号 / 控制台）

本文说明如何把 NPU-Assistant 接到 QQ（NapCat + OneBot 11）和微信公众号（明文模式），以及如何用控制台通道在无任何外部账号时完成调试。接口细节见 [CONTRACT.md](CONTRACT.md) §5.5。

## 0. 快速添加（v0.1.1 推荐）

不用先读完整文档，按下面三步即可：

1. 打开「机器人」页 → 顶部「快速添加」选择 **QQ 机器人** / **微信公众号** / **控制台调试**；
2. 表单已预填推荐默认值：QQ 默认 `api_base=http://127.0.0.1:3000`，Token 留空会自动生成；微信填写公众号后台一致的 Token 与管理员 openid；保存后复制卡片上的 **Webhook 地址**；
3. 把 Webhook 填到 NapCat / 公众号后台，用管理员账号转发一条链接测试；成功后会回复「已收录」。

没有外部账号时先用「控制台调试」通道模拟对话，验证问答与引用格式，再接入真实平台。

## 1. 整体链路

```text
QQ 用户 ──> NapCat（OneBot 11 实现） ──反向 WS / 反向 HTTP 事件──> NPU-Assistant
                                                                  │
                    QQ 用户 <── send_private_msg / send_group_msg ──┘（HTTP API）

公众号用户 ──XML 回调──> NPU-Assistant（签名校验 + 明文回复）──> 公众号用户
```

- **入站**：NapCat 把 QQ 消息事件推给我们（反向 WebSocket 或反向 HTTP）。
- **处理**：`bot_pipeline` 归一化消息 → 调用 `counselor.answer()`（Wiki 导航选词条 + LLM 生成 + 落库）→ 得到回复与「词条 + 来源锚点」双层引用；机器人侧只使用回复文本发送。
- **出站**：QQ 侧通过 NapCat 的 HTTP API 发送；公众号侧直接返回明文 XML。
- **会话**：不同渠道使用不同 `session_key`，同一 QQ 私聊 / 群成员会延续同一会话上下文。

## 2. 准备工作

| 项目 | 要求 |
|------|------|
| NPU-Assistant | 已启动，`GET http://127.0.0.1:8000/api/health` 返回 `status=ok` |
| QQ 账号 | 建议使用**机器人小号**，遵守腾讯平台规则；主号被封风险自担 |
| NapCat | 支持 OneBot 11 协议的 QQ 机器人框架（NapCat / LLOneBot 等），本文以 NapCat 为例 |
| 网络 | NapCat 与 NPU-Assistant 在同一台机器或可互通的内网；本机默认 `127.0.0.1` |
| 公众号 | 已注册且具备开发权限的公众号（订阅号 / 服务号），能配置服务器 URL |

先在「机器人」页（`#/bots`）确认三个通道卡片存在，并记下 QQ 通道 / 微信通道的 **channel_id**（也可 `GET /api/bots/channels` 获取）。通道卡片上的「复制」按钮会给出完整的 Webhook 地址。

## 3. QQ（NapCat + OneBot 11）

### 3.1 在 NPU-Assistant 创建 QQ 通道

「机器人」页 → 新建通道 → 类型选择 `onebot11`，配置项：

```json
{
  "api_base": "http://127.0.0.1:3000",
  "access_token": "change-me-to-a-random-token",
  "self_id": "",
  "group_mention_only": "true",
  "admin_user_ids": "123456789",
  "import_enabled": "true"
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `api_base` | NapCat 的 HTTP API 地址（NapCat 的「HTTP 服务器」端口，默认示例为 3000） |
| `access_token` | 与 NapCat 网络配置中填写的 Token 保持一致；**创建时留空会自动生成随机 Token**（响应只显示脱敏值，无法读回）；通道未配置 Token 时 webhook 一律 403（本地调试可设 `NPU_BOT_ALLOW_INSECURE=1`） |
| `self_id` | 机器人 QQ 号；填空时由事件中的 `self_id` 自动识别（建议首次收到消息后按日志补全） |
| `group_mention_only` | 群聊是否仅在被 @ 时回复，默认 `"true"`；设为 `"false"` / `"0"` 可回复所有群消息（建议保持默认，避免刷屏） |
| `admin_user_ids` | 允许「转发链接即入库」的管理员 QQ 号，逗号分隔（也可传数组）；为空表示关闭该通道的入库权限 |
| `import_enabled` | 是否启用机器人入库，默认 `"true"`；`"false"` 时管理员发链接只提示已关闭 |

保存后，通道卡片会显示形如下面的地址（`{id}` 为通道 ID）：

- 反向 WebSocket：`ws://127.0.0.1:8000/api/bots/onebot/{id}/ws`（URL **不必**拼 `access_token`，NapCat 配置的 Token 字段会以 `Authorization: Bearer` 发送；若客户端只能拼 URL，也可用 `...?access_token=<token>`，后端两种都支持）
- 反向 HTTP 事件：`http://127.0.0.1:8000/api/bots/onebot/{id}/event`（通道必须已配置 `access_token`；NapCat「HTTP 客户端」Token 可留空并改用 query `?access_token=<token>`，或填写后由后端校验请求正文的 HMAC-SHA1，见下文）

### 3.2 配置 NapCat

在 NapCat WebUI 的「网络配置」中新建以下配置：

**① HTTP 服务器（NapCat 提供 API，供后端发送回复）**

| 配置项 | 示例值 |
|--------|--------|
| 启用 | 是 |
| 主机 | `127.0.0.1` |
| 端口 | `3000`（与通道 `api_base` 端口一致） |
| Token | `change-me-to-a-random-token`（与通道 `access_token` 一致） |

后端发送回复时调用 `POST {api_base}/send_private_msg` 或 `/send_group_msg`，请求头带 `Authorization: Bearer <token>`。

**② WebSocket 客户端（反向 WS，NapCat 主动连我们，推荐）**

| 配置项 | 示例值 |
|--------|--------|
| 启用 | 是 |
| URL | `ws://127.0.0.1:8000/api/bots/onebot/{id}/ws` |
| Token | `change-me-to-a-random-token`（与通道 `access_token` 一致，NapCat 以 `Authorization: Bearer` 发送） |
| 消息格式 | `array`（OneBot 11 标准消息段） |
| 上报自身消息 | 否（避免机器人回复自己触发循环；后端也会丢弃 `user_id == self_id` 的消息） |

**③ HTTP 客户端（反向 HTTP 事件，可选）**

| 配置项 | 示例值 |
|--------|--------|
| 启用 | 可选 |
| URL | `http://127.0.0.1:8000/api/bots/onebot/{id}/event` |
| Token | **建议留空**；填写后 NapCat 会对请求正文计算 HMAC-SHA1 并发送 `X-Signature: sha1=<hex>`，后端同样支持；也可在 URL 拼 `?access_token=<通道 token>` |

> 反向 WS 延迟更低、推荐优先使用。两种方式也可以同时开启：服务端按 `channel_id + message_id` 做进程内幂等去重（保留最近 512 条），同一条消息不会重复回答；少数实现上报的事件不带 `message_id`，此时仍可能重复，建议只保留一种上报方式。
>
> 反向 HTTP 事件鉴权：query 参数 `access_token` 与 `X-Signature` 二选一；**通道未配置 token 时一律 403**（本地调试可设 `NPU_BOT_ALLOW_INSECURE=1` 临时放行）。
>
> 通道配置了 `self_id` 时，事件中的 `self_id` 必须与之一致，否则事件被忽略（HTTP 仍返回 `{"status":"ok"}`，不产生日志/回复），用于防伪造/串号。

配置保存并启用后，NPU-Assistant 日志应出现 OneBot 连接 / 事件记录，机器人页通道状态变为在线。

### 3.3 测试

1. 用另一个 QQ 号私聊机器人发送「国家奖学金怎么申请？」；
2. 预期机器人在数秒内回复带 `[1][2]` 引用的回答（内容来自校园 Wiki 词条；机器人只发送文本，引用结构由 Web 端展示）；
3. 在「机器人」页「消息日志」中可看到该条入站消息与回复；会话渠道为 `onebot11`；
4. 群聊需将机器人拉入群并 @ 机器人提问（通道配置 `group_mention_only` 默认 `"true"`，即仅被 @ 时回复；设为 `"false"` 可回复所有群消息）。
5. **日志接口鉴权**：设置 `NPU_ADMIN_TOKEN` 后，`GET /api/bots/logs`（含学生消息内容）需要 `X-Admin-Token`，未带/错误 → 401；未设置令牌时本地演示可直接访问。webhook 仍使用通道自身 token/签名，不受影响。
6. **webhook 体积上限**：OneBot 事件与微信回调的请求体超过 **2MB** 一律返回 413（读取前校验 `Content-Length` + 实读长度兜底）。

### 3.4 客户端侧行为说明

- 入站只处理 `post_type=message` 的文本消息段，图片 / 表情 / 语音等非文本内容会被忽略或走引导语；
- 忽略 `user_id == self_id` 的自身消息（含 `post_type=message_sent` 回显），避免自问自答循环；
- 群聊默认仅在被 @ 时回复（`group_mention_only="true"`），可在通道配置中关闭；`qq=all` 的 @全体成员也视为提到机器人；
- 发送失败（NapCat 未启动、端口错误、Token 不匹配）**只记录日志**，不影响 webhook 的 HTTP 响应；通道状态可能显示为 `error`；HTTP 客户端已设置 `trust_env=False`、`follow_redirects=False`（`api_base` 为管理员配置，302 不跟随），且 `api_base` 仅允许 `http/https` 绝对地址；失败日志中的 URL 会去除 userinfo/query；
- OneBot HTTP API 请求规则：私聊 `POST {api_base}/send_private_msg`，群聊 `POST {api_base}/send_group_msg`，Body `{user_id|group_id, message}`，有 Token 时带 `Authorization: Bearer <token>`。

### 3.5 管理员「转发即入库」

在通道配置中把本人/值班老师的 QQ 号填入 `admin_user_ids` 后，管理员可以直接在 QQ 里把网页 / 公众号文章链接转发给机器人，自动抓取正文并编译成 Wiki 词条（详见 [MOBILE-OPS.md](MOBILE-OPS.md) §2.2）：

| 角色 | 判断方式 | 能做什么 | 不能做什么 |
|------|----------|----------|------------|
| 管理员 | 平台提供的发送者 ID ∈ 通道 `admin_user_ids` | 转发链接 / `#入库` / `#收录` / `#状态`；普通问题仍可问答 | — |
| 普通用户（学生） | ID 不在 `admin_user_ids` | 正常问答、`#帮助` | 发链接不会入库；`#入库`/`#状态` 返回“仅限管理员” |

- **ID 从哪来**：QQ 用该号私聊机器人发 `#帮助`，回复会显示“你的用户 ID”；微信看 `#帮助` 或后端日志里的 `FromUserName`；每个通道单独配置，可填多个（英文逗号分隔）。
- **不是按昵称判断**：管理员身份只认平台回调里的 user_id，且该回调必须先通过 OneBot access_token / 微信公众号签名+时间窗+nonce 校验，伪造 webhook 会在入口被拒绝。
- 群聊默认只有 @ 机器人时才处理；管理员在群里 @ 机器人转发链接同样生效。

| 消息 | 行为 |
|------|------|
| 纯链接 / 含链接的文字（如「帮我收录 https://…」） | 管理员：抓取并入库，回复「已收录《标题》：生成 N 个词条：…」；普通用户：按普通问题回答 |
| `#入库 <链接>`、`#收录 <链接>` | 管理员同上；普通用户提示仅限管理员 |
| `#状态` | 管理员查看版本、Wiki 词条数、源文件数、默认模型、最近构建状态；普通用户提示仅限管理员 |
| `#帮助` | 所有人可用；回复命令说明，并显示“你的用户 ID”（便于首次配置管理员） |

- 命令与入库消息**不进入问答会话**（不占用学生的聊天上下文）；
- 非管理员发链接：不触发入库，按普通问题回答；
- `import_enabled="false"`：管理员发链接也只回复「机器人收录功能已关闭」；
- 入库文档 `tags` 自动追加 `bot`/`QQ`（微信为 `bot`/`微信`），`source_type="url"`；
- 重复链接回复「该链接已收录过」，不会重复编译。

## 4. 微信公众号（明文模式）

> MVP 只实现**明文模式**，用于内网演示与流程验证。生产环境请改用安全模式（加密模式）并做消息验签加固。

### 4.1 创建微信通道

「机器人」页 → 新建通道 → 类型 `wechat_mp`，配置项：

```json
{
  "token": "wechat-strong-random-token-2026",
  "app_id": "",
  "welcome": "",
  "admin_user_ids": "oXxxOpenId1",
  "import_enabled": "true"
}
```

| 字段 | 说明 |
|------|------|
| `token` | 自定义强随机字符串，必须与公众号后台「服务器配置 → Token」完全一致（区分大小写）；**不能为空、也不能使用公开默认值 `npu-assistant`**（后端会 403） |
| `app_id` | 公众号 AppID，可留空（仅记录用） |
| `welcome` | 关注欢迎语，可留空使用默认欢迎语 |
| `admin_user_ids` | 允许「转发链接即入库」的管理员 openid，逗号分隔；openid 可在后端日志的 `FromUserName` 中查看 |
| `import_enabled` | 是否启用机器人入库，默认 `"true"` |

> 微信管理员发链接时，受 5 秒被动回复限制：服务端会**立即 ack**「收到，已开始收录，请稍后到 Wiki 查看」，随后由后台线程完成抓取与编译；MVP 不做客服消息异步推送，稍后到「内容管理 → 词条维护」刷新即可（重复发送会提示已收录）。
>
> 安全：公众号回调校验包含 **时间戳窗口 ±300 秒**（防重放），服务器时间需准确；本地调试可用 `NPU_BOT_ALLOW_INSECURE=1` 临时跳过 Token 安全检查（仍校验时间窗，勿用于公网）。

保存后得到回调地址：`/api/bots/wechat/{id}`。

### 4.2 准备公网可访问地址

微信服务器必须能访问你的回调 URL，因此本机服务需要公网映射（示例任选其一）：

```powershell
# 方式 A：ngrok（注册后获取 authtoken）
ngrok http 8000
# 输出形如 https://xxxx-xx-xx-xx-xx.ngrok-free.app

# 方式 B：cpolar
cpolar http 8000
```

拿到 HTTPS 公网地址后，回调 URL 为：

```text
https://<公网域名>/api/bots/wechat/{通道ID}
```

> 注意：回调端口必须是 80 或 443（微信限制）。若公网映射端口不是 80/443，请用反向代理转发。`run.ps1` 默认只监听 `127.0.0.1`，映射工具通常能访问本机回环地址；若使用局域网转发，可用 `.\scripts\run.ps1 -BindHost 0.0.0.0`。

### 4.3 公众号后台配置

公众号后台 →「设置与开发」→「基本配置」→「服务器配置」：

| 配置项 | 填写 |
|--------|------|
| URL | `https://<公网域名>/api/bots/wechat/{通道ID}` |
| Token | 与通道 `token` 完全一致（自选强随机串，勿用 `npu-assistant`） |
| EncodingAESKey | 明文模式可不填（或随机生成后不用） |
| 消息加解密方式 | **明文模式** |
| 数据格式 | XML |

点击「提交」后，微信会向该 URL 发送 GET 校验请求：

```text
GET /api/bots/wechat/{id}?signature=...&timestamp=...&nonce=...&echostr=...
```

服务端用 `sha1(sort(token, timestamp, nonce))` 与 `signature` 比对，一致则原样返回 `echostr`，校验通过后「启用」。若提示「token验证失败」，按第 6 节排查。

> 微信回调安全校验（GET 接入校验与 POST 消息一致）：
> 1. **时间窗 ±300 秒**：`timestamp` 与服务器时间偏差过大直接 403，请保持服务器 NTP 同步；
> 2. **nonce 防重放**：同一通道的同一 `nonce` 首次请求成功后会登记，TTL 内重复使用 → 403（detail 含 nonce）；签名失败不会消费 nonce；`nonce` 为空不参与重放判定但签名函数要求非空，正常微信请求都会带 nonce；
> 3. nonce 缓存为**进程内**（TTL 330s、上限 1000 条）；多 worker/多实例部署时各进程独立，跨进程重放需改用共享存储（Redis 等）。

### 4.4 测试

1. 用微信关注该公众号（测试号可直接扫码关注）；
2. 发送文本消息「请假流程是什么？病假需要什么证明？」；
3. 预期收到明文 XML 回复文本，内容为带引用的回答（被动回复，内容为 `Content` 字段；响应头为 `Content-Type: text/xml; charset=utf-8`，符合微信官方文档要求）；
4. 发送图片 / 语音等非文本消息，会收到一条引导语，提示发送文字提问；
5. 在「机器人」页消息日志中可看到渠道为 `wechat_mp` 的记录。

被动回复的限制：微信要求 5 秒内返回，且文本长度有限（约 2048 字节）。服务端对问答链路设置了约 **4.5 秒**超时：超过阈值会先回复一条兜底提示（「这个问题需要一点时间查询，请稍后再发送一次……」），避免公众号侧报「该公众号暂时无法提供服务」。MVP 未实现客服消息异步推送，用户重发一次即可；也可以换用低延迟模型，或在设置页把 `wiki_max_pages` / `wiki_answer_max_chars` 调小以缩短词条上下文。

## 5. 控制台调试通道

无需 QQ / 公众号账号，适合开发与演示：

1. 「机器人」页找到「控制台调试通道」（首启自动创建，不可删除）；
2. 在「模拟对话」输入框发送消息，前端调用 `POST /api/bots/channels/{id}/test`；
3. 返回 `{reply, citations, latency_ms, outbound}`，其中 `outbound` 展示将要发送给渠道的报文结构（QQ 为 HTTP API 请求体，微信为 XML 字符串）；
4. 「消息日志」区域可查看历史记录。

也可以用 curl 直接验证：

```powershell
$body = @{ message = "心理中心怎么预约？"; user_id = "10001"; user_name = "演示同学" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/bots/channels/<通道ID>/test" `
  -ContentType "application/json" -Body $body
```

> 注意 PowerShell 5.1 的 `ConvertTo-Json` 可能把中文转义为 `\uXXXX`，属正常现象；服务端会正确解析。

## 6. 常见故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 通道状态一直离线 | 反向 WS 未连接 / URL 错误 | 核对 `ws://` 路径、channel_id、access_token；查看 NapCat 与后端日志 |
| OneBot WS 返回 403 / 401 | `access_token` 不一致 | 通道配置与 NapCat URL 的 token 必须完全一致（含大小写、URL 编码） |
| QQ 机器人收到消息但不回复 | `api_base` 端口错误 / NapCat HTTP 服务器未启用 / Token 不匹配 | 用 `curl http://127.0.0.1:3000/get_status`（NapCat 开启 HTTP 服务器后）验证；检查后端日志「发送失败」 |
| 群聊不回复 | 未 @ 机器人（默认仅 @ 回复）；NapCat 未上报群消息 | 在群里 @ 机器人；如需回复所有群消息，把通道配置 `group_mention_only` 设为 `"false"`；检查 NapCat 上报设置与群权限 |
| 机器人自己回复自己 | 未忽略自身消息 / `self_id` 识别错误 | 确认通道 `self_id` 正确；确认 NapCat「上报自身消息」为否 |
| 同一消息被回复两次 | 反向 WS 与反向 HTTP 同时开启且事件缺少 `message_id` | 正常事件按 `channel_id + message_id` 去重；缺少 `message_id` 时只保留一种上报方式 |
| 反向 HTTP 事件 403 | NapCat「HTTP 客户端」Token 与通道 `access_token` 不匹配 / 通道未配置 token | 两者规则不同：HTTP 客户端是对正文 HMAC-SHA1（`X-Signature: sha1=...`），也可把 HTTP 客户端 Token 留空并改用 URL `?access_token=<token>`；通道空 token 默认 403（本地调试可设 `NPU_BOT_ALLOW_INSECURE=1`） |
| 公众号 token 验证失败 | 回调 URL 不可达 / Token 不一致 / 服务未启动 | 浏览器直接访问回调 URL 应返回 403 或 400（而非超时）；校准 Token、协议（https）、端口 80/443 |
| 公众号 403「未配置安全 Token」 | 通道 `token` 为空或仍为公开默认值 `npu-assistant` | 在通道配置改填自选强随机串，并同步修改公众号后台「服务器配置 → Token」 |
| 公众号 403「时间戳无效或已过期」 | 服务器与本机时间偏差 >300 秒，或请求被重放 | 校准服务器时间（NTP）；确认回调请求实时发出 |
| 公众号 403「nonce 重复（疑似重放）」 | 同一通道同一 nonce 被重复使用（或客户端重试未换 nonce） | 正常微信回调每次 nonce 不同；压测/转发时需更换 nonce；进程内缓存 330s，多 worker 不共享 |
| webhook 返回 413 | 请求体超过 2MB | 缩小报文；OneBot/微信正常消息远小于 2MB，检查是否被注入大 payload |
| 开启了管理令牌后机器人日志 401 | `GET /api/bots/logs` 属于管理员接口 | 请求头带 `X-Admin-Token`；学生问答与 webhook 不受影响 |
| 通道响应里看不到 token 明文 | 安全设计：config 只返回 `****`+后 4 位，另给 `has_access_token` | 无法读回原 token；如需更换请 PUT 新值（PUT 空串/脱敏占位不会覆盖旧值） |
| 通道响应里 `api_base` 与填写值不同 | 安全设计：`api_base` 去掉 userinfo/query/fragment 后回显（如 `https://h:8443/x`） | 编辑时回填该脱敏值=不修改，数据库仍保留原始地址；需更换请填新值 |
| 公众号收不到回复 | 超过 5 秒超时 / 返回格式错误 | 换低延迟模型；确认返回的是 `Content-Type: text/xml; charset=utf-8` 的明文 XML；查看后端日志 |
| 回复内容与词条不符 | Wiki 未导入 / 导航未选中正确词条 | 先 `POST /api/knowledge/seed` 导入预编译快照；用 `POST /api/wiki/search` 查看 `method` 与候选词条；必要时在「内容管理 → 词条维护」编辑词条的别名/标签后重试 |
| 上传源文件后机器人仍答不上 | 源文件编译失败（无真实 LLM）或尚未重新编译 | 检查默认模型是否为真实 Provider，看 `documents.error`；在「源文件」页点「重新编译」，确认生成词条后再提问 |
| 中文乱码 | 终端编码非 UTF-8 | 执行 `chcp 65001`；脚本已设置 `PYTHONUTF8=1` |

日志位置：启动服务的控制台输出；生产环境建议将 `logging` 输出定向到文件并轮转。

## 7. 安全建议

- **强随机 Token**：`access_token` / 公众号 Token 使用足够长的随机字符串，定期轮换；不要提交到 Git。OneBot 通道创建时留空会自动生成随机 Token；公众号不允许空 Token 或公开默认值 `npu-assistant`。
- **凭据不回显**：`GET/POST/PUT /api/bots/channels` 的 `config` 中 token 一律脱敏为 `****`+后 4 位，`admin_user_ids` 只显示数量；用 `has_access_token` 判断是否已配置。PUT 空串/脱敏占位表示「不修改」。
- **webhook 防护**：OneBot 空 Token 默认 403；公众号校验签名 + ±300 秒时间窗 + nonce 防重放；`self_id` 与事件不一致的事件直接忽略；webhook 请求体上限 2MB（超限 413）。仅本机调试可设 `NPU_BOT_ALLOW_INSECURE=1` 临时放行（勿用于公网）。
- **日志与展示**：配置 `NPU_ADMIN_TOKEN` 后 `GET /api/bots/logs` 需管理员令牌；通道 `config.api_base` 与 outbound `url` 均去掉 userinfo/query/fragment 后展示，避免泄露配置凭据；OneBot 发送不跟随重定向、`api_base` 仅允许 http(s)。
- **nonce 缓存边界**：微信 nonce 防重放缓存为进程内（TTL 330s、上限 1000），多 worker/多实例部署需改用 Redis 等共享存储才能跨进程防重放。
- **HTTPS + 反向代理**：公网只暴露 443，NPU-Assistant 保持监听 `127.0.0.1`；公众号必须 HTTPS。
- **来源限制**：在反向代理 / 防火墙层限制 NapCat 与微信服务器出口 IP；管理接口（模型、Wiki 词条 / 源文件写操作）建议仅内网访问，或设置 `NPU_ADMIN_TOKEN`。
- **隐私与合规**：QQ / 微信消息会落库到 `conversations` / `messages`，请告知使用者并遵守《个人信息保护法》；日志中避免打印完整 API Key 与学生敏感信息；接入第三方 LLM 前评估数据出境风险。
- **内容安全**：机器人回答可能被误读为官方政策，建议在回复尾部保留 `bot_reply_footer` 免责提示（「请以学校最新官方通知为准」），并配置人工兜底渠道。
- **平台合规**：使用 QQ 机器人需遵守腾讯平台规则，避免营销、群发与骚扰；公众号被动回复不得包含违法违规内容，违规可能导致封号。
- **频率限制**：公网部署时在反向代理层对回调接口做限流，防止恶意刷消息消耗 LLM 配额。

## 8. 参考

- OneBot 11 协议：<https://github.com/botuniverse/onebot-11>
- NapCat 文档：<https://napcat.napneko.icu/>
- 微信公众平台开发文档（接收消息 / 被动回复）：<https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Receiving_standard_messages.html>
- 接口契约：[CONTRACT.md](CONTRACT.md) §5.5

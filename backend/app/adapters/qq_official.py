"""QQ 开放平台官方机器人适配器。

QQ 官方机器人由**服务端主动连接** QQ 的 WebSocket 网关
（``wss://api.sgroup.qq.com/websocket``），事件通过该连接推送；回复通过
HTTP API 以「被动回复」发送（携带原消息 ``msg_id``，5 分钟内有效）。

本模块只做两件事：

1. ``normalize_event``：把网关 ``op=0`` 的 C2C / 群 @ 事件归一化为
   :class:`IncomingMessage`；
2. ``build_outbound``：构造「将要发送的报文」，供控制台展示；真正的网络发送
   由 :mod:`app.services.qq_gateway` 完成（需要访问令牌与限流/重试控制）。

参考：QQ 开放平台机器人文档（WebSocket 网关 / 发送消息 API）。
"""
from __future__ import annotations

import re
from typing import Any

from .base import (
    CHANNEL_QQ_OFFICIAL,
    CHAT_GROUP,
    CHAT_PRIVATE,
    IncomingMessage,
    config_bool,
)

C2C_MESSAGE_CREATE = "C2C_MESSAGE_CREATE"
GROUP_AT_MESSAGE_CREATE = "GROUP_AT_MESSAGE_CREATE"
SUPPORTED_EVENTS = (C2C_MESSAGE_CREATE, GROUP_AT_MESSAGE_CREATE)

API_BASE = "https://api.sgroup.qq.com"
SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"

# 被动回复文本上限（官方单条文本约 4000 字节，这里按字符保守截断）
MAX_REPLY_CHARS = 1800

# 官方群聊 @ 消息的正文会带 ``<@!机器人ID>`` 前缀，需要去掉
_MENTION_RE = re.compile(r"<@!?[^>]+>")


def strip_mentions(text: Any) -> str:
    """去掉官方消息正文里的 ``<@!id>`` / ``<@id>`` 提及标记。"""
    return _MENTION_RE.sub("", str(text or "")).strip()


def api_base(config: dict | None = None) -> str:
    """按通道配置返回 API 域名（沙箱环境使用 ``sandbox.api.sgroup.qq.com``）。"""
    if config_bool((config or {}).get("sandbox"), False):
        return SANDBOX_API_BASE
    return API_BASE


def _author(data: dict) -> dict:
    author = data.get("author")
    return author if isinstance(author, dict) else {}


def normalize_event(event_type: str, data: Any, *, channel_id: str,
                    config: dict | None = None) -> IncomingMessage | None:
    """归一化一条 QQ 官方机器人事件；非 C2C / 群 @ 消息返回 ``None``。"""
    if not isinstance(data, dict):
        return None
    event_type = str(event_type or "").strip().upper()
    if event_type not in SUPPORTED_EVENTS:
        return None

    author = _author(data)
    group_id = str(data.get("group_openid") or "").strip()
    if event_type == GROUP_AT_MESSAGE_CREATE:
        chat_type = CHAT_GROUP
        user_id = str(author.get("member_openid") or "").strip()
        if not group_id:
            return None
    else:
        chat_type = CHAT_PRIVATE
        user_id = str(author.get("user_openid") or "").strip()
    if not user_id:
        return None

    text = strip_mentions(data.get("content"))
    if not text:
        # 纯图片 / 语音等富媒体消息没有可用文本，交给网关忽略
        return None

    user_name = str(
        author.get("username") or author.get("nickname") or author.get("member_username") or ""
    ).strip()

    return IncomingMessage(
        channel=CHANNEL_QQ_OFFICIAL,
        channel_id=str(channel_id),
        text=text,
        user_id=user_id,
        user_name=user_name,
        chat_type=chat_type,
        group_id=group_id,
        self_id="",
        message_id=str(data.get("id") or ""),
        raw=data,
    )


def build_payload(text: Any, *, msg_id: str = "", msg_seq: int = 1) -> dict:
    """构造被动回复请求体（``msg_type=0`` 表示文本）。"""
    content = str(text or "")
    if len(content) > MAX_REPLY_CHARS:
        content = content[: MAX_REPLY_CHARS - 2].rstrip() + "……"
    payload: dict[str, Any] = {"content": content, "msg_type": 0}
    if msg_id:
        payload["msg_id"] = str(msg_id)
        payload["msg_seq"] = int(msg_seq)
    return payload


def reply_url(config: dict | None, *, chat_type: str, user_id: str,
              group_id: str) -> str:
    """被动回复的 HTTP API 地址（群聊与私聊路径不同）。"""
    base = api_base(config)
    if str(chat_type or "").lower() == CHAT_GROUP and group_id:
        return f"{base}/v2/groups/{group_id}/messages"
    return f"{base}/v2/users/{user_id}/messages"


def build_outbound(config: dict | None, *, chat_type: str, user_id: str,
                   group_id: str, text: str, msg_id: str = "",
                   msg_seq: int = 1) -> dict:
    """仅构造出站报文描述（控制台展示用），不发起任何请求。"""
    return {
        "type": CHANNEL_QQ_OFFICIAL,
        "action": "send_group_message" if str(chat_type).lower() == CHAT_GROUP
        else "send_c2c_message",
        "url": reply_url(config, chat_type=chat_type, user_id=user_id, group_id=group_id),
        "payload": build_payload(text, msg_id=msg_id, msg_seq=msg_seq),
    }

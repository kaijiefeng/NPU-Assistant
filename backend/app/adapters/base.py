"""机器人适配器基础类型。

本模块只提供与具体平台无关的归一化消息结构与**会话标识规则**，
规则来源：docs/CONTRACT.md §5.5「会话标识」。

- web：``web:{conversation_id 或 anonymous}``（由 chat 路由维护，不经过本管线）
- QQ 私聊：``onebot11:{self_id}:private:{user_id}``
- QQ 群聊：``onebot11:{self_id}:group:{group_id}:{user_id}``
- 微信：``wechat_mp:{channel_id}:{FromUserName}``
- 控制台：``console:{channel_id}:{user_id}``（调试用，契约未规定，按渠道隔离）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CHANNEL_ONEBOT = "onebot11"
CHANNEL_WECHAT = "wechat_mp"
CHANNEL_CONSOLE = "console"

SUPPORTED_CHANNEL_TYPES = (CHANNEL_ONEBOT, CHANNEL_WECHAT, CHANNEL_CONSOLE)

CHAT_PRIVATE = "private"
CHAT_GROUP = "group"

CHANNEL_TYPE_LABELS = {
    CHANNEL_ONEBOT: "QQ（OneBot 11）",
    CHANNEL_WECHAT: "微信公众号",
    CHANNEL_CONSOLE: "控制台调试",
}

# 各渠道 config 的默认字段（写入 bot_channels.config 前统一补齐）
DEFAULT_CHANNEL_CONFIGS: dict[str, dict[str, str]] = {
    CHANNEL_ONEBOT: {
        "api_base": "",
        "access_token": "",
        "self_id": "",
        # 群聊默认「仅被 @ 时回复」，设为 false 恢复为回复所有群消息
        "group_mention_only": "true",
        # 管理员转发即入库（docs/MOBILE-OPS.md §2.2）：QQ 号逗号分隔
        "admin_user_ids": "",
        "import_enabled": "true",
    },
    CHANNEL_WECHAT: {
        # 安全：不提供公开默认 Token；必须由用户填写与公众号后台一致的值，
        # 空值 / 公开默认值 "npu-assistant" 会被 webhook 拒绝（见 routers/bots.py）。
        "token": "",
        "app_id": "",
        "welcome": "",
        # 管理员 openid 逗号分隔（可在后端日志中查看 FromUserName）
        "admin_user_ids": "",
        "import_enabled": "true",
    },
    CHANNEL_CONSOLE: {
        "admin_user_ids": "",
        "import_enabled": "true",
    },
}

# 管理员 ID 列表的分隔符：英文/中文逗号、分号、空白
_ADMIN_ID_SPLIT_RE = re.compile(r"[,，;；\s]+")


def normalize_admin_user_ids(value: Any) -> str:
    """把 ``admin_user_ids`` 整理成 ``"id1,id2"`` 字符串。

    接受字符串（逗号/中文逗号/分号/空白分隔）或数组；去重并保持顺序。
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value]
    else:
        parts = _ADMIN_ID_SPLIT_RE.split(str(value))
    ordered: list[str] = []
    for part in parts:
        if part and part not in ordered:
            ordered.append(part)
    return ",".join(ordered)


def parse_admin_user_ids(value: Any) -> set[str]:
    """把通道 config 的 ``admin_user_ids`` 解析为集合，供机器人管线比对。"""
    normalized = normalize_admin_user_ids(value)
    return {item for item in normalized.split(",") if item}


def normalize_channel_type(value: Any) -> str:
    """校验并规范化通道类型，非法时抛出带中文信息的 ValueError。"""
    channel_type = str(value or "").strip().lower()
    if channel_type not in SUPPORTED_CHANNEL_TYPES:
        raise ValueError(
            "不支持的通道类型，仅支持 onebot11 / wechat_mp / console，收到："
            f"{value!r}"
        )
    return channel_type


def normalize_channel_config(channel_type: str, config: Any) -> dict:
    """把任意输入整理为渠道 config 字典（只保留该渠道已知字段并补默认值）。

    未知字段直接丢弃，避免把前端的无关数据写进数据库；值统一转成字符串。
    """
    raw = config if isinstance(config, dict) else {}
    defaults = DEFAULT_CHANNEL_CONFIGS.get(channel_type, {})
    cleaned: dict[str, str] = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        if key == "admin_user_ids":
            # 支持 "id1,id2" 字符串或数组，统一存字符串
            cleaned[key] = normalize_admin_user_ids(value)
        elif key == "import_enabled":
            cleaned[key] = "true" if config_bool(value, True) else "false"
        else:
            cleaned[key] = str(value).strip() if value is not None else ""
    return cleaned


def channel_type_label(channel_type: str) -> str:
    return CHANNEL_TYPE_LABELS.get(channel_type, channel_type)


def config_bool(value: Any, default: bool = False) -> bool:
    """把通道 config 里的字符串布尔值（"true"/"1"/"false"/"0"）转成 bool。"""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class IncomingMessage:
    """各渠道消息归一化后的统一结构，供 bot_pipeline 消费。"""

    channel: str
    channel_id: str
    text: str
    user_id: str = ""
    user_name: str = ""
    chat_type: str = CHAT_PRIVATE
    group_id: str = ""
    self_id: str = ""
    message_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.channel = str(self.channel or "").strip().lower()
        self.channel_id = str(self.channel_id or "")
        self.text = str(self.text or "")
        self.user_id = str(self.user_id or "")
        self.user_name = str(self.user_name or "")
        self.chat_type = str(self.chat_type or CHAT_PRIVATE).strip().lower()
        self.group_id = str(self.group_id or "")
        self.self_id = str(self.self_id or "")
        self.message_id = str(self.message_id or "")
        if not isinstance(self.raw, dict):
            self.raw = {}

    @property
    def is_group(self) -> bool:
        return self.chat_type == CHAT_GROUP

    @property
    def session_key(self) -> str:
        """按契约 §5.5 生成渠道内会话键。"""
        if self.channel == CHANNEL_ONEBOT:
            self_id = self.self_id or "0"
            if self.is_group:
                return f"{CHANNEL_ONEBOT}:{self_id}:group:{self.group_id}:{self.user_id}"
            return f"{CHANNEL_ONEBOT}:{self_id}:private:{self.user_id}"
        if self.channel == CHANNEL_WECHAT:
            return f"{CHANNEL_WECHAT}:{self.channel_id}:{self.user_id}"
        if self.channel == CHANNEL_CONSOLE:
            return f"{CHANNEL_CONSOLE}:{self.channel_id}:{self.user_id or 'console'}"
        # 未知渠道兜底：至少保证同一渠道内稳定
        return f"{self.channel or 'unknown'}:{self.channel_id}:{self.user_id or 'anonymous'}"

    @property
    def user_label(self) -> str:
        """展示用用户标识：优先昵称/群名片，其次平台 ID。"""
        name = self.user_name.strip()
        identifier = self.user_id.strip()
        if self.channel == CHANNEL_ONEBOT:
            base = name or (f"QQ用户{identifier}" if identifier else "QQ用户")
            if self.is_group and self.group_id:
                return f"{base}（群{self.group_id}）"
            return base
        if self.channel == CHANNEL_WECHAT:
            return name or (f"微信用户{identifier}" if identifier else "微信用户")
        if self.channel == CHANNEL_CONSOLE:
            return name or "控制台访客"
        return name or identifier or "匿名用户"

    @property
    def outbound_target(self) -> dict[str, str]:
        """发送回复所需的平台目标参数。"""
        return {
            "channel": self.channel,
            "channel_id": self.channel_id,
            "chat_type": self.chat_type,
            "user_id": self.user_id,
            "group_id": self.group_id,
        }


def make_console_message(channel_id: str, text: str, *, user_id: str = "",
                         user_name: str = "") -> IncomingMessage:
    """构造控制台调试通道消息（供 /bots/channels/{id}/test 与自测复用）。"""
    return IncomingMessage(
        channel=CHANNEL_CONSOLE,
        channel_id=channel_id,
        text=text,
        user_id=user_id or "console",
        user_name=user_name or "控制台访客",
        chat_type=CHAT_PRIVATE,
    )

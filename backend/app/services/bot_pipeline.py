"""机器人统一入站处理管线。

所有渠道（QQ OneBot 11 / 微信公众号 / 控制台调试）的消息最终都进入
:func:`handle_incoming`，由它完成：

1. 构造会话标识 ``session_key`` 与展示用 ``user_label``（契约 §5.5）；
2. 惰性调用 ``services.counselor.answer(...)``（由 backend-core 维护）；
3. 统一返回 ``{channel, channel_id, conversation_id, reply, citations,
   latency_ms, outbound}``，其中 ``outbound`` 是「将要发送给平台」的报文描述，
   供前端 / 调试展示；
4. 任何异常都不会向上抛：counselor 未就绪或 LLM/检索失败时返回友好提示，
   并尽量把兜底对话落库，保证消息日志可追溯。

设计原则：bot-gateway 不自己拼提示词、不直接访问 LLM / 知识库内部实现。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .. import db
from ..adapters import onebot, wechat_mp
from ..adapters.base import (
    CHANNEL_CONSOLE,
    CHANNEL_ONEBOT,
    CHANNEL_WECHAT,
    CHAT_GROUP,
    CHAT_PRIVATE,
    IncomingMessage,
    make_console_message,
)

logger = logging.getLogger("npu-assistant")

# 与 /api/chat 保持一致的单条消息长度上限（契约 §5.2）
MAX_QUESTION_CHARS = 2000

FALLBACK_REPLY = (
    "抱歉，助手暂时没能处理这条消息（可能是模型或知识库服务正在初始化）。"
    "请稍后再试，或直接联系你的辅导员老师。"
)


# ---------------------------------------------------------------------------
# 构造入站消息
# ---------------------------------------------------------------------------
def _channel_config(channel: dict | None) -> dict:
    if not isinstance(channel, dict):
        return {}
    return db.from_json(channel.get("config"), {}) or {}


def incoming_for_channel(channel: dict, text: str, *, user_id: str = "",
                         user_name: str = "") -> IncomingMessage:
    """把一条「模拟入站」文本包装成对应渠道的 IncomingMessage。

    用于 ``POST /api/bots/channels/{id}/test``：不真正走平台协议，但后续处理与
    真实消息完全一致（同一套 session_key / counselor / outbound）。
    """
    channel_type = str(channel.get("type") or "")
    channel_id = str(channel.get("id") or "")
    config = _channel_config(channel)
    clean = str(text or "").strip()

    if channel_type == CHANNEL_ONEBOT:
        self_id = str(config.get("self_id") or "")
        return IncomingMessage(
            channel=CHANNEL_ONEBOT,
            channel_id=channel_id,
            text=clean,
            user_id=user_id or "10001",
            user_name=user_name or "QQ测试同学",
            chat_type=CHAT_PRIVATE,
            self_id=self_id,
        )
    if channel_type == CHANNEL_WECHAT:
        return IncomingMessage(
            channel=CHANNEL_WECHAT,
            channel_id=channel_id,
            text=clean,
            user_id=user_id or "test-openid",
            user_name=user_name or "微信测试用户",
            chat_type=CHAT_PRIVATE,
        )
    return make_console_message(channel_id, clean, user_id=user_id, user_name=user_name)


# ---------------------------------------------------------------------------
# 回复装饰
# ---------------------------------------------------------------------------
def _append_footer(reply: str) -> str:
    """追加设置里的机器人回复尾注（``bot_reply_footer``）。"""
    try:
        footer = str(db.get_setting("bot_reply_footer") or "")
    except Exception:  # noqa: BLE001 - 设置缺失不应影响回复
        return reply
    footer = footer.strip()
    if not footer:
        return reply
    if reply.rstrip().endswith(footer):
        return reply
    return f"{reply}\n\n{footer}"


# ---------------------------------------------------------------------------
# 兜底落库
# ---------------------------------------------------------------------------
def _persist_fallback(message: IncomingMessage, question: str, reply: str) -> str | None:
    """counselor 不可用时，仍把这一轮对话写入 conversations/messages。

    这样「消息日志」接口不会丢失调试记录；失败只记 warning。
    """
    try:
        conversation = db.find_or_create_conversation(
            channel=message.channel,
            session_key=message.session_key,
            channel_id=message.channel_id,
            user_label=message.user_label,
        )
        conversation_id = str(conversation["id"])
        if question:
            db.add_message(conversation_id, "user", question)
        db.add_message(conversation_id, "assistant", reply, citations=[])
        return conversation_id
    except Exception:  # noqa: BLE001
        logger.warning("机器人兜底回复落库失败（已忽略）", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 出站报文构造
# ---------------------------------------------------------------------------
def _load_channel_config(message: IncomingMessage) -> dict:
    try:
        row = db.query_one("SELECT config FROM bot_channels WHERE id = ?", (message.channel_id,))
    except Exception:  # noqa: BLE001
        return {}
    return db.from_json(row.get("config") if row else None, {}) or {}


def _build_outbound(message: IncomingMessage, reply: str, channel_config: dict | None) -> dict:
    """构造将要发送给平台的报文；不发起任何网络请求。"""
    config = channel_config if isinstance(channel_config, dict) else _load_channel_config(message)
    try:
        if message.channel == CHANNEL_ONEBOT:
            return onebot.build_outbound(
                config,
                chat_type=message.chat_type,
                user_id=message.user_id,
                group_id=message.group_id,
                text=reply,
            )
        if message.channel == CHANNEL_WECHAT:
            raw = message.raw if isinstance(message.raw, dict) else {}
            return wechat_mp.build_outbound(
                {
                    "FromUserName": raw.get("FromUserName") or message.user_id,
                    "ToUserName": raw.get("ToUserName") or "",
                },
                reply,
                channel_config=config,
            )
    except Exception:  # noqa: BLE001 - 报文构造失败不应影响回复
        logger.warning("构造机器人出站报文失败（已忽略）", exc_info=True)
    return {"type": message.channel or CHANNEL_CONSOLE, "text": reply}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def handle_incoming(message: IncomingMessage, *, channel_config: dict | None = None) -> dict:
    """处理一条归一化入站消息并返回统一结果（永不抛异常）。

    返回字段严格对应契约 §5.5 ``POST /bots/channels/{id}/test`` 的响应：
    ``{channel, channel_id, conversation_id, reply, citations, latency_ms, outbound}``
    """
    started = time.perf_counter()
    session_key = message.session_key
    user_label = message.user_label
    question = (message.text or "").strip()[:MAX_QUESTION_CHARS]

    reply = ""
    citations: list = []
    conversation_id: str | None = None

    if not question:
        reply = "请告诉我你想咨询的问题，例如“国家奖学金怎么申请？”。"

    try:
        # 惰性导入：counselor 由 backend-core 并行开发，尚未就绪时也要能导入本模块
        from ..services import counselor  # noqa: PLC0415

        result = counselor.answer(
            question,
            channel=message.channel,
            session_key=session_key,
            user_label=user_label,
        ) or {}
        conversation_id = str(result.get("conversation_id") or "") or None
        reply = str(result.get("reply") or "").strip()
        citations = result.get("citations") or []
        if not reply:
            reply = FALLBACK_REPLY
    except Exception:  # noqa: BLE001 - 任何问答链路异常都降级为友好提示
        logger.exception(
            "机器人管线调用 counselor.answer 失败：channel=%s session_key=%s",
            message.channel, session_key,
        )
        reply = FALLBACK_REPLY

    reply = _append_footer(reply)

    # counselor 不可用时也要保证消息日志里有记录
    if conversation_id is None and question:
        conversation_id = _persist_fallback(message, question, reply)

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "机器人消息处理完成：channel=%s session_key=%s conversation=%s latency=%sms",
        message.channel, session_key, conversation_id, latency_ms,
    )

    return {
        "channel": message.channel,
        "channel_id": message.channel_id,
        "conversation_id": conversation_id,
        "reply": reply,
        "citations": citations,
        "latency_ms": latency_ms,
        "outbound": _build_outbound(message, reply, channel_config),
    }

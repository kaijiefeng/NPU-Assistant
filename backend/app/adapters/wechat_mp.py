"""微信公众号适配器（明文模式）。

职责：
1. ``check_signature``：校验微信服务器签名 ``sha1(sort([token, timestamp, nonce]))``；
2. ``parse_xml``：解析微信回调 XML（拒绝 DOCTYPE/ENTITY，防实体扩展攻击）；
3. ``extract_text``：提取文本消息内容，非文本消息给出引导语；
4. ``build_reply_xml``：构造被动回复 XML（所有文本字段用 CDATA 并正确转义）。

说明：MVP 只支持**明文模式**，加密模式不在范围内（见 CONTRACT.md §1）。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any

from .base import CHANNEL_WECHAT

logger = logging.getLogger("npu-assistant")

# 微信回调 XML 体积上限（防止超大请求拖垮解析）
MAX_XML_BYTES = 1024 * 1024

NON_TEXT_GUIDE = (
    "目前我只看得懂文字消息哦～请直接输入文字问题，例如“国家奖学金怎么申请？”"
    "或“请假流程是什么？”，我会结合知识库为你解答。"
)
EMPTY_TEXT_GUIDE = "请告诉我你想咨询的问题，例如“国家奖学金怎么申请？”。"


def check_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    """微信服务器签名校验。

    期望签名 = sha1("".join(sorted([token, timestamp, nonce])))，与传入的
    ``signature`` 做常量时间比较。任一参数缺失都视为校验失败。
    """
    token = str(token or "")
    signature = str(signature or "").strip().lower()
    timestamp = str(timestamp or "").strip()
    nonce = str(nonce or "").strip()
    if not token or not signature or not timestamp or not nonce:
        return False
    raw = "".join(sorted([token, timestamp, nonce]))
    expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_xml(payload: str | bytes) -> dict[str, str]:
    """解析微信回调 XML 为一层字典（``{标签: 文本}``）。

    防御措施：
    * 拒绝包含 ``<!DOCTYPE`` / ``<!ENTITY`` 的报文（不用 defusedxml 也能避免
      十亿笑声等实体扩展攻击）；
    * 限制报文体积；
    * 解析失败统一抛出带中文信息的 ``ValueError``，由路由转换为 400。
    """
    if isinstance(payload, bytes):
        if len(payload) > MAX_XML_BYTES:
            raise ValueError("微信回调 XML 过大，已拒绝解析")
        text = payload.decode("utf-8", errors="replace")
    else:
        text = str(payload or "")
        if len(text.encode("utf-8", errors="ignore")) > MAX_XML_BYTES:
            raise ValueError("微信回调 XML 过大，已拒绝解析")

    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError("微信回调 XML 包含被拒绝的 DTD/实体声明")
    if not text.strip():
        raise ValueError("微信回调 XML 为空")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"微信回调 XML 解析失败：{exc}") from exc

    result: dict[str, str] = {}
    for child in list(root):
        if not isinstance(child.tag, str):
            continue
        # CDATA 内容在 ElementTree 中就是普通 text
        result[child.tag] = (child.text or "").strip()
    return result


def extract_text(message: dict[str, str] | None) -> str:
    """提取文本消息内容；非 text 类型返回空串。"""
    if not isinstance(message, dict):
        return ""
    if str(message.get("MsgType") or "").strip().lower() != "text":
        return ""
    return str(message.get("Content") or "").strip()


def reply_text_for(message: dict[str, str] | None) -> str | None:
    """按消息类型给出回复文本：文本消息返回 ``None``（交给管线回答），
    非文本消息返回固定的引导语。"""
    if not isinstance(message, dict):
        return NON_TEXT_GUIDE
    msg_type = str(message.get("MsgType") or "").strip().lower()
    if msg_type != "text":
        return NON_TEXT_GUIDE
    if not str(message.get("Content") or "").strip():
        return EMPTY_TEXT_GUIDE
    return None


def _cdata(value: Any) -> str:
    """生成 CDATA 片段；``]]>`` 需要拆开转义，否则会提前闭合 CDATA。"""
    text = str(value if value is not None else "")
    text = text.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{text}]]>"


def build_reply_xml(to_user: str, from_user: str, content: str,
                    create_time: int | None = None) -> str:
    """构造公众号被动回复 XML（文本消息，明文模式）。"""
    timestamp = int(time.time()) if create_time is None else int(create_time)
    return (
        "<xml>"
        f"<ToUserName>{_cdata(to_user)}</ToUserName>"
        f"<FromUserName>{_cdata(from_user)}</FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        f"<MsgType>{_cdata('text')}</MsgType>"
        f"<Content>{_cdata(content)}</Content>"
        "</xml>"
    )


def build_outbound(message: dict[str, str] | None, reply: str,
                   channel_config: dict | None = None) -> dict:
    """构造「将要返回给微信服务器」的报文描述（用于 /test 展示）。"""
    message = message if isinstance(message, dict) else {}
    from_user = str(message.get("FromUserName") or "")
    to_user = str(message.get("ToUserName") or "")
    # 测试场景没有真实 XML：用占位值展示报文结构
    to_user = to_user or from_user or "test-openid"
    from_user = from_user or "npu-assistant"
    return {
        "type": CHANNEL_WECHAT,
        "content_type": "application/xml",
        "to_user": to_user,
        "from_user": from_user,
        "text": reply,
        "xml": build_reply_xml(to_user=to_user, from_user=from_user, content=reply),
    }

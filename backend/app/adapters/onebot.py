"""OneBot 11（QQ）适配器。

职责：
1. ``normalize_event``：把 OneBot 11 事件 JSON 归一化为 :class:`IncomingMessage`；
   支持私聊 / 群聊、消息段数组与 CQ 码字符串两种消息格式，并忽略机器人自己
   发出的消息（``self_id`` 与 ``user_id`` 相同，或 ``post_type=message_sent``）。
2. ``send_message``：通过 OneBot HTTP API 发送回复，失败只记日志、绝不抛出。
3. ``build_outbound``：仅构造「将要发送的报文」，用于控制台展示，不做网络请求。

参考：https://github.com/botuniverse/onebot-11
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import CHANNEL_ONEBOT, CHAT_GROUP, CHAT_PRIVATE, IncomingMessage

logger = logging.getLogger("npu-assistant")

# OneBot 字符串格式里的 CQ 码，例如 [CQ:at,qq=123]、[CQ:image,file=xxx]
_CQ_CODE_RE = re.compile(r"\[CQ:[^\]]*\]")
# 字符串格式里的 @ 码，捕获 qq 参数（数字或 all），允许 name= 等附加参数
_CQ_AT_RE = re.compile(r"\[CQ:at,qq=([^,\]]+)(?:,[^\]]*)?\]")
# CQ 码字符串格式规定的转义（见 OneBot 11 规范）
_CQ_UNESCAPE = {
    "&#91;": "[",
    "&#93;": "]",
    "&#44;": ",",
    "&amp;": "&",
}


def _unescape_cq_text(text: str) -> str:
    for entity, char in _CQ_UNESCAPE.items():
        text = text.replace(entity, char)
    return text


def _strip_cq_codes(text: str) -> str:
    """去掉 CQ 码，只保留可读文本。

    例如 ``[CQ:at,qq=123] 你好`` -> `` 你好``。图片/表情等非文本消息会被丢弃，
    归一化后若为空则由调用方忽略该事件。
    """
    cleaned = _CQ_CODE_RE.sub("", text)
    return _unescape_cq_text(cleaned)


def _at_targets(message: Any) -> set[str]:
    """提取消息中的 @ 对象（QQ 号或 ``all``），兼容消息段数组与 CQ 码字符串。"""
    targets: set[str] = set()
    if isinstance(message, str):
        for match in _CQ_AT_RE.finditer(message):
            targets.add(match.group(1).strip())
        return targets
    if isinstance(message, (list, tuple)):
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type") or "").lower() != "at":
                continue
            data = segment.get("data")
            if isinstance(data, dict) and data.get("qq") is not None:
                targets.add(str(data.get("qq")).strip())
    return targets


def is_mentioned(message: Any, self_id: str) -> bool:
    """判断消息是否 @ 了机器人（``qq=all`` 也视为提到）。"""
    targets = _at_targets(message)
    if "all" in targets:
        return True
    return bool(self_id) and str(self_id) in targets


def verify_hmac_signature(token: str, body: bytes, signature: str) -> bool:
    """校验 NapCat「HTTP 客户端」的 ``X-Signature: sha1=<hmac_sha1(body, token)>``。"""
    token = str(token or "")
    value = str(signature or "").strip()
    if not token or not value:
        return False
    if value.lower().startswith("sha1="):
        value = value[5:].strip()
    expected = hmac.new(token.encode("utf-8"), body or b"", hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, value.lower())


# 富消息段（JSON 卡片 / 分享 / XML）可能没有 text 段，但其中带有链接
_RICH_SEGMENT_TYPES = {"json", "share", "xml"}


def has_rich_url(message: Any) -> bool:
    """判断消息段数组中是否含带 http(s) 链接的 JSON 卡片/分享/XML 段。

    用于管理员「转发即入库」：这类卡片消息没有 text 段，但链接就在卡片 JSON 里；
    纯图片/表情/语音等非文本消息仍返回 False（由调用方忽略）。
    """
    if isinstance(message, (list, tuple)):
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type") or "").lower() not in _RICH_SEGMENT_TYPES:
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                blob = " ".join(str(value) for value in data.values())
            else:
                blob = str(data or "")
            if "http" in blob.lower():
                return True
    return False


def extract_text(message: Any) -> str:
    """从 OneBot 消息字段提取纯文本。

    * ``list``：消息段数组，只取 ``type == "text"`` 段的 ``data.text``；
    * ``str``：CQ 码字符串或纯文本，去掉 CQ 码后返回；
    * 其他：返回空串。
    """
    if isinstance(message, str):
        return _strip_cq_codes(message)
    if isinstance(message, (list, tuple)):
        parts: list[str] = []
        for segment in message:
            if isinstance(segment, str):
                parts.append(_unescape_cq_text(segment))
                continue
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type") or "").lower() != "text":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                parts.append(str(data.get("text") or ""))
        return "".join(parts)
    return ""


def normalize_event(event: Any, *, channel_id: str, config: dict | None = None,
                    self_id: str | None = None,
                    require_mention: bool = False) -> IncomingMessage | None:
    """归一化 OneBot 11 事件；非消息事件 / 机器人自身消息返回 ``None``。

    ``require_mention=True`` 时，群聊消息必须 @ 了机器人（或 @全体成员）才会
    被处理；私聊不受影响。该策略由路由层按通道配置 ``group_mention_only`` 传入。
    """
    if not isinstance(event, dict):
        return None

    post_type = str(event.get("post_type") or "")
    if post_type not in {"message", "message_sent"}:
        # heartbeat/lifecycle/notice/request 等交给调用方忽略
        return None
    if post_type == "message_sent":
        # 部分实现会回显机器人自己发出的消息，必须忽略，避免自问自答
        return None

    message_type = str(event.get("message_type") or "")
    if message_type not in {CHAT_PRIVATE, CHAT_GROUP}:
        return None

    configured_self_id = str((config or {}).get("self_id") or self_id or "")
    event_self_id = str(event.get("self_id") or "")
    effective_self_id = configured_self_id or event_self_id

    user_id = str(event.get("user_id") or "")
    if not user_id:
        return None
    if effective_self_id and user_id == effective_self_id:
        # 自己发的消息（部分客户端仍会推送 message 事件）
        return None

    group_id = ""
    if message_type == CHAT_GROUP:
        group_id = str(event.get("group_id") or "")
        if not group_id:
            return None
        # 群聊「仅被 @ 时回复」：同时兼容消息段数组与 CQ 码字符串
        if require_mention and effective_self_id:
            mentioned = is_mentioned(event.get("message"), effective_self_id) or is_mentioned(
                event.get("raw_message"), effective_self_id
            )
            if not mentioned:
                return None

    text = extract_text(event.get("message"))
    if not text.strip():
        # 纯图片/表情/语音等消息：交给 /test 侧兜底提示，这里视为不可处理
        fallback_text = extract_text(event.get("raw_message"))
        text = fallback_text
    if not text.strip():
        # JSON 卡片 / 分享 / XML 富消息常常没有 text 段，但其中带链接：
        # 仍归一化并交给 bot_pipeline 做管理员「转发即入库」判定。
        if not has_rich_url(event.get("message")):
            return None

    sender = event.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    user_name = str(sender.get("card") or sender.get("nickname") or "").strip()

    return IncomingMessage(
        channel=CHANNEL_ONEBOT,
        channel_id=str(channel_id),
        text=text.strip(),
        user_id=user_id,
        user_name=user_name,
        chat_type=message_type,
        group_id=group_id,
        self_id=effective_self_id,
        message_id=str(event.get("message_id") or ""),
        raw=event,
    )


def _as_int_if_numeric(value: Any) -> Any:
    """OneBot 的 user_id/group_id 通常是数字，尽量还原成 int 以兼容严格实现。"""
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return text


def _api_base(config: dict | None) -> str:
    return str((config or {}).get("api_base") or "").strip().rstrip("/")


def _is_http_url(url: str) -> bool:
    """只允许 http/https 绝对地址（api_base 不允许 file:// 等协议）。"""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def _safe_url(url: str) -> str:
    """脱敏 URL：去掉 query/fragment/userinfo，仅保留 scheme://host[:port]/path。

    用于日志与错误信息，避免把 NapCat 地址里的凭据或敏感查询参数写进日志。
    """
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return "[无效地址]"
    if not parts.scheme or not parts.netloc:
        return "[无效地址]"
    host = parts.hostname or ""
    try:
        if parts.port:
            host = f"{host}:{parts.port}"
    except ValueError:
        pass
    return f"{parts.scheme}://{host}{parts.path or ''}"


_ERROR_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


def _safe_error_text(text: str) -> str:
    """把错误文本中的 URL 替换为脱敏形式（httpx 异常常带完整 URL）。"""
    def _replace(match: re.Match[str]) -> str:
        return _safe_url(match.group(0))

    return _ERROR_URL_RE.sub(_replace, str(text or ""))[:300]


def _request_base(config: dict | None) -> str:
    """实际请求用的 api_base：去掉多余的 query/fragment（规范化后拼接 API 路径）。

    ``api_base`` 正常形如 ``http://127.0.0.1:3000``；若管理员误填了带 query/fragment
    的值，只取 scheme/host/path 部分，避免拼出 ``...?x=1/send_private_msg`` 这种非法 URL。
    """
    base = _api_base(config)
    if not base:
        return ""
    try:
        parts = urlsplit(base)
    except ValueError:
        return base
    if parts.query or parts.fragment:
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
        return base.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return base


def _endpoint(config: dict | None, chat_type: str) -> tuple[str, str]:
    action = "send_group_msg" if chat_type == CHAT_GROUP else "send_private_msg"
    base = _request_base(config)
    return action, f"{base}/{action}" if base else f"/{action}"


def build_payload(chat_type: str, *, user_id: str = "", group_id: str = "",
                  text: str = "") -> dict:
    if chat_type == CHAT_GROUP:
        return {"group_id": _as_int_if_numeric(group_id), "message": text}
    return {"user_id": _as_int_if_numeric(user_id), "message": text}


def build_outbound(config: dict | None, *, chat_type: str, user_id: str = "",
                   group_id: str = "", text: str = "") -> dict:
    """构造「将要通过 HTTP API 发送」的报文描述（用于控制台 / 前端展示）。

    ``api_base`` 可能包含 userinfo/query 等敏感信息，展示用的 ``url`` 一律脱敏，
    不把管理员配置的凭据回显到接口响应里。
    """
    action, url = _endpoint(config, chat_type)
    if _api_base(config):
        url = _safe_url(url)
    has_token = bool(str((config or {}).get("access_token") or ""))
    return {
        "type": CHANNEL_ONEBOT,
        "method": "POST",
        "action": action,
        "url": url,
        "auth": "bearer" if has_token else "none",
        "payload": build_payload(chat_type, user_id=user_id, group_id=group_id, text=text),
    }


def _client(timeout: float) -> httpx.Client:
    """OneBot HTTP API 客户端。

    * ``trust_env=False``：忽略系统 HTTP_PROXY/HTTPS_PROXY。NapCat 通常部署在本机
      （127.0.0.1），若走系统代理会被接管甚至失败（观察到约 5s 后才返回 502），
      导致机器人回复发不出去或明显延迟。
    * ``follow_redirects=False``：api_base 是管理员配置项，不跟随重定向，避免被
      跳转到非预期地址（SSRF/凭据外泄）。
    """
    return httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)


def send_message(config: dict | None, *, chat_type: str, user_id: str = "",
                 group_id: str = "", text: str = "", timeout: float = 15.0) -> dict:
    """调用 OneBot HTTP API 发送回复。

    任何失败（未配置、地址非法、网络异常、非 2xx、retcode != 0）都只写 warning
    日志，返回 ``{"ok": False, ...}``，绝不向调用方抛异常 —— 机器人回复失败不应
    影响已经完成的问答流程。日志中的 URL / 异常信息均做脱敏处理。
    """
    action = "send_group_msg" if chat_type == CHAT_GROUP else "send_private_msg"
    api_base = _api_base(config)
    if not api_base:
        logger.warning("OneBot 通道未配置 api_base，跳过发送：action=%s text=%r", action, text[:50])
        return {"ok": False, "action": action, "url": f"/{action}", "error": "通道未配置 api_base"}
    if not _is_http_url(api_base):
        logger.warning("OneBot 发送跳过：api_base 必须是 http/https 地址：action=%s", action)
        return {
            "ok": False,
            "action": action,
            "url": f"/{action}",
            "error": "api_base 必须是 http 或 https 地址",
        }

    url = f"{_request_base(config)}/{action}"
    safe_url = _safe_url(url)
    access_token = str((config or {}).get("access_token") or "").strip()
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    payload = build_payload(chat_type, user_id=user_id, group_id=group_id, text=text)

    try:
        with _client(timeout) as client:
            response = client.post(url, json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001 - 网络异常只记日志
        error_text = _safe_error_text(str(exc) or exc.__class__.__name__)
        logger.warning("OneBot 发送消息失败（已忽略）：url=%s error=%s", safe_url, error_text)
        return {"ok": False, "action": action, "url": safe_url, "error": error_text}

    body: Any = None
    try:
        body = response.json()
    except ValueError:
        body = None

    ok = 200 <= response.status_code < 300
    retcode = body.get("retcode") if isinstance(body, dict) else None
    if retcode not in (None, 0):
        ok = False
    if not ok:
        logger.warning(
            "OneBot 发送消息失败（已忽略）：url=%s status=%s retcode=%s body=%r",
            safe_url, response.status_code, retcode, body,
        )
    return {
        "ok": ok,
        "action": action,
        "url": safe_url,
        "status_code": response.status_code,
        "retcode": retcode,
        "response": body,
    }

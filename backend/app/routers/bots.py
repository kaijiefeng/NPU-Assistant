"""机器人接入路由（bot-gateway）。

覆盖 docs/CONTRACT.md §5.5 全部接口：

* 通道 CRUD（console 通道不可删除）与 webhook 地址展示；
* 控制台模拟入站 ``POST /bots/channels/{id}/test``；
* 机器人消息日志聚合 ``GET /bots/logs``；
* QQ OneBot 11 反向 WebSocket / 反向 HTTP 事件入口 + HTTP API 回复；
* 微信公众号（明文模式）GET 接入校验 / POST XML 被动回复。

设计约束：
* 单个渠道的任何异常都不得影响服务本身：网络发送失败只记日志；
* WS 处理器为 async，同步服务（counselor.answer / httpx）通过
  ``asyncio.to_thread`` 调用，避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .. import db
from ..adapters import onebot, wechat_mp
from ..adapters.base import (
    CHANNEL_CONSOLE,
    CHANNEL_ONEBOT,
    CHANNEL_QQ_OFFICIAL,
    CHANNEL_WECHAT,
    IncomingMessage,
    channel_type_label,
    config_bool,
    normalize_channel_config,
    normalize_channel_type,
    parse_admin_user_ids,
)
from ..security import require_admin_token
from ..services import bot_pipeline, qq_gateway

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["bots"])

# 与 app/main.py 中的 API_PREFIX 保持一致（不 import main，避免循环依赖）
API_PREFIX = "/api"

DEFAULT_CHANNEL_NAMES = {
    CHANNEL_QQ_OFFICIAL: "QQ 机器人（开放平台）",
    CHANNEL_ONEBOT: "QQ 机器人（OneBot 11）",
    CHANNEL_WECHAT: "微信公众号",
    CHANNEL_CONSOLE: "控制台调试通道",
}

# 微信要求 5 秒内返回被动回复；超过阈值先回兜底提示，避免公众号侧报「该公众号暂时无法提供服务」
WECHAT_REPLY_TIMEOUT_SECONDS = 4.5
WECHAT_TIMEOUT_REPLY = "这个问题需要一点时间查询，请稍后再发送一次，我会结合知识库为你详细解答。"

# 安全：凭据脱敏与 webhook 防护（见 docs/MOBILE-OPS.md §2.2 / BOT-INTEGRATION.md）
MASK_PREFIX = "****"
ADMIN_IDS_MASK_PREFIX = "***"
# 公开默认 Token（历史上用于演示），安全模式下必须更换
WECHAT_INSECURE_DEFAULT_TOKEN = "npu-assistant"
# 微信回调时间戳允许偏差（防重放）
WECHAT_TIMESTAMP_TOLERANCE_SECONDS = 300
# 本地调试逃生开关：置 1 时允许 webhook 使用空 Token（默认关闭，仅限本机）
INSECURE_ENV_VAR = "NPU_BOT_ALLOW_INSECURE"
# 微信 nonce 防重放：进程内缓存（channel_id + nonce），TTL 略大于时间窗，限量防内存膨胀
WECHAT_NONCE_TTL_SECONDS = 330
WECHAT_NONCE_CACHE_MAX = 1000
# webhook 请求体上限（Lead 已在全局中间件限制 2MB，这里再防一手）
MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _channel_config(row: dict | None) -> dict:
    if not isinstance(row, dict):
        return {}
    config = db.from_json(row.get("config"), {}) or {}
    return config if isinstance(config, dict) else {}


# ---------------------------------------------------------------------------
# 安全：凭据脱敏 / webhook Token 策略
# ---------------------------------------------------------------------------
def _credential_field(channel_type: str) -> str | None:
    """各渠道的凭据字段名（onebot 用 access_token，微信复用 token，QQ 官方用 app_secret）。"""
    if channel_type == CHANNEL_ONEBOT:
        return "access_token"
    if channel_type == CHANNEL_WECHAT:
        return "token"
    if channel_type == CHANNEL_QQ_OFFICIAL:
        return "app_secret"
    return None


def _get_credential(channel_type: str, config: dict) -> str:
    key = _credential_field(channel_type)
    if not key:
        return ""
    return str((config or {}).get(key) or "").strip()


def _mask_secret(value: Any) -> str:
    """脱敏凭据：``"****" + 后 4 位``（空值保持空串）。"""
    text = str(value or "")
    if not text:
        return ""
    suffix = text[-4:] if len(text) > 4 else ""
    return f"{MASK_PREFIX}{suffix}"


def _mask_admin_user_ids(value: Any) -> str:
    """脱敏管理员列表：只暴露数量，例如 ``"***（2 个管理员）"``。"""
    admin_ids = parse_admin_user_ids(value)
    if not admin_ids:
        return ""
    return f"{ADMIN_IDS_MASK_PREFIX}（{len(admin_ids)} 个管理员）"


def _mask_api_base(value: Any) -> str:
    """脱敏 ``api_base``：去掉 userinfo 与 query/fragment，避免泄露凭据。

    例如 ``https://user:pass@host:8443/x?token=SECRET#f`` -> ``https://host:8443/x``；
    无 scheme 的异常写法也去掉 ``?``/``#`` 后的内容并遮蔽 ``user:pass@`` 部分。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "[已配置]"
    if parts.scheme and parts.netloc:
        host = parts.hostname or ""
        try:
            if parts.port:
                host = f"{host}:{parts.port}"
        except ValueError:
            pass
        return f"{parts.scheme}://{host}{parts.path or ''}"
    # 无 scheme/netloc 的异常写法：至少去掉 query/fragment 与 userinfo
    cleaned = text.split("?", 1)[0].split("#", 1)[0]
    head, _, rest = cleaned.partition("/")
    if "@" in head:
        head = "***@" + head.split("@", 1)[1]
        cleaned = head + ("/" + rest if rest else "")
    return cleaned


def _masked_config(channel_type: str, config: dict) -> dict:
    """对外返回的通道 config：凭据、管理员 ID、api_base 中的凭据一律脱敏。"""
    masked = dict(config or {})
    credential_key = _credential_field(channel_type)
    if credential_key:
        masked[credential_key] = _mask_secret(masked.get(credential_key))
    if "admin_user_ids" in masked:
        masked["admin_user_ids"] = _mask_admin_user_ids(masked.get("admin_user_ids"))
    if "api_base" in masked:
        masked["api_base"] = _mask_api_base(masked.get("api_base"))
    return masked


def _clean_config_update(channel_type: str, updates: dict, existing_config: dict) -> dict:
    """处理 PUT config 的脱敏回填语义。

    * 凭据字段传空串、或回填脱敏占位（``****xxxx``）→ 视为「不修改」；
    * api_base 回填脱敏后的值（与当前值脱敏结果相同）→ 不修改；
    * admin_user_ids 回填脱敏占位（``***（N 个管理员）``）→ 不修改；
      传空串 / 新值 → 按值更新（空串用于清空）。
    """
    cleaned = dict(updates or {})
    credential_key = _credential_field(channel_type)
    if credential_key and credential_key in cleaned:
        raw = cleaned.get(credential_key)
        value = "" if raw is None else str(raw)
        if not value.strip() or value.startswith(MASK_PREFIX):
            cleaned.pop(credential_key, None)
    if isinstance(cleaned.get("admin_user_ids"), str):
        if cleaned["admin_user_ids"].startswith(ADMIN_IDS_MASK_PREFIX):
            cleaned.pop("admin_user_ids", None)
    if "api_base" in cleaned:
        provided = str(cleaned.get("api_base") or "").strip()
        current_masked = _mask_api_base((existing_config or {}).get("api_base"))
        # 表单回填的是脱敏值 → 保留数据库中的原始 api_base（含 userinfo/query）
        if provided and current_masked and provided == current_masked:
            cleaned.pop("api_base", None)
    return cleaned


def _allow_insecure_bots() -> bool:
    """本地调试逃生开关：``NPU_BOT_ALLOW_INSECURE=1`` 时允许 webhook 空 Token。"""
    return os.environ.get(INSECURE_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def _onebot_token_configured(config: dict) -> bool:
    return bool(_get_credential(CHANNEL_ONEBOT, config))


def _wechat_token_secure(config: dict) -> bool:
    token = _get_credential(CHANNEL_WECHAT, config)
    return bool(token) and token != WECHAT_INSECURE_DEFAULT_TOKEN


def _has_usable_credential(channel_type: str, config: dict) -> bool:
    """对外的 ``has_access_token``：表示该通道是否已配置**可用**凭据。

    微信空 Token / 公开默认 Token 不算可用（webhook 会被拒绝）。
    """
    if channel_type == CHANNEL_ONEBOT:
        return _onebot_token_configured(config)
    if channel_type == CHANNEL_WECHAT:
        return _wechat_token_secure(config)
    if channel_type == CHANNEL_QQ_OFFICIAL:
        return bool(str(config.get("app_id") or "").strip()) and bool(
            str(config.get("app_secret") or "").strip()
        )
    return False


def _wechat_webhook_security_message() -> str:
    return (
        "微信公众号通道未配置安全 Token（不能为空或使用公开默认值 npu-assistant），"
        "请在通道配置中填写与公众号后台一致的 Token 后重试"
    )


def _require_onebot_webhook_token(config: dict) -> str | None:
    """OneBot webhook Token 策略：空 Token 一律拒绝（除非显式开启本地调试）。

    返回错误提示文本（None 表示通过）。
    """
    if _onebot_token_configured(config) or _allow_insecure_bots():
        return None
    return "请先在通道配置 access_token 后再启用 OneBot webhook"


# 微信 nonce 防重放缓存：(channel_id, nonce) -> 过期时间戳；进程内、带 TTL 与容量上限
_WECHAT_NONCE_CACHE: OrderedDict[tuple[str, str], float] = OrderedDict()
_WECHAT_NONCE_LOCK = threading.Lock()


def _register_wechat_nonce(channel_id: str, nonce: str) -> bool:
    """登记微信回调 nonce；返回 False 表示重放（TTL 内已出现过）。

    * 空 nonce 无法判定，直接放行（GET 接入校验可能不带 nonce）；
    * 仅缓存有限时间（330s）与有限条数（1000），过期条目在写入时顺带清理。
    """
    nonce = str(nonce or "").strip()
    if not nonce:
        return True
    key = (str(channel_id), nonce)
    now = time.time()
    with _WECHAT_NONCE_LOCK:
        while _WECHAT_NONCE_CACHE:
            _, expires_at = next(iter(_WECHAT_NONCE_CACHE.items()))
            if expires_at <= now:
                _WECHAT_NONCE_CACHE.popitem(last=False)
            else:
                break
        if key in _WECHAT_NONCE_CACHE and _WECHAT_NONCE_CACHE[key] > now:
            return False
        _WECHAT_NONCE_CACHE[key] = now + WECHAT_NONCE_TTL_SECONDS
        _WECHAT_NONCE_CACHE.move_to_end(key)
        while len(_WECHAT_NONCE_CACHE) > WECHAT_NONCE_CACHE_MAX:
            _WECHAT_NONCE_CACHE.popitem(last=False)
    return True


async def _read_webhook_body(request: Request) -> bytes:
    """读取 webhook 请求体，并做 2MB 上限兜底（防止超大报文拖垮解析）。"""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="请求体过大（超过 2MB），已拒绝")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="请求体过大（超过 2MB），已拒绝")
    return body


def _validate_wechat_webhook(channel_id: str, config: dict, signature: str,
                             timestamp: str, nonce: str) -> None:
    """微信公众号 webhook 安全校验（Token 安全性 + 时间窗 + 签名 + nonce 防重放）。

    失败抛 403。本地调试开关 ``NPU_BOT_ALLOW_INSECURE=1`` 下：允许空/默认 Token，
    但仍校验时间窗与 nonce；若已配置安全 Token 则照常验签。
    """
    insecure_mode = _allow_insecure_bots()
    secure_token = _wechat_token_secure(config)
    if not insecure_mode and not secure_token:
        logger.warning(
            "微信公众号 webhook 拒绝：通道 token 为空或为公开默认值：channel=%s", channel_id
        )
        raise HTTPException(status_code=403, detail=_wechat_webhook_security_message())

    try:
        parsed_timestamp = int(str(timestamp or "").strip())
    except (TypeError, ValueError):
        parsed_timestamp = 0
    if parsed_timestamp <= 0 or abs(time.time() - parsed_timestamp) > WECHAT_TIMESTAMP_TOLERANCE_SECONDS:
        logger.warning("微信公众号 webhook 拒绝：时间戳无效或过期：channel=%s", channel_id)
        raise HTTPException(
            status_code=403,
            detail="微信回调时间戳无效或已过期（允许偏差 ±300 秒），请检查服务器时间后重试",
        )

    if secure_token and not wechat_mp.check_signature(
        _get_credential(CHANNEL_WECHAT, config), signature, timestamp, nonce
    ):
        logger.warning("微信公众号签名校验失败：channel=%s", channel_id)
        raise HTTPException(status_code=403, detail="微信公众号签名校验失败")

    if not _register_wechat_nonce(channel_id, nonce):
        logger.warning("微信公众号 webhook 拒绝：nonce 重复（疑似重放）：channel=%s", channel_id)
        raise HTTPException(
            status_code=403,
            detail="微信回调 nonce 重复（疑似重放攻击），已拒绝；请勿重复使用同一 nonce",
        )


def _webhook_urls(channel_id: str, channel_type: str) -> dict[str, str]:
    """返回该通道需要填给平台 / 客户端的完整相对路径。"""
    if channel_type == CHANNEL_ONEBOT:
        return {
            "ws": f"{API_PREFIX}/bots/onebot/{channel_id}/ws",
            "http": f"{API_PREFIX}/bots/onebot/{channel_id}/event",
        }
    if channel_type == CHANNEL_WECHAT:
        return {"callback": f"{API_PREFIX}/bots/wechat/{channel_id}"}
    return {}


def _serialize_channel(row: dict) -> dict:
    channel_type = str(row.get("type") or "")
    config = _channel_config(row)
    return {
        "id": row["id"],
        "type": channel_type,
        "name": row.get("name") or "",
        "config": _masked_config(channel_type, config),
        "has_access_token": _has_usable_credential(channel_type, config),
        "enabled": bool(row.get("enabled", 1)),
        "status": row.get("status") or "offline",
        "last_seen_at": row.get("last_seen_at"),
        "created_at": row.get("created_at"),
        "webhook_urls": _webhook_urls(str(row["id"]), channel_type),
    }


def _get_channel(channel_id: str) -> dict | None:
    return db.query_one("SELECT * FROM bot_channels WHERE id = ?", (channel_id,))


def _require_channel(channel_id: str, expected_type: str | None = None) -> dict:
    row = _get_channel(channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="机器人通道不存在")
    if expected_type and row.get("type") != expected_type:
        raise HTTPException(
            status_code=400,
            detail=f"该通道类型为 {channel_type_label(str(row.get('type') or ''))}，"
                   f"不是 {channel_type_label(expected_type)}",
        )
    return row


def _mark_channel(channel_id: str, status: str | None = None) -> None:
    """更新通道状态 / 最近活跃时间；失败只记日志。"""
    try:
        if status:
            db.execute(
                "UPDATE bot_channels SET status = ?, last_seen_at = ? WHERE id = ?",
                (status, db.now(), channel_id),
            )
        else:
            db.execute(
                "UPDATE bot_channels SET last_seen_at = ? WHERE id = ?",
                (db.now(), channel_id),
            )
    except Exception:  # noqa: BLE001 - 状态更新失败不影响主流程
        logger.warning("更新机器人通道状态失败（已忽略）：channel=%s", channel_id, exc_info=True)


def _bearer_token(headers: Any) -> str:
    try:
        authorization = headers.get("authorization") or ""
    except Exception:  # noqa: BLE001
        return ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization.strip()


def _onebot_ws_auth_ok(config: dict, query_token: str, headers: Any) -> bool:
    """OneBot 反向 WS 鉴权：同时接受 ``Authorization: Bearer`` 与 ``?access_token=``。

    * 通道未配置 access_token 时：仅当显式开启 ``NPU_BOT_ALLOW_INSECURE=1`` 才放行，
      否则路由层会直接返回 403；
    * 两种方式都带时值必须一致；
    * 任一方式与配置不一致 → 拒绝（路由返回 HTTP 401）。
    """
    expected = _get_credential(CHANNEL_ONEBOT, config)
    if not expected:
        return _allow_insecure_bots()
    query_token = str(query_token or "").strip()
    header_token = _bearer_token(headers).strip()
    if query_token and header_token and not hmac.compare_digest(query_token, header_token):
        return False
    provided = query_token or header_token
    return bool(provided) and hmac.compare_digest(expected, provided)


def _onebot_event_auth_ok(config: dict, query_token: str, signature_header: str,
                          body: bytes) -> bool:
    """OneBot 反向 HTTP 事件鉴权：query access_token 或 ``X-Signature`` HMAC-SHA1 二选一。

    NapCat「HTTP 客户端」的 Token 字段实际用于对请求正文做 HMAC-SHA1，
    不是 Bearer Token；两种方式都兼容。空 Token 仅本地调试开关放行。
    """
    expected = _get_credential(CHANNEL_ONEBOT, config)
    if not expected:
        return _allow_insecure_bots()
    query_token = str(query_token or "").strip()
    if query_token and hmac.compare_digest(expected, query_token):
        return True
    return onebot.verify_hmac_signature(expected, body, signature_header)


# ---------------------------------------------------------------------------
# 通道 CRUD
# ---------------------------------------------------------------------------
@router.get("/bots/channels")
def list_channels() -> dict:
    rows = db.query("SELECT * FROM bot_channels ORDER BY created_at, id")
    return {"items": [_serialize_channel(row) for row in rows]}


@router.post("/bots/channels", status_code=201)
def create_channel(payload: dict) -> dict:
    try:
        channel_type = normalize_channel_type(payload.get("type"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    name = str(payload.get("name") or "").strip() or DEFAULT_CHANNEL_NAMES[channel_type]
    config = normalize_channel_config(channel_type, payload.get("config"))
    token_generated = False
    if channel_type == CHANNEL_ONEBOT and not str(config.get("access_token") or "").strip():
        # 安全默认：OneBot webhook 不允许空 Token；未提供时自动生成随机 Token。
        # 响应只返回脱敏值；如需已知值，请通过 PUT 重新填写自定义 access_token。
        config["access_token"] = secrets.token_hex(16)
        token_generated = True
        logger.info("OneBot 通道已自动生成随机 access_token（默认不展示明文）")
    enabled = 1 if payload.get("enabled", True) else 0
    status = "online" if channel_type == CHANNEL_CONSOLE else "offline"

    channel_id = db.new_id()
    db.execute(
        "INSERT INTO bot_channels(id, type, name, config, enabled, status, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (channel_id, channel_type, name, db.to_json(config), enabled, status, db.now()),
    )
    logger.info("新增机器人通道：%s (%s)", name, channel_type)
    if channel_type == CHANNEL_QQ_OFFICIAL:
        # QQ 官方机器人是主动出站连接：创建后立刻按配置尝试连接
        qq_gateway.gateway.notify()
    serialized = _serialize_channel(_require_channel(channel_id))
    if token_generated:
        # 提示前端/管理员：随机 Token 只保存脱敏值，需自行填写已知 Token 才能配置 NapCat
        serialized["token_generated"] = True
        serialized["token_hint"] = (
            "已自动生成随机 access_token（出于安全仅展示脱敏值）；"
            "如需在 NapCat 中使用，请在通道配置中重新填写自定义 access_token"
        )
    return serialized


@router.put("/bots/channels/{channel_id}")
def update_channel(channel_id: str, payload: dict) -> dict:
    row = _require_channel(channel_id)
    channel_type = str(row.get("type") or "")

    updates: list[str] = []
    params: list[Any] = []

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="通道名称不能为空")
        updates.append("name = ?")
        params.append(name)

    if "config" in payload:
        # 与已有 config 合并；敏感字段留空 / 回填脱敏占位表示「不修改」
        raw_updates = payload.get("config")
        raw_updates = raw_updates if isinstance(raw_updates, dict) else {}
        merged = {
            **_channel_config(row),
            **_clean_config_update(channel_type, raw_updates, _channel_config(row)),
        }
        config = normalize_channel_config(channel_type, merged)
        updates.append("config = ?")
        params.append(db.to_json(config))

    if "enabled" in payload:
        updates.append("enabled = ?")
        params.append(1 if payload.get("enabled") else 0)

    if updates:
        params.append(channel_id)
        db.execute(f"UPDATE bot_channels SET {', '.join(updates)} WHERE id = ?", params)
    if channel_type == CHANNEL_QQ_OFFICIAL:
        # 配置 / 启用状态变化后重新对齐连接（旧任务会按指纹停止并重建）
        qq_gateway.gateway.notify()
    return _serialize_channel(_require_channel(channel_id))


@router.delete("/bots/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict:
    row = _require_channel(channel_id)
    if row.get("type") == CHANNEL_CONSOLE:
        raise HTTPException(status_code=400, detail="控制台调试通道不可删除")
    db.execute("DELETE FROM bot_channels WHERE id = ?", (channel_id,))
    if row.get("type") == CHANNEL_QQ_OFFICIAL:
        qq_gateway.gateway.notify()
    logger.info("删除机器人通道：%s (%s)", row.get("name"), row.get("type"))
    return {"ok": True}


# ---------------------------------------------------------------------------
# 控制台模拟入站
# ---------------------------------------------------------------------------
@router.post("/bots/channels/{channel_id}/test")
def test_channel(channel_id: str, payload: dict) -> dict:
    row = _require_channel(channel_id)
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    if len(message) > bot_pipeline.MAX_QUESTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"消息过长，最多 {bot_pipeline.MAX_QUESTION_CHARS} 字",
        )

    config = _channel_config(row)
    incoming = bot_pipeline.incoming_for_channel(
        row,
        message,
        user_id=str(payload.get("user_id") or ""),
        user_name=str(payload.get("user_name") or ""),
    )
    result = bot_pipeline.handle_incoming(incoming, channel_config=config)
    _mark_channel(channel_id)  # 只刷新 last_seen_at，测试不改变通道连接状态
    return result


# ---------------------------------------------------------------------------
# 消息日志（含学生消息/用户标签/session_key，需管理员令牌；未配置令牌时放行本地演示）
# ---------------------------------------------------------------------------
@router.get("/bots/logs", dependencies=[Depends(require_admin_token)])
def bot_logs(limit: int = Query(50, ge=1, le=200)) -> dict:
    conversations = db.query(
        "SELECT * FROM conversations WHERE channel != 'web' "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )
    items: list[dict] = []
    for conversation in conversations:
        conversation_id = str(conversation["id"])
        user_message = db.query_one(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        )
        assistant_message = db.query_one(
            "SELECT id, content, created_at FROM messages WHERE conversation_id = ? "
            "AND role = 'assistant' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        )
        items.append(
            {
                "id": (assistant_message or {}).get("id") or conversation_id,
                "channel": conversation.get("channel"),
                "session_key": conversation.get("session_key"),
                "user_label": conversation.get("user_label"),
                "message": (user_message or {}).get("content") or "",
                "reply": (assistant_message or {}).get("content") or "",
                "created_at": (assistant_message or {}).get("created_at")
                or conversation.get("updated_at"),
            }
        )
    return {"items": items}


# ---------------------------------------------------------------------------
# OneBot 11：事件处理（WS / HTTP 共用）
# ---------------------------------------------------------------------------
# 幂等缓存：NapCat 同时开启反向 WS 与反向 HTTP 时，同一事件会被投递两次；
# 按 message_id 做进程内去重（只保留最近若干条，避免内存无限增长）。
_EVENT_DEDUPE_MAX = 512
_SEEN_EVENTS: dict[str, OrderedDict[str, None]] = {}
_SEEN_EVENTS_LOCK = threading.Lock()


def _is_duplicate_event(channel_id: str, event: dict) -> bool:
    message_id = str(event.get("message_id") or "")
    if not message_id:
        return False
    with _SEEN_EVENTS_LOCK:
        cache = _SEEN_EVENTS.setdefault(channel_id, OrderedDict())
        if message_id in cache:
            return True
        cache[message_id] = None
        while len(cache) > _EVENT_DEDUPE_MAX:
            cache.popitem(last=False)
    return False


def _handle_onebot_event_sync(channel: dict, event: dict,
                              *, update_status: bool = True) -> dict | None:
    """同步处理一条 OneBot 事件：归一化 → 管线回答 → HTTP API 发回回复。

    该函数在 WS 场景下通过 ``asyncio.to_thread`` 调用；任何异常只记日志。
    ``update_status=False`` 时只刷新 ``last_seen_at``，不改连接状态 —— WS 模式下
    通道状态由连接生命周期维护，避免在途事件在断开后把状态改回 online。
    """
    channel_id = str(channel.get("id") or "")
    # 每次事件读取最新通道配置：用户在 WS 连接期间修改 api_base/token 后即时生效
    latest = _get_channel(channel_id)
    if not latest:
        logger.warning("OneBot 事件对应通道已被删除（已忽略）：channel=%s", channel_id)
        return None
    config = _channel_config(latest)
    try:
        if not isinstance(event, dict):
            return None
        # 安全：通道配置了 self_id 时，事件 self_id 必须一致（防伪造/串号）
        configured_self_id = str(config.get("self_id") or "").strip()
        if configured_self_id:
            event_self_id = str(event.get("self_id") or "").strip()
            if event_self_id != configured_self_id:
                logger.warning(
                    "忽略 self_id 与通道配置不一致的 OneBot 事件：channel=%s event_self_id=%s",
                    channel_id, event_self_id or "（空）",
                )
                return None
        post_type = str(event.get("post_type") or "")
        if post_type == "meta_event":
            # 心跳 / 生命周期事件：刷新在线时间即可
            _mark_channel(channel_id, "online" if update_status else None)
            return None

        incoming = onebot.normalize_event(
            event,
            channel_id=channel_id,
            config=config,
            require_mention=config_bool(config.get("group_mention_only"), True),
        )
        if incoming is None:
            return None
        if _is_duplicate_event(channel_id, event):
            logger.debug("忽略重复投递的 OneBot 事件：channel=%s message_id=%s",
                         channel_id, event.get("message_id"))
            return None

        result = bot_pipeline.handle_incoming(incoming, channel_config=config)
        send_result = onebot.send_message(
            config,
            chat_type=incoming.chat_type,
            user_id=incoming.user_id,
            group_id=incoming.group_id,
            text=str(result.get("reply") or ""),
        )
        if update_status:
            _mark_channel(channel_id, "online" if send_result.get("ok") else "error")
        return result
    except Exception:  # noqa: BLE001 - 单条事件失败不影响连接 / 服务
        logger.exception("处理 OneBot 事件失败（已忽略）：channel=%s", channel_id)
        return None


@router.websocket("/bots/onebot/{channel_id}/ws")
async def onebot_ws(websocket: WebSocket, channel_id: str,
                    access_token: str = "") -> None:
    """OneBot 11 反向 WebSocket。

    NapCat / go-cqhttp 配置示例（反向 WS）：
        ws://<host>:<port>/api/bots/onebot/<channel_id>/ws?access_token=<token>
    """
    row = _get_channel(channel_id)
    if not row or row.get("type") != CHANNEL_ONEBOT:
        await websocket.close(code=4404)
        return

    config = _channel_config(row)
    token_error = _require_onebot_webhook_token(config)
    if token_error:
        logger.warning("OneBot 反向 WS 拒绝：通道未配置 access_token：channel=%s", channel_id)
        await websocket.send_denial_response(
            JSONResponse({"detail": token_error}, status_code=403)
        )
        return
    if not _onebot_ws_auth_ok(config, access_token, websocket.headers):
        logger.warning("OneBot 反向 WS 鉴权失败（token 不匹配）：channel=%s", channel_id)
        await websocket.send_denial_response(
            JSONResponse(
                {"detail": "OneBot 反向 WS access_token 校验失败"},
                status_code=401,
            )
        )
        return

    await websocket.accept()
    # SQLite 写入是微秒级操作；这里直接同步更新，保证即使连接被强制取消
    # （TestClient / 客户端断线）也能可靠写入 online / offline 状态。
    _mark_channel(channel_id, "online")
    logger.info("OneBot 反向 WS 已连接：channel=%s", channel_id)

    try:
        while True:
            payload = await websocket.receive()
            if payload.get("type") == "websocket.disconnect":
                break
            raw = payload.get("text")
            if raw is None:
                data = payload.get("bytes")
                raw = data.decode("utf-8", errors="replace") if data else ""
            if not raw or not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("OneBot WS 收到非 JSON 报文（已忽略）：%r", raw[:200])
                continue
            # 同步服务（counselor / httpx）放到线程池，避免阻塞事件循环；
            # WS 模式下通道状态由连接生命周期维护。
            await asyncio.to_thread(_handle_onebot_event_sync, row, event,
                                    update_status=False)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - 连接异常只影响该连接
        logger.exception("OneBot 反向 WS 连接异常：channel=%s", channel_id)
    finally:
        # 注意：客户端硬断开时事件循环可能已取消本任务，finally 中的 await 会被
        # 取消；状态更新必须同步执行，否则通道会一直停留在 online。
        _mark_channel(channel_id, "offline")
        logger.info("OneBot 反向 WS 已断开：channel=%s", channel_id)


@router.post("/bots/onebot/{channel_id}/event")
async def onebot_event(channel_id: str, request: Request,
                       background_tasks: BackgroundTasks,
                       access_token: str = "") -> dict:
    """OneBot 11 反向 HTTP 事件入口：快速返回，事件处理放到后台任务。

    鉴权：query ``access_token`` 或 NapCat 的 ``X-Signature: sha1=<HMAC-SHA1>``
    （对原始请求正文计算）。空 Token 一律 403（本地调试需显式
    ``NPU_BOT_ALLOW_INSECURE=1``）。
    """
    row = _require_channel(channel_id, CHANNEL_ONEBOT)
    config = _channel_config(row)
    token_error = _require_onebot_webhook_token(config)
    if token_error:
        logger.warning("OneBot 反向 HTTP 事件拒绝：通道未配置 access_token：channel=%s", channel_id)
        raise HTTPException(status_code=403, detail=token_error)
    raw_body = await _read_webhook_body(request)
    signature_header = request.headers.get("x-signature") or ""
    if not _onebot_event_auth_ok(config, access_token, signature_header, raw_body):
        raise HTTPException(
            status_code=403,
            detail="OneBot 反向 HTTP 事件鉴权失败（access_token 或 X-Signature 不匹配）",
        )

    try:
        event = json.loads(raw_body) if raw_body else None
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="事件体必须是合法的 JSON")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="事件体必须是 JSON 对象")

    background_tasks.add_task(_handle_onebot_event_sync, row, event)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 微信公众号（明文模式）
# ---------------------------------------------------------------------------
@router.get("/bots/wechat/{channel_id}")
def wechat_verify(channel_id: str, signature: str = "", timestamp: str = "",
                  nonce: str = "", echostr: str = "") -> PlainTextResponse:
    """公众号接入校验：Token 安全 + 时间窗 + 签名校验通过则返回 echostr 明文，否则 403。"""
    row = _require_channel(channel_id, CHANNEL_WECHAT)
    config = _channel_config(row)
    _validate_wechat_webhook(channel_id, config, signature, timestamp, nonce)
    _mark_channel(channel_id, "online")
    return PlainTextResponse(echostr or "ok")


def _wechat_reply_xml(message: dict[str, str], reply: str) -> str:
    """按收到的 XML 生成被动回复 XML（From/To 互换）。"""
    from_user = str(message.get("FromUserName") or "")
    to_user = str(message.get("ToUserName") or "")
    # 微信文本消息内容上限约 2048 字节，这里按字符保守截断
    safe_reply = reply if len(reply) <= 1800 else reply[:1800].rstrip() + "……"
    return wechat_mp.build_reply_xml(to_user=from_user, from_user=to_user, content=safe_reply)


def _incoming_for_wechat(channel_id: str, from_user: str, text: str,
                         raw: dict[str, str]) -> IncomingMessage:
    """构造微信入站消息（session_key 规则：wechat_mp:{channel_id}:{openid}）。"""
    return IncomingMessage(
        channel=CHANNEL_WECHAT,
        channel_id=channel_id,
        text=text,
        user_id=from_user,
        raw=raw,
    )


@router.post("/bots/wechat/{channel_id}")
async def wechat_callback(channel_id: str, request: Request, signature: str = "",
                          timestamp: str = "", nonce: str = "") -> Response:
    """接收公众号 XML 消息并被动回复明文 XML。

    * 签名错误 → 403；
    * 非文本消息 → 返回引导语；
    * 文本消息 → 复用 bot_pipeline 的完整问答链路。
    """
    row = _require_channel(channel_id, CHANNEL_WECHAT)
    config = _channel_config(row)
    _validate_wechat_webhook(channel_id, config, signature, timestamp, nonce)

    raw_body = await _read_webhook_body(request)
    try:
        message = wechat_mp.parse_xml(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from_user = str(message.get("FromUserName") or "")
    if not from_user:
        raise HTTPException(status_code=400, detail="微信回调 XML 缺少 FromUserName")

    _mark_channel(channel_id, "online")

    msg_type = str(message.get("MsgType") or "").strip().lower()
    event_name = str(message.get("Event") or "").strip().lower()
    reply = wechat_mp.reply_text_for(message)

    if reply is None:
        # 文本消息：与真实渠道完全一致地走统一管线。
        # 微信被动回复限时 5 秒，这里 4.5s 兜底，避免真实模型较慢时公众号侧超时。
        incoming = _incoming_for_wechat(
            channel_id, from_user, str(message.get("Content") or ""), message
        )
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(bot_pipeline.handle_incoming, incoming,
                                  channel_config=config),
                timeout=WECHAT_REPLY_TIMEOUT_SECONDS,
            )
            reply = str(result.get("reply") or "")
        except asyncio.TimeoutError:
            logger.warning(
                "微信公众号问答超时（>%.1fs），返回兜底提示：channel=%s",
                WECHAT_REPLY_TIMEOUT_SECONDS, channel_id,
            )
            reply = WECHAT_TIMEOUT_REPLY
    elif msg_type == "event" and event_name == "subscribe":
        welcome = str(config.get("welcome") or "").strip()
        reply = welcome or reply

    xml = _wechat_reply_xml(message, reply or wechat_mp.NON_TEXT_GUIDE)
    # 微信被动回复官方要求 text/xml（Starlette 会自动补 charset=utf-8）
    return Response(content=xml, media_type="text/xml")

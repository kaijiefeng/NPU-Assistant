"""对话相关路由（backend-core）。

接口契约见 ``docs/CONTRACT.md`` §5.2：

* ``POST   /api/chat``
* ``GET    /api/chat/conversations?channel=&limit=20``
* ``GET    /api/chat/conversations/{id}/messages``
* ``DELETE /api/chat/conversations/{id}``
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..security import require_admin_token
from ..services import counselor

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["chat"])

ALLOWED_CHANNELS = {"web", "onebot11", "wechat_mp", "console"}
MAX_MESSAGE_CHARS = 2000


def _parse_citations(raw) -> list:
    value = db.from_json(raw, [])
    return value if isinstance(value, list) else []


@router.post("/chat")
def chat(payload: dict) -> dict:
    """学生提问：检索知识库 → LLM/降级 → 返回回答与引用。"""
    message = payload.get("message")
    if not isinstance(message, str):
        raise HTTPException(status_code=400, detail="message 字段必须为字符串")
    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="请输入问题内容")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"问题内容过长（最多 {MAX_MESSAGE_CHARS} 字）",
        )

    channel = str(payload.get("channel") or "web").strip().lower() or "web"
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的渠道：{channel}（可选 web/onebot11/wechat_mp/console）",
        )

    conversation_id = payload.get("conversation_id")
    if conversation_id is not None:
        conversation_id = str(conversation_id).strip() or None
    if conversation_id and db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在，请新建会话后重试")

    session_key = payload.get("session_key")
    session_key = str(session_key).strip() if session_key is not None else ""
    if not session_key:
        session_key = f"{channel}:{conversation_id or 'anonymous'}"

    try:
        return counselor.answer(
            message,
            channel=channel,
            session_key=session_key,
            conversation_id=conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("问答编排失败")
        raise HTTPException(status_code=500, detail="问答服务暂时不可用，请稍后重试")


@router.get("/chat/conversations")
def list_conversations(channel: str | None = None, limit: int = 20) -> dict:
    """会话列表（按最近更新时间倒序）。"""
    try:
        limit_value = int(limit)
    except (TypeError, ValueError):
        limit_value = 20
    limit_value = max(1, min(limit_value, 100))

    where = ""
    params: list = []
    channel_value = (channel or "").strip()
    if channel_value:
        where = "WHERE c.channel = ?"
        params.append(channel_value)
    params.append(limit_value)

    rows = db.query(
        "SELECT c.id, c.channel, c.session_key, c.user_label, c.updated_at, "
        "  (SELECT m.content FROM messages m WHERE m.conversation_id = c.id "
        "     ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1) AS last_message, "
        "  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
        "FROM conversations c "
        f"{where} "
        "ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?",
        params,
    )
    items = [
        {
            "id": row["id"],
            "channel": row["channel"],
            "session_key": row["session_key"],
            "user_label": row.get("user_label"),
            "last_message": row.get("last_message"),
            "message_count": int(row.get("message_count") or 0),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
    return {"items": items}


@router.get("/chat/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str) -> dict:
    """某会话的完整消息列表。"""
    conversation = db.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    rows = db.query(
        "SELECT id, role, content, citations, model, latency_ms, created_at "
        "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC",
        (conversation_id,),
    )
    items = [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "citations": _parse_citations(row.get("citations")),
            "model": row.get("model"),
            "latency_ms": row.get("latency_ms"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {
        "conversation": {
            "id": conversation["id"],
            "channel": conversation["channel"],
            "channel_id": conversation.get("channel_id"),
            "session_key": conversation["session_key"],
            "user_label": conversation.get("user_label"),
            "created_at": conversation["created_at"],
            "updated_at": conversation["updated_at"],
        },
        "items": items,
    }


@router.delete(
    "/chat/conversations/{conversation_id}",
    dependencies=[Depends(require_admin_token)],
)
def delete_conversation(conversation_id: str) -> dict:
    """删除会话及其消息（级联删除）。"""
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    with db.connect() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
    return {"ok": True}

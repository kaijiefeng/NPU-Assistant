"""管理助手会话持久化（agent_conversations / agent_messages）。

服务端持有全部对话历史：面板只传 conversation_id，历史从库读——
前端无法伪造对话上下文；上下文压缩摘要（summary/summary_upto）
也持久化在会话行上，见 :func:`build_context`。
"""
from __future__ import annotations

import json
import logging

from .. import db
from . import agent_context

logger = logging.getLogger("npu-assistant")

_ROLES = {"user", "assistant"}
# 历史和完整请求统一使用 token 预算；原始消息保留在数据库中。


def _row_to_conversation(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row.get("title") or "新会话",
        "summary": row.get("summary") or "",
        "summary_upto": row.get("summary_upto") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _row_to_message(row: dict) -> dict:
    steps = db.from_json(row.get("steps"), [])
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row.get("content") or "",
        "steps": steps if isinstance(steps, list) else [],
        "created_at": row.get("created_at"),
        "_rowid": int(row["rowid"]),
    }


def _get_conversation(conversation_id: str) -> dict:
    row = db.query_one(
        "SELECT * FROM agent_conversations WHERE id = ?", (conversation_id,)
    )
    if not row:
        raise ValueError("会话不存在或已被删除")
    return _row_to_conversation(row)


def get_conversation(conversation_id: str) -> dict:
    """公共读取（路由 404 判定用）。"""
    return _get_conversation(conversation_id)


def create_conversation(title: str = "") -> dict:
    conv_id = db.new_id()
    now = db.now()
    clean_title = str(title or "").strip()[:60]
    db.execute(
        "INSERT INTO agent_conversations(id, title, summary, summary_upto, created_at, updated_at) "
        "VALUES(?, ?, '', '', ?, ?)",
        (conv_id, clean_title or "新会话", now, now),
    )
    return _get_conversation(conv_id)


def list_conversations(limit: int = 30) -> dict:
    value = max(1, min(int(limit or 30), 100))
    rows = db.query(
        "SELECT c.id, c.title, c.created_at, c.updated_at, "
        "(SELECT COUNT(*) FROM agent_messages m WHERE m.conversation_id = c.id) AS message_count, "
        "(SELECT content FROM agent_messages m WHERE m.conversation_id = c.id "
        " AND m.role = 'user' ORDER BY m.created_at DESC LIMIT 1) AS last_message "
        "FROM agent_conversations c ORDER BY c.updated_at DESC LIMIT ?",
        (value,),
    )
    return {
        "items": [
            {
                "id": row["id"],
                "title": row.get("title") or "新会话",
                "updated_at": row.get("updated_at"),
                "created_at": row.get("created_at"),
                "message_count": int(row.get("message_count") or 0),
                "last_message": (row.get("last_message") or "")[:80],
            }
            for row in rows
        ]
    }


def rename_conversation(conversation_id: str, title: str) -> dict:
    _get_conversation(conversation_id)
    clean = str(title or "").strip()[:60]
    db.execute(
        "UPDATE agent_conversations SET title = ?, updated_at = ? WHERE id = ?",
        (clean or "新会话", db.now(), conversation_id),
    )
    return _get_conversation(conversation_id)


def delete_conversation(conversation_id: str) -> dict:
    _get_conversation(conversation_id)
    db.execute("DELETE FROM agent_messages WHERE conversation_id = ?", (conversation_id,))
    db.execute("DELETE FROM agent_conversations WHERE id = ?", (conversation_id,))
    return {"ok": True, "id": conversation_id}


def get_messages(conversation_id: str, limit: int = 200) -> list[dict]:
    _get_conversation(conversation_id)
    value = max(1, min(int(limit or 200), 500))
    rows = db.query(
        "SELECT *, rowid AS rowid FROM agent_messages WHERE conversation_id = ? "
        "ORDER BY rowid DESC LIMIT ?",
        (conversation_id, value),
    )
    return [_row_to_message(row) for row in reversed(rows)]


def append_message(
    conversation_id: str, role: str, content: str, steps: list | None = None
) -> dict:
    conv = _get_conversation(conversation_id)
    if role not in _ROLES:
        raise ValueError("消息角色只能是 user 或 assistant")
    text = str(content or "")
    message_id = db.new_id()
    now = db.now()
    db.execute(
        "INSERT INTO agent_messages(id, conversation_id, role, content, steps, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            message_id,
            conversation_id,
            role,
            text,
            json.dumps(steps or [], ensure_ascii=False),
            now,
        ),
    )
    # 首条用户消息自动作为会话标题
    title_patch = ""
    params: tuple = (now, conversation_id)
    if role == "user" and (not conv["title"].strip() or conv["title"] == "新会话"):
        first_user = db.query_one(
            "SELECT content FROM agent_messages WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY created_at, rowid LIMIT 1",
            (conversation_id,),
        )
        auto_title = str((first_user or {}).get("content") or "").strip()[:24]
        if auto_title:
            title_patch = ", title = ?"
            params = (now, auto_title, conversation_id)
    db.execute(
        f"UPDATE agent_conversations SET updated_at = ?{title_patch} WHERE id = ?", params
    )
    message = db.query_one("SELECT *, rowid AS rowid FROM agent_messages WHERE id = ?", (message_id,))
    return _row_to_message(message)


def truncate_from(conversation_id: str, message_id: str) -> dict:
    """回滚：删除该消息及其之后的消息（含摘要水位越界时的重置）。"""
    conv = _get_conversation(conversation_id)
    target = db.query_one(
        "SELECT *, rowid AS rowid FROM agent_messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    )
    if not target:
        raise ValueError("要回滚到的消息不存在")
    db.execute(
        "DELETE FROM agent_messages WHERE conversation_id = ? AND rowid >= ?",
        (conversation_id, target["rowid"]),
    )
    # 摘要水位若落在被删区间，摘要一并作废
    watermark = int(conv["summary_upto"] or 0)
    if watermark and watermark >= int(target["rowid"]):
        db.execute(
            "UPDATE agent_conversations SET summary = '', summary_upto = '', updated_at = ? "
            "WHERE id = ?",
            (db.now(), conversation_id),
        )
    else:
        db.execute(
            "UPDATE agent_conversations SET updated_at = ? WHERE id = ?",
            (db.now(), conversation_id),
        )
    remaining = db.query_one(
        "SELECT COUNT(*) AS n FROM agent_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    return {"ok": True, "conversation_id": conversation_id, "remaining": int((remaining or {}).get("n") or 0)}


# ---------------------------------------------------------------------------
# 上下文组装与压缩
# ---------------------------------------------------------------------------
def _compact_chunk(messages: list[dict]) -> str:
    return agent_context.summarize_history(messages)


def build_context(
    conversation_id: str,
    *,
    budget: int | None = None,
    reserved_messages: list[dict] | None = None,
) -> list[dict]:
    """180K tokens 触发压缩，200K 窗口；预算包含调用方预留的系统与本轮消息。"""
    conv = _get_conversation(conversation_id)
    watermark = int(conv["summary_upto"] or 0)
    # 展示接口仍分页；给模型组装上下文时不能以 500 条展示上限静默丢历史。
    rows = db.query(
        "SELECT rowid AS _rowid, role, content FROM agent_messages "
        "WHERE conversation_id = ? AND rowid > ? ORDER BY rowid",
        (conversation_id, watermark),
    )
    history = []
    if conv["summary"]:
        history.append({"role": "user", "content": agent_context.SUMMARY_PREFIX + conv["summary"]})
    history.extend({"role": row["role"], "content": row["content"]} for row in rows)
    compacted, consumed = agent_context.compact_history(
        history,
        reserved_messages,
        threshold=agent_context.COMPACT_THRESHOLD if budget is None else budget,
        summarizer=_compact_chunk,
    )
    if consumed:
        summary_offset = 1 if conv["summary"] else 0
        consumed_rows = consumed - summary_offset
        new_watermark = int(rows[consumed_rows - 1]["_rowid"]) if consumed_rows > 0 else watermark
        summary = compacted[0]["content"][len(agent_context.SUMMARY_PREFIX):]
        db.execute(
            "UPDATE agent_conversations SET summary = ?, summary_upto = ?, updated_at = ? WHERE id = ?",
            (summary, str(new_watermark), db.now(), conversation_id),
        )
        logger.info("会话 %s 上下文压缩：%d 条消息，水位 %s", conversation_id, consumed_rows, new_watermark)
    return compacted

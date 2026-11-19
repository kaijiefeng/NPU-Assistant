"""管理助手会话持久化（agent_conversations / agent_messages）。

服务端持有全部对话历史：面板只传 conversation_id，历史从库读——
前端无法伪造对话上下文；上下文压缩摘要（summary/summary_upto）
也持久化在会话行上，见 :func:`build_context`。
"""
from __future__ import annotations

import json
import logging
from collections import deque

from .. import db

logger = logging.getLogger("npu-assistant")

_ROLES = {"user", "assistant"}
# 上下文预算（字符）：system 提示词 + 摘要 + 近期消息的总量级参考
_CONTEXT_BUDGET = 12000
_SUMMARY_CHARS = 600


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
    """把一段旧消息交给 LLM 压成短摘要；失败降级为截断拼接（标注未经 AI 压缩）。"""
    from . import llm

    corpus = "\n".join(
        f"[{'用户' if m['role'] == 'user' else '助手'}] {str(m.get('content') or '')[:400]}"
        for m in messages
    )
    try:
        response = llm.chat(
            [
                {
                    "role": "system",
                    "content": "你是对话压缩器。把管理助手会话的旧消息压缩成一段 300 字以内的纪要，保留：做过什么操作（工具名+对象）、关键结论、未完成事项。只输出纪要正文。",
                },
                {"role": "user", "content": corpus[:8000]},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        if not response.get("fallback") and str(response.get("content") or "").strip():
            return str(response["content"]).strip()[:_SUMMARY_CHARS * 2]
    except Exception:  # noqa: BLE001 - 压缩失败降级
        logger.info("会话摘要压缩失败，降级为截断", exc_info=True)
    lines = [f"[{'用户' if m['role'] == 'user' else '助手'}] {str(m.get('content') or '')[:80]}" for m in messages]
    return "（旧消息截断纪要，未经 AI 压缩）\n" + "\n".join(lines)[:_SUMMARY_CHARS * 2]


def build_context(conversation_id: str, *, budget: int = _CONTEXT_BUDGET) -> list[dict]:
    """组装发给模型的上下文：既有摘要 + 水位之后的近期消息；超预算时压缩最旧一半。

    返回 ``[{"role":"user"|"assistant", ...}]``（不含 system 提示词）；压缩摘要
    以 ``{"role": "user", "content": "【此前会话纪要】..."}`` 的形式放在最前，
    与既有 llm.chat 消息模型兼容。
    """
    conv = _get_conversation(conversation_id)
    watermark = int(conv["summary_upto"] or 0)
    messages = [m for m in get_messages(conversation_id, limit=500) if m["_rowid"] > watermark]
    summary = conv["summary"]
    total = len(summary) + sum(len(m["content"]) for m in messages)
    if total <= budget or len(messages) <= 4:
        context: list[dict] = []
        if summary:
            context.append({"role": "user", "content": f"【此前会话纪要】{summary}"})
        context.extend({"role": m["role"], "content": m["content"]} for m in messages)
        return context

    # 超预算：把最旧一半压进摘要（含旧摘要），水位推进
    keep = max(2, len(messages) // 2)
    to_compress = messages[:-keep]
    kept = messages[-keep:]
    merged_summary = _compact_chunk(
        ([{"role": "user", "content": f"【此前会话纪要】{summary}"}] if summary else [])
        + to_compress
    )
    new_watermark = to_compress[-1]["_rowid"]
    db.execute(
        "UPDATE agent_conversations SET summary = ?, summary_upto = ?, updated_at = ? WHERE id = ?",
        (merged_summary[: _SUMMARY_CHARS * 2], str(new_watermark), db.now(), conversation_id),
    )
    logger.info(
        "会话 %s 上下文压缩：%d 条旧消息并入摘要（水位 %s）",
        conversation_id,
        len(to_compress),
        new_watermark,
    )
    return [{"role": "user", "content": f"【此前会话纪要】{merged_summary}"}] + [
        {"role": m["role"], "content": m["content"]} for m in kept
    ]

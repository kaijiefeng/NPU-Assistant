"""RSI loop: answer feedback, knowledge gaps and service-desk overview.

This module is the data layer for the v0.2.0 "recursive self-improvement" loop:

    student question -> answer signal -> knowledge gap -> counselor action
        -> new/updated source -> better answer

It deliberately uses lexical clustering (``text_utils.tokenize`` + Jaccard) instead
of embeddings, matching the LLM Wiki design.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Iterable

from .. import db
from . import text_utils

logger = logging.getLogger("npu-assistant")

ALLOWED_REASONS = {"not_found", "incomplete", "expired", "bad_citation", "other"}
REASON_LABELS = {
    "not_found": "没找到",
    "incomplete": "回答不完整",
    "expired": "内容已过期",
    "bad_citation": "引用不对",
    "other": "其他",
}
STATUSES = {"open", "triaged", "resolved", "ignored"}
GAP_STATUSES = {"open", "linked", "resolved", "ignored"}

_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "多少", "为什么", "是否", "可以",
    "需要", "请问", "一下", "这个", "那个", "我们", "学校", "学院", "同学", "老师",
    "现在", "最近", "今年", "明年", "去年", "时候", "问题", "情况", "办法", "流程",
    "办理", "申请", "相关", "具体", "一般", "应该", "能不能", "有没有",
}
_PUNCT_RE = re.compile(r"[\s，。！？、；：“”‘’（）()\[\]【】<>《》/\\|,.!?;:'\"]+")


def _iso_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=max(0, int(days)))).isoformat(timespec="seconds")


def _tokens(text: Any) -> set[str]:
    raw = [token for token in text_utils.tokenize(str(text or "")) if token]
    tokens = {token for token in raw if len(token) > 1 and token not in _STOPWORDS}
    if not tokens:
        cleaned = _PUNCT_RE.sub("", str(text or "")).strip()
        if cleaned:
            tokens = {cleaned[:24]}
    return tokens


def _normalized_key(text: Any) -> str:
    return " ".join(sorted(_tokens(text))[:16])


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _gap_reasons(retrieval: dict | None, fallback: bool, rating: int | None,
                 reason_code: str = "") -> list[str]:
    reasons: list[str] = []
    data = retrieval if isinstance(retrieval, dict) else {}
    if data.get("chitchat") or data.get("non_campus"):
        return []
    if not data.get("wiki_used"):
        reasons.append("no_hit")
    if data.get("complete") is False:
        reasons.append("incomplete")
    if data.get("remote_used"):
        reasons.append("remote")
    if fallback:
        reasons.append("fallback")
    if rating == -1:
        reasons.append("negative_feedback")
    if reason_code in ALLOWED_REASONS and reason_code not in {"other"}:
        reasons.append(reason_code)
    # Deduplicate while preserving order.
    return list(dict.fromkeys(reasons))


def _merge_reasons(existing: list[str], incoming: Iterable[str]) -> list[str]:
    merged = list(existing)
    for item in incoming:
        if item not in merged:
            merged.append(item)
    return merged


def _priority_for(count: int, channels: set[str], reasons: list[str]) -> int:
    if count >= 5 or len(channels) >= 2 or "negative_feedback" in reasons:
        return 1
    return 0


def _find_similar_gap(question: str, tokens: set[str]) -> dict | None:
    rows = db.query(
        "SELECT * FROM knowledge_gaps WHERE status IN ('open','linked') "
        "AND last_seen_at >= ? ORDER BY last_seen_at DESC LIMIT 200",
        (_iso_days_ago(30),),
    )
    best: tuple[float, dict] | None = None
    for row in rows:
        existing_tokens = _tokens(row.get("question") or "")
        score = _jaccard(tokens, existing_tokens)
        overlap = len(tokens & existing_tokens)
        if score >= 0.6 and overlap >= 2:
            if best is None or score > best[0]:
                best = (score, row)
    return best[1] if best else None


def _upsert_gap(
    *,
    question: str,
    channel: str,
    message_id: str,
    reasons: list[str],
) -> dict | None:
    tokens = _tokens(question)
    if not tokens:
        return None
    existing = _find_similar_gap(question, tokens)
    timestamp = db.now()
    if existing:
        current_reasons = db.from_json(existing.get("reasons"), [])
        if not isinstance(current_reasons, list):
            current_reasons = []
        merged = _merge_reasons([str(item) for item in current_reasons], reasons)
        count = int(existing.get("count") or 1) + 1
        channels = {str(existing.get("channel") or ""), str(channel or "")}
        channels.discard("")
        priority = _priority_for(count, channels, merged)
        db.execute(
            "UPDATE knowledge_gaps SET count = ?, reasons = ?, last_seen_at = ?, "
            "priority = ?, updated_at = ? WHERE id = ?",
            (
                count,
                db.to_json(merged),
                timestamp,
                priority,
                timestamp,
                existing["id"],
            ),
        )
        return db.query_one("SELECT * FROM knowledge_gaps WHERE id = ?", (existing["id"],))
    gap_id = db.new_id()
    db.execute(
        "INSERT INTO knowledge_gaps(id, question, normalized_key, sample_message_id, "
        "channel, count, reasons, first_seen_at, last_seen_at, status, priority, "
        "created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, 'open', ?, ?, ?)",
        (
            gap_id,
            str(question or "").strip()[:500],
            _normalized_key(question),
            message_id or "",
            str(channel or "web"),
            db.to_json(reasons),
            timestamp,
            timestamp,
            _priority_for(1, {str(channel or "web")}, reasons),
            timestamp,
            timestamp,
        ),
    )
    return db.query_one("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,))


def record_answer_signal(
    *,
    message_id: str,
    conversation_id: str,
    channel: str,
    question: str,
    retrieval: dict | None,
    fallback: bool,
) -> dict | None:
    """Create/update a knowledge gap from one answer, if it needs attention."""
    reasons = _gap_reasons(retrieval, bool(fallback), None)
    if not reasons:
        return None
    try:
        return _upsert_gap(
            question=question,
            channel=channel,
            message_id=message_id,
            reasons=reasons,
        )
    except Exception:
        logger.exception("记录知识缺口失败（已忽略）")
        return None


def submit_feedback(
    *,
    message_id: str,
    voter_key: str,
    rating: int,
    reason_code: str = "",
    comment: str = "",
) -> dict:
    """Create or update feedback for one assistant message.

    Raises ``ValueError`` with a Chinese message for invalid input.
    """
    message = db.query_one("SELECT * FROM messages WHERE id = ?", (message_id,))
    if not message:
        raise ValueError("消息不存在或已被删除")
    if message.get("role") != "assistant":
        raise ValueError("只能对助手回答进行反馈")
    if rating not in (1, -1):
        raise ValueError("rating 只能是 1（有帮助）或 -1（没帮助）")
    reason_code = str(reason_code or "").strip()
    if reason_code and reason_code not in ALLOWED_REASONS:
        raise ValueError("不支持的反馈原因")
    comment = str(comment or "").strip()[:200]
    voter_key = str(voter_key or "").strip()[:80] or "anonymous"
    conversation = db.get_conversation(str(message.get("conversation_id") or "")) or {}
    channel = str(conversation.get("channel") or "web")
    timestamp = db.now()
    existing = db.query_one(
        "SELECT * FROM answer_feedback WHERE message_id = ? AND voter_key = ?",
        (message_id, voter_key),
    )
    if existing:
        db.execute(
            "UPDATE answer_feedback SET rating = ?, reason_code = ?, comment = ?, "
            "status = 'open', updated_at = ? WHERE id = ?",
            (rating, reason_code, comment, timestamp, existing["id"]),
        )
        feedback_id = existing["id"]
    else:
        feedback_id = db.new_id()
        db.execute(
            "INSERT INTO answer_feedback(id, message_id, conversation_id, channel, rating, "
            "reason_code, comment, voter_key, status, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (
                feedback_id,
                message_id,
                str(message.get("conversation_id") or ""),
                channel,
                rating,
                reason_code,
                comment,
                voter_key,
                timestamp,
                timestamp,
            ),
        )
    gap = None
    was_negative = bool(existing and int(existing.get("rating") or 0) == -1)
    if rating == -1 and not was_negative:
        question = _question_for_message(message)
        gap = _upsert_gap(
            question=question,
            channel=channel,
            message_id=message_id,
            reasons=_gap_reasons({}, False, rating, reason_code),
        )
    return {
        "ok": True,
        "feedback_id": feedback_id,
        "status": "open",
        "gap_id": (gap or {}).get("id") or "",
    }


def _question_for_message(message: dict) -> str:
    """Find the user question preceding an assistant message."""
    row = db.query_one(
        "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' "
        "AND rowid < (SELECT rowid FROM messages WHERE id = ?) "
        "ORDER BY rowid DESC LIMIT 1",
        (message.get("conversation_id"), message.get("id")),
    )
    return str((row or {}).get("content") or "").strip()


def feedback_for_message(message_id: str, voter_key: str = "") -> dict | None:
    if voter_key:
        return db.query_one(
            "SELECT * FROM answer_feedback WHERE message_id = ? AND voter_key = ?",
            (message_id, voter_key),
        )
    return db.query_one(
        "SELECT * FROM answer_feedback WHERE message_id = ? ORDER BY updated_at DESC LIMIT 1",
        (message_id,),
    )


def _row_to_feedback(row: dict) -> dict:
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "channel": row.get("channel") or "web",
        "rating": int(row.get("rating") or 0),
        "reason_code": row.get("reason_code") or "",
        "reason_label": REASON_LABELS.get(row.get("reason_code") or "", ""),
        "comment": row.get("comment") or "",
        "status": row.get("status") or "open",
        "handled_by": row.get("handled_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_feedback(
    *,
    status: str = "",
    rating: int | None = None,
    days: int = 30,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where = ["f.created_at >= ?"]
    params: list[Any] = [_iso_days_ago(days)]
    if status:
        where.append("f.status = ?")
        params.append(status)
    if rating in (1, -1):
        where.append("f.rating = ?")
        params.append(rating)
    clause = " AND ".join(where)
    total_row = db.query_one(
        f"SELECT COUNT(*) AS n FROM answer_feedback f WHERE {clause}", params
    )
    rows = db.query(
        "SELECT f.*, m.content AS reply_content, m.citations AS reply_citations, "
        "m.retrieval AS reply_retrieval, "
        "(SELECT content FROM messages q WHERE q.conversation_id = f.conversation_id "
        " AND q.role = 'user' AND q.rowid < (SELECT rowid FROM messages mm WHERE mm.id = f.message_id) "
        " ORDER BY q.rowid DESC LIMIT 1) AS question "
        f"FROM answer_feedback f LEFT JOIN messages m ON m.id = f.message_id "
        f"WHERE {clause} ORDER BY f.created_at DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
    )
    items = []
    for row in rows:
        item = _row_to_feedback(row)
        item["question"] = row.get("question") or ""
        item["reply"] = str(row.get("reply_content") or "")[:500]
        items.append(item)
    return {"items": items, "total": int((total_row or {}).get("n") or 0)}


def update_feedback_status(feedback_id: str, status: str, handled_by: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("不支持的反馈状态")
    row = db.query_one("SELECT id FROM answer_feedback WHERE id = ?", (feedback_id,))
    if not row:
        raise ValueError("反馈不存在")
    db.execute(
        "UPDATE answer_feedback SET status = ?, handled_by = ?, handled_at = ?, updated_at = ? "
        "WHERE id = ?",
        (status, handled_by or None, db.now(), db.now(), feedback_id),
    )
    return {"ok": True, "id": feedback_id, "status": status}


def _row_to_gap(row: dict, *, detail: bool = False) -> dict:
    reasons = db.from_json(row.get("reasons"), [])
    item = {
        "id": row["id"],
        "question": row.get("question") or "",
        "count": int(row.get("count") or 1),
        "channel": row.get("channel") or "web",
        "reasons": reasons if isinstance(reasons, list) else [],
        "priority": int(row.get("priority") or 0),
        "status": row.get("status") or "open",
        "first_seen_at": row.get("first_seen_at"),
        "last_seen_at": row.get("last_seen_at"),
        "linked_page_id": row.get("linked_page_id"),
        "linked_document_id": row.get("linked_document_id"),
        "assignee_id": row.get("assignee_id"),
        "note": row.get("note") or "",
        "sample_message_id": row.get("sample_message_id"),
    }
    if detail:
        page = (
            db.query_one(
                "SELECT id, title, status FROM wiki_pages WHERE id = ?",
                (row.get("linked_page_id"),),
            )
            if row.get("linked_page_id")
            else None
        )
        document = (
            db.query_one(
                "SELECT id, title, status FROM documents WHERE id = ?",
                (row.get("linked_document_id"),),
            )
            if row.get("linked_document_id")
            else None
        )
        item["linked_page"] = page
        item["linked_document"] = document
        samples = db.query(
            "SELECT id, conversation_id, content, created_at FROM messages "
            "WHERE role = 'user' AND content LIKE ? ORDER BY created_at DESC LIMIT 5",
            (f"%{str(row.get('question') or '')[:20]}%",),
        )
        item["samples"] = samples
    return item


def list_gaps(
    *,
    status: str = "open",
    days: int = 30,
    channel: str = "",
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    where = ["last_seen_at >= ?"]
    params: list[Any] = [_iso_days_ago(days)]
    if status:
        where.append("status = ?")
        params.append(status)
    if channel:
        where.append("channel = ?")
        params.append(channel)
    needle = str(query or "").strip()
    if needle:
        where.append("(question LIKE ? OR note LIKE ?)")
        params.extend([f"%{needle}%", f"%{needle}%"])
    clause = " AND ".join(where)
    total_row = db.query_one(
        f"SELECT COUNT(*) AS n FROM knowledge_gaps WHERE {clause}", params
    )
    rows = db.query(
        f"SELECT * FROM knowledge_gaps WHERE {clause} "
        "ORDER BY priority DESC, count DESC, last_seen_at DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
    )
    return {
        "items": [_row_to_gap(row) for row in rows],
        "total": int((total_row or {}).get("n") or 0),
    }


def get_gap(gap_id: str) -> dict:
    row = db.query_one("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,))
    if not row:
        raise ValueError("知识缺口不存在")
    return _row_to_gap(row, detail=True)


def update_gap(gap_id: str, action: str, *, page_id: str = "", document_id: str = "",
               note: str = "", assignee_id: str = "") -> dict:
    row = db.query_one("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,))
    if not row:
        raise ValueError("知识缺口不存在")
    timestamp = db.now()
    if action == "link":
        db.execute(
            "UPDATE knowledge_gaps SET status = 'linked', linked_page_id = ?, "
            "linked_document_id = ?, note = ?, updated_at = ? WHERE id = ?",
            (page_id or row.get("linked_page_id"), document_id or row.get("linked_document_id"),
             note or row.get("note") or "", timestamp, gap_id),
        )
    elif action == "resolve":
        db.execute(
            "UPDATE knowledge_gaps SET status = 'resolved', note = ?, updated_at = ? WHERE id = ?",
            (note or row.get("note") or "", timestamp, gap_id),
        )
    elif action == "ignore":
        db.execute(
            "UPDATE knowledge_gaps SET status = 'ignored', note = ?, updated_at = ? WHERE id = ?",
            (note or row.get("note") or "", timestamp, gap_id),
        )
    elif action == "assign":
        db.execute(
            "UPDATE knowledge_gaps SET assignee_id = ?, updated_at = ? WHERE id = ?",
            (assignee_id or None, timestamp, gap_id),
        )
    else:
        raise ValueError("不支持的操作")
    return get_gap(gap_id)


def overview(days: int = 7) -> dict:
    since = _iso_days_ago(days)
    feedback_rows = db.query(
        "SELECT rating, COUNT(*) AS n FROM answer_feedback WHERE created_at >= ? GROUP BY rating",
        (since,),
    )
    feedback_counts = {int(row["rating"]): int(row["n"]) for row in feedback_rows}
    total_feedback = sum(feedback_counts.values())
    up = feedback_counts.get(1, 0)
    down = feedback_counts.get(-1, 0)
    gaps = db.query(
        "SELECT status, COUNT(*) AS n FROM knowledge_gaps WHERE last_seen_at >= ? GROUP BY status",
        (since,),
    )
    gap_counts = {row["status"]: int(row["n"]) for row in gaps}
    open_gaps = gap_counts.get("open", 0)
    resolved = gap_counts.get("resolved", 0)
    top = db.query(
        "SELECT * FROM knowledge_gaps WHERE status IN ('open','linked') "
        "ORDER BY priority DESC, count DESC, last_seen_at DESC LIMIT 10"
    )
    answers = db.query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE role = 'assistant' AND created_at >= ?",
        (since,),
    )
    return {
        "days": int(days),
        "answers": int((answers or {}).get("n") or 0),
        "feedback": {
            "total": total_feedback,
            "up": up,
            "down": down,
            "helpful_rate": round(up / total_feedback, 3) if total_feedback else None,
        },
        "gaps": {
            "open": open_gaps,
            "linked": gap_counts.get("linked", 0),
            "resolved": resolved,
            "ignored": gap_counts.get("ignored", 0),
            "total": sum(gap_counts.values()),
            "close_rate": round(resolved / max(1, resolved + open_gaps), 3),
        },
        "top_gaps": [_row_to_gap(row) for row in top],
    }


def recompute_gaps(days: int = 90) -> dict:
    """Rebuild open gaps from recent assistant messages (idempotent enough)."""
    since = _iso_days_ago(days)
    rows = db.query(
        "SELECT m.id, m.conversation_id, m.retrieval, m.fallback, "
        "c.channel, "
        "(SELECT q.content FROM messages q WHERE q.conversation_id = m.conversation_id "
        " AND q.role = 'user' AND q.rowid < m.rowid ORDER BY q.rowid DESC LIMIT 1) AS question "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.role = 'assistant' AND m.created_at >= ? "
        "ORDER BY m.created_at ASC",
        (since,),
    )
    created = updated = 0
    for row in rows:
        question = str(row.get("question") or "").strip()
        if not question:
            continue
        retrieval = db.from_json(row.get("retrieval"), {})
        if not isinstance(retrieval, dict):
            retrieval = {}
        reasons = _gap_reasons(retrieval, bool(row.get("fallback")))
        if not reasons:
            continue
        existing = _upsert_gap(
            question=question,
            channel=str(row.get("channel") or "web"),
            message_id=str(row.get("id") or ""),
            reasons=reasons,
        )
        if existing and int(existing.get("count") or 0) <= 1:
            created += 1
        else:
            updated += 1
    return {"ok": True, "created": created, "updated": updated, "scanned": len(rows)}


__all__ = [
    "ALLOWED_REASONS",
    "REASON_LABELS",
    "record_answer_signal",
    "submit_feedback",
    "feedback_for_message",
    "list_feedback",
    "update_feedback_status",
    "list_gaps",
    "get_gap",
    "update_gap",
    "overview",
    "recompute_gaps",
]

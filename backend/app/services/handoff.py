"""Human handoff + crisis escalation (v0.2.0).

Design goals
------------
* A student can ask for a human at any time; the request becomes a ticket with a
  visible number and SLA.
* Crisis signals are detected **before** any LLM call and never depend on model
  availability.  The answer is a fixed safety card; a human ticket is created and
  an optional webhook notifies the on-call counselor.
* Student-facing lookup only requires ``ticket_no + ticket_token`` — never the
  full conversation.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from .. import db

logger = logging.getLogger("npu-assistant")

HANDOFF_REASONS = {
    "not_solved": "问题没解决",
    "confirm": "需要人工确认",
    "personal": "涉及个人情况",
    "complaint": "投诉建议",
    "other": "其他",
    "crisis": "紧急情况",
}
HANDOFF_STATUSES = {"pending", "claimed", "replied", "resolved", "closed"}
CRISIS_STATUSES = {"open", "acknowledged", "resolved", "false_positive"}

# Rule-based, high-recall crisis detection.  Explicit expressions always win;
# negative contexts (lectures, prevention campaigns, news) suppress the match.
CRISIS_RULES: list[dict[str, Any]] = [
    {
        "category": "suicide",
        "severity": "high",
        "patterns": [
            r"自杀", r"不想活", r"活不下去", r"不想活了", r"去死", r"结束生命",
            r"割腕", r"跳楼", r"安眠药", r"烧炭", r"自残", r"伤害自己",
            r"轻生", r"一了百了",
        ],
    },
    {
        "category": "violence",
        "severity": "high",
        "patterns": [r"杀人", r"砍人", r"打死", r"弄死", r"带刀", r"报复社会", r"同归于尽"],
    },
    {
        "category": "sexual",
        "severity": "high",
        "patterns": [r"强奸", r"性侵", r"猥亵", r"性骚扰", r"偷拍"],
    },
    {
        "category": "fraud",
        "severity": "medium",
        "patterns": [r"被骗", r"诈骗", r"刷单", r"冒充公检法", r"验证码.*转", r"转账.*被骗"],
    },
    {
        "category": "medical",
        "severity": "high",
        "patterns": [r"晕倒", r"抽搐", r"呼吸困难", r"大出血", r"吃错药", r"急救", r"昏迷"],
    },
    {
        "category": "bullying",
        "severity": "medium",
        "patterns": [r"霸凌", r"校园暴力", r"被围殴", r"被威胁", r"被勒索"],
    },
]
NEGATIVE_CONTEXT = re.compile(
    r"讲座|宣传|科普|预防|论文|作业|电影|电视剧|新闻|案例|演练|主题班会|海报|"
    r"怎么写|如何预防|研究|课程|考试题"
)
URGENT_CONTEXT = re.compile(r"我想|我要|救我|帮帮我|现在|已经|正在|马上|控制不住|身边")


def _settings() -> dict[str, Any]:
    raw = db.all_settings()
    for key in ("crisis_hotlines", "crisis_keywords_extra"):
        try:
            value = json.loads(raw.get(key, "[]"))
            raw[key] = value if isinstance(value, list) else []
        except (TypeError, ValueError):
            raw[key] = []
    for key, default in (
        ("handoff_enabled", True),
        ("crisis_enabled", True),
    ):
        raw[key] = str(raw.get(key, str(default))).strip().lower() in {"1", "true", "yes", "on"}
    for key, default in (("handoff_sla_hours", 24), ("crisis_sla_minutes", 5)):
        try:
            raw[key] = int(raw.get(key, default) or default)
        except (TypeError, ValueError):
            raw[key] = default
    raw["crisis_notify_webhook"] = str(raw.get("crisis_notify_webhook") or "").strip()
    return raw


def detect_crisis(text: str) -> dict | None:
    """Return ``{category, terms, severity}`` or ``None``."""
    value = str(text or "").strip()
    if not value:
        return None
    negative = NEGATIVE_CONTEXT.search(value)
    urgent = URGENT_CONTEXT.search(value)
    if negative and not urgent:
        return None
    for rule in CRISIS_RULES:
        matched = [pattern for pattern in rule["patterns"] if re.search(pattern, value)]
        if matched:
            return {
                "category": rule["category"],
                "severity": rule.get("severity", "high"),
                "terms": matched[:4],
            }
    settings = _settings()
    for item in settings.get("crisis_keywords_extra") or []:
        keyword = str(item or "").strip()
        if keyword and keyword in value:
            return {"category": "other", "severity": "medium", "terms": [keyword]}
    return None


def _ticket_no() -> str:
    day = datetime.now().strftime("%Y%m%d")
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM handoff_requests WHERE ticket_no LIKE ?",
        (f"NPU-{day}-%",),
    )
    seq = int((row or {}).get("n") or 0) + 1
    return f"NPU-{day}-{seq:04d}"


def _conversation(conversation_id: str) -> dict:
    conversation = db.get_conversation(conversation_id) if conversation_id else None
    if not conversation:
        raise ValueError("会话不存在，无法提交人工请求")
    return conversation


def create_handoff(
    *,
    conversation_id: str,
    reason: str = "other",
    contact: str = "",
    note: str = "",
    message_id: str = "",
    source: str = "student",
    priority: str = "normal",
    summary: str = "",
) -> dict:
    """Create (or return the active) handoff ticket for a conversation."""
    conversation = _conversation(conversation_id)
    reason = str(reason or "other").strip()
    if reason not in HANDOFF_REASONS:
        raise ValueError("不支持的人工请求原因")
    active = db.query_one(
        "SELECT * FROM handoff_requests WHERE conversation_id = ? "
        "AND status IN ('pending','claimed','replied') "
        "ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    )
    if active:
        return _row_to_handoff(active, conversation)
    timestamp = db.now()
    summary = str(summary or note or "").strip()
    if not summary:
        last_user = db.query_one(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        )
        summary = str((last_user or {}).get("content") or "")[:200]
    ticket_no = _ticket_no()
    ticket_token = secrets.token_urlsafe(18)
    for _ in range(6):
        try:
            db.execute(
                "INSERT INTO handoff_requests(id, ticket_no, ticket_token, conversation_id, "
                "message_id, channel, session_key, user_label, contact, reason, priority, "
                "status, summary, source, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    db.new_id(),
                    ticket_no,
                    ticket_token,
                    conversation_id,
                    message_id or "",
                    str(conversation.get("channel") or "web"),
                    str(conversation.get("session_key") or ""),
                    conversation.get("user_label"),
                    str(contact or "").strip()[:120],
                    reason,
                    "urgent" if priority == "urgent" else "normal",
                    summary[:500],
                    source,
                    timestamp,
                    timestamp,
                ),
            )
            break
        except Exception:
            ticket_no = _ticket_no()
    row = db.query_one("SELECT * FROM handoff_requests WHERE ticket_no = ?", (ticket_no,))
    return _row_to_handoff(row or {}, conversation)


def _row_to_handoff(row: dict, conversation: dict | None = None) -> dict:
    conversation = conversation or {}
    return {
        "id": row.get("id"),
        "ticket_no": row.get("ticket_no") or "",
        "ticket_token": row.get("ticket_token") or "",
        "conversation_id": row.get("conversation_id") or "",
        "channel": row.get("channel") or conversation.get("channel") or "web",
        "session_key": row.get("session_key") or conversation.get("session_key") or "",
        "user_label": row.get("user_label") or conversation.get("user_label"),
        "contact": row.get("contact") or "",
        "reason": row.get("reason") or "other",
        "reason_label": HANDOFF_REASONS.get(row.get("reason") or "", "其他"),
        "priority": row.get("priority") or "normal",
        "status": row.get("status") or "pending",
        "summary": row.get("summary") or "",
        "assignee_id": row.get("assignee_id"),
        "resolution": row.get("resolution") or "",
        "source": row.get("source") or "student",
        "created_at": row.get("created_at"),
        "claimed_at": row.get("claimed_at"),
        "first_response_at": row.get("first_response_at"),
        "resolved_at": row.get("resolved_at"),
    }


def student_ticket(ticket_no: str, token: str) -> dict:
    row = db.query_one(
        "SELECT * FROM handoff_requests WHERE ticket_no = ? AND ticket_token = ?",
        (str(ticket_no or "").strip(), str(token or "").strip()),
    )
    if not row:
        raise ValueError("工单不存在或查询凭证不正确")
    return {
        "ticket_no": row["ticket_no"],
        "status": row.get("status"),
        "reason_label": HANDOFF_REASONS.get(row.get("reason") or "", "其他"),
        "created_at": row.get("created_at"),
        "resolved_at": row.get("resolved_at"),
    }


def _sla_text(ticket: dict, settings: dict) -> str:
    if ticket.get("priority") == "urgent":
        return f"紧急情况已记录，值班老师会尽快联系你（目标 {settings.get('crisis_sla_minutes', 5)} 分钟内）。"
    return f"已记录，工作时间 {settings.get('handoff_sla_hours', 24)} 小时内回复。"


def _days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=max(0, int(days)))).isoformat(timespec="seconds")


def list_handoffs(
    *, status: str = "", priority: str = "", days: int = 30, limit: int = 50, offset: int = 0
) -> dict:
    where = ["created_at >= ?"]
    params: list[Any] = [_days_ago(days)]
    if status:
        where.append("status = ?")
        params.append(status)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    clause = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM handoff_requests WHERE {clause}", params)
    rows = db.query(
        f"SELECT * FROM handoff_requests WHERE {clause} "
        "ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, created_at DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
    )
    conversation_ids = [row["conversation_id"] for row in rows]
    conversations = {}
    if conversation_ids:
        placeholders = ",".join("?" * len(conversation_ids))
        for item in db.query(
            f"SELECT * FROM conversations WHERE id IN ({placeholders})", conversation_ids
        ):
            conversations[item["id"]] = item
    return {
        "items": [_row_to_handoff(row, conversations.get(row["conversation_id"])) for row in rows],
        "total": int((total or {}).get("n") or 0),
    }


def get_handoff(handoff_id: str) -> dict:
    row = db.query_one("SELECT * FROM handoff_requests WHERE id = ?", (handoff_id,))
    if not row:
        raise ValueError("工单不存在")
    payload = _row_to_handoff(row)
    payload["messages"] = [
        {
            "id": item["id"],
            "role": item["role"],
            "content": item["content"],
            "created_at": item["created_at"],
        }
        for item in db.query(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at ASC, rowid ASC LIMIT 200",
            (row["conversation_id"],),
        )
    ]
    return payload


def claim_handoff(handoff_id: str, assignee_id: str = "admin") -> dict:
    row = db.query_one("SELECT * FROM handoff_requests WHERE id = ?", (handoff_id,))
    if not row:
        raise ValueError("工单不存在")
    if row.get("status") in {"resolved", "closed"}:
        raise ValueError("工单已结束，无法认领")
    db.execute(
        "UPDATE handoff_requests SET status = 'claimed', assignee_id = ?, "
        "claimed_at = COALESCE(claimed_at, ?), updated_at = ? WHERE id = ?",
        (assignee_id, db.now(), db.now(), handoff_id),
    )
    return get_handoff(handoff_id)


def reply_handoff(handoff_id: str, content: str, author: str = "辅导员") -> dict:
    text = str(content or "").strip()
    if not text:
        raise ValueError("回复内容不能为空")
    row = db.query_one("SELECT * FROM handoff_requests WHERE id = ?", (handoff_id,))
    if not row:
        raise ValueError("工单不存在")
    db.add_message(
        row["conversation_id"],
        "assistant",
        text,
        citations=[],
        model=author,
        retrieval={"handoff": True},
        fallback=False,
        navigation_method="handoff",
    )
    db.execute(
        "UPDATE handoff_requests SET status = 'replied', "
        "first_response_at = COALESCE(first_response_at, ?), updated_at = ? WHERE id = ?",
        (db.now(), db.now(), handoff_id),
    )
    return get_handoff(handoff_id)


def resolve_handoff(handoff_id: str, resolution: str = "") -> dict:
    row = db.query_one("SELECT * FROM handoff_requests WHERE id = ?", (handoff_id,))
    if not row:
        raise ValueError("工单不存在")
    db.execute(
        "UPDATE handoff_requests SET status = 'resolved', resolution = ?, "
        "resolved_at = ?, updated_at = ? WHERE id = ?",
        (str(resolution or "").strip()[:1000], db.now(), db.now(), handoff_id),
    )
    return get_handoff(handoff_id)


# ---------------------------------------------------------------------------
# Crisis
# ---------------------------------------------------------------------------
def crisis_reply(settings: dict | None = None) -> str:
    settings = settings or _settings()
    lines = [
        "⚠️ 你现在的安全最重要。",
        "请立即联系你信任的人：辅导员、学院学工办、学校心理中心或保卫处；"
        "如果有立即的人身危险，请马上拨打 120 / 110。",
    ]
    hotlines = settings.get("crisis_hotlines") or []
    if hotlines:
        lines.append("可用联系方式：")
        for item in hotlines[:6]:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                phone = str(item.get("phone") or "").strip()
                if name or phone:
                    lines.append(f"- {name}{('：' + phone) if phone else ''}")
            elif str(item).strip():
                lines.append(f"- {str(item).strip()}")
    lines.append("我已记录本次求助，请同时直接联系辅导员或值班老师，不要只等待线上回复。")
    return "\n".join(lines)


def create_crisis(
    *,
    conversation_id: str,
    message_id: str,
    text: str,
    crisis: dict,
    settings: dict | None = None,
) -> dict:
    settings = settings or _settings()
    handoff = create_handoff(
        conversation_id=conversation_id,
        reason="crisis",
        note=str(text or "")[:500],
        message_id=message_id,
        source="crisis",
        priority="urgent",
        summary=str(text or "")[:200],
    )
    event_id = db.new_id()
    timestamp = db.now()
    db.execute(
        "INSERT INTO crisis_events(id, handoff_id, conversation_id, message_id, category, "
        "matched_terms, severity, status, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (
            event_id,
            handoff.get("id") or "",
            conversation_id,
            message_id or "",
            crisis.get("category") or "other",
            db.to_json(crisis.get("terms") or []),
            crisis.get("severity") or "high",
            timestamp,
        ),
    )
    notify_async(
        "crisis",
        event_id,
        {
            "event": "crisis",
            "ticket_no": handoff.get("ticket_no"),
            "category": crisis.get("category"),
            "severity": crisis.get("severity"),
            "priority": "urgent",
            "created_at": timestamp,
        },
        settings,
    )
    return {"event_id": event_id, "handoff": handoff, "settings": settings}


def crisis_reply_payload(settings: dict, crisis: dict, handoff: dict) -> dict:
    return {
        "category": crisis.get("category") or "other",
        "severity": crisis.get("severity") or "high",
        "terms": crisis.get("terms") or [],
        "ticket_no": handoff.get("ticket_no") or "",
        "handoff_id": handoff.get("id") or "",
        "hotlines": settings.get("crisis_hotlines") or [],
    }


def crisis_enabled() -> bool:
    try:
        return bool(_settings().get("crisis_enabled", True))
    except Exception:  # pragma: no cover
        return True


def handle_crisis_answer(
    *,
    text: str,
    conversation: dict | None,
    channel: str,
    settings: dict | None = None,
    crisis: dict,
    started: float,
) -> dict:
    """Fixed safety answer + persistence for a detected crisis message."""
    settings = settings or _settings()
    conversation_id = str((conversation or {}).get("id") or "")
    reply = crisis_reply(settings)
    message_id = ""
    crisis_payload = {
        "category": crisis.get("category") or "other",
        "severity": crisis.get("severity") or "high",
        "terms": crisis.get("terms") or [],
        "ticket_no": "",
        "handoff_id": "",
        "hotlines": settings.get("crisis_hotlines") or [],
    }
    if conversation_id:
        try:
            db.add_message(conversation_id, "user", text)
            saved = db.add_message(
                conversation_id,
                "assistant",
                reply,
                citations=[],
                model="危机求助引导",
                latency_ms=int((time.perf_counter() - started) * 1000),
                retrieval={"crisis": True, "wiki_used": False, "complete": False},
                fallback=True,
                navigation_method="crisis",
            )
            message_id = str((saved or {}).get("id") or "")
            result = create_crisis(
                conversation_id=conversation_id,
                message_id=message_id,
                text=text,
                crisis=crisis,
                settings=settings,
            )
            crisis_payload = crisis_reply_payload(settings, crisis, result["handoff"])
            crisis_payload["sla"] = _sla_text(result["handoff"], settings)
        except Exception:
            logger.exception("危机求助落库/升级失败（回答仍返回）")
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "reply": reply,
        "citations": [],
        "model": "危机求助引导",
        "provider_type": "none",
        "latency_ms": latency_ms,
        "fallback": True,
        "sources_used": 0,
        "navigation": {"method": "crisis", "pages": []},
        "retrieval": {
            "wiki_used": False,
            "remote_used": False,
            "complete": False,
            "reason": "crisis",
            "crisis": True,
        },
        "crisis": crisis_payload,
        "handoff": {
            "ticket_no": crisis_payload.get("ticket_no") or "",
            "status": "pending",
            "priority": "urgent",
        },
    }


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------
def _send_webhook(url: str, payload: dict) -> None:
    response = httpx.post(url, json=payload, timeout=5.0)
    response.raise_for_status()


def notify_async(event_type: str, resource_id: str, payload: dict, settings: dict | None = None) -> None:
    settings = settings or _settings()
    url = str(settings.get("crisis_notify_webhook") or "").strip()
    if not url:
        return
    log_id = db.new_id()
    db.execute(
        "INSERT INTO notification_log(id, channel, target, event_type, resource_id, status, "
        "attempts, created_at) VALUES(?, 'webhook', ?, ?, ?, 'pending', 0, ?)",
        (log_id, url[:200], event_type, resource_id, db.now()),
    )

    def worker() -> None:
        error = ""
        for attempt in range(1, 4):
            try:
                _send_webhook(url, payload)
                db.execute(
                    "UPDATE notification_log SET status = 'sent', attempts = ?, sent_at = ? WHERE id = ?",
                    (attempt, db.now(), log_id),
                )
                return
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:300]
                time.sleep(min(15, attempt * 2))
        db.execute(
            "UPDATE notification_log SET status = 'failed', attempts = 3, last_error = ? WHERE id = ?",
            (error, log_id),
        )

    threading.Thread(target=worker, daemon=True, name="npu-notify").start()


def list_crisis_events(status: str = "", limit: int = 50) -> dict:
    where = "WHERE status = ?" if status else ""
    params: list[Any] = [status] if status else []
    rows = db.query(
        f"SELECT * FROM crisis_events {where} ORDER BY created_at DESC LIMIT ?",
        [*params, max(1, min(int(limit or 50), 200))],
    )
    return {
        "items": [
            {
                "id": row["id"],
                "handoff_id": row.get("handoff_id"),
                "conversation_id": row.get("conversation_id"),
                "category": row.get("category"),
                "matched_terms": db.from_json(row.get("matched_terms"), []),
                "severity": row.get("severity"),
                "status": row.get("status"),
                "notified_at": row.get("notified_at"),
                "notify_result": row.get("notify_result"),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]
    }


def update_crisis_event(event_id: str, status: str, note: str = "", handled_by: str = "admin") -> dict:
    if status not in CRISIS_STATUSES:
        raise ValueError("不支持的危机状态")
    row = db.query_one("SELECT id FROM crisis_events WHERE id = ?", (event_id,))
    if not row:
        raise ValueError("危机事件不存在")
    db.execute(
        "UPDATE crisis_events SET status = ?, note = ?, handled_by = ?, handled_at = ? WHERE id = ?",
        (status, str(note or "")[:1000], handled_by, db.now(), event_id),
    )
    return {"ok": True, "id": event_id, "status": status}


__all__ = [
    "HANDOFF_REASONS",
    "detect_crisis",
    "crisis_enabled",
    "create_handoff",
    "student_ticket",
    "list_handoffs",
    "get_handoff",
    "claim_handoff",
    "reply_handoff",
    "resolve_handoff",
    "crisis_reply",
    "create_crisis",
    "handle_crisis_answer",
    "notify_async",
    "list_crisis_events",
    "update_crisis_event",
]

"""System alerts (v0.2.0 M4).

Alerts are the operational inbox: job failures, circuit-breaker openings and
notification failures land here so an administrator can see them without
reading logs.  Deduplication keeps repeated identical failures from flooding
the list.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db

LEVELS = {"info", "warning", "error", "critical"}
STATUSES = {"open", "acknowledged", "resolved"}


def create_alert(
    title: str,
    *,
    level: str = "warning",
    category: str = "system",
    detail: str = "",
    source: str = "",
    resource_id: str = "",
    dedupe_hours: int = 1,
) -> dict:
    title = str(title or "").strip()[:200] or "系统告警"
    if level not in LEVELS:
        level = "warning"
    if dedupe_hours > 0:
        since = (datetime.now() - timedelta(hours=dedupe_hours)).isoformat(timespec="seconds")
        existing = db.query_one(
            "SELECT * FROM system_alerts WHERE status = 'open' AND title = ? "
            "AND category = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
            (title, category, since),
        )
        if existing:
            db.execute(
                "UPDATE system_alerts SET detail = ?, updated_at = ? WHERE id = ?",
                (str(detail or existing.get("detail") or "")[:1000], db.now(), existing["id"]),
            )
            return get_alert(existing["id"]) or {}
    alert_id = db.new_id()
    timestamp = db.now()
    db.execute(
        "INSERT INTO system_alerts(id, level, category, title, detail, status, source, "
        "resource_id, created_at, updated_at) VALUES(?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
        (
            alert_id,
            level,
            str(category or "system")[:40],
            title,
            str(detail or "")[:1000],
            str(source or "")[:80],
            str(resource_id or "")[:80],
            timestamp,
            timestamp,
        ),
    )
    return get_alert(alert_id) or {}


def get_alert(alert_id: str) -> dict | None:
    return db.query_one("SELECT * FROM system_alerts WHERE id = ?", (alert_id,))


def list_alerts(*, status: str = "", level: str = "", limit: int = 50) -> dict:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if level:
        where.append("level = ?")
        params.append(level)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = db.query(
        f"SELECT * FROM system_alerts {clause} ORDER BY created_at DESC LIMIT ?",
        [*params, max(1, min(int(limit or 50), 200))],
    )
    open_count = db.query_one("SELECT COUNT(*) AS n FROM system_alerts WHERE status = 'open'")
    return {"items": rows, "total": len(rows), "open": int((open_count or {}).get("n") or 0)}


def update_alert(alert_id: str, status: str, *, actor: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("不支持的告警状态")
    row = get_alert(alert_id)
    if not row:
        raise ValueError("告警不存在")
    timestamp = db.now()
    db.execute(
        "UPDATE system_alerts SET status = ?, updated_at = ?, resolved_at = ?, "
        "resolved_by = ? WHERE id = ?",
        (
            status,
            timestamp,
            timestamp if status == "resolved" else row.get("resolved_at"),
            str(actor or "")[:80] if status == "resolved" else (row.get("resolved_by") or ""),
            alert_id,
        ),
    )
    return get_alert(alert_id) or {}


def open_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM system_alerts WHERE status = 'open'")
    return int((row or {}).get("n") or 0)


__all__ = ["LEVELS", "STATUSES", "create_alert", "get_alert", "list_alerts", "update_alert", "open_count"]

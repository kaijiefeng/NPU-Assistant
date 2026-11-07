"""Freshness / time-awareness helpers (v0.2.0).

The LLM Wiki is not a live feed: a page compiled from a 2024 notice may still be
returned for a 2026 question.  This module derives a *read-time* freshness state
from the dates stored on pages/documents.  Nothing is persisted, so the state
cannot go stale in the database.

States
------
* ``expired``  – ``expires_at`` is in the past.
* ``stale``    – not verified for ``freshness_stale_days`` or older academic year.
* ``aging``    – verified more than 90 days ago (soft warning).
* ``fresh``    – has date metadata and is neither stale nor expired.
* ``unknown``  – no date metadata at all.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from .. import config, db

_DATE_RE = re.compile(r"^\s*(\d{4})[-/.年]\s*(\d{1,2})(?:[-/.月]\s*(\d{1,2}))?")

STATE_LABELS = {
    "fresh": "最新",
    "aging": "待核对",
    "stale": "可能过时",
    "expired": "已过期",
    "unknown": "无日期",
}


def parse_date(value: Any) -> date | None:
    """Parse common Chinese date strings; return ``None`` when unusable."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    match = _DATE_RE.match(text)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3) or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def current_academic_year(today: date | None = None) -> str:
    moment = today or date.today()
    start = moment.year if moment.month >= 8 else moment.year - 1
    return f"{start}-{start + 1}"


def configured_academic_year() -> str:
    value = (db.get_setting("academic_year") or "").strip()
    return value or current_academic_year()


def _setting_int(key: str, default: int) -> int:
    try:
        return int(db.get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _academic_start(value: str) -> int | None:
    match = re.match(r"^\s*(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def freshness_for(
    *,
    published_at: Any = "",
    effective_at: Any = "",
    expires_at: Any = "",
    verified_at: Any = "",
    academic_year: Any = "",
    today: date | None = None,
) -> dict:
    """Return a serialisable freshness descriptor for a page/document."""
    moment = today or date.today()
    expires = parse_date(expires_at)
    verified = parse_date(verified_at)
    published = parse_date(published_at)
    effective = parse_date(effective_at)
    stale_days = _setting_int("freshness_stale_days", 180)
    warn_days = _setting_int("freshness_expiry_warn_days", 30)
    current_year = configured_academic_year() or current_academic_year(moment)

    days_until_expiry = (expires - moment).days if expires else None
    days_since_verified = (moment - verified).days if verified else None

    page_year = _academic_start(str(academic_year or ""))
    current_year_start = _academic_start(current_year)
    older_year = bool(
        page_year is not None
        and current_year_start is not None
        and page_year < current_year_start
    )

    if expires and expires < moment:
        status = "expired"
    elif (days_since_verified is not None and days_since_verified > stale_days) or older_year:
        status = "stale"
    elif days_since_verified is not None and days_since_verified > 90:
        status = "aging"
    elif any((expires, verified, published, effective, academic_year)):
        status = "fresh"
    else:
        status = "unknown"

    return {
        "status": status,
        "label": STATE_LABELS[status],
        "published_at": published.isoformat() if published else "",
        "effective_at": effective.isoformat() if effective else "",
        "expires_at": expires.isoformat() if expires else "",
        "verified_at": verified.isoformat() if verified else "",
        "academic_year": str(academic_year or ""),
        "current_academic_year": current_year,
        "days_until_expiry": days_until_expiry,
        "days_since_verified": days_since_verified,
        "expiring_soon": bool(
            days_until_expiry is not None and 0 <= days_until_expiry <= warn_days
        ),
        "is_expired": status == "expired",
        "is_stale": status in {"stale", "expired"},
    }


def navigation_boost(page: dict) -> float:
    """Small score adjustment: fresh content wins, expired content loses."""
    status = (page.get("freshness") or {}).get("status") or "unknown"
    return {
        "fresh": 0.04,
        "aging": -0.02,
        "stale": -0.08,
        "expired": -0.25,
        "unknown": 0.0,
    }.get(status, 0.0)


def should_exclude(page: dict) -> bool:
    """Expired pages are excluded unless no other candidate exists."""
    return (page.get("freshness") or {}).get("status") == "expired"


def expiry_soon(page: dict) -> bool:
    return bool((page.get("freshness") or {}).get("expiring_soon"))


def describe(page: dict) -> str:
    """One-line Chinese description for prompt/context."""
    freshness = page.get("freshness") or {}
    parts: list[str] = []
    if freshness.get("published_at"):
        parts.append(f"发布 {freshness['published_at']}")
    if freshness.get("verified_at"):
        parts.append(f"核对 {freshness['verified_at']}")
    if freshness.get("academic_year"):
        parts.append(f"适用 {freshness['academic_year']}")
    if freshness.get("expires_at"):
        parts.append(f"有效期至 {freshness['expires_at']}")
    if freshness.get("status") in {"stale", "expired"}:
        parts.append(f"【{freshness.get('label')}】")
    return " · ".join(parts)


__all__ = [
    "STATE_LABELS",
    "parse_date",
    "current_academic_year",
    "configured_academic_year",
    "freshness_for",
    "navigation_boost",
    "should_exclude",
    "expiry_soon",
    "describe",
]

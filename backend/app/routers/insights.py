"""Service-desk / RSI routes (v0.2.0).

Public:
* ``POST /api/chat/feedback`` — student rates an assistant answer.

Admin (requires ``X-Admin-Token``):
* ``GET  /api/insights/overview``
* ``GET  /api/insights/gaps`` / ``GET /api/insights/gaps/{id}``
* ``POST /api/insights/gaps/{id}/link|resolve|ignore|assign``
* ``GET  /api/insights/feedback``
* ``POST /api/insights/feedback/{id}/status``
* ``POST /api/insights/recompute``
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..security import require_admin_token
from ..services import insights

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["insights"])


class FeedbackRequest(BaseModel):
    message_id: str
    rating: int = Field(description="1=helpful, -1=unhelpful")
    reason_code: str = ""
    comment: str = ""
    voter_key: str = ""


class GapLinkRequest(BaseModel):
    page_id: str = ""
    document_id: str = ""
    note: str = ""


class GapNoteRequest(BaseModel):
    note: str = ""
    assignee_id: str = ""


class FeedbackStatusRequest(BaseModel):
    status: str
    note: str = ""


@router.post("/chat/feedback")
def submit_feedback(payload: FeedbackRequest) -> dict:
    """学生提交回答反馈（公开接口，限流由中间件统一处理）。"""
    try:
        return insights.submit_feedback(
            message_id=payload.message_id,
            voter_key=payload.voter_key,
            rating=payload.rating,
            reason_code=payload.reason_code,
            comment=payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chat/feedback/{message_id}")
def get_feedback(message_id: str, voter_key: str = "") -> dict:
    row = insights.feedback_for_message(message_id, voter_key)
    if not row:
        return {"item": None}
    return {
        "item": {
            "rating": int(row.get("rating") or 0),
            "reason_code": row.get("reason_code") or "",
            "comment": row.get("comment") or "",
        }
    }


@router.get(
    "/insights/overview", dependencies=[Depends(require_admin_token)]
)
def overview(days: int = 7) -> dict:
    return insights.overview(days=max(1, min(int(days or 7), 90)))


@router.get("/insights/gaps", dependencies=[Depends(require_admin_token)])
def list_gaps(
    status: str = "open",
    days: int = 30,
    channel: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return insights.list_gaps(
        status=status,
        days=days,
        channel=channel,
        query=q,
        limit=limit,
        offset=offset,
    )


@router.get("/insights/gaps/{gap_id}", dependencies=[Depends(require_admin_token)])
def get_gap(gap_id: str) -> dict:
    try:
        return insights.get_gap(gap_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/insights/gaps/{gap_id}/link", dependencies=[Depends(require_admin_token)]
)
def link_gap(gap_id: str, payload: GapLinkRequest) -> dict:
    try:
        return insights.update_gap(
            gap_id,
            "link",
            page_id=payload.page_id,
            document_id=payload.document_id,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/insights/gaps/{gap_id}/resolve", dependencies=[Depends(require_admin_token)]
)
def resolve_gap(gap_id: str, payload: GapNoteRequest) -> dict:
    try:
        return insights.update_gap(gap_id, "resolve", note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/insights/gaps/{gap_id}/ignore", dependencies=[Depends(require_admin_token)]
)
def ignore_gap(gap_id: str, payload: GapNoteRequest) -> dict:
    try:
        return insights.update_gap(gap_id, "ignore", note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/insights/gaps/{gap_id}/assign", dependencies=[Depends(require_admin_token)]
)
def assign_gap(gap_id: str, payload: GapNoteRequest) -> dict:
    try:
        return insights.update_gap(
            gap_id, "assign", assignee_id=payload.assignee_id, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/insights/feedback", dependencies=[Depends(require_admin_token)])
def list_feedback(
    status: str = "",
    rating: int | None = None,
    days: int = 30,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return insights.list_feedback(
        status=status,
        rating=rating,
        days=days,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/insights/feedback/{feedback_id}/status",
    dependencies=[Depends(require_admin_token)],
)
def feedback_status(feedback_id: str, payload: FeedbackStatusRequest) -> dict:
    try:
        return insights.update_feedback_status(feedback_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/insights/recompute", dependencies=[Depends(require_admin_token)])
def recompute(days: int = 90) -> dict:
    return insights.recompute_gaps(days=max(1, min(int(days or 90), 365)))


@router.get("/insights/freshness", dependencies=[Depends(require_admin_token)])
def freshness_overview(window: int = 30) -> dict:
    """Expiring / expired / stale pages for the content desk."""
    from .. import db
    from ..services import freshness

    rows = db.query("SELECT * FROM wiki_pages ORDER BY updated_at DESC")
    expiring, expired, stale = [], [], []
    for row in rows:
        info = freshness.freshness_for(
            published_at=row.get("published_at"),
            effective_at=row.get("effective_at"),
            expires_at=row.get("expires_at"),
            verified_at=row.get("verified_at"),
            academic_year=row.get("academic_year"),
        )
        item = {
            "id": row["id"],
            "title": row.get("title") or "",
            "status": row.get("status") or "ready",
            "freshness": info,
        }
        if info["status"] == "expired":
            expired.append(item)
        elif info["status"] == "stale":
            stale.append(item)
        elif info.get("expiring_soon"):
            expiring.append(item)
    return {
        "window_days": int(window or 30),
        "expiring": expiring,
        "expired": expired,
        "stale": stale,
        "total": len(rows),
    }


__all__ = ["router"]

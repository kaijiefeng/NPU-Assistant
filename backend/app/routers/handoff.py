"""Handoff / crisis routes (v0.2.0)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..security import require_admin_token
from ..services import handoff

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["handoff"])


class HandoffRequest(BaseModel):
    conversation_id: str
    message_id: str = ""
    reason: str = "other"
    contact: str = ""
    note: str = ""
    summary: str = ""


class HandoffReply(BaseModel):
    content: str


class HandoffResolve(BaseModel):
    resolution: str = ""


class HandoffAssign(BaseModel):
    assignee_id: str = "admin"


class CrisisUpdate(BaseModel):
    status: str
    note: str = ""


@router.post("/handoff")
def create_handoff(payload: HandoffRequest) -> dict:
    try:
        ticket = handoff.create_handoff(
            conversation_id=payload.conversation_id,
            reason=payload.reason,
            contact=payload.contact,
            note=payload.note,
            message_id=payload.message_id,
            summary=payload.summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = handoff._settings()
    return {
        "ticket_no": ticket["ticket_no"],
        "ticket_token": ticket["ticket_token"],
        "status": ticket["status"],
        "reason_label": ticket["reason_label"],
        "sla": (
            f"紧急情况已记录，值班老师会尽快联系你（目标 {settings.get('crisis_sla_minutes', 5)} 分钟内）。"
            if ticket["priority"] == "urgent"
            else f"已记录，工作时间 {settings.get('handoff_sla_hours', 24)} 小时内回复。"
        ),
    }


@router.get("/handoff/ticket/{ticket_no}")
def ticket_status(ticket_no: str, token: str = "") -> dict:
    try:
        return handoff.student_ticket(ticket_no, token)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/handoff", dependencies=[Depends(require_admin_token)])
def list_handoffs(
    status: str = "",
    priority: str = "",
    days: int = 30,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return handoff.list_handoffs(
        status=status, priority=priority, days=days, limit=limit, offset=offset
    )


@router.get("/handoff/{handoff_id}", dependencies=[Depends(require_admin_token)])
def get_handoff(handoff_id: str) -> dict:
    try:
        return handoff.get_handoff(handoff_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/handoff/{handoff_id}/claim", dependencies=[Depends(require_admin_token)])
def claim_handoff(handoff_id: str, payload: HandoffAssign | None = None) -> dict:
    try:
        return handoff.claim_handoff(handoff_id, (payload.assignee_id if payload else "admin") or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/handoff/{handoff_id}/assign", dependencies=[Depends(require_admin_token)])
def assign_handoff(handoff_id: str, payload: HandoffAssign) -> dict:
    try:
        return handoff.claim_handoff(handoff_id, payload.assignee_id or "admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/handoff/{handoff_id}/reply", dependencies=[Depends(require_admin_token)])
def reply_handoff(handoff_id: str, payload: HandoffReply) -> dict:
    try:
        return handoff.reply_handoff(handoff_id, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/handoff/{handoff_id}/resolve", dependencies=[Depends(require_admin_token)])
def resolve_handoff(handoff_id: str, payload: HandoffResolve | None = None) -> dict:
    try:
        return handoff.resolve_handoff(handoff_id, (payload.resolution if payload else "") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/crisis/events", dependencies=[Depends(require_admin_token)])
def crisis_events(status: str = "", limit: int = 50) -> dict:
    return handoff.list_crisis_events(status=status, limit=limit)


@router.post(
    "/crisis/events/{event_id}/status", dependencies=[Depends(require_admin_token)]
)
def crisis_status(event_id: str, payload: CrisisUpdate) -> dict:
    try:
        return handoff.update_crisis_event(event_id, payload.status, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]

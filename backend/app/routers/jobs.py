"""Background jobs and system alerts (v0.2.0 M4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..security import require_admin_token, require_permission
from ..services import alerts, jobs

router = APIRouter(tags=["jobs"])


@router.get("/jobs", dependencies=[Depends(require_admin_token)])
def list_jobs(
    status: str = "", type: str = "", limit: int = 50, offset: int = 0
) -> dict:
    return jobs.list_jobs(status=status, kind=type, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", dependencies=[Depends(require_admin_token)])
def get_job(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    request: Request,
    principal: dict = Depends(require_permission("content:write")),
) -> dict:
    try:
        job = jobs.retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from ..services import auth

    auth.record_audit(
        principal,
        "job.retry",
        target_type="job",
        target_id=job_id,
        ip=request.client.host if request.client else "",
    )
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    request: Request,
    principal: dict = Depends(require_permission("content:write")),
) -> dict:
    try:
        job = jobs.request_cancel(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from ..services import auth

    auth.record_audit(
        principal,
        "job.cancel",
        target_type="job",
        target_id=job_id,
        ip=request.client.host if request.client else "",
    )
    return job


@router.get("/alerts", dependencies=[Depends(require_admin_token)])
def list_alerts(status: str = "", level: str = "", limit: int = 50) -> dict:
    return alerts.list_alerts(status=status, level=level, limit=limit)


@router.post("/alerts/{alert_id}/ack")
def ack_alert(
    alert_id: str,
    principal: dict = Depends(require_permission("insights:write")),
) -> dict:
    try:
        return alerts.update_alert(
            alert_id, "acknowledged", actor=principal.get("username") or ""
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    principal: dict = Depends(require_permission("insights:write")),
) -> dict:
    try:
        return alerts.update_alert(
            alert_id, "resolved", actor=principal.get("username") or ""
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]

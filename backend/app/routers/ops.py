"""运维 Agent 路由（v0.3.0 开发中）——仅超级管理员可用。

* ``GET    /api/ops/agent/tasks``            任务列表（可按 status 过滤）
* ``GET    /api/ops/agent/tasks/{id}`        任务详情（含日志在 log 字段外另行返回）
* ``POST   /api/ops/agent/tasks``            粘贴报错创建任务（自动排队分析）
* ``POST   /api/ops/agent/tasks/{id}/approve`` 许可执行（planned → executing，自动排队）
* ``POST   /api/ops/agent/tasks/{id}/cancel``  取消（清理工作树）
* ``POST   /api/ops/agent/tasks/{id}/cleanup`` 清理已结束任务残留的工作树
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import config
from ..security import resolve_principal
from ..services import auth as auth_service
from ..services import jobs, ops_agent

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["ops"])


def _require_super() -> None:
    """open 演示模式（无账号/无令牌）放行；否则仅 ops:manage（超级管理员）。"""

    def dependency(request: Request) -> dict:
        principal = resolve_principal(request)
        open_mode = auth_service.user_count() == 0 and not config.ADMIN_TOKEN
        if open_mode:
            return {"role": "super_admin", "auth": "open"}
        if not principal:
            raise HTTPException(status_code=401, detail="请先登录超级管理员账号")
        if not auth_service.has_permission(str(principal.get("role") or ""), "ops:manage"):
            raise HTTPException(status_code=403, detail="运维 Agent 仅超级管理员可用")
        return principal

    return Depends(dependency)


PERMISSION = _require_super()


class OpsCreateRequest(BaseModel):
    error_input: str
    title: str = ""


class OpsApproveRequest(BaseModel):
    run_e2e: bool = False


@router.get("/ops/agent/tasks", dependencies=[PERMISSION])
def list_tasks(status: str = "", limit: int = 50) -> dict:
    return ops_agent.list_tasks(status=status, limit=limit)


@router.get("/ops/agent/tasks/{task_id}", dependencies=[PERMISSION])
def get_task(task_id: str) -> dict:
    try:
        return ops_agent.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ops/agent/tasks", status_code=202, dependencies=[PERMISSION])
def create_task(payload: OpsCreateRequest) -> dict:
    try:
        task = ops_agent.create_task(payload.error_input, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = jobs.create_job("ops_analyze", {"task_id": task["id"]}, created_by="admin")
    return {
        "task_id": task["id"],
        "job_id": job["id"],
        "status": task["status"],
        "message": "已开始分析报错，方案生成后需你批准执行。",
    }


@router.post("/ops/agent/tasks/{task_id}/approve", status_code=202, dependencies=[PERMISSION])
def approve_task(task_id: str, payload: OpsApproveRequest) -> dict:
    try:
        task = ops_agent.approve(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = jobs.create_job(
        "ops_execute",
        {"task_id": task_id, "run_e2e": bool(payload.run_e2e)},
        created_by="admin",
        max_attempts=1,
    )
    return {
        "task_id": task_id,
        "job_id": job["id"],
        "status": task["status"],
        "message": "已批准，修复流水线执行中（工作树内进行，失败会保留现场）。",
    }


@router.post("/ops/agent/tasks/{task_id}/cancel", dependencies=[PERMISSION])
def cancel_task(task_id: str) -> dict:
    try:
        return ops_agent.cancel(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ops/agent/tasks/{task_id}/cleanup", dependencies=[PERMISSION])
def cleanup_task(task_id: str) -> dict:
    try:
        return ops_agent.cleanup(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

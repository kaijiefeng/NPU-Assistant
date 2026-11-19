"""管理 Agent（右侧常驻面板）：SSE 对话 + 会话管理 + 工具清单。

对话端点要求登录（任意账号）；工具执行时按调用者角色过滤权限
（services/agent_tools.call_tool），真正的权限闸在工具层。
会话与历史全部服务端持久化（services/agent_sessions），前端只传
conversation_id——对话上下文不可被客户端伪造。
"""
from __future__ import annotations

import json
import logging
import queue as queue_module
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..security import require_admin_token, require_permission
from ..services import agent_chat, agent_sessions, agent_tools, scheduler

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["agent"])


@router.get("/agent/tools")
def list_agent_tools(principal: dict = Depends(require_admin_token)) -> dict:
    """当前账号可用的管理工具清单（面板展示与自检用）。"""
    specs = agent_tools.tools_for(str(principal.get("role") or ""))
    return {
        "items": [
            {
                "name": spec.name,
                "description": spec.description,
                "permission": spec.permission,
                "write": spec.write,
                "dangerous": spec.dangerous,
            }
            for spec in specs
        ],
        "total": len(specs),
    }


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------
@router.get("/agent/conversations")
def list_conversations(limit: int = 30, principal: dict = Depends(require_admin_token)) -> dict:
    return agent_sessions.list_conversations(limit)


@router.get("/agent/conversations/{conversation_id}")
def get_conversation(conversation_id: str, principal: dict = Depends(require_admin_token)) -> dict:
    try:
        conversation = agent_sessions.get_conversation(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"conversation": conversation, "items": agent_sessions.get_messages(conversation_id)}


@router.patch("/agent/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str, payload: dict, principal: dict = Depends(require_admin_token)
) -> dict:
    try:
        return agent_sessions.rename_conversation(conversation_id, str(payload.get("title") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/agent/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, principal: dict = Depends(require_admin_token)) -> dict:
    try:
        return agent_sessions.delete_conversation(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agent/conversations/{conversation_id}/rewind")
def rewind_conversation(
    conversation_id: str, payload: dict, principal: dict = Depends(require_admin_token)
) -> dict:
    """回滚对话上下文到指定消息之前（该消息及其之后删除）。

    只回退对话内容，不撤销已执行的工具副作用（删除/收录等操作仍生效）。
    """
    message_id = str((payload or {}).get("message_id") or "").strip()
    if not message_id:
        raise HTTPException(status_code=400, detail="缺少 message_id")
    try:
        return agent_sessions.truncate_from(conversation_id, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 定时任务（写操作收紧到 content:write）
# ---------------------------------------------------------------------------
@router.get("/agent/schedules")
def list_schedules(principal: dict = Depends(require_admin_token)) -> dict:
    return {**scheduler.list_schedules(), "kinds": dict(scheduler.SCHEDULE_KINDS)}


@router.post("/agent/schedules", status_code=201)
def create_schedule(
    payload: dict, principal: dict = Depends(require_permission("content:write"))
) -> dict:
    try:
        return scheduler.create_schedule(
            str(payload.get("name") or ""),
            str(payload.get("kind") or ""),
            payload.get("config") if isinstance(payload.get("config"), dict) else {},
            int(payload.get("interval_minutes") or 60),
            created_by=str(principal.get("username") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/agent/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str,
    payload: dict,
    principal: dict = Depends(require_permission("content:write")),
) -> dict:
    try:
        return scheduler.update_schedule(
            schedule_id,
            name=payload.get("name"),
            config=payload.get("config") if isinstance(payload.get("config"), dict) else None,
            interval_minutes=payload.get("interval_minutes"),
            enabled=payload.get("enabled"),
        )
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/agent/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: str, principal: dict = Depends(require_permission("content:write"))
) -> dict:
    try:
        return scheduler.delete_schedule(schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agent/schedules/{schedule_id}/run")
def run_schedule(
    schedule_id: str, principal: dict = Depends(require_permission("content:write"))
) -> dict:
    try:
        return scheduler.run_now(schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 对话（SSE）
# ---------------------------------------------------------------------------
class AgentChatRequest(BaseModel):
    message: str = Field(default="", description="本轮用户输入")
    conversation_id: str = Field(default="", description="会话 ID；空则自动新建")
    # 兼容旧字段：历史已服务端持久化，客户端回传一律忽略（防伪造）
    history: list[dict] = Field(default_factory=list)


@router.post("/agent/chat")
def agent_chat_stream(payload: AgentChatRequest, principal: dict = Depends(require_admin_token)) -> StreamingResponse:
    """SSE 管理对话：``tool`` → ``tool_result``（若干轮）→ ``delta`` → ``done``。

    ``done`` 附带 ``conversation_id``；消息（含工具步骤）落库到该会话。
    """
    message = str(payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="请输入你的问题")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="输入过长（最多 4000 字）")
    requested_conv = str(payload.conversation_id or "").strip()

    queue: queue_module.Queue = queue_module.Queue()
    stop = threading.Event()
    holder: dict = {}

    def worker() -> None:
        try:
            conversation = None
            if requested_conv:
                conversation = agent_sessions.get_conversation(requested_conv)
            if conversation is None:
                conversation = agent_sessions.create_conversation()
            holder["conversation"] = conversation
            history = agent_sessions.build_context(conversation["id"])
            agent_sessions.append_message(conversation["id"], "user", message)
            holder["result"] = agent_chat.run_agent_chat(
                history,
                message,
                principal,
                on_event=lambda kind, data: (
                    None if stop.is_set() else queue.put((kind, data))
                ),
            )
            result = holder["result"] or {}
            agent_sessions.append_message(
                conversation["id"], "assistant", str(result.get("reply") or ""), result.get("steps") or []
            )
        except Exception:  # noqa: BLE001 - 任何异常都转为 SSE error
            logger.exception("管理助手对话失败")
            queue.put(("error", {"detail": "管理助手暂时不可用，请稍后重试"}))
        finally:
            queue.put(("end", None))

    def event_stream():
        threading.Thread(target=worker, name="npu-agent-chat", daemon=True).start()
        try:
            while True:
                try:
                    kind, data = queue.get(timeout=15)
                except queue_module.Empty:
                    yield ": ping\n\n"
                    continue
                if kind == "end":
                    break
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            result = holder.get("result")
            conversation = holder.get("conversation") or {}
            if isinstance(result, dict) and result:
                result.setdefault("conversation_id", conversation.get("id") or "")
                result.setdefault("conversation_title", conversation.get("title") or "")
                yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
        finally:
            stop.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]

"""管理 Agent（右侧常驻面板）：SSE 对话 + 工具清单。

对话端点要求登录（任意账号）；工具执行时按调用者角色过滤权限
（services/agent_tools.call_tool），真正的权限闸在工具层。
"""
from __future__ import annotations

import json
import logging
import queue as queue_module
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..security import require_admin_token
from ..services import agent_chat, agent_tools

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["agent"])


class AgentChatMessage(BaseModel):
    role: str = ""
    content: str = ""


class AgentChatRequest(BaseModel):
    message: str = Field(default="", description="本轮用户输入")
    history: list[AgentChatMessage] = Field(default_factory=list, description="最近的对话历史（前端回传）")


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


@router.post("/agent/chat")
def agent_chat_stream(payload: AgentChatRequest, principal: dict = Depends(require_admin_token)) -> StreamingResponse:
    """SSE 管理对话：``tool`` → ``tool_result``（若干轮）→ ``delta`` → ``done``。"""
    message = str(payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="请输入你的问题")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="输入过长（最多 4000 字）")
    history = [
        {"role": item.role, "content": item.content}
        for item in (payload.history or [])
        if item.role in {"user", "assistant"}
    ][-12:]

    queue: queue_module.Queue = queue_module.Queue()
    stop = threading.Event()
    holder: dict = {}

    def worker() -> None:
        try:
            holder["result"] = agent_chat.run_agent_chat(
                history,
                message,
                principal,
                on_event=lambda kind, data: (
                    None if stop.is_set() else queue.put((kind, data))
                ),
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
            if isinstance(result, dict) and result:
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

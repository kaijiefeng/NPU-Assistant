"""管理 Agent 对话编排：LLM ↔ 工具循环。

流程：系统提示词（角色 + 工具目录 + 规则）→ LLM 输出 JSON
（``{"tool": name, "args": {...}}`` 或 ``{"reply": "..."}``）→ 工具执行结果
作为下一轮输入 → 直到给出回答或达到轮次上限。

约束：llm.chat 从不抛异常（失败 = ``fallback=True``），这里据此降级为
友好提示；历史由前端回传，服务端只保留 user/assistant 两种角色并截断。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

from . import llm
from .agent_tools import call_tool, tool_catalog, tools_for
from .wiki_builder import _extract_json

logger = logging.getLogger("npu-assistant")

_MAX_TOOL_ROUNDS = 6
_HISTORY_LIMIT = 12
_MESSAGE_CHARS = 4000  # 单条历史消息截断长度
_RESULT_CHARS = 2400  # 工具结果回灌给模型的截断长度

_FALLBACK_REPLY = (
    "模型暂时不可用，我无法完成这次请求。请稍后再试，"
    "或直接在「知识库管理 / 运维管理」页面手动操作。"
)

_ROLES = {"user", "assistant"}


def _system_prompt(role: str) -> str:
    tools = tool_catalog(tools_for(role))
    return (
        "你是 NPU-Assistant 校园知识库的「管理助手」，通过调用工具帮管理员查询和处理事务。\n"
        "可用工具目录（JSON）：\n"
        + json.dumps(tools, ensure_ascii=False)
        + "\n\n规则：\n"
        "1. 每次只输出一个 JSON 对象，不要输出其他文字或代码块围栏：\n"
        '   - 需要信息/操作：{"tool": "工具名", "args": {参数: 值}}\n'
        '   - 可以回答用户了：{"reply": "面向管理员的中文回答"}\n'
        "2. 标记 dangerous 的工具（删除、全量重建、批准修复等）首次调用会返回 need_confirm："
        "此时改用 reply 向用户复述将要执行的操作和参数，等用户明确同意后，"
        '带 "confirm": true 重新调用同一工具；用户不同意就放弃并说明。\n'
        "3. 参数不明确时先用查询工具获取（如先 list_gaps 拿 gap_id 再 gap_suggest），不要编造 ID。\n"
        "4. 工具返回 job_id 的长任务，向用户说明已提交后台，需要结果时再调 get_job 查询。\n"
        "5. 工具返回 error 时向用户说明原因或换一个做法，不要原样重试同一个失败调用。\n"
        "6. 用户粘贴报错或描述故障时：先用 read_logs / read_file 看现场，再给出报错解释"
        "与操作指导；需要改代码修复时，说明可用 ops_task_create 走运维修复流水线"
        "（会先分析并征求批准）。\n"
        "7. 回答用简短中文，列表优先；引用数据时给出数字和对象名称。"
    )


def _clean_history(history: list) -> list[dict]:
    messages: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")[:_MESSAGE_CHARS]
        if role in _ROLES and content:
            messages.append({"role": role, "content": content})
    return messages[-_HISTORY_LIMIT:]


def _clip_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)[:_RESULT_CHARS]


def _summarize_result(result: dict) -> str:
    """工具结果的一句话摘要（前端步骤条展示用）。"""
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return str(result["error"])[:120]
    if isinstance(result.get("items"), list):
        return f"{len(result['items'])} 条"
    for key in ("message", "title", "job_id", "document_id", "task_id", "build_id", "total"):
        if result.get(key):
            return str(result[key])[:120]
    if "ok" in result:
        return "成功" if result.get("ok") else "失败"
    return ""


def run_agent_chat(
    history: list,
    message: str,
    principal: dict,
    *,
    on_event: Callable[[str, dict], None] | None = None,
) -> dict:
    """跑一轮管理对话；``on_event(kind, data)`` 用于 SSE 实时推送工具步骤。"""

    def emit(kind: str, data: dict) -> None:
        if on_event:
            try:
                on_event(kind, data)
            except Exception:  # noqa: BLE001 - 事件推送失败不影响主流程
                logger.debug("agent 事件推送失败", exc_info=True)

    role = str((principal or {}).get("role") or "")
    if not tools_for(role):
        return {"reply": "当前账号没有可用的管理工具权限，请联系超级管理员调整角色。", "steps": []}

    text = str(message or "").strip()
    if not text:
        return {"reply": "请输入你的问题。", "steps": []}

    messages: list[dict] = [{"role": "system", "content": _system_prompt(role)}]
    messages.extend(_clean_history(history))
    messages.append({"role": "user", "content": text})

    steps: list[dict] = []
    for _ in range(_MAX_TOOL_ROUNDS + 1):
        response = llm.chat(messages, temperature=0.2, max_tokens=1400)
        if response.get("fallback"):
            logger.warning("管理助手 LLM 不可用：%s", response.get("error"))
            return {"reply": _FALLBACK_REPLY, "steps": steps}
        content = str(response.get("content") or "")
        parsed = _extract_json(content, dict) or {}

        tool_name = str(parsed.get("tool") or "").strip()
        if tool_name and not parsed.get("reply"):
            args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
            emit("tool", {"name": tool_name, "args": args})
            result = call_tool(tool_name, args, principal)
            summary = _summarize_result(result)
            ok = not (isinstance(result, dict) and result.get("error"))
            steps.append({"name": tool_name, "ok": ok, "summary": summary})
            emit("tool_result", {"name": tool_name, "ok": ok, "summary": summary})
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"工具 {tool_name} 的执行结果（JSON）：\n{_clip_result(result)}\n"
                        '请继续：还需要信息/操作就继续输出 {"tool":...,"args":{...}}，'
                        '可以回答了就输出 {"reply":"..."}。'
                    ),
                }
            )
            continue

        reply = parsed.get("reply")
        reply = str(reply) if reply is not None and str(reply).strip() else content
        reply = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(reply).strip()).strip()
        if not reply:
            reply = "（这轮没有生成内容，请换个说法再试。）"
        emit("delta", {"text": reply})
        return {"reply": reply, "steps": steps}

    return {
        "reply": (
            f"这轮已执行 {len(steps)} 步操作，达到单轮工具调用上限，我先停在这里。"
            "可以换个更具体的说法，或让我分步执行。"
        ),
        "steps": steps,
    }

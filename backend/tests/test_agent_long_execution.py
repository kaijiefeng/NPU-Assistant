"""200轮执行任务的预算边界、停止与长工具轨迹压缩。"""
import json
from types import SimpleNamespace

from app.services import agent_chat, agent_context, llm

ADMIN = {"role": "admin", "username": "long-operation"}


def test_tool_budget_is_200_and_does_not_stop_after_180_seconds(monkeypatch):
    assert agent_chat._MAX_TOOL_ROUNDS == 200
    calls = []
    executed = []
    clock = [0]
    def monotonic():
        clock[0] += 30
        return clock[0]
    monkeypatch.setattr(agent_chat, "time", SimpleNamespace(monotonic=monotonic))
    def model(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 11:
            return {"content": '{"reply":"十步操作已完成"}', "fallback": False}
        return {"content": json.dumps({"tool":"get_job","args":{"job_id":f"job-{len(calls)}"}}), "fallback":False}
    monkeypatch.setattr(llm, "chat", model)
    monkeypatch.setattr(agent_chat, "call_tool", lambda name, args, principal: executed.append(args) or {"status":"succeeded"})
    result = agent_chat.run_agent_chat([], "检查这些不同任务并完成汇报", ADMIN)
    assert len(executed) == 10 and result["stop_reason"] == "completed"
    assert result["elapsed_ms"] > 180_000


def test_user_stop_interrupts_long_operation_before_200(monkeypatch):
    calls, executed = [], []
    def model(messages, **kwargs):
        calls.append(messages)
        return {"content": json.dumps({"tool":"get_job","args":{"job_id":f"job-{len(calls)}"}}), "fallback":False}
    monkeypatch.setattr(llm, "chat", model)
    monkeypatch.setattr(agent_chat, "call_tool", lambda name, args, principal: executed.append(args) or {"ok":True})
    result = agent_chat.run_agent_chat([], "逐项检查任务", ADMIN, should_stop=lambda: len(executed) >= 20)
    assert len(executed) == 20 and result["stop_reason"] == "cancelled"
    assert "停止" in result["reply"]


def test_success_resets_consecutive_failures_during_long_task(monkeypatch):
    count = [0]
    calls = [0]
    def model(messages, **kwargs):
        calls[0] += 1
        if calls[0] == 13:
            return {"content": '{"reply":"任务完成"}', "fallback":False}
        return {"content": json.dumps({"tool":"get_job","args":{"job_id":f"job-{calls[0]}"}}), "fallback":False}
    def tool(*args):
        count[0] += 1
        return {"error":"该对象暂不可查询"} if count[0] % 3 else {"status":"succeeded"}
    monkeypatch.setattr(llm, "chat", model)
    monkeypatch.setattr(agent_chat, "call_tool", tool)
    result = agent_chat.run_agent_chat([], "检查各对象并汇总", ADMIN)
    assert count[0] == 12 and result["stop_reason"] == "completed"
    assert sum(not step["ok"] for step in result["steps"]) == 8


def test_current_tool_trace_compacts_before_model_request(monkeypatch):
    task = "原始任务约束必须保留：检查不同文件并汇报"
    original = agent_context.compact_history
    compacted = []
    def compact(history, reserved):
        result, consumed = original(history, reserved, threshold=18_000, summarizer=lambda old: compacted.extend(old) or "旧工具已检查，继续剩余任务")
        return result, consumed
    monkeypatch.setattr(agent_context, "compact_history", compact)
    calls = [0]
    saw_summary = []
    def model(messages, **kwargs):
        calls[0] += 1
        assert any(item["content"] == task for item in messages)
        assert agent_context.request_tokens(messages) < 18_000
        if any(item["content"].startswith(agent_context.SUMMARY_PREFIX) for item in messages):
            saw_summary.append(True)
        if calls[0] == 31:
            return {"content": '{"reply":"检查完成"}', "fallback":False}
        return {"content": json.dumps({"tool":"get_job","args":{"job_id":f"job-{calls[0]}"}}), "fallback":False}
    monkeypatch.setattr(llm, "chat", model)
    monkeypatch.setattr(agent_chat, "call_tool", lambda *args: {"content":"长" * 2_200})
    result = agent_chat.run_agent_chat([], task, ADMIN)
    assert len(result["steps"]) == 30 and result["stop_reason"] == "completed"
    assert compacted and saw_summary


def test_history_and_tool_trace_partition_keeps_latest_result(monkeypatch):
    history = [{"role":"assistant","content":"旧历史" * 2_000} for _ in range(4)]
    current = [{"role":"user","content":"本轮任务"}]
    current.extend({"role":"user","content":f"工具结果-{i}" + "长" * 2_000} for i in range(6))
    original = agent_context.compact_history
    def compact(old, reserved):
        return original(old, reserved, threshold=12_000, summarizer=lambda chunk: "已处理前缀的纪要")
    monkeypatch.setattr(agent_context, "compact_history", compact)
    old, recent = agent_chat._fit_context(history, current, {"role":"system","content":"工具目录"})
    assert old[0]["content"].startswith(agent_context.SUMMARY_PREFIX)
    assert recent[0]["content"] == "本轮任务"
    assert recent[-1] == current[-1]
    assert agent_context.request_tokens(old + recent) < 12_000


def test_sse_200_tool_steps_and_final_reply_are_persisted(client, monkeypatch):
    calls = [0]
    executed = [0]
    def model(messages, **kwargs):
        calls[0] += 1
        # 第201次模型调用仍索要工具，必须拦截而不是执行第201次。
        return {"content":json.dumps({"tool":"get_job","args":{"job_id":f"job-{calls[0]}"}}),"fallback":False}
    def tool(*args):
        executed[0] += 1
        return {"ok":True}
    monkeypatch.setattr(llm, "chat", model)
    monkeypatch.setattr(agent_chat, "call_tool", tool)
    response = client.post("/api/agent/chat", json={"message":"检查200个任务"})
    assert response.status_code == 200
    events = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), "")
        data = next((line[6:] for line in lines if line.startswith("data: ")), "")
        if event and data:
            events.append((event, json.loads(data)))
    done = next(data for event,data in events if event == "done")
    assert executed[0] == 200 and calls[0] == 201
    assert len(done["steps"]) == 200 and done["stop_reason"] == "tool_limit"
    assert sum(event == "tool_result" for event,_ in events) == 200
    assert ''.join(data["text"] for event,data in events if event == "delta") == done["reply"]
    history = client.get("/api/agent/conversations/" + done["conversation_id"]).json()["items"]
    assert len(history[-1]["steps"]) == 200 and history[-1]["content"] == done["reply"]

"""管理助手 200K 上下文、180K 压缩与长历史回归。"""
from __future__ import annotations

import json

import pytest

from app import db
from app.services import agent_chat, agent_context, agent_sessions, llm

ADMIN = {"role": "admin", "username": "context-test"}


def test_budget_constants_and_tokenizer_are_tokens_not_characters():
    assert agent_context.CONTEXT_WINDOW == 200_000
    assert agent_context.COMPACT_THRESHOLD == 180_000
    assert agent_context.COMPACT_TARGET == 120_000
    assert agent_context.text_tokens(" x" * 100) == 100
    assert agent_context.text_tokens("长" * 100) == 100


def test_under_threshold_preserves_full_history():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}" + " x" * 4_500} for i in range(30)]
    result, consumed = agent_context.compact_history(history, [{"role": "system", "content": "工具目录"}])
    assert result == history and consumed == 0
    assert len(result) > 12 and len(result[0]["content"]) > 4000


def test_threshold_includes_system_user_and_output_reserve():
    reserved = [{"role": "system", "content": "目录"}, {"role": "user", "content": "本轮问题"}]
    history = [{"role": "assistant", "content": " x" * 5_000}]
    total = agent_context.request_tokens(reserved + history)
    result, consumed = agent_context.compact_history(history, reserved, threshold=total + 1, summarizer=lambda old: "旧结论")
    assert consumed == 0
    result, consumed = agent_context.compact_history(history, reserved, threshold=total, summarizer=lambda old: "旧结论")
    assert consumed == 1 and result[0]["content"].endswith("旧结论")


def test_actual_180k_threshold_compacts_and_keeps_recent():
    history = [{"role": "user", "content": f"消息{i}" + " x" * 10_000} for i in range(19)]
    reserved = [{"role": "system", "content": "真实工具目录"}, {"role": "user", "content": "当前请求"}]
    captured = []
    result, consumed = agent_context.compact_history(history, reserved, summarizer=lambda old: captured.extend(old) or "已确认的事实、路径与任务号")
    assert consumed > 0 and captured == history[:consumed]
    assert result[0]["content"].startswith(agent_context.SUMMARY_PREFIX)
    assert result[-1] == history[-1]
    assert agent_context.request_tokens(reserved + result) < 180_000
    assert agent_context.request_tokens(reserved + result) <= 200_000


def test_single_huge_message_is_compacted_not_exempt():
    history = [{"role": "user", "content": " x" * 205_000}]
    result, consumed = agent_context.compact_history(history, summarizer=lambda old: "超长输入已归纳")
    assert consumed == 1 and len(result) == 1
    assert agent_context.request_tokens(result) < 180_000


def test_noncompressible_current_input_fails_before_model(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: calls.append(args) or {})
    result = agent_chat.run_agent_chat([], " x" * 201_000, ADMIN)
    assert "200K" in result["reply"] and not calls


def test_fallback_estimate_is_conservative(monkeypatch):
    monkeypatch.setattr(agent_context, "_encoding", lambda: None)
    assert agent_context.text_tokens("中😀a") == len("中😀a".encode("utf-8"))
    assert agent_context.text_tokens(agent_context.clip_text("中文😀" * 100, 30)) <= 30


def test_summary_reads_long_message_tail_and_merges_batches(monkeypatch):
    calls = []
    def fake_chat(messages, **kwargs):
        calls.append(messages)
        assert agent_context.request_tokens(messages, output_tokens=agent_context.SUMMARY_TOKENS) <= agent_context.CONTEXT_WINDOW
        return {"content": "压缩纪要", "fallback": False}
    monkeypatch.setattr(llm, "chat", fake_chat)
    value = agent_context.summarize_history([{"role": "user", "content": " x" * 70_000 + "最后的故障证据-END"}])
    assert value == "压缩纪要" and len(calls) >= 4
    assert any("最后的故障证据-END" in call[1]["content"] for call in calls)


def test_failed_summary_remains_bounded_and_marked(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: {"content": "", "fallback": True})
    summary = agent_context.summarize_history([{"role": "user", "content": " x" * 4_000} for _ in range(10)])
    assert "压缩失败" in summary
    assert agent_context.text_tokens(summary) <= agent_context.SUMMARY_TOKENS


def test_session_history_not_limited_to_500_rows():
    conv = agent_sessions.create_conversation("完整短历史")
    stamp = db.now()
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO agent_messages(id,conversation_id,role,content,steps,created_at) VALUES(?,?,'user',?,'[]',?)",
            [(f"context-{i}", conv["id"], f"消息-{i}", stamp) for i in range(520)],
        )
    context = agent_sessions.build_context(conv["id"])
    assert len(context) == 520 and context[0]["content"] == "消息-0"
    assert context[-1]["content"] == "消息-519"


def test_session_summary_is_kept_when_many_recent_messages(monkeypatch):
    conv = agent_sessions.create_conversation("保留纪要")
    db.execute("UPDATE agent_conversations SET summary=? WHERE id=?", ("必须记得目录 scan/", conv["id"]))
    for i in range(20):
        agent_sessions.append_message(conv["id"], "user", f"近期消息{i}")
    history = agent_chat.prepare_context(conv["id"], "现在继续", ADMIN)
    calls = []
    monkeypatch.setattr(llm, "chat", lambda messages, **kwargs: calls.append(messages) or {"content": '{"reply":"完成"}', "fallback": False})
    agent_chat.run_agent_chat(history, "现在继续", ADMIN)
    assert len(history) == 21
    assert any("必须记得目录 scan/" in item["content"] for item in calls[0])
    assert any(item["content"] == "近期消息0" for item in calls[0])


def test_sse_retains_history_and_reserves_system_and_current_input(client, monkeypatch):
    calls = []
    def fake_chat(messages, **kwargs):
        calls.append(messages)
        return {"content": json.dumps({"reply": "完整历史已读取"}), "fallback": False}
    monkeypatch.setattr(llm, "chat", fake_chat)
    conv = agent_sessions.create_conversation("超过12条的历史")
    for i in range(15):
        agent_sessions.append_message(conv["id"], "user", f"长期信息-{i}" + "内容" * 2500)
    response = client.post("/api/agent/chat", json={"conversation_id": conv["id"], "message": "继续"})
    assert response.status_code == 200 and "完整历史已读取" in response.text
    model_messages = calls[-1]
    assert "长期信息-0" in model_messages[1]["content"]
    assert len(model_messages[1]["content"]) > 4000
    assert agent_context.request_tokens(model_messages) <= 200_000


def test_tool_results_cross_threshold_trigger_compaction(monkeypatch):
    calls = []
    summaries = []
    history = [{"role": "user", "content": f"历史{i}" + " x" * 35_000} for i in range(5)]
    reserved = [{"role": "system", "content": agent_chat._system_prompt("admin")}, {"role": "user", "content": "检查"}]
    before = agent_context.request_tokens(reserved + history)
    monkeypatch.setattr(agent_context, "COMPACT_THRESHOLD", before + 100)
    original = agent_context.compact_history
    def compact(old, reserved_messages, **kwargs):
        return original(old, reserved_messages, threshold=agent_context.COMPACT_THRESHOLD, summarizer=lambda chunk: summaries.extend(chunk) or "工具前历史纪要")
    monkeypatch.setattr(agent_context, "compact_history", compact)
    def fake_chat(messages, **kwargs):
        calls.append(messages)
        assert agent_context.request_tokens(messages) <= 200_000
        if len(calls) == 1:
            return {"content": '{"tool":"system_stats","args":{}}', "fallback": False}
        return {"content": '{"reply":"检查完成"}', "fallback": False}
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(agent_chat, "call_tool", lambda *args: {"detail": " x" * 1000})
    result = agent_chat.run_agent_chat(history, "检查", ADMIN)
    assert result["reply"] == "检查完成" and summaries
    assert any("工具前历史纪要" in item["content"] for item in calls[1])
    assert calls[1][-1]["content"].startswith("工具 system_stats 的执行结果")


def test_persisted_summary_watermark_and_rewind_after_180k_compaction(monkeypatch):
    conv = agent_sessions.create_conversation("200K水位")
    rows = [agent_sessions.append_message(conv["id"], "user", f"消息{i}" + " x" * 10_000) for i in range(19)]
    captured = []
    monkeypatch.setattr(agent_sessions, "_compact_chunk", lambda old: captured.extend(old) or "目录 scan/；已提交 job-001，待核对结果")
    context = agent_chat.prepare_context(conv["id"], "继续", ADMIN)
    saved = agent_sessions.get_conversation(conv["id"])
    assert saved["summary"] and int(saved["summary_upto"]) >= rows[0]["_rowid"]
    assert context[0]["content"].startswith(agent_context.SUMMARY_PREFIX)
    expected = [row["content"] for row in rows if row["_rowid"] > int(saved["summary_upto"])]
    assert [item["content"] for item in context[1:]] == expected
    # 摘要和未压缩消息再次组装不重复压缩，不丢失最新消息。
    count = len(captured)
    context2 = agent_chat.prepare_context(conv["id"], "再次继续", ADMIN)
    assert context2 == context and len(captured) == count
    # 原始消息未删除，回滚仍能使越界摘要作废。
    assert len(agent_sessions.get_messages(conv["id"])) == 19
    agent_sessions.truncate_from(conv["id"], rows[0]["id"])
    assert agent_sessions.get_conversation(conv["id"])["summary"] == ""


def test_system_directory_counts_towards_session_compression(monkeypatch):
    conv = agent_sessions.create_conversation("系统预算")
    agent_sessions.append_message(conv["id"], "user", " x" * 170_000)
    monkeypatch.setattr(agent_sessions, "_compact_chunk", lambda old: "历史已归纳")
    no_reserve = agent_sessions.build_context(conv["id"])
    assert no_reserve[0]["content"].startswith(" x")
    reserved = [{"role": "system", "content": " x" * 12_000}, {"role": "user", "content": "继续"}]
    context = agent_sessions.build_context(conv["id"], reserved_messages=reserved)
    assert context[0]["content"].startswith(agent_context.SUMMARY_PREFIX)
    assert agent_sessions.get_conversation(conv["id"])["summary"] == "历史已归纳"
    assert agent_context.request_tokens(reserved + context) < 180_000


def test_scheduled_agent_keeps_long_history(monkeypatch):
    from app.services import scheduler

    conv = agent_sessions.create_conversation("定时长历史")
    for i in range(15):
        agent_sessions.append_message(conv["id"], "user", f"定时历史-{i}" + " x" * 5_000)
    schedule = scheduler.create_schedule("定时检查", "agent_prompt", {"prompt": "检查后汇报", "conversation_id": conv["id"]}, 60)
    calls = []
    monkeypatch.setattr(llm, "chat", lambda messages, **kwargs: calls.append(messages) or {"content": '{"reply":"已检查"}', "fallback": False})
    scheduler.run_agent_prompt(schedule["id"])
    assert "定时历史-0" in calls[0][1]["content"]
    assert len(calls[0][1]["content"]) > 4_000
    assert agent_context.request_tokens(calls[0]) <= 200_000


def test_tiny_single_message_does_not_create_empty_summary():
    history = [{"role": "user", "content": "短消息"}]
    compacted, consumed = agent_context.compact_history(
        history, threshold=1, summarizer=lambda chunk: "不应生成空纪要"
    )
    assert consumed == 0 and compacted == history


def test_existing_summary_merges_with_new_prefix_without_duplicate_rows(monkeypatch):
    conv = agent_sessions.create_conversation("累计压缩")
    old = agent_sessions.append_message(conv["id"], "user", "旧消息已经摘要")
    db.execute("UPDATE agent_conversations SET summary=?,summary_upto=? WHERE id=?", ("早先的约束", str(old["_rowid"]), conv["id"]))
    rows = [agent_sessions.append_message(conv["id"], "user", f"新的历史{i}" + " x" * 10_000) for i in range(19)]
    captured = []
    monkeypatch.setattr(agent_sessions, "_compact_chunk", lambda messages: captured.extend(messages) or "合并后的约束和任务号")
    result = agent_sessions.build_context(conv["id"])
    assert captured[0]["content"] == agent_context.SUMMARY_PREFIX + "早先的约束"
    saved = agent_sessions.get_conversation(conv["id"])
    remaining = [row["content"] for row in rows if row["_rowid"] > int(saved["summary_upto"])]
    assert [item["content"] for item in result[1:]] == remaining
    assert result[-1]["content"] == rows[-1]["content"]

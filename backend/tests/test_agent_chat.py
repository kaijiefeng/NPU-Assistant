"""管理 Agent：工具注册表权限过滤 / 对话循环 / SSE 端点。"""
from __future__ import annotations

import json

from app import db
from app.services import agent_chat, agent_tools

SUPER = {"role": "super_admin", "auth": "token", "username": "root"}
ADMIN = {"role": "admin", "auth": "session", "username": "admin1"}
VIEWER = {"role": "viewer", "auth": "session", "username": "viewer1"}


def _names(role: str) -> set[str]:
    return {spec.name for spec in agent_tools.tools_for(role)}


# ---------------------------------------------------------------------------
# 工具注册表与权限闸
# ---------------------------------------------------------------------------
def test_tools_filtered_by_role():
    viewer = _names("viewer")
    counselor = _names("counselor")
    admin = _names("admin")
    super_admin = _names("super_admin")

    # 查询类所有角色可见；写工具按权限键过滤
    assert "list_gaps" in viewer and "list_alerts" in viewer
    assert "import_url" not in viewer and "delete_document" not in viewer
    assert "gap_suggest" not in viewer  # insights:write

    # 辅导员：有 insights:write、无 content:write / ops:manage
    assert "gap_suggest" in counselor and "subscription_create" in counselor
    assert "import_url" not in counselor and "rebuild_wiki" not in counselor
    assert "ops_task_create" not in counselor

    # 管理员：内容写齐、运维不可见
    assert {"import_url", "rebuild_wiki", "delete_document"} <= admin
    assert "ops_task_approve" not in admin

    # 超级管理员：全部
    assert {"ops_task_create", "ops_task_approve", "delete_document"} <= super_admin
    assert super_admin == set(agent_tools.REGISTRY)


def test_call_tool_permission_and_unknown():
    denied = agent_tools.call_tool("delete_document", {"document_id": "x"}, VIEWER)
    assert denied.get("error") and "权限不足" in denied["error"]

    ops_denied = agent_tools.call_tool("ops_task_create", {"error_input": "x" * 30}, ADMIN)
    assert ops_denied.get("error")

    unknown = agent_tools.call_tool("no_such_tool", {}, SUPER)
    assert unknown.get("error") and "未知工具" in unknown["error"]


def test_call_tool_value_error_becomes_error_dict():
    result = agent_tools.call_tool("get_job", {"job_id": "missing"}, SUPER)
    assert result.get("error") == "任务不存在"


def test_write_tool_records_audit(monkeypatch):
    from app.services import importer

    monkeypatch.setattr(
        importer,
        "import_url",
        lambda url, **kwargs: {"duplicated": False, "document": {"id": "d1", "title": "测试"}, "pages": []},
    )
    result = agent_tools.call_tool("import_url", {"url": "https://example.com/a"}, ADMIN)
    assert result.get("document_id") == "d1"

    rows = db.query("SELECT * FROM audit_log WHERE action = ?", ("agent.tool.import_url",))
    assert len(rows) == 1
    assert rows[0]["username"] == "admin1"
    assert rows[0]["role"] == "admin"


def test_system_stats_counts(monkeypatch):
    db.execute(
        "INSERT INTO knowledge_gaps(id, question, normalized_key, first_seen_at, last_seen_at, "
        "created_at, updated_at, status) "
        "VALUES('g1', '怎么申请奖学金？', 'nk1', ?, ?, ?, ?, 'open')",
        (db.now(), db.now(), db.now(), db.now()),
    )
    stats = agent_tools.call_tool("system_stats", {}, SUPER)
    assert stats.get("open_gaps") == 1
    assert all(key in stats for key in ("documents", "wiki_pages", "active_jobs", "open_alerts", "subscriptions"))


# ---------------------------------------------------------------------------
# 对话循环（monkeypatch llm.chat）
# ---------------------------------------------------------------------------
def _patch_llm(monkeypatch, replies: list[dict]):
    from app.services import llm

    calls: list[list[dict]] = []

    def fake_chat(messages, **kwargs):
        calls.append([dict(item) for item in messages])
        return replies.pop(0)

    monkeypatch.setattr(llm, "chat", fake_chat)
    return calls


def test_agent_loop_tool_then_reply(monkeypatch):
    calls = _patch_llm(
        monkeypatch,
        [
            {"content": json.dumps({"tool": "system_stats", "args": {}}), "fallback": False},
            {"content": json.dumps({"reply": "当前有 3 篇文档、1 个未处理缺口。"}), "fallback": False},
        ],
    )
    events: list[tuple[str, dict]] = []
    result = agent_chat.run_agent_chat(
        [], "现在系统里有多少内容？", ADMIN, on_event=lambda kind, data: events.append((kind, data))
    )

    assert result["reply"] == "当前有 3 篇文档、1 个未处理缺口。"
    assert [kind for kind, _ in events] == ["tool", "tool_result", "delta"]
    assert events[0][1]["name"] == "system_stats"
    assert result["steps"][0]["name"] == "system_stats"
    assert result["steps"][0]["ok"] is True
    assert result["steps"][0]["summary"] == ""
    # 第二轮模型输入包含工具结果回灌
    assert any("工具 system_stats 的执行结果" in item["content"] for item in calls[1])
    # 系统提示词含工具目录
    assert "可用工具目录" in calls[0][0]["content"]


def test_agent_loop_fallback_degrades(monkeypatch):
    _patch_llm(monkeypatch, [{"content": "", "fallback": True, "error": "连接超时"}])
    result = agent_chat.run_agent_chat([], "帮我看下任务", ADMIN)
    assert "模型暂时不可用" in result["reply"]
    assert result["steps"] == []


def test_agent_loop_plain_text_reply(monkeypatch):
    """模型没按 JSON 输出时，整段文本按回答兜底返回。"""
    _patch_llm(monkeypatch, [{"content": "知识库当前运行正常。", "fallback": False}])
    result = agent_chat.run_agent_chat([], "状态如何？", ADMIN)
    assert result["reply"] == "知识库当前运行正常。"


def test_agent_loop_denied_tool_feeds_error(monkeypatch):
    _patch_llm(
        monkeypatch,
        [
            {"content": json.dumps({"tool": "delete_document", "args": {"document_id": "d1"}}), "fallback": False},
            {"content": json.dumps({"reply": "当前角色无法删除文档，已说明原因。"}), "fallback": False},
        ],
    )
    result = agent_chat.run_agent_chat([], "删掉第一篇文档", VIEWER)
    assert result["steps"][0]["ok"] is False
    assert "权限不足" in result["steps"][0]["summary"]
    assert result["reply"].startswith("当前角色")


def test_agent_no_tools_for_role():
    result = agent_chat.run_agent_chat([], "帮我做点什么", {"role": "unknown_role"})
    assert "没有可用的管理工具权限" in result["reply"]


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------
def test_agent_tools_endpoint_open_mode(client):
    response = client.get("/api/agent/tools")
    assert response.status_code == 200
    items = response.json()["items"]
    names = {item["name"] for item in items}
    assert "delete_document" in names and "ops_task_approve" in names
    dangerous = [item for item in items if item["dangerous"]]
    assert {item["name"] for item in dangerous} >= {"delete_document", "rebuild_wiki", "ops_task_approve"}


def test_agent_chat_sse_stream(client, monkeypatch):
    _patch_llm(monkeypatch, [{"content": json.dumps({"reply": "一切正常。"}), "fallback": False}])
    with client.stream("POST", "/api/agent/chat", json={"message": "系统状态？", "history": []}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    assert "event: delta" in text
    assert "event: done" in text
    assert "一切正常" in text


def test_agent_chat_sse_empty_message_rejected(client):
    response = client.post("/api/agent/chat", json={"message": "  "})
    assert response.status_code == 400


def test_agent_chat_sse_tool_events(client, monkeypatch):
    _patch_llm(
        monkeypatch,
        [
            {"content": json.dumps({"tool": "system_stats", "args": {}}), "fallback": False},
            {"content": json.dumps({"reply": "统计完成。"}), "fallback": False},
        ],
    )
    with client.stream("POST", "/api/agent/chat", json={"message": "看下统计", "history": []}) as response:
        text = "".join(response.iter_text())
    assert "event: tool" in text and "event: tool_result" in text
    assert '"name": "system_stats"' in text

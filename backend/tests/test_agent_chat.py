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
    assert [kind for kind, _ in events if kind != "status"] == ["tool", "tool_result", "delta"]
    assert next(data for kind, data in events if kind == "tool")["name"] == "system_stats"
    assert result["steps"][0]["name"] == "system_stats"
    assert result["steps"][0]["ok"] is True
    assert "源文件" in result["steps"][0]["summary"]
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
    document = next(item for item in items if item["name"] == "get_document")
    assert document["required"] == ["document_id"]
    assert "document_id" in document["parameters"]


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


def test_agent_chat_sse_no_internal_error_leak(client, monkeypatch):
    """LLM 不可用时 done 事件只含降级话术与步骤，不外发内部错误信息。"""
    _patch_llm(monkeypatch, [{"content": "", "fallback": True, "error": "连接 https://llm.internal/api 超时"}])
    with client.stream("POST", "/api/agent/chat", json={"message": "hi", "history": []}) as response:
        text = "".join(response.iter_text())
    assert "模型暂时不可用" in text
    assert "llm.internal" not in text


# ---------------------------------------------------------------------------
# 高危确认门 / 错误脱敏 / 布尔解析（独立审阅修复回归）
# ---------------------------------------------------------------------------
def test_dangerous_tool_confirm_gate():
    first = agent_tools.call_tool("delete_document", {"document_id": "d1"}, SUPER)
    assert first.get("need_confirm") is True
    assert "复述" in first["message"]
    # 未执行：不留成功审计
    assert not db.query("SELECT * FROM audit_log WHERE action = 'agent.tool.delete_document'")

    # 带 confirm 后过闸进入 handler（文档不存在 → 业务错误，且失败尝试留痕）
    second = agent_tools.call_tool("delete_document", {"document_id": "d1", "confirm": True}, SUPER)
    assert second.get("error") == "源文件不存在"
    rows = db.query("SELECT * FROM audit_log WHERE action = 'agent.tool.delete_document'")
    assert len(rows) == 1 and rows[0]["status_code"] == 400


def test_tool_error_sanitizes_local_paths(monkeypatch):
    def boom(_job_id: str):
        raise ValueError("不是文件夹：C:\\Users\\fkj92\\secret\\data")

    monkeypatch.setattr(agent_tools.jobs, "get_job", boom)
    result = agent_tools.call_tool("get_job", {"job_id": "x"}, SUPER)
    assert result.get("error") and "C:\\Users" not in result["error"]
    assert "<path>" in result["error"]


def test_bool_argument_parsing():
    from app.services.agent_tools import _bool

    assert _bool({"x": "false"}, "x", True) is False
    assert _bool({"x": "true"}, "x", False) is True
    assert _bool({"x": "0"}, "x", True) is False
    assert _bool({}, "x", True) is True
    assert _bool({}, "x", False) is False


def test_agent_loop_handles_confirm_flow(monkeypatch):
    """模型先调高危工具 → 收到 need_confirm → 复述确认 → 用户同意后带 confirm 重调。"""
    # 第一轮：工具调用被确认门拦下，模型转为向用户复述确认
    _patch_llm(
        monkeypatch,
        [
            {"content": json.dumps({"tool": "delete_document", "args": {"document_id": "d1"}}), "fallback": False},
            {"content": json.dumps({"reply": "将删除《d1》，确认执行吗？"}), "fallback": False},
        ],
    )
    first = agent_chat.run_agent_chat([], "删掉 d1", SUPER)
    assert "确认" in first["reply"]
    assert len(first["steps"]) == 1
    assert first["steps"][0]["name"] == "delete_document"
    assert first["steps"][0]["ok"] is False
    assert first["steps"][0]["status"] == "confirmation"
    assert "确认" in first["steps"][0]["summary"]

    # 第二轮：用户同意，模型带 confirm 重调（文档不存在 → 业务错误回报）
    _patch_llm(
        monkeypatch,
        [
            {"content": json.dumps({"tool": "delete_document", "args": {"document_id": "d1", "confirm": True}}), "fallback": False},
            {"content": json.dumps({"reply": "该文档不存在，无需删除。"}), "fallback": False},
        ],
    )
    history = [
        {"role": "user", "content": "删掉 d1"},
        {"role": "assistant", "content": first["reply"]},
    ]
    second = agent_chat.run_agent_chat(history, "确认", SUPER)
    assert second["reply"].startswith("该文档不存在")
    assert second["steps"][0]["ok"] is False


# ---------------------------------------------------------------------------
# 轻量维护工具（read_logs / read_file）
# ---------------------------------------------------------------------------
def test_maintenance_read_tools():
    from app import config

    # read_logs：关键词过滤 + 本机路径脱敏
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_FILE.write_text(
        "2026-11-19 10:00:00 | INFO | npu | line C:\\Users\\fkj92\\secret path\n"
        "2026-11-19 10:00:01 | ERROR | npu | boom 失败\n"
        "2026-11-19 10:00:02 | INFO | npu | other\n",
        encoding="utf-8",
    )
    result = agent_tools.call_tool("read_logs", {"keyword": "ERROR", "lines": 10}, SUPER)
    assert result["count"] == 1
    assert "boom" in result["lines"][0]
    no_path = agent_tools.call_tool("read_logs", {"lines": 10}, SUPER)
    assert all("C:\\Users" not in line for line in no_path["lines"])
    assert any("<path>" in line for line in no_path["lines"])

    # read_file：仓库内合法路径
    ok = agent_tools.call_tool("read_file", {"path": "backend/app/config.py"}, SUPER)
    assert "DATA_DIR" in str(ok.get("content") or "")

    # 拒绝：..、绝对路径、非文本扩展名、不存在
    escape = agent_tools.call_tool("read_file", {"path": "../outside.txt"}, SUPER)
    assert escape.get("error")
    absolute = agent_tools.call_tool("read_file", {"path": "C:/Windows/win.ini"}, SUPER)
    assert absolute.get("error")
    binary = agent_tools.call_tool("read_file", {"path": "docs/screenshots/v0.2.2/chat.png"}, SUPER)
    assert binary.get("error") and "文本" in binary["error"]
    missing = agent_tools.call_tool("read_file", {"path": "backend/no_such.py"}, SUPER)
    assert missing.get("error")

    # 权限：admin（无 ops:manage）不可用
    denied = agent_tools.call_tool("read_logs", {}, ADMIN)
    assert denied.get("error") and "权限不足" in denied["error"]

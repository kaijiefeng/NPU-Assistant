"""全部内建工具的端到端联调：协议 → 权限 → 校验 → 执行 → 最终回复。

外部网络/LLM/队列副作用用最小桩隔离；工具本身走真实 handler 与临时数据库。
新增工具若未纳入本文件的覆盖清单会直接失败。
"""
from __future__ import annotations

import json

import pytest

from app import config, db
from app.services import agent_chat, agent_tools, alerts, discovery, jobs, llm, ops_agent

SUPER = {"role": "super_admin", "username": "coverage-root"}

READ_TOOLS = {
    "system_stats", "list_documents", "get_document", "list_gaps", "get_gap",
    "insights_overview", "list_feedback", "list_subscriptions", "get_subscription",
    "list_jobs", "get_job", "list_alerts", "list_wiki_builds", "list_ops_tasks",
    "get_ops_task", "read_logs", "read_file", "search_repo", "get_scan_directory",
}

WRITE_TOOLS = {
    "import_url", "import_text", "set_scan_directory", "scan_dir", "reindex_document",
    "retry_job", "cancel_job", "gap_suggest", "gap_collect", "gap_link", "gap_resolve",
    "gap_ignore", "subscription_create", "subscription_delete", "subscription_toggle",
    "subscription_check", "subscription_collect", "subscription_ignore", "alert_ack",
    "alert_resolve", "ops_task_create", "ops_task_cancel", "ops_task_cleanup",
}

DANGER_TOOLS = {"rebuild_wiki", "delete_document", "ops_task_approve"}


class Script:
    def __init__(self) -> None:
        self.responses: list[str] = []

    def __call__(self, messages, **kwargs):
        assert self.responses, "脚本模型被额外调用"
        return {"content": self.responses.pop(0), "fallback": False}


@pytest.fixture()
def script(monkeypatch):
    fake = Script()
    monkeypatch.setattr(llm, "chat", fake)
    return fake


@pytest.fixture(autouse=True)
def _pause_job_worker(monkeypatch):
    """工具联调只验证入队/取消语义，禁止后台线程抢跑或改写任务状态。"""
    monkeypatch.setattr(jobs, "_claim_next", lambda: None)


def _invoke(script: Script, name: str, args: dict, expect: str = "succeeded"):
    script.responses[:] = [
        json.dumps({"tool": name, "args": args}, ensure_ascii=False),
        json.dumps({"reply": "已完成。"}, ensure_ascii=False),
    ]
    result = agent_chat.run_agent_chat([], "联调执行", SUPER)
    assert len(result["steps"]) == 1, (name, result)
    step = result["steps"][0]
    assert step["name"] == name
    assert step["status"] == expect, (name, step["summary"])
    if expect == "succeeded":
        assert result["stop_reason"] == "completed"
    return result


def _seed(monkeypatch, tmp_path):
    now = db.now()
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "note.md").write_text("# 测试资料\n\n用于扫描联调。", encoding="utf-8")
    monkeypatch.setattr(config, "SCAN_DEFAULT_DIR", scan_dir)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])

    doc_id = db.new_id()
    db.execute(
        "INSERT INTO documents(id, title, filename, stored_path, file_ext, status, tags, "
        "created_at, updated_at) VALUES(?, ?, ?, ?, ?, 'ready', '[]', ?, ?)",
        (doc_id, "联调源文件", "coverage.md", "uploads/coverage.md", ".md", now, now),
    )
    gaps = {}
    for key in ("open", "link", "resolve", "ignore"):
        gap_id = db.new_id()
        gaps[key] = gap_id
        db.execute(
            "INSERT INTO knowledge_gaps(id, question, normalized_key, count, status, "
            "first_seen_at, last_seen_at, created_at, updated_at) VALUES(?, ?, ?, 1, 'open', ?, ?, ?, ?)",
            (gap_id, f"联调问题 {key}", f"coverage-{key}", now, now, now, now),
        )
    failed_job = jobs.create_job("reindex", {"document_id": doc_id})
    db.execute(
        "UPDATE jobs SET status = 'failed', error = '联调失败' WHERE id = ?",
        (failed_job["id"],),
    )
    queued_job = jobs.create_job("reindex", {"document_id": doc_id})
    subscription = discovery.create_subscription("联调订阅", "https://example.edu/list")
    alert = alerts.create_alert("联调告警", dedupe_hours=0)
    task = ops_agent.create_task("联调错误：调用接口时出现 500，需要定位原因。", "联调运维任务", "tester")

    log_file = config.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("2026-11-20 INFO 联调日志\n2026-11-20 ERROR 联调错误\n", encoding="utf-8")

    monkeypatch.setattr(
        jobs,
        "create_job",
        lambda kind, payload=None, **kwargs: {"id": f"job-{kind}", "type": kind, "status": "queued"},
    )
    monkeypatch.setattr(
        agent_tools.importer,
        "import_url",
        lambda url, title=None, tags=None: {
            "duplicated": False,
            "document": {"id": "doc-imported", "title": title or "联调网页"},
            "pages": [],
        },
    )
    monkeypatch.setattr(
        agent_tools.importer,
        "import_text",
        lambda title=None, text="", tags=None: {
            "document": {"id": "doc-text", "title": title or "联调文本"},
            "pages": [],
        },
    )
    monkeypatch.setattr(
        discovery,
        "collect_discovery",
        lambda sub_id, url, title=None: {"document": {"id": "doc-sub", "title": title or "订阅收录"}},
    )
    monkeypatch.setattr(discovery, "ignore_discovery", lambda sub_id, url: {"ok": True})
    from app.services import gap_suggest as gap_suggest_service

    monkeypatch.setattr(
        gap_suggest_service,
        "collect_suggestion",
        lambda gap_id, url, title=None: {"document": {"id": "doc-gap", "title": title or "缺口收录"}},
    )
    return {
        "doc": doc_id,
        "gaps": gaps,
        "failed_job": failed_job["id"],
        "queued_job": queued_job["id"],
        "sub": subscription["id"],
        "alert": alert["id"],
        "task": task["id"],
        "scan_dir": str(scan_dir),
    }


def test_registry_is_fully_covered():
    registered = set(agent_tools.REGISTRY)
    assert registered == READ_TOOLS | WRITE_TOOLS | DANGER_TOOLS
    assert len(registered) == 45


def test_all_read_tools_through_agent(monkeypatch, script, tmp_path):
    ids = _seed(monkeypatch, tmp_path)
    calls = {
        "system_stats": {},
        "list_documents": {"query": "联调"},
        "get_document": {"document_id": ids["doc"]},
        "list_gaps": {"status": "open"},
        "get_gap": {"gap_id": ids["gaps"]["open"]},
        "insights_overview": {"days": 7},
        "list_feedback": {},
        "list_subscriptions": {},
        "get_subscription": {"subscription_id": ids["sub"]},
        "list_jobs": {},
        "get_job": {"job_id": ids["queued_job"]},
        "list_alerts": {},
        "list_wiki_builds": {},
        "list_ops_tasks": {},
        "get_ops_task": {"task_id": ids["task"]},
        "read_logs": {"keyword": "联调"},
        "read_file": {"path": "backend/app/main.py"},
        "search_repo": {"query": "_retryable", "path": "backend/app/services"},
        "get_scan_directory": {},
    }
    assert set(calls) == READ_TOOLS
    for name, args in calls.items():
        _invoke(script, name, args)


def test_all_write_tools_through_agent(monkeypatch, script, tmp_path):
    ids = _seed(monkeypatch, tmp_path)
    calls = [
        ("import_url", {"url": "https://example.edu/article", "title": "联调网页"}),
        ("import_text", {"text": "这是一段用于联调的测试文本，长度足够触发收录流程。", "title": "联调文本"}),
        ("set_scan_directory", {"path": ids["scan_dir"]}),
        ("scan_dir", {"path": ids["scan_dir"]}),
        ("reindex_document", {"document_id": ids["doc"]}),
        ("retry_job", {"job_id": ids["failed_job"]}),
        ("cancel_job", {"job_id": ids["queued_job"]}),
        ("gap_suggest", {"gap_id": ids["gaps"]["open"]}),
        ("gap_collect", {"gap_id": ids["gaps"]["open"], "url": "https://example.edu/gap"}),
        ("gap_link", {"gap_id": ids["gaps"]["link"], "document_id": ids["doc"], "note": "已有关联词条"}),
        ("gap_resolve", {"gap_id": ids["gaps"]["resolve"], "note": "已补充词条"}),
        ("gap_ignore", {"gap_id": ids["gaps"]["ignore"], "note": "重复问题"}),
        ("subscription_create", {"url": "https://example.edu/list2", "name": "联调订阅二"}),
        ("subscription_toggle", {"subscription_id": ids["sub"], "enabled": False}),
        ("subscription_check", {"subscription_id": ids["sub"]}),
        ("subscription_collect", {"subscription_id": ids["sub"], "url": "https://example.edu/new"}),
        ("subscription_ignore", {"subscription_id": ids["sub"], "url": "https://example.edu/old"}),
        ("alert_ack", {"alert_id": ids["alert"]}),
        ("alert_resolve", {"alert_id": ids["alert"]}),
        ("ops_task_create", {"error_input": "联调错误：接口返回 500，需要分析根因并给出修复方案。"}),
        ("ops_task_cancel", {"task_id": ids["task"]}),
        ("ops_task_cleanup", {"task_id": ids["task"]}),
    ]
    assert {name for name, _ in calls} == WRITE_TOOLS - {"subscription_delete"}
    for name, args in calls:
        _invoke(script, name, args)
    _invoke(script, "subscription_delete", {"subscription_id": ids["sub"]})


def test_dangerous_tools_stop_at_confirmation(monkeypatch, script, tmp_path):
    ids = _seed(monkeypatch, tmp_path)
    for name, args in (
        ("rebuild_wiki", {}),
        ("delete_document", {"document_id": ids["doc"]}),
        ("ops_task_approve", {"task_id": ids["task"]}),
    ):
        result = _invoke(script, name, args, expect="confirmation")
        assert result["stop_reason"] == "confirmation_required"
        assert "尚未执行" in result["reply"]
    assert db.query_one("SELECT id FROM documents WHERE id = ?", (ids["doc"],))

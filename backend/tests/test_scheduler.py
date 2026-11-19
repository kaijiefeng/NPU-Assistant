"""管理助手定时任务：白名单校验 / 原子抢占 / agent_prompt 端到端。"""
from __future__ import annotations

import json
import time

from app import db
from app.services import agent_sessions, scheduler


def _wait_job(client, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    raise AssertionError(f"任务 {job_id} 未在 {timeout}s 内结束")


def _insert_subscription(sub_id: str = "sub1") -> None:
    db.execute(
        "INSERT INTO subscriptions(id, name, url, enabled, discoveries, ignored_urls, created_at) "
        "VALUES(?, '测试订阅', 'https://example.com/list', 1, '[]', '[]', ?)",
        (sub_id, db.now()),
    )


# ---------------------------------------------------------------------------
# CRUD 与白名单校验
# ---------------------------------------------------------------------------
def test_schedule_validation():
    for kind in ("no_such_kind", ""):
        try:
            scheduler.create_schedule("x", kind, {}, 60)
        except ValueError as exc:
            assert "不支持" in str(exc)
        else:
            raise AssertionError(f"kind={kind!r} 应被拒绝")

    try:
        scheduler.create_schedule("x", "agent_prompt", {}, 60)
    except ValueError as exc:
        assert "prompt" in str(exc)
    else:
        raise AssertionError("agent_prompt 缺 prompt 应被拒绝")

    for bad_interval in (5, 20000):
        try:
            scheduler.create_schedule("x", "gap_suggest_all", {}, bad_interval)
        except ValueError:
            pass
        else:
            raise AssertionError(f"interval={bad_interval} 应被拒绝")


def test_schedule_crud_and_toggle():
    created = scheduler.create_schedule("每天查订阅", "subscription_check_all", {}, 360)
    assert created["enabled"] is True and created["next_run_at"]

    listing = scheduler.list_schedules()
    assert any(item["id"] == created["id"] for item in listing["items"])

    disabled = scheduler.update_schedule(created["id"], enabled=False)
    assert disabled["enabled"] is False

    re_enabled = scheduler.update_schedule(created["id"], interval_minutes=120, enabled=True)
    assert re_enabled["interval_minutes"] == 120 and re_enabled["enabled"] is True

    assert scheduler.delete_schedule(created["id"])["ok"] is True
    assert not scheduler.list_schedules()["items"]


# ---------------------------------------------------------------------------
# tick 触发与原子抢占
# ---------------------------------------------------------------------------
def test_tick_fires_due_subscription_check():
    _insert_subscription()
    schedule = scheduler.create_schedule("订阅检查", "subscription_check_all", {}, 60)
    # 拨到过去两个周期
    db.execute(
        "UPDATE agent_schedules SET next_run_at = '2000-01-01T00:00:00' WHERE id = ?",
        (schedule["id"],),
    )

    assert scheduler._tick() == 1
    rows = db.query("SELECT * FROM jobs WHERE type = 'subscription_check'")
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["subscription_id"] == "sub1"

    refreshed = scheduler.get_schedule(schedule["id"])
    assert "已提交 1" in refreshed["last_status"] and refreshed["last_run_at"]
    # 错过多个周期只补跑一次：next_run_at 已推到未来
    assert scheduler._tick() == 0


def test_tick_fires_gap_suggest_all():
    db.execute(
        "INSERT INTO knowledge_gaps(id, question, normalized_key, first_seen_at, last_seen_at, "
        "created_at, updated_at, status) VALUES('g1', '怎么转专业？', 'nk1', ?, ?, ?, ?, 'open')",
        (db.now(), db.now(), db.now(), db.now()),
    )
    schedule = scheduler.create_schedule("缺口找备选", "gap_suggest_all", {"limit": 3}, 60)
    db.execute(
        "UPDATE agent_schedules SET next_run_at = '2000-01-01T00:00:00' WHERE id = ?",
        (schedule["id"],),
    )
    assert scheduler._tick() == 1
    rows = db.query("SELECT * FROM jobs WHERE type = 'gap_suggest'")
    assert len(rows) == 1 and json.loads(rows[0]["payload"])["gap_id"] == "g1"


def test_recover_schedules_backfills_next_run():
    schedule = scheduler.create_schedule("r", "gap_suggest_all", {}, 60)
    db.execute("UPDATE agent_schedules SET next_run_at = '' WHERE id = ?", (schedule["id"],))
    scheduler.recover_schedules()
    assert scheduler.get_schedule(schedule["id"])["next_run_at"]


# ---------------------------------------------------------------------------
# agent_prompt 端到端（jobs worker + 会话落库 + 告警）
# ---------------------------------------------------------------------------
def test_agent_prompt_end_to_end(client, fake_llm):
    schedule = scheduler.create_schedule(
        "每晚汇报", "agent_prompt", {"prompt": "检查订阅新内容并汇报"}, 1440
    )
    result = scheduler.run_now(schedule["id"])
    assert "已提交" in result["result"]

    row = db.query_one("SELECT id FROM jobs WHERE type = 'agent_prompt'")
    assert row, "agent_prompt 任务未入队"
    job = _wait_job(client, row["id"])
    assert job["status"] == "succeeded", job.get("error")

    conv_id = scheduler.get_schedule(schedule["id"])["config"].get("conversation_id")
    assert conv_id
    conversation = agent_sessions.get_conversation(conv_id)
    assert conversation["title"].startswith("定时任务·每晚汇报")
    messages = agent_sessions.get_messages(conv_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "检查订阅新内容并汇报"

    alert = db.query_one("SELECT * FROM system_alerts WHERE category = 'agent'")
    assert alert and "每晚汇报" in alert["title"]


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------
def test_schedules_endpoint(client):
    listing = client.get("/api/agent/schedules")
    assert listing.status_code == 200
    assert listing.json()["kinds"]["subscription_check_all"]

    created = client.post(
        "/api/agent/schedules",
        json={"name": "接口测试", "kind": "gap_suggest_all", "interval_minutes": 720},
    )
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    bad = client.post("/api/agent/schedules", json={"name": "x", "kind": "nope"})
    assert bad.status_code == 400

    missing = client.delete("/api/agent/schedules/nope")
    assert missing.status_code == 404

    assert client.delete(f"/api/agent/schedules/{schedule_id}").status_code == 200

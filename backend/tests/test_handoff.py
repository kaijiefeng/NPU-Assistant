"""M2: human handoff and crisis escalation."""
from __future__ import annotations


def _chat(client, message="请假流程是什么？", session_key="web:handoff"):
    response = client.post(
        "/api/chat",
        json={"message": message, "session_key": session_key, "channel": "web"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_crisis_creates_urgent_ticket_without_calling_llm(client, fake_llm, seeded_wiki):
    before = len(fake_llm.calls)
    data = _chat(client, "我不想活了，想自杀", "web:crisis-1")
    assert data["crisis"]["ticket_no"].startswith("NPU-")
    assert data["crisis"]["category"] == "suicide"
    assert "120" in data["reply"] and "110" in data["reply"]
    assert data["handoff"]["priority"] == "urgent"
    assert len(fake_llm.calls) == before  # crisis branch never calls the model

    handoffs = client.get("/api/handoff?priority=urgent").json()
    assert handoffs["total"] >= 1
    ticket = handoffs["items"][0]
    assert ticket["reason"] == "crisis"
    assert ticket["priority"] == "urgent"

    events = client.get("/api/crisis/events").json()
    assert events["items"]
    assert events["items"][0]["category"] == "suicide"


def test_crisis_negative_context_is_not_escalated(client, fake_llm, seeded_wiki):
    data = _chat(client, "学校举办自杀预防讲座，主题班会怎么组织？", "web:crisis-2")
    assert not data.get("crisis")
    assert data["reply"]


def test_handoff_ticket_lifecycle(client, fake_llm, seeded_wiki):
    data = _chat(client, "国家奖学金怎么申请？", "web:handoff-1")
    conversation_id = data["conversation_id"]

    created = client.post(
        "/api/handoff",
        json={
            "conversation_id": conversation_id,
            "message_id": data["message_id"],
            "reason": "not_solved",
            "contact": "13800000000",
            "note": "我想确认截止时间",
        },
    )
    assert created.status_code == 200, created.text
    ticket = created.json()
    assert ticket["ticket_no"].startswith("NPU-")
    assert ticket["ticket_token"]
    assert "24" in ticket["sla"]

    # Idempotent: an active ticket is returned instead of creating a duplicate.
    again = client.post(
        "/api/handoff",
        json={"conversation_id": conversation_id, "reason": "not_solved"},
    )
    assert again.json()["ticket_no"] == ticket["ticket_no"]

    status = client.get(
        f"/api/handoff/ticket/{ticket['ticket_no']}?token={ticket['ticket_token']}"
    )
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert client.get(f"/api/handoff/ticket/{ticket['ticket_no']}?token=wrong").status_code == 404

    listed = client.get("/api/handoff").json()
    item = next(item for item in listed["items"] if item["ticket_no"] == ticket["ticket_no"])

    claimed = client.post(f"/api/handoff/{item['id']}/claim", json={"assignee_id": "counselor-1"})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "claimed"

    replied = client.post(
        f"/api/handoff/{item['id']}/reply", json={"content": "截止时间是本周五，请尽快提交。"}
    )
    assert replied.status_code == 200, replied.text
    assert replied.json()["status"] == "replied"
    assert replied.json()["messages"][-1]["content"].startswith("截止时间")

    resolved = client.post(
        f"/api/handoff/{item['id']}/resolve", json={"resolution": "已电话确认"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


def test_handoff_requires_admin_when_token_configured(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "handoff-token-123456")
    assert client.get("/api/handoff").status_code == 401
    assert (
        client.get("/api/handoff", headers={"X-Admin-Token": "handoff-token-123456"}).status_code
        == 200
    )
    assert client.get("/api/crisis/events").status_code == 401


def test_crisis_settings_are_editable(client):
    response = client.put(
        "/api/settings",
        json={
            "handoff_sla_hours": 48,
            "crisis_sla_minutes": 10,
            "crisis_hotlines": [
                {"name": "校心理中心", "phone": "029-00000000"},
                {"name": "保卫处", "phone": "029-11111111"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    settings = response.json()
    assert settings["handoff_sla_hours"] == 48
    assert settings["crisis_sla_minutes"] == 10
    assert settings["crisis_hotlines"][0]["name"] == "校心理中心"

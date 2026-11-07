"""M1 RSI: feedback -> knowledge gap -> service desk, and freshness."""
from __future__ import annotations

from datetime import date


def _ask(client, message="国家奖学金怎么申请？需要哪些材料？", session_key="web:rsi"):
    response = client.post(
        "/api/chat",
        json={"message": message, "session_key": session_key, "channel": "web"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_chat_returns_message_id_and_persists_retrieval(client, fake_llm, seeded_wiki):
    data = _ask(client)
    assert data["message_id"]
    assert "当前日期：" in fake_llm.calls[-1]
    # The retrieval signal is persisted on the assistant message.
    from app import db

    row = db.query_one(
        "SELECT retrieval, fallback, navigation_method FROM messages WHERE id = ?",
        (data["message_id"],),
    )
    assert row is not None
    assert isinstance(db.from_json(row["retrieval"], {}), dict)
    assert row["navigation_method"] in {"exact", "keyword", "llm", "remote", "none", "chitchat"}


def test_feedback_creates_gap_and_overview(client, fake_llm, seeded_wiki):
    data = _ask(client)
    message_id = data["message_id"]

    feedback = client.post(
        "/api/chat/feedback",
        json={
            "message_id": message_id,
            "rating": -1,
            "reason_code": "incomplete",
            "comment": "没有写截止时间",
            "voter_key": "browser-1",
        },
    )
    assert feedback.status_code == 200, feedback.text
    body = feedback.json()
    assert body["ok"] is True
    assert body["gap_id"]

    gaps = client.get("/api/insights/gaps").json()
    assert gaps["total"] >= 1
    target = next(item for item in gaps["items"] if item["id"] == body["gap_id"])
    assert target["count"] >= 1
    assert "negative_feedback" in target["reasons"]
    assert target["status"] == "open"

    overview = client.get("/api/insights/overview?days=7").json()
    assert overview["feedback"]["down"] >= 1
    assert overview["gaps"]["open"] >= 1

    # Same voter/message re-submission updates instead of creating a second gap.
    again = client.post(
        "/api/chat/feedback",
        json={
            "message_id": message_id,
            "rating": -1,
            "reason_code": "incomplete",
            "voter_key": "browser-1",
        },
    )
    assert again.status_code == 200
    gaps2 = client.get("/api/insights/gaps").json()
    target2 = next(item for item in gaps2["items"] if item["id"] == body["gap_id"])
    assert target2["count"] == target["count"]


def test_feedback_requires_assistant_message(client, fake_llm, seeded_wiki):
    data = _ask(client)
    # A user message cannot be rated.
    from app import db

    user_message = db.query_one(
        "SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' LIMIT 1",
        (data["conversation_id"],),
    )
    response = client.post(
        "/api/chat/feedback",
        json={"message_id": user_message["id"], "rating": 1, "voter_key": "v"},
    )
    assert response.status_code == 400


def test_gap_lifecycle(client, fake_llm, seeded_wiki):
    data = _ask(client)
    feedback = client.post(
        "/api/chat/feedback",
        json={
            "message_id": data["message_id"],
            "rating": -1,
            "reason_code": "not_found",
            "voter_key": "browser-2",
        },
    ).json()
    gap_id = feedback["gap_id"]

    linked = client.post(
        f"/api/insights/gaps/{gap_id}/link",
        json={"page_id": "a" * 32, "note": "已关联国奖词条"},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["status"] == "linked"
    assert linked.json()["linked_page"]["title"]

    resolved = client.post(
        f"/api/insights/gaps/{gap_id}/resolve", json={"note": "已补充截止时间"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


def test_freshness_fields_expired_warning_and_verify(client, fake_llm, seeded_wiki):
    page_id = "a" * 32
    updated = client.put(
        f"/api/wiki/pages/{page_id}",
        json={
            "expires_at": "2020-01-01",
            "published_at": "2019-09-01",
            "academic_year": "2019-2020",
            "doc_type": "notice",
        },
    )
    assert updated.status_code == 200, updated.text
    page = updated.json()
    assert page["freshness"]["status"] == "expired"
    assert page["expires_at"] == "2020-01-01"

    data = _ask(client)
    assert data["citations"], data
    source = data["citations"][0]["sources"][0]
    assert source["expires_at"] == "2020-01-01"
    assert source["freshness"] == "expired"
    assert "有效期已过" in data["reply"]

    verified = client.post(f"/api/wiki/pages/{page_id}/verify", json={})
    assert verified.status_code == 200
    assert verified.json()["verified_at"] == date.today().isoformat()
    # Verifying does not silently extend an explicit expiry date.
    assert verified.json()["freshness"]["status"] == "expired"

    extended = client.put(
        f"/api/wiki/pages/{page_id}",
        json={"expires_at": "2099-01-01", "academic_year": "2099-2100"},
    )
    assert extended.status_code == 200
    assert extended.json()["freshness"]["status"] in {"fresh", "aging"}


def test_insights_requires_token_when_configured(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "test-token-1234567890")
    assert client.get("/api/insights/gaps").status_code == 401
    assert (
        client.get("/api/insights/gaps", headers={"X-Admin-Token": "test-token-1234567890"}).status_code
        == 200
    )
    assert client.get("/api/insights/overview").status_code == 401
    assert client.get("/api/insights/overview", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_settings_include_freshness_defaults(client):
    settings = client.get("/api/settings").json()
    assert settings.get("academic_year")
    assert int(settings["freshness_stale_days"]) > 0
    assert int(settings["freshness_expiry_warn_days"]) > 0
    response = client.put(
        "/api/settings",
        json={"academic_year": "2026-2027", "freshness_stale_days": 120},
    )
    assert response.status_code == 200, response.text
    assert response.json()["academic_year"] == "2026-2027"
    assert response.json()["freshness_stale_days"] == 120

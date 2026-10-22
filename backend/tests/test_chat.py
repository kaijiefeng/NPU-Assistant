"""对话接口测试：mock 降级 + 引用、多轮会话、无命中兜底、历史与删除（契约 §5.2）。"""
from __future__ import annotations

import re

QUESTION = "国家奖学金怎么申请？需要哪些材料？"


def _chat(client, message: str, **extra):
    payload = {"message": message, "channel": "web", "session_key": "web:test"}
    payload.update(extra)
    return client.post("/api/chat", json=payload)


def test_chat_with_mock_model_returns_citations_and_fallback(client, seeded):
    response = _chat(client, QUESTION)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["reply"].strip()
    assert data["fallback"] is True  # 内置演示模型：降级为抽取式回答
    assert data["provider_type"] == "mock"
    assert data["model"]
    assert isinstance(data["latency_ms"], int) and data["latency_ms"] >= 0
    assert data["conversation_id"]

    citations = data["citations"]
    assert citations, "命中知识库时必须返回引用"
    assert data["sources_used"] == len(citations)
    assert [c["index"] for c in citations] == list(range(1, len(citations) + 1))
    first = citations[0]
    assert first["chunk_id"] and first["document_id"]
    assert first["document_title"]
    assert first["snippet"].strip()
    assert first["score"] > 0

    # 回答中的 [n] 标注必须与 citations 编号一致，且不越界
    marks = {int(value) for value in re.findall(r"\[(\d+)\]", data["reply"])}
    assert marks, "抽取式回答应带 [n] 引用标注"
    assert max(marks) <= len(citations)


def test_chat_multiturn_keeps_same_conversation(client, seeded):
    first = _chat(client, QUESTION).json()
    conversation_id = first["conversation_id"]

    second_response = _chat(
        client,
        "那同一学年可以同时拿国家奖学金和国家励志奖学金吗？",
        conversation_id=conversation_id,
    )
    assert second_response.status_code == 200
    second = second_response.json()
    assert second["conversation_id"] == conversation_id
    assert second["reply"].strip()

    history = client.get(f"/api/chat/conversations/{conversation_id}/messages")
    assert history.status_code == 200
    data = history.json()
    assert data["conversation"]["id"] == conversation_id
    assert data["conversation"]["channel"] == "web"

    items = data["items"]
    assert len(items) == 4
    assert [item["role"] for item in items] == ["user", "assistant", "user", "assistant"]
    assert items[0]["content"] == QUESTION
    assert items[1]["citations"]
    assert items[3]["model"]


def test_chat_without_knowledge_hit_uses_polite_fallback(client, seeded):
    data = _chat(client, "xyzzy quux frobnicate zzz").json()

    assert data["citations"] == []
    assert data["sources_used"] == 0
    assert data["fallback"] is True
    assert "知识库" in data["reply"]
    assert "咨询" in data["reply"]


def test_chat_validates_message(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 400
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 400
    assert client.post("/api/chat", json={"message": 123}).status_code == 400
    assert client.post("/api/chat", json={}).status_code == 400


def test_chat_rejects_unknown_conversation_and_channel(client, seeded):
    missing = _chat(client, QUESTION, conversation_id="not-exist-conversation")
    assert missing.status_code == 404

    bad_channel = client.post(
        "/api/chat", json={"message": QUESTION, "channel": "telegram"}
    )
    assert bad_channel.status_code == 400


def test_conversation_list_and_delete(client, seeded):
    conversation_id = _chat(client, QUESTION).json()["conversation_id"]

    listing = client.get("/api/chat/conversations", params={"channel": "web"}).json()
    matched = [item for item in listing["items"] if item["id"] == conversation_id]
    assert matched, "新建会话应出现在会话列表中"
    item = matched[0]
    assert item["channel"] == "web"
    assert item["message_count"] == 2
    assert item["last_message"]

    deleted = client.delete(f"/api/chat/conversations/{conversation_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/chat/conversations/{conversation_id}/messages").status_code == 404
    assert client.delete(f"/api/chat/conversations/{conversation_id}").status_code == 404

    # 删除后列表里不再出现该会话
    remaining = client.get("/api/chat/conversations").json()["items"]
    assert all(entry["id"] != conversation_id for entry in remaining)

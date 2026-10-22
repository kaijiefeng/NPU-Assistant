"""对话接口测试（LLM Wiki）：双层引用、真实 LLM 成功 / mock / 故障降级、无命中、多轮与历史（契约 §5.2）。"""
from __future__ import annotations

import re

QUESTION = "国家奖学金怎么申请？需要哪些材料？"


def _chat(client, message: str, **extra):
    payload = {"message": message, "channel": "web", "session_key": "web:test"}
    payload.update(extra)
    return client.post("/api/chat", json=payload)


def test_chat_with_real_model_returns_dual_citations(client, seeded_wiki, fake_llm, real_model):
    response = _chat(client, QUESTION)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["reply"].strip()
    assert data["fallback"] is False, "真实 LLM 成功时应 fallback=false"
    assert data["provider_type"] == "openai"
    assert data["model"] == "Fake DeepSeek"
    assert isinstance(data["latency_ms"], int)

    citations = data["citations"]
    assert citations, "命中词条时必须返回引用"
    assert data["sources_used"] == len(citations)
    first = citations[0]
    assert first["index"] == 1
    assert first["page_id"] == "a" * 32
    assert first["page_title"] == "国家奖学金申请"
    assert first["summary"]
    assert first["snippet"]
    assert first["sources"], "双层引用的第二层是来源锚点"
    source = first["sources"][0]
    assert source["document_id"] == "d" * 32
    assert source["document_title"]
    assert source["source_file"].endswith(".md")
    assert source["section"] == "五、申请材料清单"
    assert source["quote"]

    # 回答中的 [n] 与 citations 编号一致
    marks = {int(value) for value in re.findall(r"\[(\d+)\]", data["reply"])}
    assert marks == {1}
    assert max(marks) <= len(citations)

    navigation = data["navigation"]
    assert navigation["method"] in {"exact", "keyword"}
    assert navigation["pages"][0]["id"] == "a" * 32


def test_chat_appends_reference_list_when_model_omits_marks(
    client, seeded_wiki, fake_llm, real_model
):
    fake_llm.answer_text = "国家奖学金需要提交申请表和成绩单。"
    data = _chat(client, QUESTION).json()

    assert data["fallback"] is False
    assert "参考词条" in data["reply"]
    assert data["citations"], "模型未标编号时应追加参考词条清单"
    assert data["citations"][0]["page_id"] == "a" * 32


def test_chat_with_mock_model_falls_back_to_page_excerpt(client, seeded_wiki):
    # 默认模型是 mock：导航（词法）仍可用，回答降级为词条摘录
    data = _chat(client, QUESTION).json()

    assert data["fallback"] is True
    assert data["provider_type"] == "mock"
    assert data["citations"], "降级回答仍应带词条引用"
    assert data["citations"][0]["page_id"] == "a" * 32
    assert data["citations"][0]["sources"]
    assert "Wiki" in data["reply"] or "知识库" in data["reply"]
    assert "以上内容摘自" in data["reply"]


def test_chat_with_broken_llm_degrades_without_500(client, seeded_wiki, real_model, broken_llm):
    response = _chat(client, QUESTION)
    assert response.status_code == 200, "LLM 故障必须降级为词条摘录，不能 500"
    data = response.json()
    assert data["fallback"] is True
    assert data["citations"]
    assert data["reply"].strip()


def test_chat_no_hit_returns_polite_fallback(client, seeded_wiki, fake_llm, real_model):
    data = _chat(client, "xyzzy quux frobnicate zzz").json()

    assert data["citations"] == []
    assert data["sources_used"] == 0
    assert data["fallback"] is True
    assert data["navigation"]["method"] == "none"
    assert "暂未收录" in data["reply"]
    assert "咨询" in data["reply"]


def test_chat_multiturn_and_history_structure(client, seeded_wiki, fake_llm, real_model):
    first = _chat(client, QUESTION).json()
    conversation_id = first["conversation_id"]

    second = _chat(
        client,
        "那同一学年可以同时拿国家奖学金和国家励志奖学金吗？",
        conversation_id=conversation_id,
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    history = client.get(f"/api/chat/conversations/{conversation_id}/messages")
    assert history.status_code == 200
    data = history.json()
    assert data["conversation"]["channel"] == "web"
    items = data["items"]
    assert len(items) == 4
    assert [item["role"] for item in items] == ["user", "assistant", "user", "assistant"]
    assert items[0]["content"] == QUESTION
    # 历史中的 citations 反序列化为双层结构
    citations = items[1]["citations"]
    assert citations and citations[0]["page_id"]
    assert citations[0]["sources"][0]["section"]


def test_chat_validation_and_unknown_conversation(client, seeded_wiki, fake_llm, real_model):
    assert client.post("/api/chat", json={"message": ""}).status_code == 400
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 400
    assert client.post("/api/chat", json={"message": 123}).status_code == 400
    assert client.post("/api/chat", json={}).status_code == 400
    assert _chat(client, QUESTION, conversation_id="not-exist").status_code == 404
    assert (
        client.post("/api/chat", json={"message": QUESTION, "channel": "telegram"}).status_code
        == 400
    )


def test_conversation_list_and_delete(client, seeded_wiki, fake_llm, real_model):
    conversation_id = _chat(client, QUESTION).json()["conversation_id"]

    listing = client.get("/api/chat/conversations", params={"channel": "web"}).json()["items"]
    matched = [item for item in listing if item["id"] == conversation_id]
    assert matched
    assert matched[0]["message_count"] == 2
    assert matched[0]["last_message"]

    assert client.delete(f"/api/chat/conversations/{conversation_id}").json() == {"ok": True}
    assert client.get(f"/api/chat/conversations/{conversation_id}/messages").status_code == 404
    assert client.delete(f"/api/chat/conversations/{conversation_id}").status_code == 404
    remaining = client.get("/api/chat/conversations").json()["items"]
    assert all(item["id"] != conversation_id for item in remaining)

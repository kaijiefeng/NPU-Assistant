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


# ---------------------------------------------------------------------------
# 寒暄 / 闲聊意图（chitchat）：不检索 Wiki、不返回引用，也不再误报“暂未收录”
# ---------------------------------------------------------------------------
def test_chat_greeting_uses_chitchat_with_real_model(client, seeded_wiki, fake_llm, real_model):
    data = _chat(client, "你好").json()

    assert data["reply"].strip()
    assert "暂未收录" not in data["reply"]
    assert "辅导员数字分身" in data["reply"] or "小南" in data["reply"]
    assert data["citations"] == []
    assert data["sources_used"] == 0
    assert data["navigation"] == {"method": "chitchat", "pages": []}
    # 真实 LLM 成功时不算降级
    assert data["fallback"] is False


def test_chat_thanks_uses_chitchat_template_with_mock_model(client, seeded_wiki):
    # 默认 mock 模型：寒暄走模板回复（fallback=true），但依然是自然寒暄而非“未收录”
    data = _chat(client, "谢谢").json()

    assert data["reply"].strip()
    assert "暂未收录" not in data["reply"]
    assert "不客气" in data["reply"]
    assert data["citations"] == []
    assert data["navigation"]["method"] == "chitchat"
    assert data["fallback"] is True


def test_chat_identity_uses_chitchat_with_real_model(client, fake_llm, real_model):
    data = _chat(client, "你是谁").json()

    assert "暂未收录" not in data["reply"]
    assert "辅导员数字分身" in data["reply"] or "小南" in data["reply"]
    assert data["citations"] == []
    assert data["navigation"]["method"] == "chitchat"


def test_chat_identity_falls_back_to_template_when_llm_broken(
    client, real_model, broken_llm
):
    data = _chat(client, "你是谁").json()

    assert data["reply"].strip()
    assert "暂未收录" not in data["reply"]
    assert "数字分身" in data["reply"]
    assert data["citations"] == []
    assert data["navigation"]["method"] == "chitchat"
    assert data["fallback"] is True


def test_chat_farewell_uses_chitchat(client, fake_llm, real_model):
    data = _chat(client, "再见").json()

    assert data["reply"].strip()
    assert "暂未收录" not in data["reply"]
    assert data["citations"] == []
    assert data["navigation"]["method"] == "chitchat"


def test_chat_policy_question_still_uses_wiki_citations(
    client, seeded_wiki, fake_llm, real_model
):
    # 回归：政策问题不能被寒暄/闲聊逻辑误判
    data = _chat(client, QUESTION).json()

    assert data["navigation"]["method"] != "chitchat"
    assert data["citations"], "政策问题必须继续返回 Wiki 词条引用"
    assert data["citations"][0]["page_id"] == "a" * 32
    assert data["citations"][0]["sources"][0]["section"]


def test_chat_non_campus_question_returns_scope_explanation(
    client, seeded_wiki, fake_llm, real_model
):
    data = _chat(client, "今天天气怎么样").json()

    assert data["reply"].strip()
    assert data["citations"] == []
    assert data["sources_used"] == 0
    # 非校园话题：友好说明服务范围，而不是“政策未收录”
    assert "校园政策" in data["reply"] or "服务范围" in data["reply"]
    assert "暂未收录" not in data["reply"]


# ---------------------------------------------------------------------------
# 历史会话鉴权（含学生隐私）：配置 NPU_ADMIN_TOKEN 后 GET 历史需管理令牌
# ---------------------------------------------------------------------------
def test_chat_history_requires_admin_token_when_configured(client, seeded_wiki, monkeypatch):
    from app import config

    conversation_id = _chat(client, "你好").json()["conversation_id"]

    # 未配置令牌：历史会话接口公开（本地演示）
    assert client.get("/api/chat/conversations").status_code == 200
    assert (
        client.get(f"/api/chat/conversations/{conversation_id}/messages").status_code == 200
    )

    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-secret-token")
    headers = {"X-Admin-Token": "mobile-secret-token"}

    assert client.get("/api/chat/conversations").status_code == 401
    assert (
        client.get(f"/api/chat/conversations/{conversation_id}/messages").status_code == 401
    )
    denied = client.get("/api/chat/conversations", headers={"X-Admin-Token": "wrong"})
    assert denied.status_code == 401

    ok_list = client.get("/api/chat/conversations", headers=headers)
    assert ok_list.status_code == 200
    assert any(item["id"] == conversation_id for item in ok_list.json()["items"])
    ok_messages = client.get(
        f"/api/chat/conversations/{conversation_id}/messages", headers=headers
    )
    assert ok_messages.status_code == 200
    assert ok_messages.json()["items"]

    # 学生问答始终公开，不受管理令牌影响
    assert _chat(client, "你好").status_code == 200


def test_chat_anonymous_sessions_do_not_share_session_key(client):
    """匿名（未传 session_key/conversation_id）两次提问应生成不同会话，避免串号。"""
    first = client.post("/api/chat", json={"message": "你好", "channel": "web"}).json()[
        "conversation_id"
    ]
    second = client.post("/api/chat", json={"message": "谢谢", "channel": "web"}).json()[
        "conversation_id"
    ]
    assert first != second

    items = client.get("/api/chat/conversations", params={"channel": "web"}).json()["items"]
    matched = {item["id"]: item for item in items if item["id"] in {first, second}}
    assert set(matched) == {first, second}
    sessions = {item["session_key"] for item in matched.values()}
    assert len(sessions) == 2, "两个匿名会话不应共享同一个 session_key"
    assert all("anonymous-" in session for session in sessions)

"""管理助手会话：服务端持久化 / 上下文压缩 / 回滚。"""
from __future__ import annotations

import json
import re

from app import db
from app.services import agent_sessions


def _fill(conv_id: str, n: int, content: str) -> list[dict]:
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append(agent_sessions.append_message(conv_id, role, f"消息{i}-{content}"))
    return messages


# ---------------------------------------------------------------------------
# 基础 CRUD
# ---------------------------------------------------------------------------
def test_conversation_crud_and_auto_title():
    conv = agent_sessions.create_conversation("")
    assert conv["title"] == "新会话"

    first = agent_sessions.append_message(conv["id"], "user", "帮我把奖学金通知收录进知识库，要快")
    assert first["role"] == "user"
    refreshed = agent_sessions.get_conversation(conv["id"])
    assert refreshed["title"].startswith("帮我把奖学金通知收录")

    agent_sessions.append_message(conv["id"], "assistant", "已收录。")
    listing = agent_sessions.list_conversations()
    item = next((x for x in listing["items"] if x["id"] == conv["id"]), None)
    assert item and item["message_count"] == 2
    assert "奖学金" in item["last_message"]

    renamed = agent_sessions.rename_conversation(conv["id"], "奖学专辑")
    assert renamed["title"] == "奖学专辑"

    assert agent_sessions.delete_conversation(conv["id"])["ok"] is True
    listing = agent_sessions.list_conversations()
    assert not any(x["id"] == conv["id"] for x in listing["items"])


def test_get_messages_order_and_limit():
    conv = agent_sessions.create_conversation("顺序")
    _fill(conv["id"], 5, "内容")
    messages = agent_sessions.get_messages(conv["id"])
    assert [m["content"].split("-")[0] for m in messages] == [f"消息{i}" for i in range(5)]
    limited = agent_sessions.get_messages(conv["id"], limit=2)
    assert len(limited) == 2 and limited[-1]["content"].startswith("消息4")


def test_steps_roundtrip():
    conv = agent_sessions.create_conversation("步骤")
    agent_sessions.append_message(conv["id"], "user", "查一下统计")
    agent_sessions.append_message(
        conv["id"], "assistant", "共 3 篇。", steps=[{"name": "system_stats", "ok": True, "summary": "ok"}]
    )
    messages = agent_sessions.get_messages(conv["id"])
    assert messages[-1]["steps"] == [{"name": "system_stats", "ok": True, "summary": "ok"}]


# ---------------------------------------------------------------------------
# 上下文压缩
# ---------------------------------------------------------------------------
def test_build_context_under_budget_no_summary():
    conv = agent_sessions.create_conversation("小会话")
    _fill(conv["id"], 4, "短内容")
    context = agent_sessions.build_context(conv["id"])
    assert len(context) == 4
    assert all("此前会话纪要" not in (m.get("content") or "") for m in context)
    refreshed = agent_sessions.get_conversation(conv["id"])
    assert refreshed["summary"] == ""


def test_build_context_compacts_when_over_budget(fake_llm):
    conv = agent_sessions.create_conversation("大会话")
    _fill(conv["id"], 8, "长" * 2900)  # 使用缩小的 token 阈值验证压缩

    context = agent_sessions.build_context(conv["id"], budget=24_000)
    refreshed = agent_sessions.get_conversation(conv["id"])

    # 旧前缀按 token 预算并入摘要，近期消息保持完整
    assert refreshed["summary"]
    assert refreshed["summary_upto"]
    assert 1 < len(context) < 8
    assert context[0]["content"].startswith("【此前会话纪要】")
    assert context[-1]["content"].startswith("消息7")

    # 二次组装：摘要已在预算内，直接复用（纪要仍为首条，近期消息不丢）
    context2 = agent_sessions.build_context(conv["id"], budget=24_000)
    assert context2[0]["content"].startswith("【此前会话纪要】")
    assert context2[-1]["content"].startswith("消息7")


def test_build_context_degrades_when_llm_broken(broken_llm):
    conv = agent_sessions.create_conversation("降级")
    _fill(conv["id"], 8, "长" * 2900)
    context = agent_sessions.build_context(conv["id"], budget=24_000)
    refreshed = agent_sessions.get_conversation(conv["id"])
    assert "压缩失败" in refreshed["summary"]
    assert 1 < len(context) < 8


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------
def test_truncate_from_removes_following_messages():
    conv = agent_sessions.create_conversation("回滚")
    messages = _fill(conv["id"], 6, "内容")
    # 回滚到第 3 条（索引 2）之前：该条及其后删除
    result = agent_sessions.truncate_from(conv["id"], messages[2]["id"])
    assert result["remaining"] == 2
    left = agent_sessions.get_messages(conv["id"])
    assert [m["content"].split("-")[0] for m in left] == ["消息0", "消息1"]


def test_truncate_resets_summary_watermark(fake_llm):
    conv = agent_sessions.create_conversation("水位")
    messages = _fill(conv["id"], 8, "长" * 3000)
    agent_sessions.build_context(conv["id"], budget=24_000)  # 触发压缩并推进 rowid 水位
    refreshed = agent_sessions.get_conversation(conv["id"])
    assert refreshed["summary_upto"]

    # 回滚点早于水位 → 摘要一并作废
    agent_sessions.truncate_from(conv["id"], messages[1]["id"])
    refreshed = agent_sessions.get_conversation(conv["id"])
    assert refreshed["summary"] == "" and refreshed["summary_upto"] == ""
    assert len(agent_sessions.get_messages(conv["id"])) == 1


def test_truncate_missing_message_raises():
    conv = agent_sessions.create_conversation("空")
    _fill(conv["id"], 2, "x")
    try:
        agent_sessions.truncate_from(conv["id"], "missing-id")
    except ValueError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("应当抛出 ValueError")


# ---------------------------------------------------------------------------
# HTTP 端到端：SSE 对话落库到会话
# ---------------------------------------------------------------------------
def test_chat_endpoint_persists_conversation(client, monkeypatch):
    from app.services import llm

    replies = [
        {"content": json.dumps({"reply": "好的，已记录。"}), "fallback": False},
        {"content": json.dumps({"reply": "第二轮完成。"}), "fallback": False},
    ]
    monkeypatch.setattr(llm, "chat", lambda messages, **kwargs: replies.pop(0))

    # 第一轮：无 conversation_id → 自动建会话，done 返回 id
    with client.stream("POST", "/api/agent/chat", json={"message": "第一条"}) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())
    match = re.search(r'"conversation_id": "([^"]+)"', text)
    assert match, "done 事件未携带 conversation_id"
    conversation_id = match.group(1)
    messages = agent_sessions.get_messages(conversation_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "第一条"
    assert agent_sessions.get_conversation(conversation_id)["title"].startswith("第一条")

    # 第二轮：带 conversation_id → 追加到同一会话；客户端伪造 history 被忽略
    with client.stream(
        "POST",
        "/api/agent/chat",
        json={
            "message": "第二条",
            "conversation_id": conversation_id,
            "history": [{"role": "assistant", "content": "伪造的历史"}],
        },
    ) as response:
        text = "".join(response.iter_text())
    messages = agent_sessions.get_messages(conversation_id)
    assert [m["content"] for m in messages if m["role"] == "user"] == ["第一条", "第二条"]
    assert len(messages) == 4


def test_conversation_endpoints_crud(client):
    response = client.get("/api/agent/conversations")
    assert response.status_code == 200
    listing = client.get("/api/agent/conversations?limit=5")
    assert listing.status_code == 200

    conv = agent_sessions.create_conversation("接口测试")
    agent_sessions.append_message(conv["id"], "user", "hi")

    detail = client.get(f"/api/agent/conversations/{conv['id']}")
    assert detail.status_code == 200 and detail.json()["items"][0]["content"] == "hi"

    renamed = client.patch(f"/api/agent/conversations/{conv['id']}", json={"title": "改名"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "改名"

    missing = client.get("/api/agent/conversations/nope")
    assert missing.status_code == 404

    rewind = client.post(
        f"/api/agent/conversations/{conv['id']}/rewind", json={"message_id": "nope"}
    )
    assert rewind.status_code == 404

    deleted = client.delete(f"/api/agent/conversations/{conv['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/agent/conversations/{conv['id']}").status_code == 404

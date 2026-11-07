"""M5: SSE streaming, retry and circuit breaker."""
from __future__ import annotations

import json

QUESTION = "国家奖学金怎么申请？需要哪些材料？"


def _events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        kind, data = "", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if kind and data:
            try:
                events.append((kind, json.loads(data)))
            except ValueError:
                continue
    return events


def test_chat_stream_emits_deltas_and_final_answer(client, seeded_wiki, fake_llm, real_model, monkeypatch):
    from app.services import llm

    calls = {"n": 0}

    def fake_stream(messages, *, model_id=None, temperature=None, max_tokens=None, on_delta):
        calls["n"] += 1
        for part in ("国家奖学金", "需要提交", "申请表、成绩单和事迹材料。"):
            on_delta(part)
        return {
            "content": "国家奖学金需要提交申请表、成绩单和事迹材料。",
            "model": "Fake DeepSeek",
            "provider_type": "openai",
            "fallback": False,
            "usage": {},
            "latency_ms": 5,
            "error": None,
        }

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    response = client.post(
        "/api/chat/stream",
        json={"message": QUESTION, "channel": "web", "session_key": "web:stream"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    kinds = [kind for kind, _ in events]
    assert "delta" in kinds
    assert kinds[-1] == "done"
    assert calls["n"] == 1

    text = "".join(data["text"] for kind, data in events if kind == "delta")
    assert "国家奖学金" in text
    done = events[-1][1]
    assert done["message_id"]
    assert done["citations"]
    assert done["fallback"] is False


def test_chat_stream_validation(client):
    response = client.post("/api/chat/stream", json={"message": ""})
    assert response.status_code == 400


def test_llm_retries_transient_error_once(client, real_model, monkeypatch):
    from app.services import llm

    monkeypatch.setattr(llm, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(llm, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(llm, "_CIRCUIT", {})
    calls = {"n": 0}

    def flaky(target, messages, temperature, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise llm.LLMError("调用「Fake」超时（60 秒）")
        return "重试成功", {}

    monkeypatch.setattr(llm, "_chat_openai", flaky)
    result = llm.chat([{"role": "user", "content": "你好"}])
    assert result["fallback"] is False
    assert result["content"] == "重试成功"
    assert calls["n"] == 2


def test_llm_circuit_breaker_opens_and_alerts(client, real_model, monkeypatch):
    from app.services import llm

    monkeypatch.setattr(llm, "RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(llm, "CIRCUIT_THRESHOLD", 2)
    monkeypatch.setattr(llm, "CIRCUIT_COOLDOWN_SECONDS", 120)
    monkeypatch.setattr(llm, "_CIRCUIT", {})

    def down(target, messages, temperature, max_tokens):
        raise llm.LLMError("无法连接到「Fake」：连接被拒绝")

    monkeypatch.setattr(llm, "_chat_openai", down)
    first = llm.chat([{"role": "user", "content": "你好"}])
    second = llm.chat([{"role": "user", "content": "你好"}])
    assert first["fallback"] and second["fallback"]
    third = llm.chat([{"role": "user", "content": "你好"}])
    assert third["fallback"] is True
    assert "熔断" in third["error"]

    alerts = client.get("/api/alerts?status=open").json()
    assert any(item["category"] == "llm" for item in alerts["items"])

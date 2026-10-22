"""机器人网关测试（LLM Wiki）：通道 CRUD、凭据脱敏、Webhook 安全、控制台双层引用、公众号签名/XML、OneBot 归一化与事件入口。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi import WebSocketDisconnect

# 安全默认：公众号不再接受公开默认 Token，测试使用自选强 token
WECHAT_TOKEN = "wechat-test-token-2026"
NONCE = "npu-nonce-001"


def _now_ts() -> str:
    """微信公众号回调时间窗 ±300s：测试内动态时间戳，避免过期 403。"""
    return str(int(time.time()))


TIMESTAMP = _now_ts()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _channels(client) -> list[dict]:
    return client.get("/api/bots/channels").json()["items"]


def _find_channel(client, channel_type: str) -> dict:
    return next(item for item in _channels(client) if item["type"] == channel_type)


def _create_onebot(client, *, access_token: str = "evt-token",
                   admin_user_ids: str = "", import_enabled: str = "true") -> dict:
    response = client.post(
        "/api/bots/channels",
        json={
            "type": "onebot11",
            "name": "测试 QQ 通道",
            "config": {
                "api_base": "http://127.0.0.1:9",
                "access_token": access_token,
                "self_id": "10001",
                "admin_user_ids": admin_user_ids,
                "import_enabled": import_enabled,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_wechat(client, *, token: str = WECHAT_TOKEN,
                   admin_user_ids: str = "", import_enabled: str = "true") -> dict:
    response = client.post(
        "/api/bots/channels",
        json={
            "type": "wechat_mp",
            "name": "测试公众号",
            "config": {
                "token": token,
                "admin_user_ids": admin_user_ids,
                "import_enabled": import_enabled,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_channel_config(channel_id: str, config: dict) -> None:
    """测试安全边界：直接写库模拟历史/异常通道配置（绕过创建时的自动补 token）。"""
    from app import db

    db.execute(
        "UPDATE bot_channels SET config = ? WHERE id = ?",
        (json.dumps(config, ensure_ascii=False), channel_id),
    )


def _signature(token: str = WECHAT_TOKEN, timestamp: str = TIMESTAMP,
               nonce: str = NONCE) -> str:
    raw = "".join(sorted([token, timestamp, nonce]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _wechat_xml(msg_type: str = "text", content: str = "请假流程是什么？",
                from_user: str = "openid-001") -> bytes:
    return f"""<xml>
  <ToUserName><![CDATA[gh_test_account]]></ToUserName>
  <FromUserName><![CDATA[{from_user}]]></FromUserName>
  <CreateTime>{TIMESTAMP}</CreateTime>
  <MsgType><![CDATA[{msg_type}]]></MsgType>
  <Content><![CDATA[{content}]]></Content>
  <MsgId>1234567890</MsgId>
</xml>""".encode("utf-8")


# ---------------------------------------------------------------------------
# 通道 CRUD / webhook 地址
# ---------------------------------------------------------------------------
def test_channels_default_console_and_webhook_urls(client):
    _find_channel(client, "console")  # 首启自动创建
    assert _find_channel(client, "console")["enabled"] is True
    assert _find_channel(client, "console")["webhook_urls"] == {}

    onebot = _create_onebot(client)
    assert onebot["status"] == "offline"
    assert onebot["config"]["self_id"] == "10001"
    assert onebot["webhook_urls"]["ws"].endswith(f"/api/bots/onebot/{onebot['id']}/ws")
    assert onebot["webhook_urls"]["http"].endswith(f"/api/bots/onebot/{onebot['id']}/event")

    wechat = _create_wechat(client)
    assert wechat["webhook_urls"]["callback"] == f"/api/bots/wechat/{wechat['id']}"

    assert (
        client.post("/api/bots/channels", json={"type": "telegram", "name": "x"}).status_code
        == 400
    )
    console = _find_channel(client, "console")
    assert client.delete(f"/api/bots/channels/{console['id']}").status_code == 400
    assert client.delete(f"/api/bots/channels/{onebot['id']}").json() == {"ok": True}
    assert client.put("/api/bots/channels/not-exist", json={"name": "x"}).status_code == 404


def test_channel_partial_update_keeps_config(client):
    onebot = _create_onebot(client)
    updated = client.put(
        f"/api/bots/channels/{onebot['id']}",
        json={"name": "改名后的 QQ 通道", "config": {"api_base": "http://127.0.0.1:3000"}},
    )
    assert updated.status_code == 200
    data = updated.json()
    assert data["name"] == "改名后的 QQ 通道"
    assert data["config"]["api_base"] == "http://127.0.0.1:3000"
    # 安全：凭据不再回显明文，只保留脱敏值 + has_access_token
    assert data["config"]["access_token"] == "****oken"
    assert data["has_access_token"] is True
    assert data["config"]["self_id"] == "10001"


# ---------------------------------------------------------------------------
# 控制台通道：复用 counselor，返回双层引用
# ---------------------------------------------------------------------------
def test_console_simulated_chat_returns_dual_citations(
    client, seeded_wiki, fake_llm, real_model
):
    console = _find_channel(client, "console")
    response = client.post(
        f"/api/bots/channels/{console['id']}/test",
        json={"message": "国家奖学金怎么申请？", "user_id": "10001", "user_name": "演示同学"},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["channel"] == "console"
    assert data["channel_id"] == console["id"]
    assert data["conversation_id"]
    assert data["reply"].strip()
    assert data["citations"], "机器人回复同样带双层引用"
    citation = data["citations"][0]
    assert citation["page_id"] == "a" * 32
    assert citation["page_title"] == "国家奖学金申请"
    assert citation["sources"][0]["section"]
    assert citation["sources"][0]["quote"]
    assert isinstance(data["latency_ms"], int)
    assert data["outbound"]["type"] == "console"
    assert data["outbound"]["text"].strip()

    logs = client.get("/api/bots/logs").json()["items"]
    matched = [item for item in logs if item["channel"] == "console"]
    assert matched
    assert matched[0]["reply"].strip()
    assert "国家奖学金" in matched[0]["message"]


def test_console_simulated_chat_with_mock_model_still_replies(client, seeded_wiki):
    console = _find_channel(client, "console")
    data = client.post(
        f"/api/bots/channels/{console['id']}/test",
        json={"message": "请假流程是什么？"},
    ).json()

    assert data["reply"].strip()
    assert data["citations"]
    assert data["citations"][0]["page_id"] == "b" * 32
    # 空消息 400
    assert (
        client.post(f"/api/bots/channels/{console['id']}/test", json={"message": "  "}).status_code
        == 400
    )


# ---------------------------------------------------------------------------
# 微信公众号
# ---------------------------------------------------------------------------
def test_wechat_get_verification(client):
    wechat = _create_wechat(client)
    params = {
        "signature": _signature(),
        "timestamp": TIMESTAMP,
        "nonce": NONCE,
        "echostr": "echo-12345",
    }
    response = client.get(f"/api/bots/wechat/{wechat['id']}", params=params)
    assert response.status_code == 200
    assert response.text == "echo-12345"

    bad = {**params, "signature": "0" * 40}
    assert client.get(f"/api/bots/wechat/{wechat['id']}", params=bad).status_code == 403


def test_wechat_text_message_replies_with_xml(client, seeded_wiki, fake_llm, real_model):
    wechat = _create_wechat(client)
    params = {"signature": _signature(), "timestamp": TIMESTAMP, "nonce": NONCE}
    response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=_wechat_xml("text", "请假流程是什么？"),
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 200, response.text
    # 微信官方文档要求 text/xml（兼容 application/xml）
    assert "xml" in response.headers["content-type"].lower()

    root = ET.fromstring(response.content)
    content = root.findtext("Content") or ""
    assert content.strip()
    assert root.findtext("ToUserName") == "openid-001"
    assert root.findtext("FromUserName") == "gh_test_account"
    assert root.findtext("MsgType") == "text"

    wrong = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params={**params, "signature": "0" * 40},
        content=_wechat_xml(),
        headers={"Content-Type": "application/xml"},
    )
    assert wrong.status_code == 403


def test_wechat_non_text_message_gets_guide(client):
    wechat = _create_wechat(client)
    params = {"signature": _signature(), "timestamp": TIMESTAMP, "nonce": NONCE}
    response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=_wechat_xml("image", ""),
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 200
    root = ET.fromstring(response.content)
    assert "文字" in (root.findtext("Content") or "")


def test_wechat_rejects_malformed_xml(client):
    wechat = _create_wechat(client)
    params = {"signature": _signature(), "timestamp": TIMESTAMP, "nonce": NONCE}
    response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=b"<xml><broken>",
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# OneBot 11 归一化（单元级）
# ---------------------------------------------------------------------------
PRIVATE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "self_id": 10001,
    "user_id": 10002,
    "message": [{"type": "text", "data": {"text": "国家奖学金怎么申请？"}}],
    "sender": {"nickname": "小明"},
    "message_id": 321,
}

GROUP_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "self_id": 10001,
    "user_id": 10002,
    "group_id": 88888,
    "message": [{"type": "text", "data": {"text": "请假流程是什么？"}}],
    "sender": {"card": "小明", "nickname": "小明"},
    "message_id": 322,
}


def test_onebot_normalize_private_and_group():
    from app.adapters.onebot import normalize_event

    private = normalize_event(
        PRIVATE_EVENT, channel_id="channel-1", config={"self_id": "10001"}
    )
    assert private is not None
    assert private.channel == "onebot11"
    assert private.chat_type == "private"
    assert private.session_key == "onebot11:10001:private:10002"
    assert private.user_label == "小明"

    group = normalize_event(GROUP_EVENT, channel_id="channel-1", config={"self_id": "10001"})
    assert group is not None
    assert group.chat_type == "group"
    assert group.group_id == "88888"
    assert group.session_key == "onebot11:10001:group:88888:10002"


def test_onebot_ignores_self_and_non_message_events():
    from app.adapters.onebot import normalize_event

    assert normalize_event(dict(PRIVATE_EVENT, user_id=10001), channel_id="c1") is None
    assert normalize_event(dict(PRIVATE_EVENT, post_type="message_sent"), channel_id="c1") is None
    assert normalize_event(dict(PRIVATE_EVENT, post_type="notice"), channel_id="c1") is None
    image_only = dict(PRIVATE_EVENT, message=[{"type": "image", "data": {"file": "a.jpg"}}])
    assert normalize_event(image_only, channel_id="c1") is None


def test_onebot_extracts_text_from_cq_codes():
    from app.adapters.onebot import normalize_event

    event = dict(PRIVATE_EVENT, message="[CQ:at,qq=10001] 请假流程是什么？")
    incoming = normalize_event(event, channel_id="c1", config={})
    assert incoming is not None
    assert "请假流程" in incoming.text
    assert "CQ:" not in incoming.text


def test_onebot_group_mention_only_gating():
    from app.adapters.onebot import normalize_event

    config = {"self_id": "10001"}
    assert normalize_event(GROUP_EVENT, channel_id="c1", config=config, require_mention=True) is None
    mentioned = dict(
        GROUP_EVENT,
        message=[
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " 请假流程是什么？"}},
        ],
    )
    incoming = normalize_event(mentioned, channel_id="c1", config=config, require_mention=True)
    assert incoming is not None and "请假流程" in incoming.text
    assert (
        normalize_event(PRIVATE_EVENT, channel_id="c1", config=config, require_mention=True)
        is not None
    )


# ---------------------------------------------------------------------------
# OneBot 反向 HTTP 事件入口
# ---------------------------------------------------------------------------
def test_onebot_http_event_accepts_and_logs(client, seeded_wiki, fake_llm, real_model):
    onebot = _create_onebot(client, access_token="evt-token")
    response = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "evt-token"},
        json=PRIVATE_EVENT,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    logs = client.get("/api/bots/logs").json()["items"]
    matched = [item for item in logs if item["channel"] == "onebot11"]
    assert matched, "OneBot 事件应写入消息日志"
    assert "国家奖学金" in matched[0]["message"]


def test_onebot_http_event_rejects_bad_token_and_wrong_channel(client):
    onebot = _create_onebot(client, access_token="evt-token")
    denied = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "wrong-token"},
        json=PRIVATE_EVENT,
    )
    assert denied.status_code == 403

    wechat = _create_wechat(client)
    wrong_type = client.post(f"/api/bots/onebot/{wechat['id']}/event", json=PRIVATE_EVENT)
    assert wrong_type.status_code == 400


def test_onebot_http_event_accepts_x_signature(client):
    onebot = _create_onebot(client, access_token="hmac-token")
    body = json.dumps(PRIVATE_EVENT, ensure_ascii=False).encode("utf-8")
    signature = hmac.new(b"hmac-token", body, hashlib.sha1).hexdigest()

    accepted = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": f"sha1={signature}"},
    )
    assert accepted.status_code == 200

    rejected = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "sha1=" + "0" * 40},
    )
    assert rejected.status_code == 403


def test_onebot_http_event_deduplicates_same_message_id(client, seeded_wiki):
    onebot = _create_onebot(client, access_token="evt-token")
    url = f"/api/bots/onebot/{onebot['id']}/event"
    for _ in range(2):
        response = client.post(url, params={"access_token": "evt-token"}, json=PRIVATE_EVENT)
        assert response.status_code == 200

    conversations = client.get("/api/chat/conversations", params={"channel": "onebot11"}).json()
    items = conversations["items"]
    assert len(items) == 1, "相同 message_id 的重复事件只应处理一次"
    history = client.get(f"/api/chat/conversations/{items[0]['id']}/messages").json()
    assert len(history["items"]) == 2


def test_onebot_ws_accepts_valid_token_and_heartbeat(client):
    onebot = _create_onebot(client, access_token="ws-token")
    try:
        with client.websocket_connect(
            f"/api/bots/onebot/{onebot['id']}/ws?access_token=ws-token"
        ) as websocket:
            websocket.send_text(
                json.dumps({"post_type": "meta_event", "meta_event_type": "heartbeat"})
            )
    except WebSocketDisconnect as exc:  # pragma: no cover
        pytest.fail(f"合法 token 的 OneBot WS 不应被拒绝：{exc}")


# ---------------------------------------------------------------------------
# 管理员转发即入库（docs/MOBILE-OPS.md §2.2）
# ---------------------------------------------------------------------------
def _simulate_onebot(client, channel: dict, message: str, user_id: str = "10002"):
    return client.post(
        f"/api/bots/channels/{channel['id']}/test",
        json={"message": message, "user_id": user_id, "user_name": "管理员"},
    )


def test_admin_link_triggers_import_and_reply(client, fake_importer):
    channel = _create_onebot(client, admin_user_ids="10002")
    response = _simulate_onebot(
        client, channel, "帮我收录 https://example.com/article?id=1", user_id="10002"
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert fake_importer.calls, "管理员发链接应触发 import_url"
    call = fake_importer.calls[0]
    assert call["url"] == "https://example.com/article?id=1"
    assert {"bot", "QQ"}.issubset(set(call["tags"]))
    assert "已收录" in data["reply"]
    assert "词条A" in data["reply"]
    assert data["citations"] == []
    # 命令/入库消息不进入问答会话
    assert data["conversation_id"] is None
    assert data["outbound"]["type"] == "onebot11"


def test_admin_import_command_triggers_import(client, fake_importer):
    channel = _create_onebot(client, admin_user_ids="10002")
    response = _simulate_onebot(
        client, channel, "#入库 https://example.com/command", user_id="10002"
    )
    assert response.status_code == 200
    assert fake_importer.calls
    assert fake_importer.calls[0]["url"] == "https://example.com/command"
    assert "已收录" in response.json()["reply"]


def test_non_admin_link_is_not_imported(client, fake_importer):
    channel = _create_onebot(client, admin_user_ids="10002")
    response = _simulate_onebot(
        client, channel, "https://example.com/article", user_id="99999"
    )
    assert response.status_code == 200
    assert fake_importer.calls == [], "非管理员发链接不得触发入库"
    assert "已收录" not in response.json()["reply"]


def test_admin_status_and_help_commands(client, fake_importer):
    channel = _create_onebot(client, admin_user_ids="10002")

    status = _simulate_onebot(client, channel, "#状态", user_id="10002").json()
    assert "运行状态" in status["reply"]
    assert "Wiki 词条" in status["reply"]
    assert "版本" in status["reply"]
    assert status["citations"] == []

    help_reply = _simulate_onebot(client, channel, "#帮助", user_id="10002").json()
    assert "#入库" in help_reply["reply"]
    assert "#状态" in help_reply["reply"]
    assert "#帮助" in help_reply["reply"]
    assert fake_importer.calls == []

    # 非管理员发命令：提示仅限管理员
    restricted = _simulate_onebot(client, channel, "#状态", user_id="99999").json()
    assert "仅限管理员" in restricted["reply"]


def test_admin_import_disabled_does_not_import(client, fake_importer):
    channel = _create_onebot(
        client, admin_user_ids="10002", import_enabled="false"
    )
    response = _simulate_onebot(
        client, channel, "https://example.com/article", user_id="10002"
    ).json()
    assert "已关闭" in response["reply"]
    assert fake_importer.calls == []


def test_wechat_admin_link_acks_fast_and_imports_in_background(client, fake_importer):
    import time as _time

    wechat = _create_wechat(client, admin_user_ids="openid-admin")
    params = {"signature": _signature(), "timestamp": TIMESTAMP, "nonce": NONCE}
    started = _time.perf_counter()
    response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=_wechat_xml("text", "https://example.com/wx-article", from_user="openid-admin"),
        headers={"Content-Type": "application/xml"},
    )
    elapsed = _time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 2.0, f"公众号入库必须先快速 ack（实测 {elapsed:.2f}s）"
    root = ET.fromstring(response.content)
    assert "已开始收录" in (root.findtext("Content") or "")
    # 后台线程最终完成入库
    assert fake_importer.event.wait(5.0), "后台线程未调用 import_url"
    assert fake_importer.calls[0]["url"] == "https://example.com/wx-article"
    assert {"bot", "微信"}.issubset(set(fake_importer.calls[0]["tags"]))


def test_wechat_non_admin_link_falls_back_to_qa(client, fake_importer):
    wechat = _create_wechat(client, admin_user_ids="openid-admin")
    params = {"signature": _signature(), "timestamp": TIMESTAMP, "nonce": NONCE}
    response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=_wechat_xml("text", "https://example.com/non-admin"),
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 200
    root = ET.fromstring(response.content)
    assert "已开始收录" not in (root.findtext("Content") or "")
    assert fake_importer.calls == []


def test_channel_test_endpoint_for_onebot_and_wechat_channels(client):
    """回归：非 console 通道的 /test 也必须可用（历史上 incoming_for_channel 缺 import 导致 500）。"""
    onebot = _create_onebot(client)
    onebot_response = client.post(
        f"/api/bots/channels/{onebot['id']}/test",
        json={"message": "你好，国家奖学金怎么申请？", "user_id": "10002"},
    )
    assert onebot_response.status_code == 200, onebot_response.text
    onebot_data = onebot_response.json()
    assert onebot_data["channel"] == "onebot11"
    assert onebot_data["reply"]
    assert onebot_data["outbound"]["action"] == "send_private_msg"
    assert onebot_data["outbound"]["payload"]["user_id"] == 10002

    wechat = _create_wechat(client)
    wechat_response = client.post(
        f"/api/bots/channels/{wechat['id']}/test",
        json={"message": "请假流程是什么？", "user_id": "openid-001"},
    )
    assert wechat_response.status_code == 200, wechat_response.text
    wechat_data = wechat_response.json()
    assert wechat_data["channel"] == "wechat_mp"
    assert wechat_data["reply"]
    assert str(wechat_data["outbound"]["xml"]).startswith("<xml>")


# ---------------------------------------------------------------------------
# 安全：凭据脱敏 / PUT 回填语义 / webhook Token 策略 / 微信时间窗 / self_id
# ---------------------------------------------------------------------------
def test_channel_config_credentials_are_masked(client):
    onebot = _create_onebot(client, access_token="evt-token", admin_user_ids="10002")
    assert onebot["config"]["access_token"] == "****oken"
    assert onebot["has_access_token"] is True
    assert onebot["config"]["admin_user_ids"] == "***（1 个管理员）"
    assert "evt-token" not in json.dumps(onebot, ensure_ascii=False)
    assert "10002" not in json.dumps(onebot["config"], ensure_ascii=False)

    wechat = _create_wechat(client, admin_user_ids="openid-1,openid-2")
    assert wechat["config"]["token"] == "****2026"
    assert wechat["has_access_token"] is True
    assert wechat["config"]["admin_user_ids"] == "***（2 个管理员）"
    assert WECHAT_TOKEN not in json.dumps(wechat, ensure_ascii=False)

    # 列表接口同样脱敏；console 无凭据
    listing = _channels(client)
    for item in listing:
        if item["type"] == "onebot11":
            assert item["config"]["access_token"] == "****oken"
        if item["type"] == "wechat_mp":
            assert item["config"]["token"] == "****2026"
        if item["type"] == "console":
            assert item["has_access_token"] is False


def test_channel_put_empty_or_masked_credentials_keep_existing(client):
    onebot = _create_onebot(client, access_token="evt-token", admin_user_ids="10002")

    # 空串 / 脱敏占位 → 不修改（仍可继续用原 token 调用 webhook）
    for placeholder in ("", "****oken"):
        updated = client.put(
            f"/api/bots/channels/{onebot['id']}",
            json={"config": {"api_base": "http://127.0.0.1:9", "access_token": placeholder}},
        )
        assert updated.status_code == 200
        assert updated.json()["has_access_token"] is True
        event = client.post(
            f"/api/bots/onebot/{onebot['id']}/event",
            params={"access_token": "evt-token"},
            json={**PRIVATE_EVENT, "message_id": 9001},
        )
        assert event.status_code == 200, "空串/脱敏占位不应覆盖已有 token"

    # admin_user_ids：脱敏占位 → 不修改；空串 → 清空；新值 → 替换
    kept = client.put(
        f"/api/bots/channels/{onebot['id']}",
        json={"config": {"admin_user_ids": "***（1 个管理员）"}},
    ).json()
    assert kept["config"]["admin_user_ids"] == "***（1 个管理员）"
    cleared = client.put(
        f"/api/bots/channels/{onebot['id']}", json={"config": {"admin_user_ids": ""}}
    ).json()
    assert cleared["config"]["admin_user_ids"] == ""
    replaced = client.put(
        f"/api/bots/channels/{onebot['id']}", json={"config": {"admin_user_ids": ["1", "2"]}}
    ).json()
    assert replaced["config"]["admin_user_ids"] == "***（2 个管理员）"


def test_onebot_created_without_token_gets_random_secret(client):
    onebot = _create_onebot(client, access_token="")
    assert onebot["has_access_token"] is True
    masked = onebot["config"]["access_token"]
    assert masked.startswith("****") and len(masked) == 8  # **** + 4 位
    assert "evt-token" not in masked
    # 自动生成时给出一次性提示（明文不可回读）
    assert onebot.get("token_generated") is True
    assert onebot.get("token_hint")
    assert "token" in str(onebot["token_hint"]).lower() or "access_token" in str(
        onebot["token_hint"]
    )
    # 无法得知随机 token：缺失/错误 token 均拒绝
    assert client.post(
        f"/api/bots/onebot/{onebot['id']}/event", json=PRIVATE_EVENT
    ).status_code == 403
    assert client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "guess-token"},
        json=PRIVATE_EVENT,
    ).status_code == 403


def test_onebot_webhook_rejects_empty_token(client):
    onebot = _create_onebot(client)
    _set_channel_config(
        onebot["id"], {"api_base": "http://127.0.0.1:9", "self_id": "10001"}
    )
    response = client.post(f"/api/bots/onebot/{onebot['id']}/event", json=PRIVATE_EVENT)
    assert response.status_code == 403
    assert "access_token" in response.json()["detail"]
    # 带任意 token 也拒绝（通道配置本身为空）
    assert client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "whatever"},
        json=PRIVATE_EVENT,
    ).status_code == 403


def test_onebot_event_self_id_mismatch_is_ignored(client):
    onebot = _create_onebot(client, access_token="evt-token")  # self_id=10001
    before = len(client.get("/api/bots/logs").json()["items"])
    response = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "evt-token"},
        json={**PRIVATE_EVENT, "self_id": 99999, "message_id": 9101},
    )
    # HTTP 仍返回 ok，但不处理、不落日志
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    after = len(client.get("/api/bots/logs").json()["items"])
    assert after == before, "self_id 不一致的事件应被忽略"

    matched = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "evt-token"},
        json={**PRIVATE_EVENT, "message_id": 9102},
    )
    assert matched.status_code == 200
    assert len(client.get("/api/bots/logs").json()["items"]) == before + 1


def test_wechat_rejects_public_default_token(client):
    wechat = _create_wechat(client, token="npu-assistant")
    timestamp = _now_ts()
    params = {
        "signature": _signature(token="npu-assistant", timestamp=timestamp),
        "timestamp": timestamp,
        "nonce": NONCE,
        "echostr": "echo-12345",
    }
    response = client.get(f"/api/bots/wechat/{wechat['id']}", params=params)
    assert response.status_code == 403
    assert "安全" in response.json()["detail"] or "npu-assistant" in response.json()["detail"]


def test_wechat_rejects_stale_timestamp(client):
    wechat = _create_wechat(client)
    stale = str(int(time.time()) - 3600)
    params = {"signature": _signature(timestamp=stale), "timestamp": stale, "nonce": NONCE}
    response = client.get(f"/api/bots/wechat/{wechat['id']}", params=params)
    assert response.status_code == 403
    assert "时间戳" in response.json()["detail"]
    message = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=_wechat_xml("text", "请假流程是什么？"),
        headers={"Content-Type": "application/xml"},
    )
    assert message.status_code == 403


def test_insecure_env_allows_empty_token_only_when_enabled(client, monkeypatch):
    # 默认拒绝
    onebot = _create_onebot(client)
    _set_channel_config(
        onebot["id"], {"api_base": "http://127.0.0.1:9", "self_id": "10001"}
    )
    assert client.post(
        f"/api/bots/onebot/{onebot['id']}/event", json=PRIVATE_EVENT
    ).status_code == 403

    # 显式开启本地调试开关后放行空 Token（仅限本机调试）
    monkeypatch.setenv("NPU_BOT_ALLOW_INSECURE", "1")
    relaxed = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        json={**PRIVATE_EVENT, "message_id": 9201},
    )
    assert relaxed.status_code == 200 and relaxed.json() == {"status": "ok"}

    wechat = _create_wechat(client, token="npu-assistant")
    timestamp = _now_ts()
    params = {
        "signature": _signature(token="npu-assistant", timestamp=timestamp),
        "timestamp": timestamp,
        "nonce": NONCE,
        "echostr": "echo-insecure",
    }
    verified = client.get(f"/api/bots/wechat/{wechat['id']}", params=params)
    assert verified.status_code == 200 and verified.text == "echo-insecure"



# ---------------------------------------------------------------------------
# 安全复审补充：日志鉴权 / api_base 脱敏 / 微信 nonce 防重放 / webhook body 上限
# ---------------------------------------------------------------------------
def test_bots_logs_requires_admin_token_when_configured(client, monkeypatch):
    from app import config

    wechat = _create_wechat(client)  # 令牌开启前创建，避免主中间件拦截
    assert client.get("/api/bots/logs").status_code == 200

    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-secret-token")
    headers = {"X-Admin-Token": "mobile-secret-token"}
    assert client.get("/api/bots/logs").status_code == 401
    assert client.get("/api/bots/logs", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/bots/logs", headers=headers).status_code == 200

    # 机器人 webhook 使用自身 token/签名，不受管理令牌中间件影响
    ts = _now_ts()
    params = {"signature": _signature(timestamp=ts, nonce="logs-nonce-1"), "timestamp": ts, "nonce": "logs-nonce-1"}
    verified = client.get(
        f"/api/bots/wechat/{wechat['id']}", params={**params, "echostr": "echo-1"}
    )
    assert verified.status_code == 200 and verified.text == "echo-1"


def _create_onebot_with_api_base(client, api_base: str) -> dict:
    response = client.post(
        "/api/bots/channels",
        json={
            "type": "onebot11",
            "name": "api_base 脱敏测试",
            "config": {
                "api_base": api_base,
                "access_token": "evt-token",
                "self_id": "10001",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_channel_api_base_masked_and_put_keeps_original(client):
    from app import db

    original = "https://user:pass@example.com:8443/base?token=SECRET#frag"
    channel = _create_onebot_with_api_base(client, original)
    masked = "https://example.com:8443/base"
    assert channel["config"]["api_base"] == masked
    serialized = json.dumps(channel, ensure_ascii=False)
    assert "pass@" not in serialized and "SECRET" not in serialized

    # outbound（控制台展示）中的 url 同样脱敏
    tested = client.post(
        f"/api/bots/channels/{channel['id']}/test",
        json={"message": "你好", "user_id": "10002"},
    )
    assert tested.status_code == 200
    outbound_url = tested.json()["outbound"]["url"]
    assert outbound_url == f"{masked}/send_private_msg"
    assert "user:pass" not in outbound_url and "SECRET" not in outbound_url

    # PUT 回填脱敏值 → 不修改数据库中的原始 api_base（保留 query/userinfo）
    updated = client.put(
        f"/api/bots/channels/{channel['id']}", json={"config": {"api_base": masked}}
    )
    assert updated.status_code == 200
    assert updated.json()["config"]["api_base"] == masked
    row = db.query_one("SELECT config FROM bot_channels WHERE id = ?", (channel["id"],))
    stored = json.loads(row["config"])
    assert stored["api_base"] == original, "回填脱敏值不应覆盖原始 api_base"


def test_wechat_nonce_replay_rejected(client):
    wechat = _create_wechat(client)
    timestamp = _now_ts()

    def params(nonce: str, token: str = WECHAT_TOKEN):
        return {
            "signature": _signature(token=token, timestamp=timestamp, nonce=nonce),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": "echo-nonce",
        }

    first = client.get(f"/api/bots/wechat/{wechat['id']}", params=params("nonce-replay-1"))
    assert first.status_code == 200
    replay = client.get(f"/api/bots/wechat/{wechat['id']}", params=params("nonce-replay-1"))
    assert replay.status_code == 403
    assert "nonce" in replay.json()["detail"]
    fresh = client.get(f"/api/bots/wechat/{wechat['id']}", params=params("nonce-replay-2"))
    assert fresh.status_code == 200

    # POST 文本消息同样防重放
    post_params = {
        "signature": _signature(timestamp=timestamp, nonce="nonce-post-1"),
        "timestamp": timestamp,
        "nonce": "nonce-post-1",
    }
    payload = _wechat_xml("text", "请假流程是什么？")
    assert (
        client.post(
            f"/api/bots/wechat/{wechat['id']}",
            params=post_params,
            content=payload,
            headers={"Content-Type": "application/xml"},
        ).status_code
        == 200
    )
    replay_post = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=post_params,
        content=payload,
        headers={"Content-Type": "application/xml"},
    )
    assert replay_post.status_code == 403
    assert "nonce" in replay_post.json()["detail"]

    # 空 nonce 不参与重放判定（签名函数本身要求 nonce 非空，这里直接单测登记函数）
    from app.routers.bots import _register_wechat_nonce

    assert _register_wechat_nonce(wechat["id"], "") is True
    assert _register_wechat_nonce(wechat["id"], "") is True


def test_wechat_nonce_failed_signature_does_not_consume(client):
    wechat = _create_wechat(client)
    timestamp = _now_ts()
    nonce = "nonce-badsig-1"
    bad = {
        "signature": "0" * 40,
        "timestamp": timestamp,
        "nonce": nonce,
        "echostr": "echo-bad",
    }
    assert client.get(f"/api/bots/wechat/{wechat['id']}", params=bad).status_code == 403
    # 签名失败不消费 nonce：同一 nonce 的正确请求仍可成功
    good = {
        "signature": _signature(timestamp=timestamp, nonce=nonce),
        "timestamp": timestamp,
        "nonce": nonce,
        "echostr": "echo-good",
    }
    assert client.get(f"/api/bots/wechat/{wechat['id']}", params=good).status_code == 200


def test_webhook_body_size_limit(client):
    onebot = _create_onebot(client, access_token="evt-token")
    oversized = b'{"x":"' + b"a" * (2 * 1024 * 1024 + 10) + b'"}'
    response = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        params={"access_token": "evt-token"},
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413

    wechat = _create_wechat(client)
    timestamp = _now_ts()
    nonce = "nonce-body-limit"
    params = {
        "signature": _signature(timestamp=timestamp, nonce=nonce),
        "timestamp": timestamp,
        "nonce": nonce,
    }
    huge_xml = b"<xml>" + b"a" * (2 * 1024 * 1024 + 10) + b"</xml>"
    wechat_response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=huge_xml,
        headers={"Content-Type": "application/xml"},
    )
    assert wechat_response.status_code == 413
    assert "过大" in wechat_response.json()["detail"]

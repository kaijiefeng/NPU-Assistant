"""机器人网关测试：通道 CRUD、Webhook 地址、控制台模拟、公众号签名/XML、OneBot 归一化与事件入口（契约 §5.5）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import xml.etree.ElementTree as ET

import pytest
from fastapi import WebSocketDisconnect

WECHAT_TOKEN = "npu-assistant"
TIMESTAMP = "1700000000"
NONCE = "npu-nonce-001"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _channels(client) -> list[dict]:
    return client.get("/api/bots/channels").json()["items"]


def _find_channel(client, channel_type: str) -> dict:
    return next(item for item in _channels(client) if item["type"] == channel_type)


def _create_onebot(client, *, access_token: str = "evt-token") -> dict:
    response = client.post(
        "/api/bots/channels",
        json={
            "type": "onebot11",
            "name": "测试 QQ 通道",
            "config": {
                "api_base": "http://127.0.0.1:9",
                "access_token": access_token,
                "self_id": "10001",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_wechat(client, *, token: str = WECHAT_TOKEN) -> dict:
    response = client.post(
        "/api/bots/channels",
        json={"type": "wechat_mp", "name": "测试公众号", "config": {"token": token}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _signature(token: str = WECHAT_TOKEN, timestamp: str = TIMESTAMP,
               nonce: str = NONCE) -> str:
    raw = "".join(sorted([token, timestamp, nonce]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _wechat_xml(msg_type: str = "text", content: str = "请假流程是什么？") -> bytes:
    return f"""<xml>
  <ToUserName><![CDATA[gh_test_account]]></ToUserName>
  <FromUserName><![CDATA[openid-001]]></FromUserName>
  <CreateTime>{TIMESTAMP}</CreateTime>
  <MsgType><![CDATA[{msg_type}]]></MsgType>
  <Content><![CDATA[{content}]]></Content>
  <MsgId>1234567890</MsgId>
</xml>""".encode("utf-8")


# ---------------------------------------------------------------------------
# 通道 CRUD / webhook 地址
# ---------------------------------------------------------------------------
def test_channels_default_console_and_webhook_urls(client):
    channels = _channels(client)
    console = _find_channel(client, "console")
    assert console["enabled"] is True
    assert console["webhook_urls"] == {}

    onebot = _create_onebot(client)
    assert onebot["status"] == "offline"
    assert onebot["config"]["self_id"] == "10001"
    assert onebot["webhook_urls"]["ws"].endswith(f"/api/bots/onebot/{onebot['id']}/ws")
    assert onebot["webhook_urls"]["http"].endswith(f"/api/bots/onebot/{onebot['id']}/event")

    wechat = _create_wechat(client)
    assert wechat["webhook_urls"]["callback"] == f"/api/bots/wechat/{wechat['id']}"

    # 非法类型 400；console 不可删除 400；普通通道可删除
    assert (
        client.post("/api/bots/channels", json={"type": "telegram", "name": "x"}).status_code
        == 400
    )
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
    # 未提交的字段应保留（合并更新）
    assert data["config"]["access_token"] == "evt-token"
    assert data["config"]["self_id"] == "10001"


# ---------------------------------------------------------------------------
# 控制台通道模拟
# ---------------------------------------------------------------------------
def test_console_simulated_chat_end_to_end(client, seeded):
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
    assert data["citations"]
    assert isinstance(data["latency_ms"], int)
    assert data["outbound"]["type"] == "console"
    assert data["outbound"]["text"].strip()

    # 消息日志应记录这条非 web 渠道对话
    logs = client.get("/api/bots/logs").json()["items"]
    matched = [item for item in logs if item["channel"] == "console"]
    assert matched
    assert matched[0]["reply"].strip()
    assert "国家奖学金" in matched[0]["message"]

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

    bad_params = {**params, "signature": "0" * 40}
    assert client.get(f"/api/bots/wechat/{wechat['id']}", params=bad_params).status_code == 403


def test_wechat_text_message_replies_with_xml(client, seeded):
    wechat = _create_wechat(client)
    params = {"signature": _signature(), "timestamp": TIMESTAMP, "nonce": NONCE}
    response = client.post(
        f"/api/bots/wechat/{wechat['id']}",
        params=params,
        content=_wechat_xml("text", "国家奖学金怎么申请？"),
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 200, response.text
    # 微信官方文档要求 text/xml；部分实现返回 application/xml，两者都是合法 XML
    assert "xml" in response.headers["content-type"].lower()

    root = ET.fromstring(response.content)
    content = root.findtext("Content") or ""
    assert content.strip()
    assert root.findtext("ToUserName") == "openid-001"  # 与来消息 From/To 互换
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
    content = root.findtext("Content") or ""
    assert "文字" in content


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
    assert private.text == "国家奖学金怎么申请？"
    assert private.session_key == "onebot11:10001:private:10002"
    assert private.user_label == "小明"

    group = normalize_event(
        GROUP_EVENT, channel_id="channel-1", config={"self_id": "10001"}
    )
    assert group is not None
    assert group.chat_type == "group"
    assert group.group_id == "88888"
    assert group.session_key == "onebot11:10001:group:88888:10002"


def test_onebot_ignores_self_and_non_message_events():
    from app.adapters.onebot import normalize_event

    self_message = dict(PRIVATE_EVENT, user_id=10001)
    assert normalize_event(self_message, channel_id="c1", config={}) is None
    assert normalize_event(dict(PRIVATE_EVENT, post_type="message_sent"), channel_id="c1") is None
    assert normalize_event(dict(PRIVATE_EVENT, post_type="notice"), channel_id="c1") is None
    # 纯图片消息：无可读文本，忽略
    image_only = dict(
        PRIVATE_EVENT, message=[{"type": "image", "data": {"file": "a.jpg"}}]
    )
    assert normalize_event(image_only, channel_id="c1") is None


def test_onebot_extracts_text_from_cq_codes():
    from app.adapters.onebot import normalize_event

    event = dict(PRIVATE_EVENT, message="[CQ:at,qq=10001] 请假流程是什么？")
    incoming = normalize_event(event, channel_id="c1", config={})
    assert incoming is not None
    assert "请假流程" in incoming.text
    assert "CQ:" not in incoming.text


# ---------------------------------------------------------------------------
# OneBot 反向 HTTP 事件入口
# ---------------------------------------------------------------------------
def test_onebot_http_event_accepts_and_logs(client, seeded):
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
    wrong_type = client.post(
        f"/api/bots/onebot/{wechat['id']}/event",
        json=PRIVATE_EVENT,
    )
    assert wrong_type.status_code == 400


def test_onebot_http_event_accepts_x_signature(client):
    """NapCat「HTTP 客户端」使用 X-Signature: sha1=HMAC-SHA1(body, token) 鉴权。"""
    onebot = _create_onebot(client, access_token="hmac-token")
    body = json.dumps(PRIVATE_EVENT, ensure_ascii=False).encode("utf-8")
    signature = hmac.new(b"hmac-token", body, hashlib.sha1).hexdigest()

    accepted = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": f"sha1={signature}"},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"status": "ok"}

    rejected = client.post(
        f"/api/bots/onebot/{onebot['id']}/event",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "sha1=" + "0" * 40},
    )
    assert rejected.status_code == 403


def test_onebot_http_event_deduplicates_same_message_id(client, seeded):
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


def test_onebot_group_mention_only_gating():
    """群聊默认仅 @ 机器人时处理；私聊不受限制。"""
    from app.adapters.onebot import normalize_event

    config = {"self_id": "10001"}
    assert normalize_event(GROUP_EVENT, channel_id="c1", config=config, require_mention=True) is None

    mentioned_event = dict(
        GROUP_EVENT,
        message=[
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " 请假流程是什么？"}},
        ],
    )
    incoming = normalize_event(
        mentioned_event, channel_id="c1", config=config, require_mention=True
    )
    assert incoming is not None
    assert "请假流程" in incoming.text

    assert (
        normalize_event(PRIVATE_EVENT, channel_id="c1", config=config, require_mention=True)
        is not None
    )
    # 未开启 require_mention 时群聊普通消息照常处理
    assert (
        normalize_event(GROUP_EVENT, channel_id="c1", config=config, require_mention=False)
        is not None
    )


def test_onebot_ws_accepts_valid_token_and_heartbeat(client):
    onebot = _create_onebot(client, access_token="ws-token")
    try:
        with client.websocket_connect(
            f"/api/bots/onebot/{onebot['id']}/ws?access_token=ws-token"
        ) as websocket:
            websocket.send_text(
                json.dumps({"post_type": "meta_event", "meta_event_type": "heartbeat"})
            )
    except WebSocketDisconnect as exc:  # pragma: no cover - 仅在连接被拒绝时触发
        pytest.fail(f"合法 token 的 OneBot WS 不应被拒绝：{exc}")

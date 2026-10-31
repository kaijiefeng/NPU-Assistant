"""QQ 开放平台官方机器人：通道 CRUD、凭据脱敏、事件归一化与被动回复报文。"""
from __future__ import annotations

import json

SECRET = "qq-secret-2026"


def _create_qq(client, *, app_id: str = "102000001", app_secret: str = SECRET,
               admin_user_ids: str = "", sandbox: str = "false") -> dict:
    response = client.post(
        "/api/bots/channels",
        json={
            "type": "qq_official",
            "name": "测试 QQ 官方机器人",
            "config": {
                "app_id": app_id,
                "app_secret": app_secret,
                "sandbox": sandbox,
                "admin_user_ids": admin_user_ids,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 适配器（单元级）
# ---------------------------------------------------------------------------
C2C_EVENT = {
    "id": "evt-c2c-1",
    "content": " 国家奖学金怎么申请？",
    "author": {"user_openid": "openid-001"},
}
GROUP_EVENT = {
    "id": "evt-group-1",
    "content": "<@!bot-id> 请假流程是什么？",
    "group_openid": "group-001",
    "author": {"member_openid": "member-001"},
}


def test_qq_official_normalize_c2c_and_group():
    from app.adapters.qq_official import normalize_event

    c2c = normalize_event("C2C_MESSAGE_CREATE", C2C_EVENT, channel_id="channel-1")
    assert c2c is not None
    assert c2c.channel == "qq_official"
    assert c2c.chat_type == "private"
    assert c2c.user_id == "openid-001"
    assert c2c.message_id == "evt-c2c-1"
    assert c2c.text == "国家奖学金怎么申请？"
    assert c2c.session_key == "qq_official:channel-1:private:openid-001"

    group = normalize_event("GROUP_AT_MESSAGE_CREATE", GROUP_EVENT, channel_id="channel-1")
    assert group is not None
    assert group.chat_type == "group"
    assert group.group_id == "group-001"
    assert group.user_id == "member-001"
    assert group.text == "请假流程是什么？", "群聊 @ 前缀应被去掉"
    assert group.session_key == "qq_official:channel-1:group:group-001:member-001"


def test_qq_official_ignores_unsupported_or_empty_events():
    from app.adapters.qq_official import normalize_event

    assert normalize_event("AT_MESSAGE_CREATE", C2C_EVENT, channel_id="c1") is None
    assert normalize_event("C2C_MESSAGE_CREATE", {"content": "hi"}, channel_id="c1") is None
    assert (
        normalize_event(
            "C2C_MESSAGE_CREATE",
            {"id": "x", "content": "  ", "author": {"user_openid": "u1"}},
            channel_id="c1",
        )
        is None
    )
    assert (
        normalize_event(
            "GROUP_AT_MESSAGE_CREATE",
            {**GROUP_EVENT, "group_openid": ""},
            channel_id="c1",
        )
        is None
    )


def test_qq_official_outbound_uses_passive_reply():
    from app.adapters.qq_official import build_outbound

    private = build_outbound(
        {"sandbox": "false"},
        chat_type="private",
        user_id="openid-001",
        group_id="",
        text="你好",
        msg_id="evt-1",
    )
    assert private["type"] == "qq_official"
    assert private["action"] == "send_c2c_message"
    assert private["url"] == "https://api.sgroup.qq.com/v2/users/openid-001/messages"
    assert private["payload"] == {
        "content": "你好",
        "msg_type": 0,
        "msg_id": "evt-1",
        "msg_seq": 1,
    }

    group = build_outbound(
        {"sandbox": "true"},
        chat_type="group",
        user_id="member-1",
        group_id="group-1",
        text="你好",
        msg_id="evt-2",
    )
    assert group["action"] == "send_group_message"
    assert group["url"] == "https://sandbox.api.sgroup.qq.com/v2/groups/group-1/messages"


def test_qq_official_reply_text_is_truncated():
    from app.adapters.qq_official import MAX_REPLY_CHARS, build_payload

    payload = build_payload("长" * (MAX_REPLY_CHARS + 50), msg_id="evt-1")
    assert len(payload["content"]) <= MAX_REPLY_CHARS + 1
    assert payload["content"].endswith("……")


# ---------------------------------------------------------------------------
# 通道 CRUD / 凭据脱敏 / 控制台模拟
# ---------------------------------------------------------------------------
def test_qq_official_channel_crud_masks_secret(client):
    channel = _create_qq(client)
    assert channel["type"] == "qq_official"
    assert channel["has_access_token"] is True
    assert channel["config"]["app_id"] == "102000001"
    assert channel["config"]["app_secret"] == "****2026"
    assert channel["webhook_urls"] == {}
    assert SECRET not in json.dumps(channel, ensure_ascii=False)

    listing = client.get("/api/bots/channels").json()["items"]
    qq = next(item for item in listing if item["type"] == "qq_official")
    assert qq["config"]["app_secret"] == "****2026"

    # 空串 / 脱敏占位表示不修改
    for placeholder in ("", "****2026"):
        updated = client.put(
            f"/api/bots/channels/{channel['id']}",
            json={"config": {"app_secret": placeholder}},
        )
        assert updated.status_code == 200
        assert updated.json()["has_access_token"] is True

    assert client.delete(f"/api/bots/channels/{channel['id']}").json() == {"ok": True}


def test_qq_official_channel_simulated_chat(client):
    channel = _create_qq(client)
    response = client.post(
        f"/api/bots/channels/{channel['id']}/test",
        json={"message": "你好", "user_id": "openid-001"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["channel"] == "qq_official"
    assert data["reply"]
    assert data["outbound"]["type"] == "qq_official"
    assert data["outbound"]["url"].endswith("/v2/users/openid-001/messages")
    assert data["outbound"]["payload"]["content"]


def test_qq_official_admin_link_triggers_import(client, fake_importer):
    channel = _create_qq(client, admin_user_ids="openid-admin")
    response = client.post(
        f"/api/bots/channels/{channel['id']}/test",
        json={
            "message": "帮我收录 https://example.com/qq-official",
            "user_id": "openid-admin",
            "user_name": "管理员",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_importer.calls
    assert fake_importer.calls[0]["url"] == "https://example.com/qq-official"
    assert {"bot", "QQ"}.issubset(set(fake_importer.calls[0]["tags"]))
    assert data["outbound"]["type"] == "qq_official"

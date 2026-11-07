"""机器人网关测试（LLM Wiki）：通道 CRUD、凭据脱敏、Webhook 安全、控制台双层引用、公众号签名/XML、QQ 官方通道。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import xml.etree.ElementTree as ET

import pytest

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


def _create_qq(client, *, access_token: str = "", admin_user_ids: str = "",
               import_enabled: str = "true") -> dict:
    _ = access_token
    response = client.post(
        "/api/bots/channels",
        json={
            "type": "qq_official",
            "name": "测试 QQ 官方通道",
            "config": {
                "app_id": "102000001",
                "app_secret": "test-secret-1234",
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

    qq = _create_qq(client)
    assert qq["status"] == "offline"
    assert qq["config"]["app_id"] == "102000001"
    assert qq["webhook_urls"] == {}  # QQ 官方主动连接，无需 Webhook

    wechat = _create_wechat(client)
    assert wechat["webhook_urls"]["callback"] == f"/api/bots/wechat/{wechat['id']}"

    assert (
        client.post("/api/bots/channels", json={"type": "telegram", "name": "x"}).status_code
        == 400
    )
    # OneBot 11 已移除，不能再创建
    assert (
        client.post("/api/bots/channels", json={"type": "onebot11", "name": "x"}).status_code
        == 400
    )
    console = _find_channel(client, "console")
    assert client.delete(f"/api/bots/channels/{console['id']}").status_code == 400
    assert client.delete(f"/api/bots/channels/{qq['id']}").json() == {"ok": True}
    assert client.put("/api/bots/channels/not-exist", json={"name": "x"}).status_code == 404


def test_channel_partial_update_keeps_config(client):
    qq = _create_qq(client)
    updated = client.put(
        f"/api/bots/channels/{qq['id']}",
        json={"name": "改名后的 QQ 官方通道", "config": {"sandbox": "true"}},
    )
    assert updated.status_code == 200
    data = updated.json()
    assert data["name"] == "改名后的 QQ 官方通道"
    assert data["config"]["sandbox"] == "true"
    # 安全：凭据不再回显明文，只保留脱敏值 + has_access_token
    assert data["config"]["app_secret"] == "****1234"
    assert data["has_access_token"] is True
    assert data["config"]["app_id"] == "102000001"


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
    # 回答末尾附带源文件下载链接（绝对地址，QQ/微信可直接点击）
    assert "相关文件" in data["reply"]
    assert "/api/knowledge/documents/" in data["reply"]
    assert "/download" in data["reply"]

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
# 管理员转发即入库（docs/MOBILE-OPS.md §2.2）
# ---------------------------------------------------------------------------
def _simulate_channel(client, channel: dict, message: str, user_id: str = "10002"):
    return client.post(
        f"/api/bots/channels/{channel['id']}/test",
        json={"message": message, "user_id": user_id, "user_name": "管理员"},
    )


def test_admin_link_triggers_import_and_reply(client, fake_importer):
    channel = _create_qq(client, admin_user_ids="10002")
    response = _simulate_channel(
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
    assert data["outbound"]["type"] == "qq_official"


def test_admin_import_command_triggers_import(client, fake_importer):
    channel = _create_qq(client, admin_user_ids="10002")
    response = _simulate_channel(
        client, channel, "#入库 https://example.com/command", user_id="10002"
    )
    assert response.status_code == 200
    assert fake_importer.calls
    assert fake_importer.calls[0]["url"] == "https://example.com/command"
    assert "已收录" in response.json()["reply"]


def test_non_admin_link_is_not_imported(client, fake_importer):
    channel = _create_qq(client, admin_user_ids="10002")
    response = _simulate_channel(
        client, channel, "https://example.com/article", user_id="99999"
    )
    assert response.status_code == 200
    assert fake_importer.calls == [], "非管理员发链接不得触发入库"
    assert "已收录" not in response.json()["reply"]


def test_admin_status_and_help_commands(client, fake_importer):
    channel = _create_qq(client, admin_user_ids="10002")

    status = _simulate_channel(client, channel, "#状态", user_id="10002").json()
    assert "运行状态" in status["reply"]
    assert "Wiki 词条" in status["reply"]
    assert "版本" in status["reply"]
    assert status["citations"] == []

    help_reply = _simulate_channel(client, channel, "#帮助", user_id="10002").json()
    assert "#入库" in help_reply["reply"]
    assert "#状态" in help_reply["reply"]
    assert "#帮助" in help_reply["reply"]
    assert fake_importer.calls == []

    # 非管理员发命令：提示仅限管理员
    restricted = _simulate_channel(client, channel, "#状态", user_id="99999").json()
    assert "仅限管理员" in restricted["reply"]


def test_admin_import_disabled_does_not_import(client, fake_importer):
    channel = _create_qq(
        client, admin_user_ids="10002", import_enabled="false"
    )
    response = _simulate_channel(
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


def test_channel_test_endpoint_for_qq_and_wechat_channels(client):
    """回归：非 console 通道的 /test 也必须可用（历史上 incoming_for_channel 缺 import 导致 500）。"""
    qq = _create_qq(client)
    qq_response = client.post(
        f"/api/bots/channels/{qq['id']}/test",
        json={"message": "你好，国家奖学金怎么申请？", "user_id": "openid-user"},
    )
    assert qq_response.status_code == 200, qq_response.text
    qq_data = qq_response.json()
    assert qq_data["channel"] == "qq_official"
    assert qq_data["reply"]
    assert qq_data["outbound"]["action"] == "send_c2c_message"

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
# 安全：凭据脱敏 / PUT 回填语义 / 微信时间窗
# ---------------------------------------------------------------------------
def test_channel_config_credentials_are_masked(client):
    qq = _create_qq(client, admin_user_ids="10002")
    assert qq["config"]["app_secret"] == "****1234"
    assert qq["has_access_token"] is True
    assert qq["config"]["admin_user_ids"] == "***（1 个管理员）"
    assert "test-secret-1234" not in json.dumps(qq, ensure_ascii=False)
    assert "10002" not in json.dumps(qq["config"], ensure_ascii=False)

    wechat = _create_wechat(client, admin_user_ids="openid-1,openid-2")
    assert wechat["config"]["token"] == "****2026"
    assert wechat["has_access_token"] is True
    assert wechat["config"]["admin_user_ids"] == "***（2 个管理员）"
    assert WECHAT_TOKEN not in json.dumps(wechat, ensure_ascii=False)

    # 列表接口同样脱敏；console 无凭据
    listing = _channels(client)
    for item in listing:
        if item["type"] == "qq_official":
            assert item["config"]["app_secret"] == "****1234"
        if item["type"] == "wechat_mp":
            assert item["config"]["token"] == "****2026"
        if item["type"] == "console":
            assert item["has_access_token"] is False


def test_channel_put_empty_or_masked_credentials_keep_existing(client):
    qq = _create_qq(client, admin_user_ids="10002")

    # 空串 / 脱敏占位 → 不修改
    for placeholder in ("", "****1234"):
        updated = client.put(
            f"/api/bots/channels/{qq['id']}",
            json={"config": {"app_secret": placeholder}},
        )
        assert updated.status_code == 200
        assert updated.json()["has_access_token"] is True
        assert updated.json()["config"]["app_secret"] == "****1234"

    # admin_user_ids：脱敏占位 → 不修改；空串 → 清空；新值 → 替换
    kept = client.put(
        f"/api/bots/channels/{qq['id']}",
        json={"config": {"admin_user_ids": "***（1 个管理员）"}},
    ).json()
    assert kept["config"]["admin_user_ids"] == "***（1 个管理员）"
    cleared = client.put(
        f"/api/bots/channels/{qq['id']}", json={"config": {"admin_user_ids": ""}}
    ).json()
    assert cleared["config"]["admin_user_ids"] == ""
    replaced = client.put(
        f"/api/bots/channels/{qq['id']}", json={"config": {"admin_user_ids": ["1", "2"]}}
    ).json()
    assert replaced["config"]["admin_user_ids"] == "***（2 个管理员）"


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
    wechat = _create_wechat(client, token="npu-assistant")
    timestamp = _now_ts()
    params = {
        "signature": _signature(token="npu-assistant", timestamp=timestamp),
        "timestamp": timestamp,
        "nonce": NONCE,
        "echostr": "echo-insecure",
    }
    # 默认拒绝公开默认 Token
    assert client.get(f"/api/bots/wechat/{wechat['id']}", params=params).status_code == 403

    # 显式开启本地调试开关后放行（仅限本机调试）
    monkeypatch.setenv("NPU_BOT_ALLOW_INSECURE", "1")
    verified = client.get(f"/api/bots/wechat/{wechat['id']}", params=params)
    assert verified.status_code == 200 and verified.text == "echo-insecure"


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

"""机器人链路端到端验证脚本（微信公众号）。

验证：微信公众号 GET 验签、XML 文本消息被动回复、错误签名拒绝。

前置：NPU-Assistant 已在 http://127.0.0.1:8000 启动。
用法：.\.venv\Scripts\python.exe scripts\e2e_bot_check.py
"""
from __future__ import annotations

import hashlib

import httpx

BASE = "http://127.0.0.1:8000"


def run_wechat(client: httpx.Client) -> None:
    token = "wechat-e2e-token"
    channel = client.post(
        "/api/bots/channels",
        json={"type": "wechat_mp", "name": "E2E 公众号", "config": {"token": token}},
    ).json()
    channel_id = channel["id"]
    try:
        timestamp, nonce, echostr = "1710000000", "123456", "hello-npu"
        signature = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()
        r = client.get(
            f"/api/bots/wechat/{channel_id}",
            params={"signature": signature, "timestamp": timestamp, "nonce": nonce, "echostr": echostr},
        )
        assert r.status_code == 200 and r.text == echostr, (r.status_code, r.text)
        print("[PASS] 微信公众号：GET 验签并返回 echostr 明文")

        xml = f"""<xml>
<ToUserName><![CDATA[gh_test]]></ToUserName>
<FromUserName><![CDATA[oUserE2E]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[心理中心怎么预约？]]></Content>
<MsgId>1234567890</MsgId>
</xml>"""
        r = client.post(
            f"/api/bots/wechat/{channel_id}",
            params={"signature": signature, "timestamp": timestamp, "nonce": nonce},
            content=xml.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
        )
        assert r.status_code == 200, r.text
        assert "text/xml" in r.headers.get("content-type", ""), r.headers
        assert "<ToUserName><![CDATA[oUserE2E]]>" in r.text, r.text
        assert "<Content><![CDATA[" in r.text, r.text
        print("[PASS] 微信公众号：XML 文本消息得到被动 XML 回复")

        bad = client.get(
            f"/api/bots/wechat/{channel_id}",
            params={"signature": "bad", "timestamp": timestamp, "nonce": nonce, "echostr": "x"},
        )
        assert bad.status_code == 403, bad.status_code
        print("[PASS] 微信公众号：错误签名返回 403")
    finally:
        client.delete(f"/api/bots/channels/{channel_id}")


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=60)
    health = client.get("/api/health")
    assert health.status_code == 200, "服务未启动"
    run_wechat(client)
    channels = client.get("/api/bots/channels").json()["items"]
    print(f"[PASS] 清理完成，剩余通道：{[c['name'] for c in channels]}")
    print("\n机器人端到端验证全部通过 ✅")


if __name__ == "__main__":
    main()

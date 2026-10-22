"""机器人链路端到端验证脚本（本地模拟 OneBot 11 与微信公众号）。

自动启动一个假的 OneBot HTTP API 服务，通过真实 WebSocket 反向连接 NPU-Assistant，
验证：反向 WS 鉴权 → 事件归一化 → 辅导员问答 → 通过 OneBot HTTP API 发回回复；
同时验证群聊 @ 策略、微信公众号 GET 验签与 XML 被动回复。

前置：NPU-Assistant 已在 http://127.0.0.1:8000 启动。
用法：.\.venv\Scripts\python.exe scripts\e2e_bot_check.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
ONEBOT_PORT = 3010
TOKEN = "e2e-token-123"
SELF_ID = "10001"
sent: list[dict] = []


class FakeOneBot(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: A003
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        sent.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization", ""),
                "body": body,
            }
        )
        payload = json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_fake_onebot() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", ONEBOT_PORT), FakeOneBot)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait_for_sent(count: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(sent) >= count:
            return True
        time.sleep(0.2)
    return False


async def wait_for_sent_async(count: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(sent) >= count:
            return True
        await asyncio.sleep(0.2)
    return False


async def run_onebot(client: httpx.Client) -> None:
    channel = client.post(
        "/api/bots/channels",
        json={
            "type": "onebot11",
            "name": "E2E QQ 通道",
            "config": {
                "api_base": f"http://127.0.0.1:{ONEBOT_PORT}",
                "access_token": TOKEN,
                "self_id": SELF_ID,
                "group_mention_only": "true",
            },
        },
    ).json()
    channel_id = channel["id"]
    try:
        uri = f"ws://127.0.0.1:8000/api/bots/onebot/{channel_id}/ws"
        async with websockets.connect(uri, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            # 1) 私聊消息 -> 应通过 send_private_msg 回复
            await ws.send(
                json.dumps(
                    {
                        "time": int(time.time()),
                        "self_id": int(SELF_ID),
                        "post_type": "message",
                        "message_type": "private",
                        "user_id": 20002,
                        "message_id": 9001,
                        "message": [{"type": "text", "data": {"text": "国家奖学金怎么申请？"}}],
                        "raw_message": "国家奖学金怎么申请？",
                    },
                    ensure_ascii=False,
                )
            )
            assert await wait_for_sent_async(1), "私聊消息未触发 OneBot 发送"
            first = sent[0]
            assert first["path"] == "/send_private_msg", first
            assert first["auth"] == f"Bearer {TOKEN}", first
            assert first["body"].get("user_id") == 20002, first
            reply = first["body"].get("message")
            text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
            assert "奖学金" in text or "知识库" in text, text
            print(f"[PASS] QQ 私聊：回答已通过 OneBot HTTP API 发回（{len(text)} 字符）")

            # 2) 群聊未 @ -> 不回复
            before = len(sent)
            await ws.send(
                json.dumps(
                    {
                        "time": int(time.time()),
                        "self_id": int(SELF_ID),
                        "post_type": "message",
                        "message_type": "group",
                        "group_id": 30003,
                        "user_id": 20002,
                        "message_id": 9002,
                        "message": [{"type": "text", "data": {"text": "随便说句话"}}],
                        "raw_message": "随便说句话",
                    },
                    ensure_ascii=False,
                )
            )
            await asyncio.sleep(1.5)
            assert len(sent) == before, "未 @ 的群消息不应回复"
            print("[PASS] QQ 群聊：未 @ 机器人时不回复")

            # 3) 群聊 @ -> 回复 send_group_msg
            await ws.send(
                json.dumps(
                    {
                        "time": int(time.time()),
                        "self_id": int(SELF_ID),
                        "post_type": "message",
                        "message_type": "group",
                        "group_id": 30003,
                        "user_id": 20002,
                        "message_id": 9003,
                        "message": [
                            {"type": "at", "data": {"qq": SELF_ID}},
                            {"type": "text", "data": {"text": " 请假流程是什么？"}},
                        ],
                        "raw_message": f"[CQ:at,qq={SELF_ID}] 请假流程是什么？",
                    },
                    ensure_ascii=False,
                )
            )
            assert await wait_for_sent_async(before + 1), "群聊 @ 消息未触发发送"
            group_send = sent[-1]
            assert group_send["path"] == "/send_group_msg", group_send
            assert group_send["body"].get("group_id") == 30003, group_send
            print("[PASS] QQ 群聊：@ 机器人后通过 send_group_msg 回复")

            # 4) 错误 token 应被拒绝
            try:
                async with websockets.connect(uri, additional_headers={"Authorization": "Bearer wrong"}) as _:
                    raise AssertionError("错误 token 不应建立连接")
            except websockets.exceptions.InvalidStatus as exc:
                assert exc.response.status_code == 401, exc
                print("[PASS] QQ 反向 WS：错误 token 返回 401")
    finally:
        client.delete(f"/api/bots/channels/{channel_id}")


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
    server = start_fake_onebot()
    try:
        asyncio.run(run_onebot(client))
        run_wechat(client)
    finally:
        server.shutdown()
    channels = client.get("/api/bots/channels").json()["items"]
    print(f"[PASS] 清理完成，剩余通道：{[c['name'] for c in channels]}")
    print("\n机器人端到端验证全部通过 ✅")


if __name__ == "__main__":
    main()

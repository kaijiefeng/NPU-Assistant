"""QQ 开放平台官方机器人 WebSocket 网关管理。

设计要点
--------
* **主动出站连接**：服务启动后为每个启用中的 ``qq_official`` 通道建立到
  ``api.sgroup.qq.com`` 的 WebSocket 连接，因此**不需要公网 webhook**；
* **令牌与重连**：``AppID + AppSecret`` 换取 access_token，令牌过期前自动刷新；
  连接断开按指数退避重连，并尽量使用 ``op=6 Resume`` 补发漏掉的事件；
* **被动回复**：收到消息后复用 :mod:`app.services.bot_pipeline` 的问答链路，
  再通过 HTTP API 携带原消息 ``msg_id`` 回复（QQ 官方限制 5 分钟内、同一消息
  最多回复 5 次）；
* **隔离**：任何单通道异常只记录日志并重试，不影响应用其他部分；测试环境通过
  ``NPU_QQ_OFFICIAL_AUTOSTART=0`` 关闭自动连接，避免访问真实网络。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any

import httpx
import websockets

from .. import db
from ..adapters import qq_official
from ..adapters.base import CHANNEL_QQ_OFFICIAL
from . import bot_pipeline

logger = logging.getLogger("npu-assistant")

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
# 群聊 + C2C 消息事件（C2C_MESSAGE_CREATE / GROUP_AT_MESSAGE_CREATE）
INTENTS = 1 << 25
AUTOSTART_ENV = "NPU_QQ_OFFICIAL_AUTOSTART"
# 令牌提前刷新窗口与重连上限
TOKEN_REFRESH_MARGIN_SECONDS = 120
MAX_RECONNECT_DELAY_SECONDS = 60
# 事件幂等缓存（每个通道最近 512 条）
DEDUPE_MAX = 512


def _autostart_enabled() -> bool:
    return os.environ.get(AUTOSTART_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _channel_config(row: dict | None) -> dict:
    if not isinstance(row, dict):
        return {}
    config = db.from_json(row.get("config"), {}) or {}
    return config if isinstance(config, dict) else {}


def _mark(channel_id: str, status: str | None = None) -> None:
    """更新通道状态 / 最近活跃时间；失败只记日志。"""
    try:
        if status:
            db.execute(
                "UPDATE bot_channels SET status = ?, last_seen_at = ? WHERE id = ?",
                (status, db.now(), channel_id),
            )
        else:
            db.execute(
                "UPDATE bot_channels SET last_seen_at = ? WHERE id = ?",
                (db.now(), channel_id),
            )
    except Exception:  # noqa: BLE001 - 状态更新失败不影响连接
        logger.warning("更新 QQ 官方通道状态失败（已忽略）：channel=%s", channel_id, exc_info=True)


class QQBotClient:
    """单个通道的令牌缓存与 HTTP 发送能力。"""

    def __init__(self, channel_id: str, config: dict) -> None:
        self.channel_id = str(channel_id)
        self.config = dict(config or {})
        self.app_id = str(self.config.get("app_id") or "").strip()
        self.app_secret = str(self.config.get("app_secret") or "").strip()
        self._http = httpx.AsyncClient(timeout=15.0, follow_redirects=False)
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        try:
            await self._http.aclose()
        except Exception:  # noqa: BLE001
            pass

    def token_remaining(self) -> float:
        return self._expires_at - time.monotonic()

    async def access_token(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and self.token_remaining() > 0:
                return self._token
            response = await self._http.post(
                TOKEN_URL,
                json={"appId": self.app_id, "clientSecret": self.app_secret},
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"获取 QQ access_token 失败：HTTP {response.status_code} "
                    f"{response.text[:200]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("获取 QQ access_token 失败：响应不是 JSON") from exc
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise RuntimeError("获取 QQ access_token 失败：响应缺少 access_token")
            try:
                expires_in = int(payload.get("expires_in") or 7200)
            except (TypeError, ValueError):
                expires_in = 7200
            self._token = token
            self._expires_at = time.monotonic() + max(
                expires_in - TOKEN_REFRESH_MARGIN_SECONDS, 60
            )
            logger.info(
                "QQ 官方机器人 access_token 已刷新：channel=%s expires_in=%ss",
                self.channel_id, expires_in,
            )
            return token

    def _authorization(self, token: str) -> dict[str, str]:
        return {"Authorization": f"QQBot {token}"}

    async def gateway_url(self) -> str:
        token = await self.access_token()
        response = await self._http.get(
            f"{qq_official.api_base(self.config)}/gateway",
            headers=self._authorization(token),
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"获取 QQ WebSocket 网关失败：HTTP {response.status_code} "
                f"{response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("获取 QQ WebSocket 网关失败：响应不是 JSON") from exc
        url = str(payload.get("url") or "").strip()
        if not url:
            raise RuntimeError("获取 QQ WebSocket 网关失败：响应缺少 url")
        return url

    async def send_reply(self, incoming: Any, text: str, *, msg_id: str,
                         msg_seq: int = 1) -> bool:
        """被动回复一条消息；失败只记日志并返回 ``False``。"""
        content = str(text or "").strip()
        if not content:
            return False
        url = qq_official.reply_url(
            self.config,
            chat_type=incoming.chat_type,
            user_id=incoming.user_id,
            group_id=incoming.group_id,
        )
        payload = qq_official.build_payload(content, msg_id=msg_id, msg_seq=msg_seq)
        for attempt in range(2):
            try:
                token = await self.access_token(force=attempt > 0)
                response = await self._http.post(
                    url, headers=self._authorization(token), json=payload
                )
            except Exception as exc:  # noqa: BLE001 - 发送失败只影响本条回复
                logger.warning(
                    "QQ 官方机器人发送失败：channel=%s error=%s",
                    self.channel_id, str(exc)[:200],
                )
                return False
            if response.status_code in {200, 201}:
                return True
            # 令牌过期：刷新后重试一次
            if response.status_code == 401 and attempt == 0:
                continue
            logger.warning(
                "QQ 官方机器人发送失败：channel=%s status=%s body=%s",
                self.channel_id, response.status_code, response.text[:200],
            )
            return False
        return False


class QQOfficialGateway:
    """管理所有 ``qq_official`` 通道的连接任务（单例）。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._fingerprints: dict[str, tuple[str, str, str]] = {}
        self._sync_lock = asyncio.Lock()
        self._seen: dict[str, OrderedDict[str, None]] = {}

    # -- 生命周期 ---------------------------------------------------------
    async def startup(self) -> None:
        self._loop = asyncio.get_running_loop()
        if not _autostart_enabled():
            logger.info(
                "QQ 官方机器人自动连接已关闭（%s=0），仅保留手动/测试初始化",
                AUTOSTART_ENV,
            )
            return
        await self.sync()

    async def shutdown(self) -> None:
        for channel_id in list(self._tasks):
            await self._stop(channel_id)
        self._loop = None

    def notify(self) -> None:
        """通道 CRUD 后调用：异步对齐连接（同步路由安全）。"""
        if not _autostart_enabled():
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is loop:
                loop.create_task(self.sync())
            else:
                asyncio.run_coroutine_threadsafe(self.sync(), loop)
        except RuntimeError:  # pragma: no cover - 事件循环已关闭
            pass

    # -- 连接对齐 ---------------------------------------------------------
    async def sync(self) -> None:
        async with self._sync_lock:
            rows = db.query(
                "SELECT * FROM bot_channels WHERE type = ? AND enabled = 1",
                (CHANNEL_QQ_OFFICIAL,),
            )
            desired: dict[str, tuple[str, str, str, dict]] = {}
            for row in rows:
                config = _channel_config(row)
                app_id = str(config.get("app_id") or "").strip()
                app_secret = str(config.get("app_secret") or "").strip()
                if not app_id or not app_secret:
                    continue
                fingerprint = (
                    app_id,
                    app_secret,
                    "1" if str(config.get("sandbox") or "").lower() in {"1", "true", "yes", "on"} else "0",
                )
                desired[str(row["id"])] = (*fingerprint, config)

            for channel_id in list(self._tasks):
                current = desired.get(channel_id)
                if current is None or self._fingerprints.get(channel_id) != current[:3]:
                    await self._stop(channel_id)

            for channel_id, (app_id, app_secret, sandbox, config) in desired.items():
                if channel_id in self._tasks:
                    continue
                fingerprint = (app_id, app_secret, sandbox)
                self._fingerprints[channel_id] = fingerprint
                self._tasks[channel_id] = asyncio.create_task(
                    self._run_channel(channel_id, config),
                    name=f"qq-official:{channel_id}",
                )
                logger.info("QQ 官方机器人连接任务已启动：channel=%s", channel_id)

    async def _stop(self, channel_id: str) -> None:
        task = self._tasks.pop(channel_id, None)
        self._fingerprints.pop(channel_id, None)
        self._seen.pop(channel_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _mark(channel_id, "offline")
        logger.info("QQ 官方机器人连接任务已停止：channel=%s", channel_id)

    # -- 单通道循环 -------------------------------------------------------
    async def _run_channel(self, channel_id: str, config: dict) -> None:
        client = QQBotClient(channel_id, config)
        state: dict[str, Any] = {"session_id": "", "seq": 0}
        attempt = 0
        try:
            while True:
                try:
                    await self._session(client, state)
                    # 连接正常关闭：短暂等待后重连（Resume 会尽量补发事件）
                    delay = 2.0
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单通道失败只重试
                    attempt += 1
                    delay = min(2.0 ** attempt, MAX_RECONNECT_DELAY_SECONDS)
                    _mark(channel_id, "error")
                    logger.warning(
                        "QQ 官方机器人连接异常（%ss 后重连）：channel=%s error=%s",
                        int(delay), channel_id, str(exc)[:200],
                    )
                await asyncio.sleep(delay)
        finally:
            await client.close()
            _mark(channel_id, "offline")

    async def _session(self, client: QQBotClient, state: dict[str, Any]) -> None:
        token = await client.access_token()
        gateway = await client.gateway_url()
        async with websockets.connect(
            gateway,
            open_timeout=15,
            ping_interval=None,
            close_timeout=5,
            max_size=2 * 1024 * 1024,
        ) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            interval_ms = 30000
            if isinstance(hello, dict) and hello.get("op") == 10:
                payload = hello.get("d")
                if isinstance(payload, dict):
                    try:
                        interval_ms = int(payload.get("heartbeat_interval") or 30000)
                    except (TypeError, ValueError):
                        interval_ms = 30000
            heartbeat = asyncio.create_task(
                self._heartbeat(ws, max(interval_ms / 1000.0, 5.0), state)
            )
            token_guard = asyncio.create_task(self._token_guard(ws, client))
            try:
                if state.get("session_id") and state.get("seq"):
                    await ws.send(
                        json.dumps(
                            {
                                "op": 6,
                                "d": {
                                    "token": f"QQBot {token}",
                                    "session_id": state["session_id"],
                                    "seq": state["seq"],
                                },
                            }
                        )
                    )
                    logger.info("QQ 官方机器人尝试 Resume：channel=%s", client.channel_id)
                else:
                    await ws.send(
                        json.dumps(
                            {
                                "op": 2,
                                "d": {
                                    "token": f"QQBot {token}",
                                    "intents": INTENTS,
                                    "shard": [0, 1],
                                    "properties": {},
                                },
                            }
                        )
                    )
                _mark(client.channel_id, "online")
                logger.info("QQ 官方机器人 WebSocket 已连接：channel=%s", client.channel_id)

                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except (TypeError, ValueError):
                        logger.warning("QQ 官方网关收到非 JSON 报文（已忽略）：%r", str(raw)[:200])
                        continue
                    if not isinstance(event, dict):
                        continue
                    op = event.get("op")
                    if op == 0:
                        seq = event.get("s")
                        if isinstance(seq, int):
                            state["seq"] = seq
                        event_type = str(event.get("t") or "")
                        data = event.get("d")
                        if event_type == "READY" and isinstance(data, dict):
                            state["session_id"] = str(data.get("session_id") or "")
                        elif event_type == "RESUMED":
                            logger.info(
                                "QQ 官方机器人 Resume 成功：channel=%s", client.channel_id
                            )
                        elif event_type:
                            await self._dispatch(client, event_type, data)
                    elif op == 7:
                        # 服务端要求重连：跳出后使用 Resume
                        break
                    elif op == 9:
                        # 会话失效：清空后重新 Identify
                        state["session_id"] = ""
                        state["seq"] = 0
                        break
            finally:
                heartbeat.cancel()
                token_guard.cancel()

    async def _heartbeat(self, ws: Any, interval: float, state: dict[str, Any]) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({"op": 1, "d": state.get("seq") or None}))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 心跳失败交给主循环重连
            pass

    async def _token_guard(self, ws: Any, client: QQBotClient) -> None:
        """令牌过期前主动关闭连接，避免带着过期令牌继续收发。"""
        try:
            remaining = client.token_remaining()
            if remaining > 0:
                await asyncio.sleep(remaining)
            await ws.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    # -- 事件处理 ---------------------------------------------------------
    def _duplicate(self, channel_id: str, event_id: str) -> bool:
        if not event_id:
            return False
        cache = self._seen.setdefault(channel_id, OrderedDict())
        if event_id in cache:
            return True
        cache[event_id] = None
        while len(cache) > DEDUPE_MAX:
            cache.popitem(last=False)
        return False

    async def _dispatch(self, client: QQBotClient, event_type: str,
                        data: Any) -> None:
        if not isinstance(data, dict):
            return
        event_id = str(data.get("id") or "")
        if self._duplicate(client.channel_id, event_id):
            logger.debug(
                "忽略重复投递的 QQ 官方事件：channel=%s id=%s",
                client.channel_id, event_id,
            )
            return
        row = db.query_one(
            "SELECT * FROM bot_channels WHERE id = ?", (client.channel_id,)
        )
        if not row or row.get("type") != CHANNEL_QQ_OFFICIAL:
            return
        config = _channel_config(row)
        incoming = qq_official.normalize_event(
            event_type, data, channel_id=client.channel_id, config=config
        )
        if incoming is None:
            return
        try:
            result = await asyncio.to_thread(
                bot_pipeline.handle_incoming, incoming, channel_config=config
            )
            reply = str((result or {}).get("reply") or "")
            sent = await client.send_reply(
                incoming, reply, msg_id=str(data.get("id") or "")
            )
            _mark(client.channel_id, "online" if sent else None)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 单条消息失败不影响连接
            logger.exception(
                "处理 QQ 官方机器人事件失败（已忽略）：channel=%s",
                client.channel_id,
            )


gateway = QQOfficialGateway()

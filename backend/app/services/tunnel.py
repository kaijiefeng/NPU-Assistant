"""临时公网隧道管理（Cloudflare Quick Tunnel）。

仅在管理员/本机触发；启动 ``cloudflared`` 子进程，从日志中解析
``https://*.trycloudflare.com`` 地址。失败时返回可操作的错误信息。
"""

from __future__ import annotations

import atexit
import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .. import config

logger = logging.getLogger("npu-assistant")

_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


class TunnelManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._public_url = ""
        self._error = ""
        self._started_at = 0.0
        self._reader: threading.Thread | None = None

    def available(self) -> bool:
        path = (config.CLOUDFLARED_PATH or "cloudflared").strip()
        if not path:
            return False
        resolved = shutil.which(path)
        if resolved:
            return True
        return Path(path).is_file()

    def status(self) -> dict:
        with self._lock:
            running = bool(self._proc and self._proc.poll() is None and self._public_url)
            return {
                "available": self.available(),
                "running": running,
                "public_url": self._public_url if running else "",
                "started_at": self._started_at if running else 0,
                "error": self._error,
                "install_hint": ""
                if self.available()
                else "未检测到 cloudflared。Windows 可执行 winget install Cloudflare.cloudflared，或从 Cloudflare 官网下载后加入 PATH。",
            }

    def start(self) -> dict:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                if self._public_url:
                    return self.status()
                raise TunnelError("公网隧道正在启动，请稍候")
            if not self.available():
                raise TunnelError(
                    "未安装 cloudflared，无法一键开启公网访问。可先安装：winget install Cloudflare.cloudflared"
                )
            path = (config.CLOUDFLARED_PATH or "cloudflared").strip()
            command = [
                path,
                "tunnel",
                "--url",
                f"http://127.0.0.1:{config.PORT}",
                "--no-autoupdate",
            ]
            self._error = ""
            self._public_url = ""
            self._started_at = time.time()
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            try:
                self._proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                self._proc = None
                self._error = f"无法启动 cloudflared：{exc}"
                raise TunnelError(self._error) from exc

            ready = threading.Event()
            recent: list[str] = []

            def _read() -> None:
                assert self._proc is not None and self._proc.stdout is not None
                for line in self._proc.stdout:
                    recent.append(line.rstrip())
                    if len(recent) > 30:
                        recent.pop(0)
                    match = _TUNNEL_URL_RE.search(line)
                    if match and not self._public_url:
                        self._public_url = match.group(0)
                        logger.info("公网隧道已建立：%s", self._public_url)
                        ready.set()
                if self._proc and self._proc.poll() is not None and not self._public_url:
                    self._error = "cloudflared 已退出：" + (" / ".join(recent[-4:]) or "无日志")
                    ready.set()

            self._reader = threading.Thread(target=_read, name="cloudflared-reader", daemon=True)
            self._reader.start()
            timeout = max(5, int(config.TUNNEL_TIMEOUT_SECONDS))
            if not ready.wait(timeout):
                self.stop()
                self._error = f"公网隧道启动超时（{timeout}s）。请检查网络或 cloudflared 是否可用。"
                raise TunnelError(self._error)
            if not self._public_url:
                self.stop()
                self._error = self._error or "cloudflared 未能返回公网地址"
                raise TunnelError(self._error)
            self._error = ""
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            proc, self._proc = self._proc, None
            self._public_url = ""
            self._started_at = 0.0
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except OSError:
                    logger.debug("停止 cloudflared 失败", exc_info=True)
            return self.status()

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:  # pragma: no cover - best effort at exit
            pass


tunnel_manager = TunnelManager()
atexit.register(tunnel_manager.shutdown)

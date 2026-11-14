"""CDP 浏览器封装：真实鼠标/键盘事件、等待、截图与控制台/网络采集。"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import websockets

from .sandbox import free_port

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
]


def find_browser() -> Path:
    for path in CHROME_CANDIDATES:
        if path.is_file():
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        located = shutil.which(name)
        if located:
            return Path(located)
    raise RuntimeError("未找到 Chrome/Edge，可安装 Chrome 或设置 CHROME_PATH")


class Browser:
    def __init__(self, *, profile_dir: Path, headless: bool = True, seed: int = 7) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.debug_port = free_port()
        self.process: subprocess.Popen | None = None
        self.ws = None
        self.next_id = 0
        self.events: list[dict] = []
        self.rng = random.Random(seed)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> "Browser":
        profile = self.profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        args = [
            str(find_browser()),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-features=Translate,MediaRouter",
            "--disable-dev-shm-usage",
            "--window-size=1440,960",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={self.debug_port}",
            "about:blank",
        ]
        if self.headless:
            args.insert(1, "--headless=new")
        self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = None
        deadline = time.time() + 30
        while time.time() < deadline and not ws_url:
            try:
                targets = httpx.get(
                    f"http://127.0.0.1:{self.debug_port}/json/list", timeout=2, trust_env=False
                ).json()
                pages = [item for item in targets if item.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.3)
        if not ws_url:
            raise RuntimeError("无法连接 Chrome 调试端口")
        self.ws = await websockets.connect(ws_url, max_size=64 * 1024 * 1024)
        for method in (
            "Page.enable",
            "Runtime.enable",
            "Network.enable",
            "Log.enable",
            "DOM.enable",
        ):
            await self.call(method)
        await self.call("Network.setCacheDisabled", {"cacheDisabled": True})
        return self

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self.process is not None and self.process.poll() is None:
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except Exception:  # noqa: BLE001
                self.process.kill()

    # ------------------------------------------------------------------
    # 底层调用
    # ------------------------------------------------------------------
    async def call(self, method: str, params: dict | None = None, timeout: float = 40):
        self.next_id += 1
        message_id = self.next_id
        await self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    async def evaluate(self, expression: str, timeout: float = 40):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("value")

    # ------------------------------------------------------------------
    # 等待 / 导航
    # ------------------------------------------------------------------
    def human_delay(self, low: float = 0.06, high: float = 0.22) -> None:
        time.sleep(self.rng.uniform(low, high))

    async def wait_document(self, timeout: float = 25) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if await self.evaluate("document.readyState") == "complete":
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.3)

    async def wait_dom_quiet(self, quiet_ms: int = 350, timeout: float = 15) -> None:
        """DOM 文本与 URL 稳定 quiet_ms 即认为渲染完成。"""
        deadline = time.time() + timeout
        last = None
        stable_since = time.time()
        while time.time() < deadline:
            try:
                snapshot = await self.evaluate(
                    "({text: document.body ? document.body.innerText.length : 0, url: location.href})"
                )
            except Exception:  # noqa: BLE001
                snapshot = None
            if snapshot != last:
                last = snapshot
                stable_since = time.time()
            elif (time.time() - stable_since) * 1000 >= quiet_ms:
                return
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.2)

    async def goto(self, url: str, *, settle: float = 0.4) -> None:
        await self.call("Page.navigate", {"url": url})
        await self.wait_document()
        await self.wait_dom_quiet()
        time.sleep(settle)

    async def wait_for_text(self, text: str, timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if text in (await self.evaluate("document.body ? document.body.innerText : ''") or ""):
                    return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.25)
        return False

    async def wait_for_selector(self, selector: str, timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                found = await self.evaluate(
                    f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
                    " return Boolean(el && el.getClientRects().length); })()"
                )
                if found:
                    return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.25)
        return False

    # ------------------------------------------------------------------
    # 人类化输入
    # ------------------------------------------------------------------
    async def _rect(self, selector: str | None = None, text: str | None = None):
        if selector:
            js = (
                "(() => { const el = document.querySelector(%s);"
                " if (!el) return null; el.scrollIntoView({block:'center'});"
                " const r = el.getBoundingClientRect();"
                " if (r.width <= 0 || r.height <= 0) return null;"
                " return {x: r.x, y: r.y, w: r.width, h: r.height}; })()" % json.dumps(selector)
            )
        else:
            js = (
                "(() => { const wanted = %s;"
                " const nodes = Array.from(document.querySelectorAll('button,a,[role=button],[data-action],li,span'));"
                " const hit = nodes.find(n => (n.innerText || '').trim() === wanted) ||"
                "   nodes.find(n => (n.innerText || '').trim().includes(wanted));"
                " if (!hit) return null; hit.scrollIntoView({block:'center'});"
                " const r = hit.getBoundingClientRect();"
                " if (r.width <= 0 || r.height <= 0) return null;"
                " return {x: r.x, y: r.y, w: r.width, h: r.height}; })()"
                % json.dumps(text or "")
            )
        return await self.evaluate(js)

    async def _click_point(self, x: float, y: float) -> None:
        # 先移动再按下：贴近人类触控板轨迹
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x - 3, "y": y - 2})
        self.human_delay(0.02, 0.08)
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.human_delay(0.01, 0.05)
        await self.call(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        self.human_delay(0.03, 0.12)
        await self.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        )

    async def click(self, selector: str, *, timeout: float = 12) -> None:
        deadline = time.time() + timeout
        rect = None
        while time.time() < deadline:
            rect = await self._rect(selector=selector)
            if rect:
                break
            await asyncio.sleep(0.2)
        if not rect:
            raise AssertionError(f"找不到可点击元素：{selector}")
        x = rect["x"] + rect["w"] * self.rng.uniform(0.3, 0.7)
        y = rect["y"] + rect["h"] * self.rng.uniform(0.3, 0.7)
        await self._click_point(x, y)
        await self.wait_dom_quiet(timeout=10)

    async def click_text(self, text: str, *, timeout: float = 12) -> None:
        rect = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = await self._rect(text=text)
            if rect:
                break
            await asyncio.sleep(0.2)
        if not rect:
            raise AssertionError(f"找不到包含文字的点击目标：{text}")
        await self._click_point(
            rect["x"] + rect["w"] * 0.5, rect["y"] + rect["h"] * 0.5
        )
        await self.wait_dom_quiet(timeout=10)

    async def type(self, selector: str, text: str, *, clear: bool = True, chunk: int = 3) -> None:
        # 聚焦并清空（清空用脚本，输入用真实文本插入 + 拟人间隔）
        await self.evaluate(
            "(() => { const el = document.querySelector(%s);"
            " if (!el) return false; el.focus();"
            " if (%s) { el.value = ''; el.dispatchEvent(new Event('input', {bubbles:true})); }"
            " return true; })()" % (json.dumps(selector), "true" if clear else "false")
        )
        self.human_delay(0.1, 0.25)
        index = 0
        while index < len(text):
            piece = text[index : index + chunk]
            await self.call("Input.insertText", {"text": piece})
            index += chunk
            time.sleep(self.rng.uniform(0.05, 0.14))

    async def press(self, key: str = "Enter") -> None:
        code = {"Enter": 13, "Escape": 27, "Tab": 9}[key]
        for event_type in ("rawKeyDown", "keyUp"):
            await self.call(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": key,
                    "code": key,
                    "windowsVirtualKeyCode": code,
                    "nativeVirtualKeyCode": code,
                },
            )
            self.human_delay(0.02, 0.08)
        await self.wait_dom_quiet(timeout=10)

    async def wheel(self, delta_y: int = 600) -> None:
        await self.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": 720, "y": 480, "deltaX": 0, "deltaY": delta_y},
        )
        await asyncio.sleep(0.35)

    async def set_files(self, selector: str, paths: list[Path]) -> None:
        document = await self.call("DOM.getDocument", {"depth": -1})
        node = await self.call(
            "DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector}
        )
        if not node.get("nodeId"):
            raise AssertionError(f"找不到文件输入框：{selector}")
        await self.call(
            "DOM.setFileInputFiles",
            {"files": [str(path) for path in paths], "nodeId": node["nodeId"]},
        )
        await self.evaluate(
            "(() => { const el = document.querySelector(%s);"
            " if (el) el.dispatchEvent(new Event('change', {bubbles:true})); })()" % json.dumps(selector)
        )

    # ------------------------------------------------------------------
    # 视口 / 截图 / 采集
    # ------------------------------------------------------------------
    async def set_viewport(self, width: int, height: int, mobile: bool = False) -> None:
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": mobile,
                "screenWidth": width,
                "screenHeight": height,
            },
        )
        await self.call("Emulation.setTouchEmulationEnabled", {"enabled": mobile})

    async def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = await self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.write_bytes(base64.b64decode(data["data"]))

    async def text(self) -> str:
        return await self.evaluate("document.body ? document.body.innerText : ''") or ""

    async def body_html(self) -> str:
        return await self.evaluate("document.documentElement ? document.documentElement.outerHTML : ''") or ""

    def console_errors(self) -> list[str]:
        errors: list[str] = []
        for event in self.events:
            method = event.get("method")
            if method == "Runtime.exceptionThrown":
                detail = event.get("params", {}).get("exceptionDetails", {})
                desc = (detail.get("exception") or {}).get("description") or detail.get("text")
                errors.append(str(desc)[:400])
            elif method == "Log.entryAdded":
                entry = event.get("params", {}).get("entry", {})
                if entry.get("level") == "error":
                    errors.append(str(entry.get("text"))[:300])
            elif method == "Runtime.consoleAPICalled" and event.get("params", {}).get("type") == "error":
                errors.append(str(event.get("params", {}).get("args"))[:300])
        return errors

    def network_failures(self) -> list[dict]:
        failures: list[dict] = []
        for event in self.events:
            method = event.get("method")
            params = event.get("params", {})
            if method == "Network.responseReceived":
                response = params.get("response", {})
                if int(response.get("status") or 0) >= 500:
                    failures.append({"url": response.get("url"), "status": response.get("status")})
            elif method == "Network.loadingFailed":
                failures.append({"url": params.get("requestId"), "error": params.get("errorText")})
        return failures

    async def cookies(self) -> dict[str, str]:
        result = await self.call("Network.getAllCookies")
        return {item["name"]: item["value"] for item in result.get("cookies", []) if item.get("name")}

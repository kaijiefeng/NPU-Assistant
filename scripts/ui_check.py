r"""用 Chrome DevTools Protocol 做前端交互冒烟 + 页面截图。

前置：NPU-Assistant 已在 http://127.0.0.1:8000 启动，本机安装 Chrome。
用法：.\.venv\Scripts\python.exe scripts\ui_check.py
产物：docs/screenshots/{chat,knowledge,manage,models,bots,settings,jobs,audit}.png
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
DEBUG_PORT = 9223
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "screenshots"
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def find_browser() -> Path:
    for path in CHROME_CANDIDATES:
        if path.is_file():
            return path
    raise SystemExit("未找到 Chrome/Edge，无法执行 UI 检查")


class CDP:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.next_id = 0
        self.events: list[dict] = []

    async def call(self, method: str, params: dict | None = None, timeout: float = 30):
        self.next_id += 1
        message_id = self.next_id
        await self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    async def evaluate(self, expression: str, timeout: float = 30):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("value")


async def open_page():
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2)
            targets = [t for t in response.json() if t.get("type") == "page"]
            if targets:
                return targets[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        await asyncio.sleep(0.3)
    raise SystemExit("无法连接 Chrome 调试端口")


async def main() -> None:
    browser = find_browser()
    profile = tempfile.mkdtemp(prefix="npu-ui-")
    process = subprocess.Popen(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={DEBUG_PORT}",
            "--window-size=1440,960",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = await open_page()
        async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Log.enable")
            await cdp.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1440, "height": 960, "deviceScaleFactor": 1, "mobile": False},
            )
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            errors: list[str] = []
            token = os.environ.get("NPU_ADMIN_TOKEN", "").strip()
            if not token:
                token_file = ROOT / "backend" / "data" / "admin_token.txt"
                if token_file.is_file():
                    token = token_file.read_text(encoding="utf-8").strip()
            if token:
                await cdp.call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": f"localStorage.setItem('npu_admin_token', {json.dumps(token)})"},
                )

            async def goto(path: str) -> None:
                await cdp.call("Page.navigate", {"url": f"{BASE}/{path}"})
                deadline = time.time() + 20
                while time.time() < deadline:
                    try:
                        if await cdp.evaluate("document.readyState") == "complete":
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.2)
                await asyncio.sleep(1.8)

            async def shot(name: str) -> None:
                data = await cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                (OUT_DIR / f"{name}.png").write_bytes(base64.b64decode(data["data"]))

            # 对话页：真实发送一条问题，等引用行出现
            await goto("#/chat")
            await cdp.evaluate(
                """(() => {
                    const input = document.querySelector('#chatInput');
                    input.value = '国家奖学金怎么申请？需要哪些材料？';
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    document.querySelector('#chatForm').requestSubmit();
                    return true;
                })()"""
            )
            citations = 0
            reply_ok = False
            deadline = time.time() + 45
            while time.time() < deadline:
                await asyncio.sleep(0.5)
                citations = await cdp.evaluate("document.querySelectorAll('.citation-row').length") or 0
                reply_ok = bool(
                    await cdp.evaluate(
                        "Array.from(document.querySelectorAll('.msg-assistant')).some(n => n.innerText.includes('奖学金'))"
                    )
                )
                if citations > 0 and reply_ok:
                    break
            if not (citations > 0 and reply_ok):
                raise SystemExit(f"对话页校验失败：reply_ok={reply_ok}, citations={citations}")
            await cdp.evaluate("document.querySelector('.citation-row summary')?.click(); true")
            await asyncio.sleep(0.5)
            await shot("chat")
            print(f"[PASS] 对话页：发送问题并收到回答 + {citations} 个来源折叠行（已截图）")

            for name, path, needle in [
                ("knowledge", "#/knowledge", "知识库"),
                ("manage", "#/knowledge?manage=1", "收录资料"),
                ("models", "#/settings?tab=models", "模型"),
                ("bots", "#/settings?tab=bots", "机器人"),
                ("settings", "#/settings", "助手设置"),
                ("jobs", "#/ops?tab=jobs", "任务与告警"),
                ("audit", "#/ops?tab=audit", "审计日志"),
            ]:
                await goto(path)
                text = await cdp.evaluate("document.body.innerText") or ""
                if needle not in text:
                    raise SystemExit(f"{name} 页面渲染失败，未找到关键字 {needle}")
                await shot(name)
                print(f"[PASS] {name} 页面渲染正常（已截图）")

            # 旧书签兼容：内容管理自动落到知识库管理模式
            await goto("#/manage")
            text = await cdp.evaluate("document.body.innerText") or ""
            if "收录资料" not in text:
                raise SystemExit("旧路由 #/manage 未重定向到知识库管理模式")
            print("[PASS] 旧路由 #/manage 重定向正常")

            # 聚合页内切换：设置 → 模型 → 我的账号；运营 → 任务与告警
            await goto("#/settings")
            await cdp.evaluate("document.querySelector('[data-settings-tab=\"models\"]')?.click(); true")
            await asyncio.sleep(2.0)
            text = await cdp.evaluate("document.body.innerText") or ""
            if "Provider" not in text:
                raise SystemExit("设置聚合页切换到「模型」失败")
            await cdp.evaluate("document.querySelector('[data-settings-tab=\"account\"]')?.click(); true")
            await asyncio.sleep(0.5)
            text = await cdp.evaluate("document.body.innerText") or ""
            if "我的账号" not in text:
                raise SystemExit("设置聚合页切换到「我的账号」失败")
            await goto("#/ops")
            await cdp.evaluate("document.querySelector('[data-ops-tab=\"jobs\"]')?.click(); true")
            await asyncio.sleep(0.8)
            text = await cdp.evaluate("document.body.innerText") or ""
            if "后台任务" not in text:
                raise SystemExit("运营聚合页切换到「任务与告警」失败")
            print("[PASS] 运营 / 设置聚合页内切换正常")

            for event in cdp.events:
                method = event.get("method")
                if method == "Runtime.exceptionThrown":
                    details = event.get("params", {}).get("exceptionDetails", {})
                    desc = details.get("exception", {}).get("description") or details.get("text")
                    errors.append(str(desc)[:1200])
                elif method == "Log.entryAdded":
                    entry = event.get("params", {}).get("entry", {})
                    if entry.get("level") == "error":
                        errors.append(str(entry.get("text"))[:300])
                elif method == "Runtime.consoleAPICalled" and event.get("params", {}).get("type") == "error":
                    errors.append(str(event.get("params", {}).get("args"))[:300])
            if errors:
                raise SystemExit(f"前端存在控制台错误：{errors[:5]}")
            print("[PASS] 七个页面无控制台错误")
    finally:
        try:
            process.terminate()
            process.wait(timeout=10)
        except Exception:
            process.kill()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
    print("\nUI 冒烟检查全部通过 ✅")

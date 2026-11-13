#!/usr/bin/env python3
"""交互式浏览器会话驱动（computer use 风格）：用户手动登录，Agent 逐条发命令取页面/下载附件。

设计：打开一个**可见** Chrome（持久化 profile），用户自己完成统一身份认证登录；
之后脚本轮询 ``collect/session/cmd.txt`` 的新行，按命令操作浏览器，并把结果写到
``collect/session/pages/*.json``，供 Agent 读取后决定下一步。

命令（每行一条）：
    NAV  <url>     打开页面，等加载稳定后导出标题/正文/同源链接/内嵌附件
    PDF  <url>     用浏览器会话（带登录态）下载 PDF，保存到 session/pdfs/
    WAIT <seconds> 静默等待（拟人节奏）
    STOP           结束会话并关闭浏览器

安全：只操作用户可见窗口；不读取密码；不绕过验证码；每个命令之间默认停 2–4 秒。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import browser_collect as bc  # noqa: E402

SESSION_DIR = ROOT / "docs" / "npu-handbook" / "collect" / "session"
CMD_FILE = SESSION_DIR / "cmd.txt"
PAGES_DIR = SESSION_DIR / "pages"
PDF_DIR = SESSION_DIR / "pdfs"
EMBED_RE = re.compile(r"[\"'](/__local/[^\"']+\.(?:pdf|docx|xlsx))[\"']|showVsbpdfIframe\(\s*[\"']([^\"']+)[\"']", re.I)


class Driver:
    def __init__(self, port: int) -> None:
        self.port = port
        self.next_id = 0
        self.count = 0

    async def call(self, ws, method: str, params: dict | None = None, timeout: float = 40):
        self.next_id += 1
        mid = self.next_id
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if message.get("id") == mid:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def evaluate(self, ws, expression: str, timeout: float = 40):
        result = await self.call(
            ws,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("value")

    async def wait_ready(self, ws, settle: float = 2.0, timeout: float = 25.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.7)
            state = await self.evaluate(
                ws,
                "({ready: document.readyState, text: (document.body?document.body.innerText.length:0)})",
            ) or {}
            if state.get("ready") == "complete" and state.get("text", 0) > 150:
                break
        await asyncio.sleep(settle)

    async def navigate(self, ws, url: str) -> None:
        await self.call(ws, "Page.navigate", {"url": url})
        await self.wait_ready(ws)

    async def dump_page(self, ws, url: str) -> dict:
        self.count += 1
        data = await self.evaluate(
            ws,
            """(() => {
              const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return ''; } };
              const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
                t: (a.innerText || a.title || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
                h: abs(a.getAttribute('href'))
              })).filter(x => x.h && x.h.includes('nwpu.edu.cn')).slice(0, 300);
              return {
                url: location.href, title: document.title,
                text: (document.body ? document.body.innerText : '').slice(0, 30000),
                htmlLen: (document.documentElement ? document.documentElement.outerHTML.length : 0),
                links,
                embedded: (document.documentElement ? document.documentElement.outerHTML : '')
              };
            })()""",
        ) or {}
        html = data.pop("embedded", "") or ""
        files = set()
        for match in EMBED_RE.finditer(html):
            raw = match.group(1) or match.group(2) or ""
            if raw:
                files.add(urljoin(url, raw))
        data["files"] = sorted(files)
        data["seq"] = self.count
        data["requested"] = url
        PAGES_DIR.mkdir(parents=True, exist_ok=True)
        path = PAGES_DIR / f"{self.count:03d}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"PAGE {self.count} {data.get('url','')[:100]} | {data.get('title','')[:40]} | text={len(data.get('text') or '')} files={len(files)}", flush=True)
        return data

    async def download(self, ws, url: str) -> None:
        frame = (await self.call(ws, "Page.getFrameTree"))["frameTree"]["frame"]["id"]
        result = await self.call(
            ws,
            "Network.loadNetworkResource",
            {
                "url": url,
                "frameId": frame,
                "options": {"includeCredentials": True, "disableCache": False},
            },
            timeout=90,
        )
        resource = result.get("resource", {})
        stream = resource.get("stream")
        if not resource.get("success") or not stream:
            print(f"PDFFAIL {url[:120]} {resource.get('success')}", flush=True)
            return
        data = b""
        while True:
            chunk = await self.call(ws, "IO.read", {"handle": stream, "offset": len(data), "size": 4 * 1024 * 1024})
            data += base64.b64decode(chunk.get("data", ""))
            if chunk.get("eof"):
                break
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".pdf"
        (PDF_DIR / name).write_bytes(data)
        print(f"PDFOK {name} {len(data)} {url[:120]}", flush=True)


async def main_async(args: argparse.Namespace) -> int:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    CMD_FILE.write_text("", encoding="utf-8")
    profile = Path(args.profile or bc.default_profile())
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            bc.find_browser(),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={args.port}",
            "--window-size=1400,1000",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        deadline = time.time() + 30
        while time.time() < deadline and not ws_url:
            try:
                targets = httpx.get(f"http://127.0.0.1:{args.port}/json/list", timeout=2).json()
                pages = [item for item in targets if item.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.4)
        if not ws_url:
            print("CHROME_ERROR", flush=True)
            return 1
        driver = Driver(args.port)
        async with websockets.connect(ws_url, max_size=128 * 1024 * 1024) as ws:
            await driver.call(ws, "Page.enable")
            await driver.call(ws, "Runtime.enable")
            await driver.call(ws, "Network.enable")
            await driver.navigate(ws, args.start)
            print("READY 请先在窗口中完成登录；登录后我会开始逐页操作。", flush=True)
            print(f"session_dir={SESSION_DIR}", flush=True)
            offset = 0
            idle_deadline = time.time() + args.idle_timeout
            while time.time() < idle_deadline:
                await asyncio.sleep(1.0)
                try:
                    text = CMD_FILE.read_text(encoding="utf-8")
                except OSError:
                    continue
                if len(text) <= offset:
                    continue
                chunk = text[offset:]
                offset = len(text)
                for raw_line in chunk.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    command, _, rest = line.partition(" ")
                    command = command.upper()
                    rest = rest.strip()
                    if command == "STOP":
                        print("STOPPED", flush=True)
                        return 0
                    if command == "NAV" and rest:
                        try:
                            await driver.navigate(ws, rest)
                            await asyncio.sleep(2)
                            await driver.dump_page(ws, rest)
                        except Exception as exc:  # noqa: BLE001
                            print(f"NAVFAIL {rest[:120]} {type(exc).__name__}", flush=True)
                        continue
                    if command == "PDF" and rest:
                        try:
                            await driver.download(ws, rest)
                        except Exception as exc:  # noqa: BLE001
                            print(f"PDFFAIL {rest[:120]} {type(exc).__name__}", flush=True)
                        await asyncio.sleep(2)
                        continue
                    if command == "WAIT" and rest.isdigit():
                        await asyncio.sleep(min(60, int(rest)))
                        print(f"WAITED {rest}", flush=True)
                        continue
                idle_deadline = time.time() + args.idle_timeout
            print("IDLE_TIMEOUT", flush=True)
            return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="交互式浏览器会话驱动（用户登录 + Agent 逐页操作）")
    parser.add_argument("--start", default="https://ecampus.nwpu.edu.cn/", help="起始页面")
    parser.add_argument("--profile", default="", help="Chrome profile 目录（默认 NPU-Assistant 专用 profile）")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--idle-timeout", type=int, default=1800, help="无新命令多少秒后自动退出")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

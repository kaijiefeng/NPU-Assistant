"""Generate screenshots and short GIF demos for the end-user manual.

依赖：Pillow（`.\.venv\Scripts\python.exe -m pip install Pillow`），仅生成素材时需要。

Run with the dev server on :8000 (DeepSeek configured) and, for the token card,
a token-protected server on :8011 (NPU_ADMIN_TOKEN=manual-demo-2026).

Outputs:
  frontend/assets/manual/student-chat.png
  frontend/assets/manual/student-ask.gif
  frontend/assets/manual/quick-import.png
  frontend/assets/manual/quick-import.gif
  frontend/assets/manual/source-list.png
  frontend/assets/manual/token-card.png
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import websockets
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "assets" / "manual"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
BASE = "http://127.0.0.1:8000"
TOKEN_BASE = "http://127.0.0.1:8011"
TOKEN = "manual-demo-2026"
QUESTION = "国家奖学金怎么申请？需要哪些材料？"


class CDP:
    def __init__(self, title: str, width: int, height: int, dpr: float = 1.0, mobile: bool = False):
        self.title = title
        self.width = width
        self.height = height
        self.dpr = dpr
        self.mobile = mobile
        self.counter = 0
        self.ws = None
        self.proc = None

    async def __aenter__(self):
        profile = tempfile.mkdtemp(prefix="npu-manual-")
        self.proc = subprocess.Popen(
            [
                str(CHROME),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--remote-debugging-port=9231",
                "--window-size=%d,%d" % (self.width, self.height),
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ws_url = None
        for _ in range(80):
            try:
                data = httpx.get("http://127.0.0.1:9231/json/list", timeout=2).json()
                pages = [item for item in data if item.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        if not ws_url:
            raise RuntimeError("Chrome CDP 启动失败")
        self.ws = await websockets.connect(ws_url, max_size=30 * 1024 * 1024)
        await self.call("Page.enable")
        await self.call("Runtime.enable")
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.width,
                "height": self.height,
                "deviceScaleFactor": self.dpr,
                "mobile": self.mobile,
            },
        )
        return self

    async def __aexit__(self, *args):
        if self.ws:
            await self.ws.close()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()

    async def call(self, method: str, params: dict | None = None):
        self.counter += 1
        mid = self.counter
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == mid:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def eval(self, expression: str):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    async def goto(self, url: str, wait: float = 2.5):
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(wait)

    async def png(self) -> bytes:
        result = await self.call("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(result["data"])

    async def jpg(self) -> bytes:
        result = await self.call(
            "Page.captureScreenshot", {"format": "jpeg", "quality": 82}
        )
        return base64.b64decode(result["data"])

    async def clip_png(self, selector: str, padding: int = 16) -> bytes | None:
        rect = await self.eval(
            f"""(() => {{
              const el = document.querySelector({json.dumps(selector)});
              if (!el) return null;
              el.scrollIntoView({{block: 'center'}});
              const r = el.getBoundingClientRect();
              return {{x: r.left, y: r.top, width: r.width, height: r.height}};
            }})()"""
        )
        if not rect:
            return None
        await asyncio.sleep(0.4)
        rect = await self.eval(
            f"""(() => {{
              const el = document.querySelector({json.dumps(selector)});
              const r = el.getBoundingClientRect();
              return {{x: r.left, y: r.top, width: r.width, height: r.height}};
            }})()"""
        )
        clip = {
            "x": max(0, rect["x"] - padding),
            "y": max(0, rect["y"] - padding),
            "width": min(self.width - max(0, rect["x"] - padding), rect["width"] + padding * 2),
            "height": min(self.height - max(0, rect["y"] - padding), rect["height"] + padding * 2),
            "scale": 1,
        }
        result = await self.call("Page.captureScreenshot", {"format": "png", "clip": clip})
        return base64.b64decode(result["data"])


def frames_to_gif(frames: list[bytes], path: Path, width: int, duration: int = 500, colors: int = 96):
    images = []
    for raw in frames:
        image = Image.open(__import__("io").BytesIO(raw)).convert("RGB")
        if image.width != width:
            height = round(image.height * width / image.width)
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        images.append(image.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors))
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=duration,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print("gif", path.name, len(frames), "frames", path.stat().st_size, "bytes")


async def mobile_chat_shot() -> None:
    async with CDP("mobile-chat", 390, 844, dpr=2, mobile=True) as cdp:
        await cdp.goto(BASE + "/#/chat", 3)
        clicked = await cdp.eval(
            """(() => {
              const item = document.querySelector('.history-item');
              if (item) { item.click(); return true; }
              return false;
            })()"""
        )
        print("history item clicked:", clicked)
        await asyncio.sleep(2.5)
        await cdp.eval(
            """(() => {
              const scroll = document.querySelector('#chatScroll');
              if (scroll) scroll.scrollTop = 999999;
            })()"""
        )
        await asyncio.sleep(0.6)
        (OUT / "student-mobile-chat.png").write_bytes(await cdp.png())


async def chat_demo() -> None:
    async with CDP("chat", 1440, 960) as cdp:
        await cdp.goto(BASE + "/#/chat", 3)
        frames: list[bytes] = []
        await cdp.eval("document.querySelector('#chatInput').value = ''")
        for end in range(4, len(QUESTION) + 1, 4):
            await cdp.eval(
                "(function(t){const el=document.querySelector('#chatInput');"
                "el.value=t;el.dispatchEvent(new Event('input',{bubbles:true}));})("
                + json.dumps(QUESTION[:end], ensure_ascii=False)
                + ")"
            )
            await asyncio.sleep(0.07)
            frames.append(await cdp.jpg())
        await cdp.eval(
            "(function(t){const el=document.querySelector('#chatInput');"
            "el.value=t;el.dispatchEvent(new Event('input',{bubbles:true}));})("
            + json.dumps(QUESTION, ensure_ascii=False)
            + ")"
        )
        await asyncio.sleep(0.3)
        frames.append(await cdp.jpg())
        await cdp.eval("document.querySelector('#sendBtn').click()")
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            await asyncio.sleep(0.55)
            frames.append(await cdp.jpg())
            done = await cdp.eval(
                """(() => {
                  const answers = [...document.querySelectorAll('.msg-assistant')]
                    .filter(n => !n.classList.contains('is-typing'));
                  const last = answers[answers.length - 1];
                  return !!(last && last.innerText.length > 120 && !document.querySelector('#typingMessage'));
                })()"""
            )
            if done and len(frames) > 14:
                break
        await asyncio.sleep(0.8)
        frames.append(await cdp.jpg())
        await cdp.eval("document.querySelector('#chatScroll').scrollTop = 999999")
        await asyncio.sleep(0.6)
        frames.append(await cdp.jpg())
        (OUT / "student-chat.png").write_bytes(await cdp.png())
        frames_to_gif(frames, OUT / "student-ask.gif", width=720, duration=450)


async def quick_import_demo() -> None:
    async with CDP("mobile", 390, 844, dpr=1, mobile=True) as cdp:
        await cdp.goto(BASE + "/#/knowledge", 3)
        await cdp.eval(
            "(function(){const el=document.querySelector('#captureUrls');"
            "el.value='';el.dispatchEvent(new Event('input',{bubbles:true}));})()"
        )
        frames: list[bytes] = []
        url = "https://www.nwpu.edu.cn/"
        for end in range(3, len(url) + 1, 3):
            await cdp.eval(
                "(function(t){const el=document.querySelector('#captureUrls');"
                "el.value=t;el.dispatchEvent(new Event('input',{bubbles:true}));})("
                + json.dumps(url[:end])
                + ")"
            )
            await asyncio.sleep(0.08)
            frames.append(await cdp.jpg())
        await cdp.eval(
            "(function(t){const el=document.querySelector('#captureUrls');"
            "el.value=t;el.dispatchEvent(new Event('input',{bubbles:true}));})("
            + json.dumps(url)
            + ")"
        )
        await asyncio.sleep(0.3)
        frames.append(await cdp.jpg())
        await cdp.eval("document.querySelector('#importLinksBtn').click()")
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            frames.append(await cdp.jpg())
            state = await cdp.eval(
                """(() => {
                  const box = document.querySelector('#captureLinkResults');
                  return box ? box.innerText : '';
                })()"""
            )
            if "已收录" in state or "失败" in state or "重复" in state:
                break
        await asyncio.sleep(0.8)
        frames.append(await cdp.jpg())
        await cdp.eval(
            """(() => {
              const box = document.querySelector('#captureLinkResults');
              if (box) box.scrollIntoView({block: 'center'});
            })()"""
        )
        await asyncio.sleep(0.6)
        frames.append(await cdp.jpg())
        frames_to_gif(frames, OUT / "quick-import.gif", width=390, duration=450)

        # 高质量静态截图（等 toast 消失、回到顶部）
        await asyncio.sleep(3.2)
        await cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True},
        )
        await asyncio.sleep(0.5)
        await cdp.eval("document.querySelector('#view').scrollTop = 0")
        (OUT / "quick-import.png").write_bytes(await cdp.png())

        # 源文件列表：切到源文件 tab 并截图
        clicked = await cdp.eval(
            """(() => {
              const tabs = [...document.querySelectorAll('.tab, button')];
              const t = tabs.find(el => el.textContent.trim().includes('源文件'));
              if (t) { t.click(); return true; }
              return false;
            })()"""
        )
        print("source tab clicked:", clicked)
        await asyncio.sleep(2)
        await cdp.eval(
            """(() => {
              const view = document.querySelector('#view');
              const panel = document.querySelector('#sourcesPanel');
              if (view && panel) {
                const delta = panel.getBoundingClientRect().top - view.getBoundingClientRect().top;
                view.scrollTop += delta - 70;
              }
            })()"""
        )
        await asyncio.sleep(0.8)
        (OUT / "source-list.png").write_bytes(await cdp.png())


async def token_card_shot() -> None:
    try:
        async with CDP("token", 1440, 960) as cdp:
            await cdp.goto(TOKEN_BASE + "/#/settings", 2.5)
            await asyncio.sleep(1)
            await cdp.eval(
                """(() => {
                  const input = document.querySelector('#adminTokenInput');
                  input.value = %s;
                  input.dispatchEvent(new Event('input', {bubbles: true}));
                  const save = [...document.querySelectorAll('[data-action="save-token"]')][0];
                  if (save) save.click();
                  return true;
                })()"""
                % json.dumps(TOKEN)
            )
            await asyncio.sleep(1.2)
            data = await cdp.clip_png("#adminTokenCard", padding=14)
            if data:
                (OUT / "token-card.png").write_bytes(data)
                print("saved token-card.png")
    except Exception as exc:
        print("token card skipped:", exc)


async def cleanup() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)
    for item in client.get("/api/chat/conversations", params={"limit": 100}).json().get("items", []):
        client.delete(f"/api/chat/conversations/{item['id']}")
    for doc in client.get("/api/knowledge/documents").json().get("items", []):
        title = doc.get("title") or ""
        source_url = doc.get("source_url") or ""
        if title in {"Example Domain", "example.co", "西北工业大学"} or source_url in {
            "https://example.com/",
            "https://example.com",
            "https://example.co",
            "https://www.nwpu.edu.cn/",
        }:
            client.delete(f"/api/knowledge/documents/{doc['id']}")
    print("cleanup done")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    await chat_demo()
    await mobile_chat_shot()
    await quick_import_demo()
    await token_card_shot()
    await cleanup()


if __name__ == "__main__":
    asyncio.run(main())

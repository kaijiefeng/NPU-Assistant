#!/usr/bin/env python3
"""用真实 Chrome（CDP）采集被 WAF 拦截的公开页面 —— 「浏览器渲染式」抓取。

背景：``scripts/handbook_collect.py`` 用 httpx 直接请求，`job.nwpu.edu.cn`、
`zsb.nwpu.edu.cn`、`tushuguan.nwpu.edu.cn` 等站点返回 412（WAF）或 403。
实测：真实 Chrome（CDP）以普通 UA 渲染后，WAF 挑战由页面 JS 自行完成，正文可正常读取。
本脚本因此复用项目已有的 CDP 方式（同 ``scripts/ui_check.py``，仅新增标准库之外的 websockets，
已是现有依赖），把渲染后的 HTML 交给 ``services.importer.extract_article`` 提取正文。

边界：
* 只渲染公开页面，不登录、不输入验证码、不点击任何授权按钮；遇验证码/登录墙直接跳过；
* 同域限速、单站限量、URL 去重，复用 ``handbook_collect`` 的清单与分类逻辑；
* 仅作为 httpx 采集的补充，产出仍写入 ``docs/npu-handbook/collect/``。

用法::

    python scripts/browser_collect.py --sites tushuguan --limit 3          # 小样本试跑
    python scripts/browser_collect.py --sites job,zsb,tushuguan --limit 80 # 补采
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import websockets
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.services import importer  # noqa: E402
from handbook_collect import (  # noqa: E402
    CORPUS_DIR,
    MANIFEST,
    MAX_CHARS,
    MIN_CHARS,
    OUT_DIR,
    article_markdown,
    classify,
    clean_title,
    doc_id,
    extract_date,
    load_existing_urls,
    normalize_audience,
    site_of,
)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
COLUMN_HINT = re.compile(
    r"(通知|公告|制度|规章|政策|文件|公开|新闻|动态|服务|办事|指南|下载|工作|管理|"
    r"招生|就业|借阅|入馆|数据库|预约|培训|党建|科研|教学|培养|学位|资助|奖助|"
    r"心理|后勤|安全|图书|医保|缴费|财务|宿舍|公寓|迎新|校历|档案|招聘|双选|三方|选调)",
    re.I,
)
NAV_NOISE = re.compile(r"^(首页|返回|更多|more|上一页|下一页|关闭|打印|English|登录|注册)$", re.I)
# 详情页路径：NWPU CMS 两种形态——/info/2141/31057.htm 与 /f/newsCenter/article/<32位hex>。
DETAIL_PATH_RE = re.compile(
    r"(?:/info/\d+/\d+\.(?:htm|html)$|/(?:f/)?newsCenter/article/[0-9a-f]{16,}$"
    r"|/(?:article|detail|news|content|show)/[0-9a-f]{16,}$)",
    re.I,
)

SITES = {
    "job": {
        "origin": "https://job.nwpu.edu.cn",
        "name": "就业信息网",
        "audience": "毕业生",
        "category": "就业与档案",
    },
    "zsb": {
        "origin": "https://zsb.nwpu.edu.cn",
        "name": "本科招生信息网",
        "audience": "本科新生",
        "category": "招生与入学",
    },
    "tushuguan": {
        "origin": "https://tushuguan.nwpu.edu.cn",
        "name": "图书馆",
        "audience": "全体",
        "category": "图书馆与文体",
    },
    "library": {
        "origin": "https://library.nwpu.edu.cn",
        "name": "图书馆（旧域名）",
        "audience": "全体",
        "category": "图书馆与文体",
    },
}


class CDP:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.next_id = 0

    async def call(self, method: str, params: dict | None = None, timeout: float = 30):
        self.next_id += 1
        message_id = self.next_id
        await self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})

    async def evaluate(self, expression: str, timeout: float = 30):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("value")


async def render(cdp: CDP, url: str, timeout: float = 20.0) -> str:
    """导航并等待页面稳定（含 WAF 挑战），返回渲染后的 outerHTML。"""
    await cdp.call("Page.navigate", {"url": url})
    deadline = time.time() + timeout
    html = ""
    while time.time() < deadline:
        await asyncio.sleep(0.6)
        state = await cdp.evaluate(
            "({ready: document.readyState, text: (document.body?document.body.innerText.length:0)})"
        ) or {}
        if state.get("ready") == "complete" and state.get("text", 0) > 200:
            break
    await asyncio.sleep(0.4)
    html = await cdp.evaluate("document.documentElement ? document.documentElement.outerHTML : ''")
    return html or ""


def same_origin(base: str, href: str) -> bool:
    return urlsplit(href).netloc == urlsplit(base).netloc


def detail_links(html: str, base: str, limit: int = 30) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"].strip()).split("#", 1)[0]
        if not href.startswith("http") or not same_origin(base, href):
            continue
        path = urlsplit(href).path.lower()
        query = urlsplit(href).query.lower()
        is_detail = bool(DETAIL_PATH_RE.search(path))
        is_detail = is_detail or bool(
            re.search(r"(detail|article|news|content|show|view|ggxx|tzgg)", path)
            and (re.search(r"(id|info|news|article)=", query) or path.endswith((".htm", ".html", "/")))
        )
        if not is_detail:
            continue
        text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
        text = anchor.get("title") or text
        text = clean_title(text)
        if not text or len(text) < 4 or NAV_NOISE.match(text):
            continue
        if href in seen:
            continue
        seen.add(href)
        result.append((href, text))
        if len(result) >= limit:
            break
    return result


def column_links(html: str, base: str, limit: int = 12) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"].strip()).split("#", 1)[0]
        if not href.startswith("http") or not same_origin(base, href):
            continue
        if DETAIL_PATH_RE.search(urlsplit(href).path):
            continue
        text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
        if not text or len(text) > 20 or NAV_NOISE.match(text):
            continue
        if not COLUMN_HINT.search(text) and not COLUMN_HINT.search(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        result.append((href, text))
        if len(result) >= limit:
            break
    return result


async def collect_site(
    ws_url: str,
    site_id: str,
    limit: int,
    existing: set[str],
    lock: asyncio.Lock,
    delay: float,
    per_column: int,
) -> dict:
    site = SITES[site_id]
    origin = site["origin"]
    summary = {"site": site_id, "collected": 0, "columns": 0, "failed": 0, "errors": []}
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        # 关键：WAF 会拦截 HeadlessChrome UA；用普通 Chrome UA 让页面 JS 自行完成挑战。
        await cdp.call(
            "Network.setUserAgentOverride",
            {"userAgent": BROWSER_UA, "acceptLanguage": "zh-CN,zh;q=0.9"},
        )
        try:
            home = await render(cdp, origin + "/")
            columns = column_links(home, origin + "/")
            summary["columns"] = len(columns)
            for column_url, column_label in columns:
                if summary["collected"] >= limit:
                    break
                await asyncio.sleep(delay)
                try:
                    listing = await render(cdp, column_url)
                except Exception as exc:  # noqa: BLE001
                    summary["errors"].append(f"{column_url}: {type(exc).__name__}")
                    continue
                taken = 0
                for detail_url, link_title in detail_links(listing, column_url):
                    if summary["collected"] >= limit or taken >= per_column:
                        break
                    async with lock:
                        if detail_url in existing:
                            continue
                    await asyncio.sleep(delay)
                    try:
                        page = await render(cdp, detail_url)
                        article = importer.extract_article(page, content_type="text/html")
                    except Exception as exc:  # noqa: BLE001
                        summary["failed"] += 1
                        continue
                    text = (article.get("text") or "").strip()
                    if len(text) < MIN_CHARS:
                        continue
                    title = clean_title(article.get("title") or link_title)
                    category, doc_type = classify(
                        title, f"{detail_url} {column_url} {column_label}", default=site["category"]
                    )
                    content_id = doc_id(detail_url)
                    record = {
                        "id": content_id,
                        "url": detail_url,
                        "title": title,
                        "site": site_of(detail_url),
                        "source_name": site["name"],
                        "category": category,
                        "doc_type": doc_type,
                        "audience": normalize_audience(site["audience"]),
                        "publish_date": extract_date(title),
                        "collected_at": datetime.now().astimezone().date().isoformat(),
                        "chars": len(text),
                        "sha256": hashlib_sha256(text),
                        "file": f"corpus/{content_id}.md",
                        "list_page": column_url,
                        "collector": "browser-cdp",
                    }
                    async with lock:
                        if detail_url in existing:
                            continue
                        body = text[:MAX_CHARS]
                        (OUT_DIR / record["file"]).write_text(
                            article_markdown(record, body), encoding="utf-8"
                        )
                        with MANIFEST.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        existing.add(detail_url)
                        summary["collected"] += 1
                        taken += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{origin}: {type(exc).__name__}: {str(exc)[:120]}")
    return summary


def hashlib_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_browser() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("未找到 Chrome/Edge，无法执行浏览器渲染采集")


async def run(args) -> int:
    sites = [item.strip() for item in args.sites.split(",") if item.strip() in SITES]
    if not sites:
        print(f"可用站点：{', '.join(SITES)}")
        return 2
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_existing_urls()
    lock = asyncio.Lock()
    browser = find_browser()
    profile = tempfile.mkdtemp(prefix="npu-cdp-collect-")
    port = args.port
    process = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--blink-settings=imagesEnabled=false",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--window-size=1440,960",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
                break
            except Exception:
                await asyncio.sleep(0.4)

        async def worker(site_id: str):
            response = httpx.put(f"http://127.0.0.1:{port}/json/new?about:blank", timeout=10)
            target = response.json()
            ws_url = target["webSocketDebuggerUrl"]
            result = await collect_site(
                ws_url, site_id, args.limit, existing, lock, args.delay, args.per_column
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

        results = await asyncio.gather(*(worker(site_id) for site_id in sites))
        total = sum(row["collected"] for row in results)
        print(f"\n浏览器渲染采集完成：{total} 篇 / {len(results)} 个站点", flush=True)
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实 Chrome 渲染采集 WAF 站点的公开页面")
    parser.add_argument("--sites", default="job,zsb,tushuguan", help="逗号分隔：job / zsb / tushuguan / library")
    parser.add_argument("--limit", type=int, default=60, help="每个站点最多采集篇数")
    parser.add_argument("--per-column", type=int, default=10, help="每个栏目最多采集篇数")
    parser.add_argument("--delay", type=float, default=0.6, help="同站请求间隔秒")
    parser.add_argument("--port", type=int, default=9333, help="Chrome 调试端口")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

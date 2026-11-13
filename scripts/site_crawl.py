#!/usr/bin/env python3
"""按站点做受限全量采集：栏目页 → 详情页 → 正文 + 附件（PDF/DOCX/XLSX）。

用于「整站公开、无需登录」的信息公开类网站（如 ``xxgk.nwpu.edu.cn``）：

* 同源 BFS，只抓 HTML 导航页与详情页，默认只允许指定路径前缀；
* 详情页（``/info/``）提取正文；页面里的 PDF/DOCX/XLSX 附件下载并解析文本；
* 统一去重、限速、个人信息脱敏，写入 ``docs/npu-handbook/collect/``。

用法::

    python scripts/site_crawl.py --seed https://xxgk.nwpu.edu.cn/ --max-pages 400 --limit 600
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.services import importer  # noqa: E402
from handbook_collect import (  # noqa: E402
    MAX_CHARS,
    MIN_CHARS,
    OUT_DIR,
    MANIFEST,
    article_markdown,
    classify,
    clean_title,
    doc_id,
    extract_date,
    load_existing_urls,
    load_manifest,
    normalize_audience,
    sanitize_text,
    site_of,
)

logger = logging.getLogger("npu-assistant.sitecrawl")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_BYTES = 10 * 1024 * 1024
DETAIL_RE = re.compile(r"/info/\d+/\d+\.(?:htm|html)$", re.I)
ATTACH_RE = re.compile(r"\.(pdf|docx|xlsx)$", re.I)
SKIP_EXT = re.compile(r"\.(jpg|jpeg|png|gif|css|js|zip|rar|doc|xls|ppt|pptx|mp4|mov|apk|exe)$", re.I)


def is_html_response(response: httpx.Response) -> bool:
    ctype = (response.headers.get("content-type") or "").lower()
    return "html" in ctype or "xml" in ctype


def parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(parts)


def parse_docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def parse_xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"# {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell) for cell in row if cell is not None]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def parse_attachment(data: bytes, url: str) -> str:
    path = urlsplit(url).path.lower()
    try:
        if path.endswith(".pdf"):
            return parse_pdf(data)
        if path.endswith(".docx"):
            return parse_docx(data)
        if path.endswith(".xlsx"):
            return parse_xlsx(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("附件解析失败 %s：%s", url, exc)
    return ""


def embedded_files(html: str, base: str) -> list[str]:
    """提取页面里内嵌的 /__local 附件（xxgk 用 showVsbpdfIframe 嵌入 PDF）。"""
    result: list[str] = []
    patterns = [
        r"showVsbpdfIframe\(\s*[\"']([^\"']+)[\"']",
        r"[\"'](/__local/[^\"']+\.(?:pdf|docx|xlsx))[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html or "", re.I):
            url = urljoin(base, match.group(1))
            if ATTACH_RE.search(urlsplit(url).path) and url not in result:
                result.append(url)
    return result


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t\u3000]+", " ", text or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class Crawler:
    def __init__(self, args) -> None:
        self.args = args
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"},
        )
        self.queue: deque[tuple[str, str]] = deque()
        self.visited: set[str] = set()
        self.existing = load_existing_urls()
        self.seen_sha = {str(row.get("sha256")) for row in load_manifest() if row.get("sha256")}
        self.manifest = MANIFEST.open("a", encoding="utf-8")
        self.added = 0
        self.pages = 0
        self.failed = 0
        self.attachments = 0

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.netloc != self.args.host:
            return False
        if SKIP_EXT.search(parts.path):
            return False
        if self.args.paths and not any(parts.path.startswith(prefix) for prefix in self.args.paths):
            return False
        return True

    def enqueue(self, url: str, title: str = "") -> None:
        url = url.split("#", 1)[0]
        if url in self.visited or not self.allowed(url):
            return
        if any(url == item for item, _ in self.queue):
            return
        self.queue.append((url, title))

    def save(self, url: str, title: str, text: str, site_name: str, parent: str = "") -> None:
        if self.added >= self.args.limit:
            return
        text, _hits, drop = sanitize_text(text, title)
        if drop or len(text) < MIN_CHARS:
            return
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if sha in self.seen_sha:
            return
        category, doc_type = classify(title, f"{url} {parent}")
        content_id = doc_id(url)
        record = {
            "id": content_id,
            "url": url,
            "title": title,
            "site": site_of(url),
            "source_name": site_name,
            "category": category,
            "doc_type": doc_type,
            "audience": normalize_audience("全体"),
            "publish_date": extract_date(title),
            "collected_at": datetime.now().astimezone().date().isoformat(),
            "chars": len(text),
            "sha256": sha,
            "file": f"corpus/{content_id}.md",
            "list_page": parent,
            "collector": "site-crawl",
        }
        (OUT_DIR / record["file"]).write_text(
            article_markdown(record, text[:MAX_CHARS]), encoding="utf-8"
        )
        self.manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.manifest.flush()
        self.existing.add(url)
        self.seen_sha.add(sha)
        self.added += 1
        if self.added % 10 == 0:
            logger.info("进度：文档 %s，已访问页面 %s，附件 %s", self.added, self.pages, self.attachments)

    def handle_attachment(self, url: str, title: str, site_name: str, parent: str) -> None:
        if url in self.existing or self.attachments > self.args.max_attachments:
            return
        try:
            time.sleep(self.args.delay)
            response = self.client.get(url)
            data = response.content or b""
            if response.status_code != 200 or len(data) > MAX_BYTES:
                return
            text = clean_text(parse_attachment(data, url))
        except Exception as exc:  # noqa: BLE001
            logger.debug("附件下载失败 %s：%s", url, exc)
            return
        self.attachments += 1
        if text:
            self.save(url, title, text, site_name, parent)

    def run(self) -> int:
        host = urlsplit(self.args.seed).netloc
        site_name = self.args.site_name or host
        self.enqueue(self.args.seed, site_name)
        while self.queue and self.pages < self.args.max_pages and self.added < self.args.limit:
            url, title = self.queue.popleft()
            if url in self.visited:
                continue
            self.visited.add(url)
            try:
                time.sleep(self.args.delay)
                response = self.client.get(url)
                self.pages += 1
            except Exception as exc:  # noqa: BLE001
                self.failed += 1
                logger.debug("抓取失败 %s：%s", url, exc)
                continue
            if response.status_code != 200:
                self.failed += 1
                continue
            if not is_html_response(response):
                continue
            html = response.text
            is_detail = bool(DETAIL_RE.search(urlsplit(url).path))
            if is_detail:
                try:
                    article = importer.extract_article(html, content_type="text/html")
                    text = clean_text(article.get("text") or "")
                    page_title = clean_title(article.get("title") or title or url)
                except Exception:  # noqa: BLE001
                    text, page_title = "", clean_title(title or url)
                page_title = clean_title(re.sub(r"[-—]\s*学校办信息公开网$", "", page_title))
                embedded = embedded_files(html, url)
                if embedded:
                    # 正文是内嵌 PDF：以下载的 PDF 文本为准，避免只存到导航文字。
                    for file_url in embedded[:2]:
                        self.handle_attachment(file_url, page_title, site_name, url)
                elif text:
                    self.save(url, page_title, text, site_name, title)
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = urljoin(url, anchor["href"].strip()).split("#", 1)[0]
                anchor_text = clean_title(anchor.get_text(" ", strip=True)) or clean_title(anchor.get("title") or "")
                if ATTACH_RE.search(urlsplit(href).path):
                    self.handle_attachment(href, anchor_text or title, site_name, url)
                else:
                    self.enqueue(href, anchor_text)
            for tag in soup.find_all(["iframe", "embed", "object"]):
                src = tag.get("src") or tag.get("data") or ""
                if src:
                    href = urljoin(url, src)
                    if ATTACH_RE.search(urlsplit(href).path):
                        self.handle_attachment(href, title, site_name, url)
        self.manifest.close()
        logger.info(
            "站点采集结束：文档 %s（附件 %s），访问页面 %s，失败 %s",
            self.added,
            self.attachments,
            self.pages,
            self.failed,
        )
        return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="受限整站采集（栏目 → 详情 → 附件）")
    parser.add_argument("--seed", required=True, help="起始 URL")
    parser.add_argument("--host", default="", help="限定域名（默认取 seed 的 host）")
    parser.add_argument("--paths", default="", help="逗号分隔的允许路径前缀，默认不限制")
    parser.add_argument("--max-pages", type=int, default=300, help="最多访问 HTML 页面数")
    parser.add_argument("--max-attachments", type=int, default=200, help="最多下载附件数")
    parser.add_argument("--limit", type=int, default=400, help="最多新增文档数")
    parser.add_argument("--delay", type=float, default=0.4, help="请求间隔秒")
    parser.add_argument("--site-name", default="", help="来源名称")
    args = parser.parse_args()
    if not args.host:
        args.host = urlsplit(args.seed).netloc
    args.paths = [item.strip() for item in args.paths.split(",") if item.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return Crawler(args).run()


if __name__ == "__main__":
    raise SystemExit(main())

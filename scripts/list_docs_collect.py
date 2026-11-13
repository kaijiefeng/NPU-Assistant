#!/usr/bin/env python3
"""按「列表页 → 详情页 → PDF 附件/正文」采集公开制度文档。

适用场景：某栏目（如 `xgb.nwpu.edu.cn/xxgk/zdgk.htm`）列表里是详情页，
详情页正文很短、原件是可直接下载的 PDF。本脚本：

1. 抓列表页（可自动翻页），用 ``campus_sync.parse_list_page`` 提取详情链接；
2. 逐条抓详情页，优先下载其中的 PDF（``*.pdf`` / ``download.jsp`` / ``__local``），
   ``pypdf`` 提取正文；没有 PDF 时退回 ``importer.extract_article`` 提取 HTML 正文；
3. 标题优先用列表页的链接文字（比 PDF 首页更像制度名）；
4. 统一做个人信息脱敏，写入 ``docs/npu-handbook/collect/corpus/`` 与 ``manifest.jsonl``。

用法::

    python scripts/list_docs_collect.py --list https://xgb.nwpu.edu.cn/xxgk/zdgk.htm --pages 5 --limit 80
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
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.services import campus_sync as cs  # noqa: E402
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

logger = logging.getLogger("npu-assistant.listdocs")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PDF_HINT = re.compile(r"(\.pdf$|/__local/|download\.jsp)", re.I)
MAX_PDF_BYTES = 8 * 1024 * 1024


def is_nwpu(url: str) -> bool:
    return urlsplit(url).netloc.lower().endswith("nwpu.edu.cn")


def pdf_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    result: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"].strip()).split("#", 1)[0]
        if is_nwpu(href) and PDF_HINT.search(href) and href not in result:
            result.append(href)
    for tag in soup.find_all(["iframe", "embed", "object"]):
        src = tag.get("src") or tag.get("data") or ""
        href = urljoin(base, src.split("#", 1)[0])
        if href and is_nwpu(href) and PDF_HINT.search(href) and href not in result:
            result.append(href)
    return result


def pdf_text(client: httpx.Client, url: str) -> str:
    from pypdf import PdfReader

    response = client.get(url)
    data = response.content or b""
    if response.status_code != 200 or len(data) > MAX_PDF_BYTES:
        return ""
    if not ("pdf" in (response.headers.get("content-type") or "").lower() or data.startswith(b"%PDF")):
        return ""
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(parts)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def list_detail_links(client: httpx.Client, list_url: str) -> list[tuple[str, str]]:
    response = client.get(list_url)
    response.raise_for_status()
    return [(item.url, item.title) for item in cs.parse_list_page(response.text, list_url)]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="列表页 → 详情页 → PDF 制度文档采集")
    parser.add_argument("--list", action="append", required=True, help="列表页 URL，可重复")
    parser.add_argument("--pages", type=int, default=1, help="自动翻页数（列表页 /1.htm、/2.htm…）")
    parser.add_argument("--limit", type=int, default=80, help="最多新增篇数")
    parser.add_argument("--delay", type=float, default=0.5, help="请求间隔秒")
    parser.add_argument("--site-name", default="", help="来源名称，默认取域名")
    args = parser.parse_args()

    existing = load_existing_urls()
    seen_sha = {str(row.get("sha256")) for row in load_manifest() if row.get("sha256")}
    added = skipped = failed = 0
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"}
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
        tasks: list[tuple[str, str]] = []
        for list_url in args.list:
            for page_index in range(0, max(1, args.pages)):
                if page_index == 0:
                    page_url = list_url
                else:
                    base, ext = list_url.rsplit(".", 1)
                    page_url = f"{base}/{page_index}.{ext}" if not base.endswith("/") else list_url
                try:
                    time.sleep(args.delay)
                    links = list_detail_links(client, page_url)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("列表页失败 %s：%s", page_url, exc)
                    continue
                logger.info("列表页 %s → %s 条", page_url, len(links))
                tasks.extend(links)
        # 去重
        deduped: list[tuple[str, str]] = []
        seen_url: set[str] = set()
        for url, title in tasks:
            if url in seen_url:
                continue
            seen_url.add(url)
            deduped.append((url, title))
        logger.info("详情页 %s 个", len(deduped))

        with MANIFEST.open("a", encoding="utf-8") as handle:
            for detail_url, list_title in deduped:
                if added >= args.limit:
                    break
                # 不跳过已采详情页：之前可能只存了 HTML 正文，需要再扫描其中的 PDF 附件。
                try:
                    time.sleep(args.delay)
                    detail = client.get(detail_url)
                    detail.raise_for_status()
                    detail_html = detail.text
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.debug("详情失败 %s：%s", detail_url, exc)
                    continue
                title_seed = clean_title(re.sub(r"^【[^】]{1,8}】", "", list_title))
                pdfs = pdf_links(detail_html, detail_url)
                logger.debug("详情 %s PDF=%s", detail_url, len(pdfs))
                contents: list[tuple[str, str]] = []
                if pdfs:
                    for pdf_url in pdfs[:2]:
                        try:
                            time.sleep(args.delay)
                            text = pdf_text(client, pdf_url)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("PDF 失败 %s：%s", pdf_url, exc)
                            continue
                        if len(text) >= MIN_CHARS:
                            contents.append((pdf_url, text))
                if not contents:
                    try:
                        article = importer.extract_article(detail_html, content_type="text/html")
                        text = (article.get("text") or "").strip()
                        if len(text) >= MIN_CHARS:
                            contents.append((detail_url, text))
                    except Exception:  # noqa: BLE001
                        pass
                if not contents:
                    skipped += 1
                    continue
                for doc_url, raw_text in contents:
                    text, _hits, drop = sanitize_text(raw_text, title_seed)
                    if drop or len(text) < MIN_CHARS:
                        skipped += 1
                        continue
                    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    if sha in seen_sha:
                        skipped += 1
                        continue
                    title = title_seed or clean_title(next(iter(text.splitlines()), ""))
                    category, doc_type = classify(title, f"{doc_url} {detail_url}")
                    if doc_type == "其他":
                        doc_type = "制度规章"
                    content_id = doc_id(doc_url)
                    record = {
                        "id": content_id,
                        "url": doc_url,
                        "title": title,
                        "site": site_of(detail_url),
                        "source_name": args.site_name or site_of(detail_url),
                        "category": category,
                        "doc_type": doc_type,
                        "audience": normalize_audience("学生"),
                        "publish_date": extract_date(title),
                        "collected_at": datetime.now().astimezone().date().isoformat(),
                        "chars": len(text),
                        "sha256": sha,
                        "file": f"corpus/{content_id}.md",
                        "list_page": args.list[0],
                        "detail_page": detail_url,
                        "collector": "list-docs",
                    }
                    (OUT_DIR / record["file"]).write_text(
                        article_markdown(record, text[:MAX_CHARS]), encoding="utf-8"
                    )
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    existing.add(doc_url)
                    seen_sha.add(sha)
                    added += 1
                    logger.info("+ [%s] %s", doc_type, title[:50])
    logger.info("列表采集结束：新增 %s，跳过 %s，失败 %s", added, skipped, failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

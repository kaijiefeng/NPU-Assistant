"""人工队列分流：互联网抓取 → 自动下载 → 标记人工接管。

对 docs/campus-seed/manual-queue.json 中的每条：
1. 先尝试直接抓公开页面/正文；正文够长则直接导入（互联网抓取）。
2. 页面内嵌 PDF/附件则自动下载；PDF 有文本则导入，扫描件保存后标记 needs_ocr。
3. 403/412、验证码、正文过短且无附件 → 标记 needs_browser / needs_captcha，
   交给 CUA 或人工；不自动破解验证码。

默认 dry-run；使用 --apply 才导入/写文件/更新队列状态。
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer, ingest  # noqa: E402
from app import db  # noqa: E402

QUEUE_PATH = ROOT / "docs/campus-seed/manual-queue.json"
RESULTS_PATH = ROOT / "docs/campus-seed/manual-queue-results.json"
FILES_DIR = ROOT / "backend/data/campus_manual/files"
UA = "NPU-Assistant campus-sync/0.1"
CAPTCHA_MARKERS = ("验证码", "captcha", "人机验证", "安全验证")
WAF_MARKERS = (
    "访问被拒绝",
    "请求被拒绝",
    "403 Forbidden",
    "安全拦截",
    "Web应用防火墙",
    "请稍后再试",
)
CONTENT_MARKERS = ("vsb_content", "v_news_content", "js_content", "rich_media_content")
PDF_RE = re.compile(r"""showVsbpdfIframe\(\s*["']([^"']+\.pdf)""", re.I)
ATTACH_RE = re.compile(r"\.(pdf|docx?|xlsx?)(?:[?#]|$)", re.I)


def log(message: str) -> None:
    logging.info(message)


def load_queue() -> dict:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def save_queue(queue: dict) -> None:
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def save_results(results: list[dict]) -> None:
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch(url: str, referer: str = "") -> httpx.Response:
    headers = {"User-Agent": UA, "Accept": "text/html,application/pdf,*/*"}
    if referer:
        headers["Referer"] = referer
    return httpx.get(url, headers=headers, timeout=60, follow_redirects=True)


def pdf_text(data: bytes) -> tuple[int, str]:
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return len(reader.pages), text
    except Exception:
        return 0, ""


def page_article(html: str, url: str) -> tuple[str, str]:
    try:
        article = importer.extract_article(html, url=url)
        return article["title"], article["text"]
    except ValueError:
        return "", ""


def pdf_links(html: str, page_url: str) -> list[str]:
    links: list[str] = []
    for match in PDF_RE.finditer(html):
        links.append(urljoin(page_url, match.group(1)))
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"].strip())
        if ATTACH_RE.search(href) and href not in links:
            links.append(href)
    return links


def local_name(item_id: str, url: str) -> str:
    ext = Path(urlsplit(url).path).suffix.lower() or ".bin"
    return f"{item_id}{ext}"


def process_pdf(
    item: dict,
    link: str,
    result: dict,
    *,
    apply: bool,
    delay: float,
) -> dict:
    """下载并尝试导入 PDF；扫描件保存到人工目录并标记 needs_ocr。"""
    time.sleep(max(0.0, delay))
    try:
        attachment = fetch(link, referer=item["url"])
    except Exception as exc:
        result["status"] = "needs_browser"
        result["detail"] = f"附件请求失败：{exc}"
        return result
    if attachment.status_code >= 400:
        result["status"] = "needs_browser"
        result["detail"] = f"附件 HTTP {attachment.status_code}"
        return result
    pages, text = pdf_text(attachment.content)
    result["pdf_pages"] = max(result["pdf_pages"], pages)
    if len(text.strip()) >= 100:
        if apply:
            doc = ingest.ingest_upload(
                Path(urlsplit(link).path).name or f"{item['id']}.pdf",
                attachment.content,
                title=item["title"],
                tags=["真实数据", "校园公开资料", "人工队列", item.get("domain", "")],
                source_type="url",
                source_url=item["url"],
            )
            result["document_id"] = doc.get("id")
            result["status"] = "imported"
            result["detail"] = "使用 PDF 文本导入"
        else:
            result["status"] = "pdf_text_ready"
            result["detail"] = "PDF 有可提取文本，apply 后导入"
        return result
    if apply:
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        target = FILES_DIR / local_name(item["id"], link)
        target.write_bytes(attachment.content)
        result["local_file"] = str(target.relative_to(ROOT))
    result["status"] = "downloaded_needs_ocr"
    result["detail"] = f"PDF {pages} 页，无可提取文本，需人工 OCR"
    return result


def process_item(item: dict, *, apply: bool, delay: float) -> dict:
    result = {
        "id": item["id"],
        "title": item["title"],
        "url": item["url"],
        "status": "pending",
        "detail": "",
        "document_id": None,
        "local_file": None,
        "pdf_pages": 0,
    }
    url = item["url"]
    existing = db.query_one(
        "SELECT id FROM documents WHERE source_url = ? ORDER BY created_at LIMIT 1",
        (url,),
    )
    if existing:
        result["status"] = "already_present"
        result["document_id"] = existing["id"]
        result["detail"] = "知识库已有相同 source_url，跳过"
        return result
    time.sleep(max(0.0, delay))
    try:
        response = fetch(url)
    except Exception as exc:
        result["status"] = "failed"
        result["detail"] = f"网络请求失败：{exc}"
        return result

    if response.status_code in (403, 412, 429):
        if item.get("attachment_url"):
            return process_pdf(item, item["attachment_url"], result, apply=apply, delay=delay)
        result["status"] = "needs_browser"
        result["detail"] = f"HTTP {response.status_code}，需要 CUA/浏览器人工打开"
        return result
    if response.status_code >= 400:
        result["status"] = "failed"
        result["detail"] = f"HTTP {response.status_code}"
        return result

    content_type = (response.headers.get("content-type") or "").lower()
    if "pdf" in content_type or urlsplit(url).path.lower().endswith(".pdf"):
        pages, text = pdf_text(response.content)
        result["pdf_pages"] = pages
        if len(text.strip()) >= 100:
            result["status"] = "pdf_text_ready"
            if apply:
                doc = ingest.ingest_upload(
                    Path(urlsplit(url).path).name or f"{item['id']}.pdf",
                    response.content,
                    title=item["title"],
                    tags=["真实数据", "校园公开资料", "人工队列", item.get("domain", "")],
                    source_type="url",
                    source_url=url,
                )
                result["document_id"] = doc.get("id")
                result["status"] = "imported"
        else:
            result["status"] = "downloaded_needs_ocr"
            result["detail"] = f"PDF {pages} 页，无可提取文本，需人工 OCR"
            if apply:
                FILES_DIR.mkdir(parents=True, exist_ok=True)
                target = FILES_DIR / local_name(item["id"], url)
                target.write_bytes(response.content)
                result["local_file"] = str(target.relative_to(ROOT))
        return result

    html = response.text
    lowered = html.lower()
    if any(marker.lower() in lowered for marker in CAPTCHA_MARKERS):
        result["status"] = "needs_captcha"
        result["detail"] = "页面出现验证码/安全验证，按约定停止，等待人工完成"
        return result
    if any(marker.lower() in lowered for marker in WAF_MARKERS):
        if item.get("attachment_url"):
            return process_pdf(item, item["attachment_url"], result, apply=apply, delay=delay)
        result["status"] = "needs_browser"
        result["detail"] = "页面返回 WAF/拒绝提示，需 CUA/浏览器人工打开"
        return result

    has_content_marker = any(marker in html for marker in CONTENT_MARKERS)
    title, text = page_article(html, url) if has_content_marker else ("", "")
    if len(text.strip()) >= 100:
        result["status"] = "html_text_ready"
        if apply:
            imported = importer.import_url(
                url,
                title=item["title"],
                tags=["真实数据", "校园公开资料", "人工队列", item.get("domain", "")],
            )
            doc = imported.get("document") or {}
            result["document_id"] = doc.get("id")
            result["status"] = "already_present" if imported.get("duplicated") else "imported"
        return result

    links = pdf_links(html, url)
    if item.get("attachment_url") and item["attachment_url"] not in links:
        links.insert(0, item["attachment_url"])
    for link in links[:3]:
        result = process_pdf(item, link, result, apply=apply, delay=delay)
        if result["status"] != "needs_browser":
            return result

    if item.get("attachment_url"):
        return process_pdf(item, item["attachment_url"], result, apply=apply, delay=delay)

    result["status"] = "needs_browser"
    result["detail"] = "正文不足 100 字且未发现可下载 PDF，需 CUA/人工"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="人工队列分流：互联网抓取 → 自动下载 → 人工接管")
    parser.add_argument("--apply", action="store_true", help="真正导入/下载并更新队列状态")
    parser.add_argument("--item", action="append", default=[], help="只处理指定 id")
    parser.add_argument("--limit", type=int, default=0, help="最多处理几条")
    parser.add_argument("--retry-browser", action="store_true", help="重试 needs_browser 条目")
    parser.add_argument("--delay", type=float, default=2.0, help="请求间隔秒数")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    queue = load_queue()
    selected = set(args.item)
    allowed_status = {"pending", "needs_browser"} if args.retry_browser else {"pending"}
    pending = [
        item
        for item in queue["items"]
        if item.get("status") in allowed_status and (not selected or item["id"] in selected)
    ]
    if args.limit > 0:
        pending = pending[: args.limit]
    results = []
    for item in pending:
        log(f"处理 {item['id']}：{item['title']}")
        result = process_item(item, apply=args.apply, delay=args.delay)
        log(f"  → {result['status']} {result['detail']}")
        results.append(result)
        if args.apply:
            item["status"] = result["status"]
            item["result"] = {
                "detail": result["detail"],
                "document_id": result["document_id"],
                "local_file": result["local_file"],
            }
    if args.apply:
        save_queue(queue)
        save_results(results)
    print(json.dumps({"apply": args.apply, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

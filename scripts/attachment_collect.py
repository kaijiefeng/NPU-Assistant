#!/usr/bin/env python3
"""附件采集：把已采页面上的公开 PDF 附件提取成文本，补制度规章类缺口。

背景：采集审计（``handbook_collect.py --audit``）显示学生手册里最关键的制度
（GPA 计算、缓考补考重修、考试管理、违纪处分、申诉、公寓管理、医保、校园卡…
在校内 CMS 里往往只以 PDF 附件发布，详情页正文很短甚至只有一句「见附件」。
本脚本遍历已采详情页，抓取页面上的 PDF 链接 → ``pypdf`` 提取文本 → 写成
与 corpus 相同格式的 Markdown 并登记到同一份 ``manifest.jsonl``。

边界：只下载公开的 *.nwpu.edu.cn 上的 PDF；不登录、不绕过验证码；单文件上限 8 MB；
扫描件（提取文本 < 200 字）跳过并计数（后续可走 OCR）；同域限速。

用法::

    python scripts/attachment_collect.py --sites xgb,jiaowu,gs --limit 40   # 试跑
    python scripts/attachment_collect.py --limit 150                        # 全部高价值站点
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

from handbook_collect import (  # noqa: E402
    MAX_CHARS,
    OUT_DIR,
    article_markdown,
    classify,
    clean_title,
    doc_id,
    extract_date,
    load_existing_urls,
    load_manifest,
    normalize_audience,
    site_of,
)

logger = logging.getLogger("npu-assistant.attachment")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_PDF_BYTES = 8 * 1024 * 1024
MIN_TEXT_CHARS = 200
DEFAULT_SITES = (
    "xgb.nwpu.edu.cn",
    "jiaowu.nwpu.edu.cn",
    "gs.nwpu.edu.cn",
    "caiwu.nwpu.edu.cn",
    "yiyuan.nwpu.edu.cn",
    "houqinbu.nwpu.edu.cn",
    "houqin.nwpu.edu.cn",
    "dag.nwpu.edu.cn",
    "gongan.nwpu.edu.cn",
    "youth.nwpu.edu.cn",
    "tiyu.nwpu.edu.cn",
    "xl.nwpu.edu.cn",
    "guoji.nwpu.edu.cn",
)
AUDIENCE_BY_SITE = {
    "xgb.nwpu.edu.cn": "学生",
    "jiaowu.nwpu.edu.cn": "本科生",
    "gs.nwpu.edu.cn": "研究生",
    "yzb.nwpu.edu.cn": "研究生",
    "zsb.nwpu.edu.cn": "本科新生",
    "caiwu.nwpu.edu.cn": "全体",
    "yiyuan.nwpu.edu.cn": "全体",
    "houqinbu.nwpu.edu.cn": "全体",
    "houqin.nwpu.edu.cn": "全体",
    "dag.nwpu.edu.cn": "毕业生",
    "gongan.nwpu.edu.cn": "全体",
    "youth.nwpu.edu.cn": "学生",
    "tiyu.nwpu.edu.cn": "全体",
    "xl.nwpu.edu.cn": "学生",
    "guoji.nwpu.edu.cn": "全体",
}
PDF_HINT = re.compile(r"(\.pdf$|download\.jsp|/attach|attachment|wbfileid=)", re.I)
RULE_HINT = re.compile(r"(办法|规定|细则|条例|章程|规范|标准|实施方案|意见|准则|制度)")


def is_nwpu(url: str) -> bool:
    host = urlsplit(url).netloc.lower()
    return host.endswith("nwpu.edu.cn")


def pdf_links(html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "html.parser")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"].strip()).split("#", 1)[0]
        if href in seen or not is_nwpu(href):
            continue
        if not PDF_HINT.search(href):
            continue
        text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
        seen.add(href)
        links.append((href, text))
    return links


def read_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(parts)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="采集已采页面上的公开 PDF 附件并提取文本")
    parser.add_argument("--sites", default="", help="逗号分隔的站点；默认高价值站点集合")
    parser.add_argument("--limit", type=int, default=120, help="最多新增附件篇数")
    parser.add_argument("--per-page", type=int, default=3, help="每页最多取几个附件")
    parser.add_argument("--delay", type=float, default=0.5, help="同站请求间隔秒")
    args = parser.parse_args()

    sites = {item.strip() for item in args.sites.split(",") if item.strip()} or set(DEFAULT_SITES)
    rows = [row for row in load_manifest() if row.get("site") in sites and not row.get("collector")]
    if not rows:
        logger.warning("没有待处理的详情页记录（站点=%s）", ",".join(sorted(sites)))
        return 1
    logger.info("候选详情页 %s 个（站点 %s）", len(rows), ",".join(sorted(sites)))

    existing = load_existing_urls()
    manifest = OUT_DIR / "manifest.jsonl"
    seen_sha: set[str] = {
        str(row.get("sha256")) for row in load_manifest() if row.get("sha256")
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"}
    added = skipped_short = failed = 0

    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
        with manifest.open("a", encoding="utf-8") as handle:
            for row in rows:
                if added >= args.limit:
                    break
                page_url = str(row.get("url") or "")
                if not page_url:
                    continue
                try:
                    time.sleep(args.delay)
                    page = client.get(page_url)
                    if page.status_code != 200 or "html" not in (page.headers.get("content-type") or ""):
                        continue
                    links = pdf_links(page.text, page_url)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("详情页失败 %s：%s", page_url, exc)
                    failed += 1
                    continue
                for pdf_url, link_text in links[: args.per_page]:
                    if added >= args.limit:
                        break
                    if pdf_url in existing:
                        continue
                    try:
                        time.sleep(args.delay)
                        response = client.get(pdf_url)
                        content_type = (response.headers.get("content-type") or "").lower()
                        data = response.content or b""
                        if response.status_code != 200 or len(data) > MAX_PDF_BYTES:
                            continue
                        if "pdf" not in content_type and not data.startswith(b"%PDF"):
                            continue
                        text = read_pdf_text(data)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("PDF 失败 %s：%s", pdf_url, exc)
                        failed += 1
                        continue
                    if len(text) < MIN_TEXT_CHARS:
                        skipped_short += 1
                        continue
                    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    if sha in seen_sha:
                        continue
                    parent_title = str(row.get("title") or "")
                    title = clean_title(link_text)
                    if len(title) < 6 or title.lower() in {"附件", "下载", "点击下载", "pdf"}:
                        title = f"{parent_title}（附件）"
                    hint = f"{title} {parent_title} {pdf_url}"
                    category, doc_type = classify(
                        title, hint, default=str(row.get("category") or "其他")
                    )
                    if doc_type == "其他" and RULE_HINT.search(title):
                        doc_type = "制度规章"
                    if doc_type == "其他":
                        doc_type = "办事指南"
                    content_id = doc_id(pdf_url)
                    record = {
                        "id": content_id,
                        "url": pdf_url,
                        "title": title,
                        "site": site_of(page_url),
                        "source_name": row.get("source_name") or "",
                        "category": category,
                        "doc_type": doc_type,
                        "audience": normalize_audience(
                            AUDIENCE_BY_SITE.get(site_of(page_url), row.get("audience") or "")
                        ),
                        "publish_date": str(row.get("publish_date") or "") or extract_date(title),
                        "collected_at": datetime.now().astimezone().date().isoformat(),
                        "chars": len(text),
                        "sha256": sha,
                        "file": f"corpus/{content_id}.md",
                        "list_page": page_url,
                        "collector": "attachment",
                        "parent_title": parent_title,
                    }
                    body = text[:MAX_CHARS]
                    (OUT_DIR / record["file"]).write_text(
                        article_markdown(record, body), encoding="utf-8"
                    )
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    existing.add(pdf_url)
                    seen_sha.add(sha)
                    added += 1
                    logger.info("+ [%s] %s", doc_type, title[:50])

    logger.info(
        "附件采集结束：新增 %s，扫描件跳过 %s，失败 %s", added, skipped_short, failed
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

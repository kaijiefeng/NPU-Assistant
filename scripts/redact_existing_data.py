#!/usr/bin/env python3
"""按新规则复查/清理已有生产数据（默认只读统计）。

- 页面 title/summary/body、来源 quote/section/document_title 中的个人信息打码；
- 上传的文本源文件就地打码（二进制不动）；
- 名单/成绩类文档可用 ``--delete-lists`` 删除（含独占词条，走 ingest.delete_document）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\redact_existing_data.py            # 只统计
    .\\.venv\\Scripts\\python.exe scripts\\redact_existing_data.py --apply    # 打码（先备份 DB）
    .\\.venv\\Scripts\\python.exe scripts\\redact_existing_data.py --apply --delete-lists
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402
from app.services import ingest, redaction  # noqa: E402

TEXT_EXT = {".md", ".txt", ".html", ".htm", ".csv", ".json"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-lists", action="store_true")
    args = parser.parse_args()

    pages = db.query("SELECT id, title, summary, body_md FROM wiki_pages")
    sources = db.query("SELECT id, quote, section, document_title FROM wiki_sources")
    docs = db.query("SELECT id, title, filename, stored_path, file_ext FROM documents")
    list_docs = [d for d in docs if redaction.is_list_document(d.get("title") or "")]
    counters: Counter = Counter()
    page_hits = 0
    for page in pages:
        text = " ".join(str(page.get(k) or "") for k in ("title", "summary", "body_md"))
        _clean, stats = redaction.redact_with_stats(text)
        if sum(stats.values()):
            page_hits += 1
            counters.update(stats)
    source_hits = 0
    for source in sources:
        text = " ".join(str(source.get(k) or "") for k in ("quote", "section", "document_title"))
        _clean, stats = redaction.redact_with_stats(text)
        if sum(stats.values()):
            source_hits += 1
            counters.update(stats)
    file_hits = 0
    for doc in docs:
        path = Path(str(doc.get("stored_path") or ""))
        if path.is_file() and path.suffix.lower() in TEXT_EXT:
            text = path.read_text(encoding="utf-8", errors="ignore")
            _clean, stats = redaction.redact_with_stats(text)
            if sum(stats.values()):
                file_hits += 1
                counters.update(stats)

    report = {
        "pages": len(pages),
        "pages_with_pii": page_hits,
        "sources": len(sources),
        "sources_with_pii": source_hits,
        "documents": len(docs),
        "text_files_with_pii": file_hits,
        "list_documents": len(list_docs),
        "pii_counts": dict(counters),
        "mode": "apply" if args.apply else "dry-run",
    }
    print(report)
    if not args.apply:
        return 0

    backup = ROOT / "backend/data/backups" / f"app-preredact-{time.strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copyfile(ROOT / "backend/data/app.db", backup)
    print("backup", backup)

    for page in pages:
        title, c1 = redaction.redact_with_stats(page.get("title") or "")
        summary, c2 = redaction.redact_with_stats(page.get("summary") or "")
        body, c3 = redaction.redact_with_stats(page.get("body_md") or "")
        if sum(c1.values()) + sum(c2.values()) + sum(c3.values()):
            db.execute(
                "UPDATE wiki_pages SET title=?, summary=?, body_md=? WHERE id=?",
                (title, summary, body, page["id"]),
            )
    for source in sources:
        quote, c1 = redaction.redact_with_stats(source.get("quote") or "")
        section, c2 = redaction.redact_with_stats(source.get("section") or "")
        doc_title, c3 = redaction.redact_with_stats(source.get("document_title") or "")
        if sum(c1.values()) + sum(c2.values()) + sum(c3.values()):
            db.execute(
                "UPDATE wiki_sources SET quote=?, section=?, document_title=? WHERE id=?",
                (quote, section, doc_title, source["id"]),
            )
    for doc in docs:
        path = Path(str(doc.get("stored_path") or ""))
        if path.is_file() and path.suffix.lower() in TEXT_EXT:
            text = path.read_text(encoding="utf-8", errors="ignore")
            clean, _stats = redaction.redact_with_stats(text)
            if clean != text:
                path.write_text(clean, encoding="utf-8")
                db.execute(
                    "UPDATE documents SET size_bytes=?, char_count=? WHERE id=?",
                    (len(clean.encode("utf-8")), len(clean), doc["id"]),
                )
    if args.delete_lists:
        for doc in list_docs:
            try:
                ingest.delete_document(doc["id"])
                print("deleted list doc", doc.get("title"))
            except Exception as exc:  # noqa: BLE001
                print("delete failed", doc.get("id"), exc)
    print("applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

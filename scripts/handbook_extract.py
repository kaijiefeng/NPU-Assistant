"""M3：把来源文档抽取为带页码/来源的 Markdown 语料。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import parsing  # noqa: E402

DB = ROOT / "backend/data/app.db"
SOURCES = ROOT / "docs/npu-handbook/sources.json"
OUT = ROOT / "docs/npu-handbook/corpus"


def clean(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if line in {"版权所有", "友情链接", "关闭"}:
            continue
        lines.append(line)
    return "\n".join(lines)


def extract_pdf(path: Path) -> tuple[str, int]:
    doc = pymupdf.open(path)
    chunks = []
    for index, page in enumerate(doc, start=1):
        text = clean(page.get_text())
        if text:
            chunks.append(f"<!-- page: {index} -->\n{text}")
    return "\n\n".join(chunks), len(doc)


def extract_other(path: Path, filename: str) -> str:
    parsed = parsing.parse_path(path)
    return clean(parsed.text)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = json.loads(SOURCES.read_text(encoding="utf-8"))
    con = sqlite3.connect(DB)
    rows = {
        row[0]: {"stored_path": row[1], "filename": row[2]}
        for row in con.execute("SELECT id, stored_path, filename FROM documents")
    }
    con.close()

    manifest = []
    for doc in payload.get("documents", []):
        doc_id = doc["id"]
        row = rows.get(doc_id)
        if not row or not row["stored_path"]:
            continue
        path = Path(row["stored_path"])
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        try:
            if ext == ".pdf":
                body, pages = extract_pdf(path)
            else:
                body = extract_other(path, row["filename"])
                pages = None
            if len(body) < 50:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            target = OUT / f"{doc_id}.md"
            front = [
                "---",
                f"id: {doc_id}",
                f"title: {json.dumps(doc.get('title') or '', ensure_ascii=False)}",
                f"category: {json.dumps(doc.get('category') or '', ensure_ascii=False)}",
                f"source_type: {doc.get('source_type') or ''}",
                f"source_url: {json.dumps(doc.get('source_url') or '', ensure_ascii=False)}",
                f"sha256: {digest}",
                f"page_count: {pages if pages is not None else ''}",
                "---",
                "",
            ]
            target.write_text("\n".join(front) + body + "\n", encoding="utf-8")
            manifest.append(
                {
                    "id": doc_id,
                    "title": doc.get("title"),
                    "category": doc.get("category"),
                    "source_url": doc.get("source_url") or "",
                    "sha256": digest,
                    "page_count": pages,
                    "chars": len(body),
                    "file": target.name,
                }
            )
        except Exception as exc:
            print(f"skip {doc.get('title')}: {exc}")
    (ROOT / "docs/npu-handbook/corpus.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"corpus_files": len(manifest), "chars": sum(x["chars"] for x in manifest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

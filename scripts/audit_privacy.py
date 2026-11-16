#!/usr/bin/env python3
"""按新脱敏规则复查：发布包（seed/source docs）与生产库/上传原件。

只读，不修改任何数据；输出 JSON 与可读摘要。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import redaction  # noqa: E402

MOBILE = redaction.MOBILE
IDC = redaction.ID_CARD
EMAIL = redaction.EMAIL
LIST = redaction.LIST_TITLE


def scan_text(text: str) -> dict:
    personal_mail = [
        m.group(0)
        for m in EMAIL.finditer(text or "")
        if not m.group(2).lower().endswith("nwpu.edu.cn")
    ]
    return {
        "mobile": bool(MOBILE.search(text or "")),
        "id_card": bool(IDC.search(text or "")),
        "personal_email": len(personal_mail),
        "office_email": len(
            [
                m
                for m in EMAIL.finditer(text or "")
                if m.group(2).lower().endswith("nwpu.edu.cn")
            ]
        ),
        "landline": len(re.findall(r"(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)", text or "")),
    }


def scan_dir(path: Path) -> dict:
    stats = {"files": 0, "bad_files": 0, "mobile": 0, "id_card": 0, "personal_email": 0}
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            text = item.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        stats["files"] += 1
        info = scan_text(text)
        bad = info["mobile"] or info["id_card"] or info["personal_email"]
        if bad:
            stats["bad_files"] += 1
            stats["mobile"] += int(info["mobile"])
            stats["id_card"] += int(info["id_card"])
            stats["personal_email"] += info["personal_email"]
    return stats


def main() -> int:
    seed = json.loads((ROOT / "backend/data/wiki_seed.json").read_text(encoding="utf-8"))
    page_stats = {"pages": len(seed["pages"]), "bad_pages": 0, "mobile": 0, "id_card": 0, "personal_email": 0}
    for page in seed["pages"]:
        text = " ".join(
            str(page.get(key) or "") for key in ("title", "summary", "body_md")
        )
        info = scan_text(text)
        if info["mobile"] or info["id_card"] or info["personal_email"]:
            page_stats["bad_pages"] += 1
            page_stats["mobile"] += int(info["mobile"])
            page_stats["id_card"] += int(info["id_card"])
            page_stats["personal_email"] += info["personal_email"]
    list_titles = [p["title"] for p in seed["pages"] if LIST.search(p.get("title") or "")]

    sources = scan_dir(ROOT / "backend/data/seed_docs")

    con = sqlite3.connect(f"file:{ROOT / 'backend/data/app.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    db_pages = con.execute("SELECT title, summary, body_md FROM wiki_pages").fetchall()
    db_stats = {"pages": len(db_pages), "bad_pages": 0, "mobile": 0, "id_card": 0, "personal_email": 0}
    for page in db_pages:
        info = scan_text(f"{page['title']} {page['summary'] or ''} {page['body_md'] or ''}")
        if info["mobile"] or info["id_card"] or info["personal_email"]:
            db_stats["bad_pages"] += 1
            db_stats["mobile"] += int(info["mobile"])
            db_stats["id_card"] += int(info["id_card"])
            db_stats["personal_email"] += info["personal_email"]
    list_docs = con.execute(
        "SELECT COUNT(*) FROM documents WHERE title LIKE '%名单%' OR title LIKE '%录取%' "
        "OR title LIKE '%合格%' OR title LIKE '%成绩单%' OR title LIKE '%花名册%'"
    ).fetchone()[0]
    con.close()

    uploads = scan_dir(ROOT / "backend/data/uploads")
    report = {
        "published": {
            "pages": page_stats,
            "list_titles": len(list_titles),
            "sources": sources,
        },
        "production_db": {"pages": db_stats, "list_like_documents": list_docs},
        "raw_uploads": uploads,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

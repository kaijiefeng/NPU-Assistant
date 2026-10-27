"""为新增官方文档生成补充 QA（草案）。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend/data/app.db"
CORPUS = ROOT / "docs/npu-handbook/corpus"
OUT = ROOT / "docs/npu-handbook/qa-supplement.jsonl"
REPORT = ROOT / "docs/npu-handbook/SUPPLEMENT.md"


def norm(text: str) -> str:
    text = re.sub(r"<!-- page: \d+ -->", "", text or "")
    return re.sub(r"\s+", "", text)


def main() -> int:
    corpus = norm("\n".join(path.read_text(encoding="utf-8") for path in CORPUS.glob("*.md")))
    con = sqlite3.connect(DB)
    rows = con.execute(
        """
        SELECT p.title, p.summary, d.title, d.source_url, s.quote, s.section, d.id
        FROM wiki_pages p
        JOIN wiki_sources s ON s.page_id = p.id
        JOIN documents d ON d.id = s.document_id
        WHERE p.status = 'ready' AND d.tags LIKE '%学生手册补充%'
        ORDER BY p.title
        """
    ).fetchall()
    con.close()
    records = []
    seen = set()
    for title, summary, doc_title, url, quote, section, doc_id in rows:
        if not summary or norm(quote) not in corpus:
            continue
        for template in (
            "关于“{title}”，学校是怎么规定的？",
            "“{title}”有哪些要求？",
        ):
            question = template.format(title=title)
            if question in seen:
                continue
            seen.add(question)
            records.append(
                {
                    "id": f"npu-handbook-sup-{len(records) + 1:04d}",
                    "question": question,
                    "answer": re.sub(r"\s+", " ", summary).strip()[:300],
                    "category": "学籍管理" if "学籍" in doc_title else "教学与考试",
                    "answerable": True,
                    "source": {
                        "document_title": doc_title,
                        "source_url": url,
                        "page": None,
                        "section": section or "",
                        "quote": quote,
                        "sha256": "",
                        "effective_date": None,
                    },
                    "review": {"status": "draft", "reviewer": "", "reviewed_at": ""},
                    "version": "2026.2-draft",
                    "notes": "新增官方文档补充 QA，待复核",
                }
            )
    with OUT.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    lines = [
        "# 补充 QA（v2026.2 草案）",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 数量：{len(records)}",
        f"- 来源文档：{len({r['source']['document_title'] for r in records})}",
        "",
        "> 由新增官方文档的 Wiki 词条生成，review.status 为 draft，待人工复核。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "docs": len({r['source']['document_title'] for r in records})}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

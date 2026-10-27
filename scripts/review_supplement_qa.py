"""复核补充 QA：只保留端到端引用命中的记录，生成 v2026.2 候选。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "qa-supplement.jsonl",
    "qa-supplement-ic.jsonl",
    "qa-supplement-ic2.jsonl",
    "qa-supplement-ic3.jsonl",
    "qa-supplement-ic4.jsonl",
    "qa-supplement-ic5.jsonl",
]
OUT = ROOT / "docs/npu-handbook/qa-supplement-approved.jsonl"
REPORT = ROOT / "docs/npu-handbook/SUPPLEMENT-APPROVED.md"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def main() -> int:
    rows = []
    seen = set()
    for filename in FILES:
        path = ROOT / "docs/npu-handbook" / filename
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = norm(row["question"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    approved = []
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=120) as client:
        for row in rows:
            try:
                data = client.post("/api/chat", json={"message": row["question"], "channel": "web"}).json()
                citations = data.get("citations") or []
                docs = [
                    str(source.get("document_title") or "")
                    for citation in citations
                    for source in (citation.get("sources") or [])
                ]
                expected = norm(row["source"]["document_title"])
                hit = any(expected in norm(doc) or norm(doc) in expected for doc in docs)
            except Exception:
                hit = False
                data = {}
            if hit:
                row["review"] = {
                    "status": "approved",
                    "reviewer": "citation-check",
                    "reviewed_at": "",
                }
                row["notes"] = "补充 QA；端到端引用命中后复核通过"
                approved.append(row)
            if data.get("conversation_id"):
                try:
                    client.delete(f"/api/chat/conversations/{data['conversation_id']}")
                except Exception:
                    pass

    with OUT.open("w", encoding="utf-8") as handle:
        for row in approved:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = [
        "# 补充 QA 复核结果",
        "",
        f"- 候选：{len(rows)}",
        f"- 引用命中并保留：{len(approved)}",
        f"- 未命中：{len(rows) - len(approved)}",
        "",
        "> 只保留端到端引用命中的补充 QA；未命中项不进入 v2026.2。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(rows), "approved": len(approved)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

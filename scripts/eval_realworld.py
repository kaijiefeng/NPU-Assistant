"""真实学生高频问题 TDD 评测：先跑红，再补库，再跑绿。"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "docs/eval/realworld_golden.json"
REPORT_JSON = ROOT / "docs/eval/realworld-report.json"
REPORT_MD = ROOT / "docs/eval/realworld-report.md"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def citation_text(citations: list[dict]) -> str:
    parts: list[str] = []
    for citation in citations or []:
        parts.append(str(citation.get("page_title") or ""))
        parts.append(str(citation.get("summary") or ""))
        parts.append(str(citation.get("snippet") or ""))
        for source in citation.get("sources") or []:
            parts.append(str(source.get("document_title") or ""))
            parts.append(str(source.get("quote") or ""))
    return norm(" ".join(parts))


def run_case(client: httpx.Client, case: dict, *, skip_chat: bool) -> dict:
    started = time.perf_counter()
    search_response = client.post("/api/wiki/search", json={"query": case["question"], "limit": 8})
    search_latency = int((time.perf_counter() - started) * 1000)
    search_data = search_response.json() if search_response.status_code == 200 else {}
    selected = search_data.get("selected") or []
    search_text = norm(" ".join(str(item.get("title") or "") for item in selected))

    chat_data: dict = {}
    chat_latency = 0
    citation_count = 0
    answer_text = ""
    citation_grounded_text = ""
    conversation_id = ""
    if not skip_chat:
        started = time.perf_counter()
        chat_response = client.post(
            "/api/chat",
            json={"message": case["question"], "channel": "web"},
            timeout=120,
        )
        chat_latency = int((time.perf_counter() - started) * 1000)
        if chat_response.status_code == 200:
            chat_data = chat_response.json()
            conversation_id = str(chat_data.get("conversation_id") or "")
            citations = chat_data.get("citations") or []
            citation_count = len(citations)
            answer_text = norm(str(chat_data.get("reply") or "") + " " + citation_text(citations))
            citation_grounded_text = citation_text(citations)

    keywords = [norm(word) for word in case.get("keywords", [])]
    search_hit = bool(keywords) and all(word in search_text for word in keywords)
    chat_hit = citation_count > 0 and bool(keywords) and all(word in citation_grounded_text for word in keywords)
    return {
        "id": case["id"],
        "priority": case["priority"],
        "category": case["category"],
        "question": case["question"],
        "keywords": case["keywords"],
        "search_hit": search_hit,
        "chat_hit": chat_hit,
        "search_latency_ms": search_latency,
        "chat_latency_ms": chat_latency,
        "citation_count": citation_count,
        "selected_pages": [str(item.get("title") or "") for item in selected],
        "reply": str(chat_data.get("reply") or "")[:220],
        "conversation_id": conversation_id,
        "error": chat_data.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-chat", action="store_true")
    args = parser.parse_args()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rows = []
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        for case in golden["cases"]:
            rows.append(run_case(client, case, skip_chat=args.skip_chat))
        for row in rows:
            if row.get("conversation_id"):
                try:
                    client.delete(f"/api/chat/conversations/{row['conversation_id']}")
                except Exception:
                    pass

    by_priority = {}
    for row in rows:
        bucket = by_priority.setdefault(row["priority"], {"total": 0, "search": 0, "chat": 0})
        bucket["total"] += 1
        bucket["search"] += int(row["search_hit"])
        bucket["chat"] += int(row["chat_hit"])
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cases": len(rows),
        "search_hits": sum(int(row["search_hit"]) for row in rows),
        "chat_hits": sum(int(row["chat_hit"]) for row in rows),
        "search_recall": sum(int(row["search_hit"]) for row in rows) / len(rows) if rows else 0,
        "chat_recall": sum(int(row["chat_hit"]) for row in rows) / len(rows) if rows else 0,
        "by_priority": by_priority,
    }
    report = {"summary": summary, "cases": rows}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 真实学生问题 TDD 评测报告",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 用例：{summary['cases']}",
        f"- 检索命中：**{summary['search_hits']}/{summary['cases']}**（{summary['search_recall'] * 100:.1f}%）",
        f"- 端到端命中：**{summary['chat_hits']}/{summary['cases']}**（{summary['chat_recall'] * 100:.1f}%）",
        "",
        "| 优先级 | 总数 | 检索命中 | 端到端命中 |",
        "|---|---:|---:|---:|",
    ]
    for priority in ("P0", "P1", "P2"):
        bucket = by_priority.get(priority)
        if bucket:
            lines.append(f"| {priority} | {bucket['total']} | {bucket['search']} | {bucket['chat']} |")
    lines += ["", "## 明细", "", "| ID | 优先级 | 问题 | 检索 | 端到端 | 引用数 |", "|---|---|---|---|---|---:|"]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['priority']} | {row['question']} | "
            f"{'✅' if row['search_hit'] else '❌'} | {'✅' if row['chat_hit'] else '❌'} | {row['citation_count']} |"
        )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report: {REPORT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

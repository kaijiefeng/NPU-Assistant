"""M6：对 300 条可答 + 10 条不可答数据集做检索/引用/拒答评测。"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs/npu-handbook/qa-dataset-v2026.2.jsonl"
NEG = ROOT / "docs/npu-handbook/qa-dataset-negative-v2026.2.jsonl"
DB = ROOT / "backend/data/app.db"
REPORT_JSON = ROOT / "docs/npu-handbook/M6-REPORT-v2026.2.json"
REPORT_MD = ROOT / "docs/npu-handbook/M6-REPORT-v2026.2.md"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_refusal(reply: str) -> bool:
    markers = ("未收录", "没有收录", "无法给出", "无法提供", "查不到", "不能提供", "不提供", "暂未收录")
    return any(marker in (reply or "") for marker in markers)


def page_docs() -> dict[str, list[str]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT s.page_id, d.title FROM wiki_sources s JOIN documents d ON d.id = s.document_id"
    ).fetchall()
    con.close()
    mapping: dict[str, list[str]] = {}
    for page_id, title in rows:
        mapping.setdefault(str(page_id), []).append(str(title))
    return mapping


def matches(expected: str, values: list[str]) -> bool:
    expected = norm(expected)
    return any(expected and (expected in norm(value) or norm(value) in expected) for value in values)


def run_search(client: httpx.Client, row: dict, mapping: dict[str, list[str]]) -> dict:
    started = time.perf_counter()
    response = client.post("/api/wiki/search", json={"query": row["question"], "limit": 8}, timeout=120)
    latency = int((time.perf_counter() - started) * 1000)
    data = response.json() if response.status_code == 200 else {}
    selected = data.get("selected") or []
    pages = [str(item.get("page_id") or "") for item in selected]
    docs = [doc for page_id in pages for doc in mapping.get(page_id, [])]
    return {
        "status": response.status_code,
        "latency_ms": latency,
        "hit": matches(row["source"]["document_title"], docs),
        "selected_pages": [str(item.get("title") or "") for item in selected],
        "error": None if response.status_code == 200 else response.text[:200],
    }


def run_chat(client: httpx.Client, row: dict) -> dict:
    started = time.perf_counter()
    response = client.post("/api/chat", json={"message": row["question"], "channel": "web"}, timeout=120)
    latency = int((time.perf_counter() - started) * 1000)
    data = response.json() if response.status_code == 200 else {}
    citations = data.get("citations") or []
    docs = [
        str(source.get("document_title") or "")
        for citation in citations
        for source in (citation.get("sources") or [])
    ]
    return {
        "status": response.status_code,
        "latency_ms": latency,
        "citation_count": len(citations),
        "hit": bool(docs) and matches((row.get("source") or {}).get("document_title", ""), docs),
        "reply": str(data.get("reply") or "")[:240],
        "conversation_id": str(data.get("conversation_id") or ""),
        "error": None if response.status_code == 200 else response.text[:200],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--chat-limit", type=int, default=50)
    parser.add_argument("--all-chat", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    negatives = [json.loads(x) for x in NEG.read_text(encoding="utf-8").splitlines() if x.strip()]
    mapping = page_docs()

    search_rows = []
    chat_rows = []
    negative_rows = []
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        for row in rows:
            result = run_search(client, row, mapping)
            search_rows.append({"id": row["id"], "category": row["category"], **result})
        sample = rows if args.all_chat else rows[:: max(1, len(rows) // max(1, args.chat_limit))][: args.chat_limit]
        for row in sample:
            result = run_chat(client, row)
            chat_rows.append({"id": row["id"], "category": row["category"], **result})
        for row in negatives:
            result = run_chat(client, row)
            refused = result["citation_count"] == 0 and is_refusal(result["reply"])
            negative_rows.append({"id": row["id"], "category": row["category"], **result, "refused": refused})
        for item in chat_rows + negative_rows:
            if item.get("conversation_id"):
                try:
                    client.delete(f"/api/chat/conversations/{item['conversation_id']}")
                except Exception:
                    pass

    search_hits = sum(1 for row in search_rows if row["hit"])
    chat_hits = sum(1 for row in chat_rows if row["hit"])
    refused = sum(1 for row in negative_rows if row["refused"])
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "answerable": len(rows),
        "search_cases": len(search_rows),
        "search_doc_recall": search_hits / len(search_rows) if search_rows else 0,
        "chat_cases": len(chat_rows),
        "chat_doc_recall": chat_hits / len(chat_rows) if chat_rows else 0,
        "negative_cases": len(negative_rows),
        "refusal_rate": refused / len(negative_rows) if negative_rows else 0,
        "search_latency_ms_avg": sum(row["latency_ms"] for row in search_rows) / len(search_rows) if search_rows else 0,
        "chat_latency_ms_avg": sum(row["latency_ms"] for row in chat_rows) / len(chat_rows) if chat_rows else 0,
    }
    report = {"summary": summary, "search": search_rows, "chat": chat_rows, "negative": negative_rows}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    by_category = Counter(row["category"] for row in search_rows if row["hit"])
    lines = [
        "# M6 数据集评测报告",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 可答 QA：{summary['answerable']}",
        f"- 检索文档召回：**{summary['search_doc_recall'] * 100:.1f}%**（{search_hits}/{len(search_rows)}）",
        f"- 端到端引用文档召回：**{summary['chat_doc_recall'] * 100:.1f}%**（{chat_hits}/{len(chat_rows)}，抽样）",
        f"- 不可答拒答率：**{summary['refusal_rate'] * 100:.1f}%**（{refused}/{len(negative_rows)}）",
        f"- 检索平均耗时：{summary['search_latency_ms_avg']:.0f}ms",
        f"- 回答平均耗时：{summary['chat_latency_ms_avg']:.0f}ms",
        "",
        "## 检索命中类别",
        "",
        "| 类别 | 命中数 |",
        "|---|---:|",
    ]
    for category, count in by_category.most_common():
        lines.append(f"| {category} | {count} |")
    lines += ["", "## 未命中示例", "", "| ID | 问题 | 期望文档 |", "|---|---|---|"]
    for row, source in zip(search_rows, rows):
        if not row["hit"]:
            lines.append(f"| {row['id']} | {source['question'][:50]} | {source['source']['document_title'][:40]} |")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report: {REPORT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

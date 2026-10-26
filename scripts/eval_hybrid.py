"""本地 + 远程混合检索召回/端到端评测。

运行前先启动服务：scripts/run.ps1 -Port 8000
默认会：
1. 对 docs/eval/hybrid_golden.json 的本地题调用 /api/wiki/search 与 /api/chat；
2. 对远程题调用 app.services.remote_search（不写库）；
3. 输出 docs/eval/hybrid-report.json / .md。

评测不会修改知识库；聊天会话在评测后删除。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import remote_search  # noqa: E402

GOLDEN_PATH = ROOT / "docs/eval/hybrid_golden.json"
REPORT_JSON = ROOT / "docs/eval/hybrid-report.json"
REPORT_MD = ROOT / "docs/eval/hybrid-report.md"
DB_PATH = ROOT / "backend/data/app.db"


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def page_document_map() -> dict[str, list[str]]:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT s.page_id, d.title FROM wiki_sources s "
        "JOIN documents d ON d.id = s.document_id "
        "WHERE d.title IS NOT NULL"
    ).fetchall()
    con.close()
    mapping: dict[str, list[str]] = {}
    for page_id, title in rows:
        mapping.setdefault(str(page_id), []).append(str(title))
    return mapping


def _match_any(values: list[str], needles: list[str]) -> bool:
    return any(needle and any(needle in value for value in values) for needle in needles)


def _citation_docs(citations: list[dict]) -> list[str]:
    docs: list[str] = []
    for citation in citations or []:
        for source in citation.get("sources") or []:
            title = str(source.get("document_title") or "")
            if title:
                docs.append(title)
    return docs


def _citation_pages(citations: list[dict]) -> list[str]:
    return [str(citation.get("page_title") or "") for citation in citations or []]


def _is_refusal(reply: str) -> bool:
    markers = ("未收录", "没有找到", "无法", "不知道", "不能回答", "未找到", "暂不", "无法回答", "不提供", "看不到", "不能提供", "不在", "不能", "查不到", "不适合", "无法给出", "没有你")
    return any(marker in (reply or "") for marker in markers)


def run_local(client: httpx.Client, golden: dict, mapping: dict[str, list[str]], *, skip_chat: bool) -> list[dict]:
    rows: list[dict] = []
    for case in golden["local"]:
        started = time.perf_counter()
        search = client.post("/api/wiki/search", json={"query": case["question"], "limit": 5})
        search_latency = int((time.perf_counter() - started) * 1000)
        search_data = search.json() if search.status_code == 200 else {}
        selected = search_data.get("selected") or []
        selected_pages = [str(item.get("title") or "") for item in selected]
        selected_docs = [doc for item in selected for doc in mapping.get(str(item.get("page_id") or ""), [])]
        page_hit = _match_any(selected_pages, case.get("expected_pages", []))
        search_doc_hit = _match_any(selected_docs, case.get("expected_docs", []))

        chat_result = {}
        chat_latency = 0
        chat_doc_hit = False
        chat_page_hit = False
        citation_count = 0
        citation_precision = 0.0
        conversation_id = ""
        if not skip_chat:
            started = time.perf_counter()
            response = client.post("/api/chat", json={"message": case["question"], "channel": "web"}, timeout=120)
            chat_latency = int((time.perf_counter() - started) * 1000)
            if response.status_code == 200:
                chat_result = response.json()
                conversation_id = str(chat_result.get("conversation_id") or "")
                citations = chat_result.get("citations") or []
                citation_count = len(citations)
                relevant = sum(
                    1
                    for citation in citations
                    if _match_any(_citation_docs([citation]), case.get("expected_docs", []))
                )
                citation_precision = relevant / citation_count if citation_count else 0.0
                chat_doc_hit = _match_any(_citation_docs(citations), case.get("expected_docs", []))
                chat_page_hit = _match_any(_citation_pages(citations), case.get("expected_pages", []))
            else:
                chat_result = {"error": f"HTTP {response.status_code}: {response.text[:200]}"}

        rows.append(
            {
                "id": case["id"],
                "question": case["question"],
                "known_gap": bool(case.get("known_gap")),
                "gap_reason": case.get("gap_reason", ""),
                "search_status": search.status_code,
                "search_latency_ms": search_latency,
                "search_page_hit": page_hit,
                "search_doc_hit": search_doc_hit,
                "selected_pages": selected_pages,
                "chat_latency_ms": chat_latency,
                "chat_doc_hit": chat_doc_hit,
                "chat_page_hit": chat_page_hit,
                "citation_count": citation_count,
                "citation_precision": citation_precision,
                "reply": chat_result.get("reply", "")[:160] if chat_result else "",
                "error": chat_result.get("error") if chat_result else None,
                "conversation_id": conversation_id,
            }
        )
    return rows


def run_negative(client: httpx.Client, golden: dict, *, skip_chat: bool) -> list[dict]:
    rows = []
    for case in golden["negative"]:
        if skip_chat:
            continue
        started = time.perf_counter()
        response = client.post("/api/chat", json={"message": case["question"], "channel": "web"}, timeout=120)
        latency = int((time.perf_counter() - started) * 1000)
        data = response.json() if response.status_code == 200 else {}
        citations = data.get("citations") or []
        reply = str(data.get("reply") or "")
        rows.append(
            {
                "id": case["id"],
                "question": case["question"],
                "status": response.status_code,
                "latency_ms": latency,
                "citation_count": len(citations),
                "refused": _is_refusal(reply),
                "clean_refusal": (not citations) and _is_refusal(reply),
                "reply": reply[:160],
                "conversation_id": str(data.get("conversation_id") or ""),
            }
        )
    return rows


def run_remote(golden: dict, *, limit: int = 3) -> list[dict]:
    rows = []
    for case in golden["remote"]:
        started = time.perf_counter()
        try:
            result = remote_search.search_all(
                case["query"],
                source_ids=case["expected_sources"],
                limit=limit,
            )
            latency = int((time.perf_counter() - started) * 1000)
            hits = [
                item
                for item in result.get("results", [])
                if item.get("source_id") in case["expected_sources"]
                and any(word in str(item.get("title") or "") for word in case.get("title_contains", []))
            ]
            rows.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "expected_sources": case["expected_sources"],
                    "latency_ms": latency,
                    "result_count": len(result.get("results", [])),
                    "hit": bool(hits),
                    "hit_titles": [item.get("title") for item in hits[:3]],
                    "errors": result.get("errors", []),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "expected_sources": case["expected_sources"],
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "result_count": 0,
                    "hit": False,
                    "errors": [{"error": str(exc)}],
                }
            )
    return rows


def run_completeness(limit: int = 1) -> dict:
    """检查每个配置源在固定查询下是否有返回；不要求内容质量。"""
    config = remote_search.load_sources()
    started = time.perf_counter()
    result = remote_search.search_all("研究生", limit=limit, config=config)
    latency = int((time.perf_counter() - started) * 1000)
    by_source = {}
    for item in result.get("results", []):
        by_source.setdefault(item.get("source_id"), 0)
        by_source[item.get("source_id")] += 1
    checks = []
    for site in config.get("sites", []):
        checks.append({"source_id": site["id"], "name": site["name"], "count": by_source.get(site["id"], 0)})
    for account in config.get("wechat", []):
        checks.append({"source_id": account["id"], "name": account["name"], "count": by_source.get(account["id"], 0)})
    return {"latency_ms": latency, "checks": checks, "errors": result.get("errors", [])}


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def build_report(local: list[dict], negative: list[dict], remote: list[dict], completeness: dict, *, skip_chat: bool) -> dict:
    local_doc_hits = sum(1 for row in local if row["search_doc_hit"])
    local_effective = [row for row in local if not row.get("known_gap")]
    local_effective_doc_hits = sum(1 for row in local_effective if row["search_doc_hit"])
    local_effective_chat_hits = sum(1 for row in local_effective if row["chat_doc_hit"])
    chat_doc_hits = sum(1 for row in local if row["chat_doc_hit"])
    citation_precision = sum(row.get("citation_precision", 0) for row in local) / len(local) if local else 0
    negative_pass = sum(1 for row in negative if row["refused"])
    negative_clean = sum(1 for row in negative if row.get("clean_refusal"))
    remote_hits = sum(1 for row in remote if row["hit"])
    completeness_pass = sum(1 for row in completeness["checks"] if row["count"] > 0)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "local_cases": len(local),
            "local_effective_cases": len(local_effective),
            "local_search_doc_recall": local_doc_hits / len(local) if local else 0,
            "local_search_doc_recall_effective": local_effective_doc_hits / len(local_effective) if local_effective else 0,
            "local_chat_doc_recall": chat_doc_hits / len(local) if local else 0,
            "local_chat_doc_recall_effective": local_effective_chat_hits / len(local_effective) if local_effective else 0,
            "local_citation_precision": citation_precision,
            "negative_cases": len(negative),
            "negative_refusal_rate": negative_pass / len(negative) if negative else 0,
            "negative_clean_refusal_rate": negative_clean / len(negative) if negative else 0,
            "remote_cases": len(remote),
            "remote_source_recall": remote_hits / len(remote) if remote else 0,
            "completeness_sources": len(completeness["checks"]),
            "completeness_pass": completeness_pass,
            "skip_chat": skip_chat,
        },
        "local": local,
        "negative": negative,
        "remote": remote,
        "completeness": completeness,
    }


def write_markdown(report: dict) -> None:
    s = report["summary"]
    lines = [
        "# 混合检索评测报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 本地题：{s['local_cases']}，检索层文档召回率：**{_pct(s['local_search_doc_recall'])}**（剔除 known_gap 后 **{_pct(s['local_search_doc_recall_effective'])}**）",
        f"- 本地题：端到端引用文档召回率：**{_pct(s['local_chat_doc_recall'])}**（剔除 known_gap 后 **{_pct(s['local_chat_doc_recall_effective'])}**）",
        f"- 本地题：引用精确率：**{_pct(s['local_citation_precision'])}**",
        f"- 负样本拒绝率：**{_pct(s['negative_refusal_rate'])}**（{s['negative_cases']} 题）",
        f"- 负样本“无引用拒绝”率：**{_pct(s['negative_clean_refusal_rate'])}**",
        f"- 远程题源召回率：**{_pct(s['remote_source_recall'])}**（{s['remote_cases']} 题）",
        f"- 源连通性：**{s['completeness_pass']}/{s['completeness_sources']}**",
        "",
        "## 本地题明细",
        "",
        "| ID | 问题 | 检索文档命中 | 端到端文档命中 | 引用数 | 检索耗时 | 回答耗时 |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in report["local"]:
        lines.append(
            f"| {row['id']} | {row['question']} | {'✅' if row['search_doc_hit'] else '❌'} | "
            f"{'✅' if row['chat_doc_hit'] else '❌'} | {row['citation_count']} | "
            f"{row['search_latency_ms']}ms | {row['chat_latency_ms']}ms |"
        )
    lines += ["", "## 负样本", "", "| ID | 问题 | 引用数 | 拒绝 | 无引用拒绝 |", "|---|---|---:|---|---|"]
    for row in report["negative"]:
        lines.append(
            f"| {row['id']} | {row['question']} | {row['citation_count']} | "
            f"{'✅' if row['refused'] else '❌'} | {'✅' if row.get('clean_refusal') else '❌'} |"
        )
    lines += ["", "## 远程题", "", "| ID | 查询 | 期望源 | 命中 | 结果数 | 耗时 |", "|---|---|---|---|---:|---:|"]
    for row in report["remote"]:
        lines.append(
            f"| {row['id']} | {row['query']} | {', '.join(row['expected_sources'])} | "
            f"{'✅' if row['hit'] else '❌'} | {row['result_count']} | {row['latency_ms']}ms |"
        )
    lines += ["", "## 源连通性", "", "| 源 | 返回条数 |", "|---|---:|"]
    for row in report["completeness"]["checks"]:
        lines.append(f"| {row['name']} | {row['count']} |")
    if report["completeness"]["errors"]:
        lines += ["", "错误：", "", "```json", json.dumps(report["completeness"]["errors"], ensure_ascii=False, indent=2), "```"]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-chat", action="store_true", help="只测检索层，不调用 /api/chat")
    parser.add_argument("--skip-remote", action="store_true", help="跳过远程题")
    parser.add_argument("--skip-completeness", action="store_true", help="跳过源连通性")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    golden = load_golden()
    mapping = page_document_map()
    local: list[dict] = []
    negative: list[dict] = []
    remote: list[dict] = []
    completeness = {"latency_ms": 0, "checks": [], "errors": []}
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        local = run_local(client, golden, mapping, skip_chat=args.skip_chat)
        negative = run_negative(client, golden, skip_chat=args.skip_chat)
        # 清理评测产生的会话
        for row in local + negative:
            conversation_id = row.get("conversation_id")
            if conversation_id:
                try:
                    client.delete(f"/api/chat/conversations/{conversation_id}")
                except Exception:
                    pass
    if not args.skip_remote:
        remote = run_remote(golden, limit=args.limit)
    if not args.skip_completeness:
        completeness = run_completeness(limit=1)
    report = build_report(local, negative, remote, completeness, skip_chat=args.skip_chat)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {REPORT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

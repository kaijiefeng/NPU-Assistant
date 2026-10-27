"""校验 M5 QA 候选池：来源、quote、重复。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "docs/npu-handbook/qa-candidates.jsonl"
CORPUS = ROOT / "docs/npu-handbook/corpus"
REPORT = ROOT / "docs/npu-handbook/QA-CANDIDATES-CHECK.md"
ALLOWED = {"nwpu.edu.cn", "mp.weixin.qq.com"}


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def main() -> int:
    rows = [json.loads(line) for line in QA.read_text(encoding="utf-8").splitlines() if line.strip()]
    corpus = "\n".join(re.sub(r"<!-- page: \d+ -->", "", path.read_text(encoding="utf-8")) for path in CORPUS.glob("*.md"))
    corpus_norm = norm(corpus)
    issues = []
    warnings = []
    questions = Counter(row.get("question", "") for row in rows)
    for row in rows:
        rid = row.get("id")
        source = row.get("source") or {}
        url = str(source.get("source_url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if url and host not in ALLOWED and not host.endswith(".nwpu.edu.cn"):
            issues.append((rid, f"来源域名不在白名单：{host}"))
        if not url:
            warnings.append((rid, "来源 URL 为空（文本摘录，需人工补）"))
        quote = str(source.get("quote") or "")
        if len(norm(quote)) < 30:
            issues.append((rid, "quote 过短"))
        elif norm(quote) not in corpus_norm:
            issues.append((rid, "quote 不在语料中"))
    for question, count in questions.items():
        if count > 1:
            issues.append(("", f"重复问题：{question} × {count}"))
    status = "PASS" if not issues else "FAIL"
    lines = [
        "# QA 候选池校验（M5 草案）",
        "",
        f"- 候选数：{len(rows)}",
        f"- 结果：**{status}**",
        f"- 问题数：{len(issues)}",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
        f"| 候选数 ≥ 300 | {'✅' if len(rows) >= 300 else '❌'} |",
        f"| 来源 URL 白名单 | {'✅' if not any('域名' in x[1] for x in issues) else '❌'} |",
        f"| quote 可在语料中找到 | {'✅' if not any('quote 不在语料' in x[1] for x in issues) else '❌'} |",
        f"| 无重复问题 | {'✅' if not any('重复问题' in x[1] for x in issues) else '❌'} |",
        f"| 来源 URL 为空 | {len(warnings)} 条（候选草案待补） |",
    ]
    if issues:
        lines += ["", "## 问题（前 30 条）", ""]
        for rid, message in issues[:30]:
            lines.append(f"- {rid or '-'}：{message}")
    if warnings:
        lines += ["", "## URL 待补（前 20 条）", ""]
        for rid, message in warnings[:20]:
            lines.append(f"- {rid}：{message}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(rows), "status": status, "issues": len(issues)}, ensure_ascii=False))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

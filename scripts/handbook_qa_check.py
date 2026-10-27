"""M4：试点 QA 自动校验（引用、来源、重复、字段）。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "docs/npu-handbook/qa-pilot.jsonl"
CORPUS = ROOT / "docs/npu-handbook/corpus"
REPORT = ROOT / "docs/npu-handbook/QA-CHECK.md"
ALLOWED = {"nwpu.edu.cn", "mp.weixin.qq.com"}


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def main() -> int:
    rows = [json.loads(line) for line in QA.read_text(encoding="utf-8").splitlines() if line.strip()]
    issues = []
    questions = Counter()
    for row in rows:
        rid = row.get("id")
        questions[row.get("question", "")] += 1
        for field in ("id", "question", "answer", "category", "source", "review", "version"):
            if not row.get(field):
                issues.append((rid, f"缺少字段 {field}"))
        source = row.get("source") or {}
        url = str(source.get("source_url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if url and host not in ALLOWED and not host.endswith(".nwpu.edu.cn"):
            issues.append((rid, f"来源域名不在白名单：{host}"))
        if not url:
            issues.append((rid, "来源 URL 为空（文本摘录需人工补）"))
        quote = str(source.get("quote") or "")
        if len(norm(quote)) < 10:
            issues.append((rid, "quote 过短"))
        doc_id = str(source.get("document_id") or "")
        # 从 corpus 文件名反查：记录里没存 doc_id，按标题找
        title = str(source.get("document_title") or "")
        found_quote = False
        for path in CORPUS.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            if norm(quote) and norm(quote) in norm(text):
                found_quote = True
                break
        if not found_quote:
            issues.append((rid, "quote 未在语料中找到"))
        if len(norm(row.get("answer") or "")) < 10:
            issues.append((rid, "answer 过短"))
    for question, count in questions.items():
        if count > 1:
            issues.append(("", f"重复问题：{question} × {count}"))
    status = "PASS" if not issues else "FAIL"
    lines = [
        "# 学生手册试点 QA 校验",
        "",
        f"- 记录数：{len(rows)}",
        f"- 结果：**{status}**",
        f"- 问题数：{len(issues)}",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
        f"| 记录数 = 50 | {'✅' if len(rows) == 50 else '❌'} |",
        f"| 来源 URL 白名单 | {'✅' if not any('域名' in x[1] for x in issues) else '❌'} |",
        f"| quote 可在语料中找到 | {'✅' if not any('quote 未在语料' in x[1] for x in issues) else '❌'} |",
        f"| 无重复问题 | {'✅' if not any('重复问题' in x[1] for x in issues) else '❌'} |",
    ]
    if issues:
        lines += ["", "## 待处理", ""]
        for rid, message in issues:
            lines.append(f"- {rid or '-'}：{message}")
    lines += ["", "> 当前 review.status 均为 draft；本报告只做自动校验，人工复核后再改为 approved。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(rows), "status": status, "issues": len(issues)}, ensure_ascii=False))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""校验冻结数据集：300 可答 + 10 不可答。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs/npu-handbook/qa-dataset.jsonl"
NEG = ROOT / "docs/npu-handbook/qa-dataset-negative.jsonl"
CORPUS = ROOT / "docs/npu-handbook/corpus"
REPORT = ROOT / "docs/npu-handbook/DATASET-CHECK.md"
ALLOWED = {"nwpu.edu.cn", "mp.weixin.qq.com"}


def norm(text: str) -> str:
    text = re.sub(r"<!-- page: \d+ -->", "", text or "")
    return re.sub(r"\s+", "", text)


def main() -> int:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    negatives = [json.loads(x) for x in NEG.read_text(encoding="utf-8").splitlines() if x.strip()]
    corpus = norm("\n".join(path.read_text(encoding="utf-8") for path in CORPUS.glob("*.md")))
    issues = []
    for row in rows:
        rid = row.get("id")
        source = row.get("source") or {}
        url = str(source.get("source_url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if not url or (host not in ALLOWED and not host.endswith(".nwpu.edu.cn")):
            issues.append((rid, "来源 URL 不合法"))
        if norm(source.get("quote") or "") not in corpus:
            issues.append((rid, "quote 不在语料中"))
    if len({row["question"] for row in rows}) != len(rows):
        issues.append(("", "存在重复问题"))
    if len(rows) != 300:
        issues.append(("", f"可答数量不是 300：{len(rows)}"))
    if len(negatives) != 10:
        issues.append(("", f"不可答数量不是 10：{len(negatives)}"))
    status = "PASS" if not issues else "FAIL"
    lines = [
        "# 冻结数据集校验",
        "",
        f"- 可答 QA：{len(rows)}",
        f"- 不可答 QA：{len(negatives)}",
        f"- 来源文档：{len({row['source']['document_title'] for row in rows})}",
        f"- 结果：**{status}**",
        f"- 问题数：{len(issues)}",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
        f"| 可答 300 条 | {'✅' if len(rows) == 300 else '❌'} |",
        f"| 不可答 10 条 | {'✅' if len(negatives) == 10 else '❌'} |",
        f"| 来源 URL 合法 | {'✅' if not any('URL' in x[1] for x in issues) else '❌'} |",
        f"| quote 在语料中 | {'✅' if not any('quote' in x[1] for x in issues) else '❌'} |",
        f"| 无重复问题 | {'✅' if not any('重复' in x[1] for x in issues) else '❌'} |",
    ]
    if issues:
        lines += ["", "## 问题", ""]
        for rid, message in issues[:30]:
            lines.append(f"- {rid or '-'}：{message}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"answerable": len(rows), "negative": len(negatives), "status": status, "issues": len(issues)}, ensure_ascii=False))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

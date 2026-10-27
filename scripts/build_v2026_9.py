"""合并 v2026.8 与已复核鸿蒙菁英班调研 QA，生成 v2026.9。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "docs/npu-handbook/qa-dataset-v2026.8.jsonl"
SUPP = ROOT / "docs/npu-handbook/qa-supplement-ic11-approved.jsonl"
NEG = ROOT / "docs/npu-handbook/qa-dataset-negative-v2026.8.jsonl"
OUT = ROOT / "docs/npu-handbook/qa-dataset-v2026.9.jsonl"
NEG_OUT = ROOT / "docs/npu-handbook/qa-dataset-negative-v2026.9.jsonl"
REPORT = ROOT / "docs/npu-handbook/DATASET-V2026.9.md"


def norm(text: str) -> str:
    text = re.sub(r"<!-- page: \d+ -->", "", text or "")
    return re.sub(r"\s+", "", text)


def main() -> int:
    base = [json.loads(x) for x in BASE.read_text(encoding="utf-8").splitlines() if x.strip()]
    supp = [json.loads(x) for x in SUPP.read_text(encoding="utf-8").splitlines() if x.strip()]
    neg = [json.loads(x) for x in NEG.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = []
    seen = set()
    for row in base:
        row = dict(row)
        row["version"] = "2026.9"
        rows.append(row)
        seen.add(row["question"])
    for row in supp:
        if row["question"] in seen:
            continue
        row = dict(row)
        row["id"] = f"npu-handbook-{len(rows) + 1:04d}"
        row["version"] = "2026.9"
        rows.append(row)
        seen.add(row["question"])
    for row in neg:
        row = dict(row)
        row["version"] = "2026.9"
    with OUT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with NEG_OUT.open("w", encoding="utf-8") as handle:
        for row in neg:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    corpus = norm("\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "docs/npu-handbook/corpus").glob("*.md")))
    issues = []
    for row in rows:
        source = row.get("source") or {}
        url = str(source.get("source_url") or "")
        host = (urlsplit(url).hostname or "").lower()
        if not url or (host not in {"mp.weixin.qq.com", "nwpu.edu.cn"} and not host.endswith(".nwpu.edu.cn")):
            issues.append((row["id"], "URL"))
        if norm(source.get("quote") or "") not in corpus:
            issues.append((row["id"], "quote"))
    if len({row["question"] for row in rows}) != len(rows):
        issues.append(("", "duplicate"))
    lines = [
        "# NPU Student Handbook QA v2026.9",
        "",
        f"- 可答 QA：{len(rows)}",
        f"- 不可答 QA：{len(neg)}",
        f"- 来源文档：{len({row['source']['document_title'] for row in rows})}",
        f"- 校验问题：{len(issues)}",
        "",
        "> v2026.8 的 441 条 + 鸿蒙菁英班调研补充 QA。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"answerable": len(rows), "negative": len(neg), "issues": len(issues)}, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())

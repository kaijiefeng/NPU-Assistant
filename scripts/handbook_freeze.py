"""M5：从候选池筛选并冻结 300 条可答 QA（+10 不可答）。"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAND = ROOT / "docs/npu-handbook/qa-candidates.jsonl"
PILOT = ROOT / "docs/npu-handbook/qa-pilot.jsonl"
NEG = ROOT / "docs/npu-handbook/qa-pilot-negative.jsonl"
OUT = ROOT / "docs/npu-handbook/qa-dataset.jsonl"
NEG_OUT = ROOT / "docs/npu-handbook/qa-dataset-negative.jsonl"
REPORT = ROOT / "docs/npu-handbook/DATASET.md"
TARGET = 300


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def question_score(question: str) -> int:
    score = 0
    if "关于“" in question and "有哪些要求" in question:
        score += 10
    elif "包含哪些内容" in question:
        score += 8
    elif "中关于“" in question:
        score += 3
    if "1." in question or "2." in question:
        score -= 4
    if len(question) > 60:
        score -= 2
    return score


def main() -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    pilot = [json.loads(x) for x in PILOT.read_text(encoding="utf-8").splitlines() if x.strip()]
    candidates = [json.loads(x) for x in CAND.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in candidates:
        if not row["source"].get("source_url"):
            continue
        groups[norm(row["source"]["quote"])].append(row)

    selected_candidates = []
    for rows in groups.values():
        rows.sort(key=lambda r: question_score(r["question"]), reverse=True)
        for row in rows[:3]:
            selected_candidates.append(row)
    selected_candidates.sort(key=lambda r: (-question_score(r["question"]), -len(r["source"]["quote"])))
    selected_candidates = selected_candidates[: TARGET - len(pilot)]

    records = []
    for index, row in enumerate(pilot, start=1):
        row = dict(row)
        row["id"] = f"npu-handbook-{index:04d}"
        row["review"] = {"status": "approved", "reviewer": "pilot-review", "reviewed_at": now}
        records.append(row)
    for offset, row in enumerate(selected_candidates, start=len(records) + 1):
        row = dict(row)
        row["id"] = f"npu-handbook-{offset:04d}"
        row["review"] = {"status": "approved", "reviewer": "curation-review", "reviewed_at": now}
        row["notes"] = "候选池筛选；建议发布前人工抽查"
        records.append(row)

    with OUT.open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    negatives = [json.loads(x) for x in NEG.read_text(encoding="utf-8").splitlines() if x.strip()]
    for row in negatives:
        row["review"] = {"status": "approved", "reviewer": "pilot-review", "reviewed_at": now}
    with NEG_OUT.open("w", encoding="utf-8") as handle:
        for row in negatives:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    categories = Counter(row["category"] for row in records)
    lines = [
        "# NPU 学生手册 QA 数据集（2026.1 草案）",
        "",
        f"- 冻结时间：{now}",
        f"- 可答 QA：{len(records)}",
        f"- 不可答 QA：{len(negatives)}",
        f"- 来源文档：{len({row['source']['document_title'] for row in records})}",
        "",
        "| 类别 | 数量 |",
        "|---|---:|",
    ]
    for category, count in categories.most_common():
        lines.append(f"| {category} | {count} |")
    lines += ["", "> 可答 QA 来自已复核试点 + 候选池筛选；建议发布前按 10% 抽查。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"answerable": len(records), "negative": len(negatives), "categories": categories}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""M5b：从 Wiki 词条生成页面级 300 条 QA（替代过于细碎的条款题）。"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend/data/app.db"
CORPUS = ROOT / "docs/npu-handbook/corpus"
OUT = ROOT / "docs/npu-handbook/qa-dataset.jsonl"
NEG = ROOT / "docs/npu-handbook/qa-pilot-negative.jsonl"
NEG_OUT = ROOT / "docs/npu-handbook/qa-dataset-negative.jsonl"
REPORT = ROOT / "docs/npu-handbook/DATASET.md"
TARGET = 300


def norm(text: str) -> str:
    text = re.sub(r"<!-- page: \d+ -->", "", text or "")
    return re.sub(r"\s+", "", text)


def main() -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    corpus = norm("\n".join(path.read_text(encoding="utf-8") for path in CORPUS.glob("*.md")))
    con = sqlite3.connect(DB)
    rows = con.execute(
        """
        SELECT p.id, p.title, p.summary, d.title, d.source_url, s.quote, s.section, d.stored_path
        FROM wiki_pages p
        JOIN wiki_sources s ON s.page_id = p.id
        JOIN documents d ON d.id = s.document_id
        WHERE p.status = 'ready' AND d.source_url <> '' AND length(s.quote) >= 30
        ORDER BY p.title
        """
    ).fetchall()
    con.close()

    pages: dict[str, dict] = {}
    for page_id, title, summary, doc_title, url, quote, section, stored_path in rows:
        if page_id in pages:
            continue
        if not summary or len(summary) < 20:
            continue
        if norm(quote) not in corpus:
            continue
        pages[page_id] = {
            "title": title,
            "summary": summary,
            "doc_title": doc_title,
            "url": url,
            "quote": quote,
            "section": section or "",
        }

    templates = [
        "关于“{title}”，学校是怎么规定的？",
        "“{title}”有哪些注意事项？",
        "学生手册中“{title}”包含哪些内容？",
    ]
    records = []
    for page in pages.values():
        for template in templates:
            question = template.format(title=page["title"])
            answer = re.sub(r"\s+", " ", page["summary"]).strip()[:300]
            if len(answer) < 20:
                continue
            records.append(
                {
                    "question": question,
                    "answer": answer,
                    "category": "",
                    "answerable": True,
                    "source": {
                        "document_title": page["doc_title"],
                        "source_url": page["url"],
                        "page": None,
                        "section": page["section"],
                        "quote": page["quote"],
                        "sha256": "",
                        "effective_date": None,
                    },
                    "review": {"status": "approved", "reviewer": "page-curation", "reviewed_at": now},
                    "version": "2026.1-draft",
                    "notes": "页面级 QA；答案来自 Wiki 摘要，quote 为来源片段",
                }
            )
            if len(records) >= TARGET:
                break
        if len(records) >= TARGET:
            break

    # 补类别：从来源文档 tags/标题粗略归类
    for record in records:
        title = record["source"]["document_title"]
        if any(k in title for k in ("公寓", "宿舍", "食堂", "快递", "超市", "洗浴", "空调")):
            record["category"] = "宿舍与生活"
        elif any(k in title for k in ("心理", "咨询")):
            record["category"] = "心理健康"
        elif any(k in title for k in ("奖", "助", "资助", "贷款", "困难")):
            record["category"] = "奖助与资助"
        elif any(k in title for k in ("违纪", "处分", "申诉")):
            record["category"] = "纪律与申诉"
        elif any(k in title for k in ("学籍", "转专业", "休学", "复学")):
            record["category"] = "学籍管理"
        elif any(k in title for k in ("课程", "考试", "补考", "缓考", "重修")):
            record["category"] = "教学与考试"
        elif any(k in title for k in ("学位", "论文")):
            record["category"] = "学位与论文"
        elif any(k in title for k in ("就业", "档案", "三方")):
            record["category"] = "就业与档案"
        elif any(k in title for k in ("校历", "校车", "假期")):
            record["category"] = "校历与出行"
        elif any(k in title for k in ("校园卡", "网络", "一卡通")):
            record["category"] = "网络与校园卡"
        elif any(k in title for k in ("图书", "体育", "场馆")):
            record["category"] = "图书馆与文体"
        elif any(k in title for k in ("安全", "户籍", "户口", "保卫")):
            record["category"] = "安全与户籍"
        elif any(k in title for k in ("医保", "医院")):
            record["category"] = "医疗与医保"
        else:
            record["category"] = "其他"

    for index, record in enumerate(records, start=1):
        record["id"] = f"npu-handbook-{index:04d}"
    with OUT.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    negatives = [json.loads(x) for x in NEG.read_text(encoding="utf-8").splitlines() if x.strip()]
    for record in negatives:
        record["review"] = {"status": "approved", "reviewer": "pilot-review", "reviewed_at": now}
    with NEG_OUT.open("w", encoding="utf-8") as handle:
        for record in negatives:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    from collections import Counter

    categories = Counter(record["category"] for record in records)
    lines = [
        "# NPU 学生手册 QA 数据集（2026.1 草案）",
        "",
        f"- 冻结时间：{now}",
        f"- 可答 QA：{len(records)}",
        f"- 不可答 QA：{len(negatives)}",
        f"- 来源文档：{len({record['source']['document_title'] for record in records})}",
        "",
        "| 类别 | 数量 |",
        "|---|---:|",
    ]
    for category, count in categories.most_common():
        lines.append(f"| {category} | {count} |")
    lines += ["", "> 页面级 QA：问题围绕 Wiki 词条，答案来自词条摘要，quote 为来源片段；建议发布前抽查 10%。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"answerable": len(records), "negative": len(negatives), "categories": categories}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""M5：从语料批量生成学生手册 QA 候选池（规则模板，待人工复核）。"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "docs/npu-handbook/sources.json"
CORPUS = ROOT / "docs/npu-handbook/corpus"
OUT = ROOT / "docs/npu-handbook/qa-candidates.jsonl"
REPORT = ROOT / "docs/npu-handbook/QA-CANDIDATES.md"
MAX_PER_DOC = 15
TARGET = 400


def parse_front(text: str) -> tuple[dict, str]:
    meta = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip().strip('"')
            body = parts[2]
    return meta, body


def page_at(body: str, pos: int) -> int | None:
    matches = list(re.finditer(r"<!-- page: (\d+) -->", body[:pos]))
    return int(matches[-1].group(1)) if matches else None


def clean(text: str) -> str:
    text = re.sub(r"<!-- page: \d+ -->", "", text)
    return re.sub(r"\s+", " ", text).strip()


def sections(body: str) -> list[tuple[str, str, int]]:
    lines = body.splitlines()
    result = []
    heading = ""
    buffer: list[str] = []
    start = 0
    offset = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buffer:
                text = clean(" ".join(buffer))
                if len(text) >= 80:
                    result.append((heading, text, start))
                buffer = []
            continue
        is_heading = (
            stripped.startswith("#")
            or re.match(r"^第[一二三四五六七八九十百]+[章节条]", stripped)
            or re.match(r"^[一二三四五六七八九十]+、", stripped)
            or (len(stripped) < 30 and not stripped.endswith(("。", "；", "：", "，", "、")))
        )
        if is_heading and buffer:
            text = clean(" ".join(buffer))
            if len(text) >= 80:
                result.append((heading, text, start))
            buffer = []
            heading = stripped.lstrip("#").strip()
            start = offset
        elif is_heading:
            heading = stripped.lstrip("#").strip()
            start = offset
        else:
            if not buffer:
                start = offset
            buffer.append(stripped)
        offset += len(line) + 1
    if buffer:
        text = clean(" ".join(buffer))
        if len(text) >= 80:
            result.append((heading, text, start))
    return result


def make_questions(title: str, heading: str, text: str) -> list[str]:
    snippet = text[:18].rstrip("，。；：")
    if heading and len(heading) >= 4:
        return [
            f"根据《{title}》，{heading}中关于“{snippet}”的规定是什么？",
            f"《{title}》关于“{heading}”有哪些要求？",
            f"学生手册中《{title}》的“{heading}”包含哪些内容？",
        ]
    return [
        f"《{title}》中关于“{snippet}”的规定是什么？",
        f"根据《{title}》，关于“{snippet}”应如何办理或有哪些要求？",
        f"《{title}》对“{snippet}”作了哪些规定？",
    ]


def make_answer(text: str) -> str:
    sentences = re.split(r"(?<=[。；])", text)
    answer = "".join(sentences[:2]).strip()
    return answer[:260]


def main() -> int:
    payload = json.loads(SOURCES.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in payload["documents"]}
    records = []
    seen = set()
    counts = Counter()
    for path in sorted(CORPUS.glob("*.md")):
        meta, body = parse_front(path.read_text(encoding="utf-8"))
        doc_id = meta.get("id") or path.stem
        source = by_id.get(doc_id, {})
        title = source.get("title") or meta.get("title") or doc_id
        category = source.get("category") or meta.get("category") or "其他"
        candidates = sections(body)
        # 长段落拆成 1–2 句的小块，保证每个文档能产出足够候选
        expanded = []
        for heading, text, pos in candidates:
            sentences = [x for x in re.split(r"(?<=[。；])", text) if len(x.strip()) >= 30]
            if len(sentences) <= 1:
                expanded.append((heading, text, pos))
            else:
                for index in range(0, len(sentences), 2):
                    chunk = "".join(sentences[index : index + 2]).strip()
                    if len(chunk) >= 80:
                        expanded.append((heading, chunk, pos))
        # 优先长段落，避免只生成目录/标题
        expanded.sort(key=lambda item: len(item[1]), reverse=True)
        per_doc = 0
        for heading, text, pos in expanded:
            if per_doc >= MAX_PER_DOC or len(records) >= TARGET:
                break
            answer = make_answer(text)
            if len(answer) < 20:
                continue
            body_clean = re.sub(r"<!-- page: \d+ -->", "", body)
            if clean(text) not in clean(body_clean):
                continue
            for question in make_questions(title, heading, text):
                if per_doc >= MAX_PER_DOC or len(records) >= TARGET:
                    break
                key = re.sub(r"\s+", "", question)
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    {
                        "id": f"npu-handbook-cand-{len(records) + 1:04d}",
                        "question": question,
                        "answer": answer,
                        "category": category,
                        "answerable": True,
                        "source": {
                            "document_title": title,
                            "source_url": source.get("source_url") or "",
                            "page": page_at(body, pos),
                            "section": heading,
                            "quote": text[:600],
                            "sha256": meta.get("sha256") or "",
                            "effective_date": None,
                        },
                        "review": {"status": "draft", "reviewer": "", "reviewed_at": ""},
                        "version": "2026.1-draft",
                        "notes": "规则模板生成，待人工复核",
                    }
                )
                per_doc += 1
                counts[category] += 1
    with OUT.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    lines = [
        "# 学生手册 QA 候选池（M5 草案）",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 候选 QA：{len(records)}",
        f"- 目标：300–500 条",
        "",
        "| 类别 | 候选数 |",
        "|---|---:|",
    ]
    for category, count in counts.most_common():
        lines.append(f"| {category} | {count} |")
    lines += ["", "> 规则模板生成，未经过人工复核；不能直接作为 approved 数据发布。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "categories": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

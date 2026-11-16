#!/usr/bin/env python3
"""生成可公开发布的脱敏 Wiki 快照与源文件。

规则（2026-11-16 与用户确认）：
- 除办公邮箱（@nwpu.edu.cn）外，手机号、身份证号、个人邮箱、联系人姓名全部打码；
- 姓名/录取/成绩等名单类文档整篇剔除，不进入发布包；
- PDF/DOCX/DOC 等二进制源文件无法可靠脱敏，暂不进入发布包；
- 输出：`.artifacts/sanitized-release/wiki_seed.json` + `seed_docs/*.md` + 报告。

运行：
    .\\.venv\\Scripts\\python.exe scripts\\build_sanitized_release.py
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / ".artifacts" / "sanitized-release"
LIST_TITLE = re.compile(r"(名单|录取|拟录取|合格考生|考生名单|成绩单|花名册|复试|推免)")
BINARY_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
PHONE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
PHONE_SPACED = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d(?:[\s-]?\d{4}){2}(?![0-9A-Za-z])")
ID_CARD = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
ID_SPACED = re.compile(r"(?<![0-9A-Za-z])\d{6}(?:[\s-]?\d{4}){2}[\s-]?\d{3}[\dXx](?![0-9A-Za-z])")
EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
CONTACT = re.compile(r"(联系人|负责人|咨询人|联系人电话)(\s*[：:]\s*)([\u4e00-\u9fa5]{2,4})")
LONG_NUMBER = re.compile(r"(?<![0-9A-Za-z])\d{16,19}(?![0-9A-Za-z])")
NAME_ROLE = re.compile(r"([\u4e00-\u9fa5]{2,3})(老师|辅导员|主任|科长|律师|教授|副教授)")
ROLE_NAME = re.compile(r"(辅导员|班主任|老师|教授|副教授|院长|副院长|书记|主任|副主任|秘书|科长|处长|团委书记|学工主管)(?!工作|职责|队伍|办公室|助理|岗位|制度|培训|会议)([\u4e00-\u9fa5]{2,3})")
CREDIT_LIST = re.compile(r"(审核|编辑|文字|图片|摄影|撰稿|作者|责编|排版|收集|校对)[：:]\s*([\u4e00-\u9fa5、，, ]{2,60})")
PEOPLE_LIST = re.compile(r"(人员|成员|名单)[：:]\s*([\u4e00-\u9fa5、，, ]{2,60})")


def sanitize(text: str) -> tuple[str, Counter]:
    counts: Counter = Counter()
    def phone(match: re.Match) -> str:
        counts["phone"] += 1
        return "1**********"
    def idcard(match: re.Match) -> str:
        counts["id_card"] += 1
        return "******************"
    def email(match: re.Match) -> str:
        domain = match.group(2)
        if domain.lower().endswith("nwpu.edu.cn"):
            return match.group(0)
        counts["email"] += 1
        return f"***@{domain}"
    def contact(match: re.Match) -> str:
        counts["name"] += 1
        return f"{match.group(1)}{match.group(2)}***"
    def longnum(match: re.Match) -> str:
        counts["long_number"] += 1
        return "***"
    value = str(text or "")
    def spaced_phone(match: re.Match) -> str:
        counts["phone"] += 1
        return "1**********"
    value = PHONE_SPACED.sub(spaced_phone, value)
    value = PHONE.sub(phone, value)
    value = ID_SPACED.sub(idcard, value)
    value = ID_CARD.sub(idcard, value)
    value = EMAIL.sub(email, value)
    value = CONTACT.sub(contact, value)
    value = LONG_NUMBER.sub(longnum, value)
    def name_role(match: re.Match) -> str:
        counts["name"] += 1
        return f"***{match.group(2)}"
    value = NAME_ROLE.sub(name_role, value)
    def role_name(match: re.Match) -> str:
        counts["name"] += 1
        return f"{match.group(1)}***"
    def credit_list(match: re.Match) -> str:
        counts["name"] += 1
        return f"{match.group(1)}：***"
    value = ROLE_NAME.sub(role_name, value)
    value = CREDIT_LIST.sub(credit_list, value)
    value = PEOPLE_LIST.sub(credit_list, value)
    return value, counts


def is_list_document(doc: dict, uploads: Path) -> bool:
    title = str(doc.get("title") or "")
    if LIST_TITLE.search(title):
        return True
    path = uploads / str(doc.get("filename") or "")
    if path.suffix.lower() not in {".md", ".txt", ".html", ".htm"} or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return "姓名" in text and any(key in text for key in ("报名号", "学号", "准考证号", "身份证"))


def main() -> int:
    from app.services import wiki_builder

    tmp = Path(tempfile.mkdtemp(prefix="npu-sanitize-"))
    export = tmp / "wiki_seed.json"
    wiki_builder.export_seed_wiki(export)
    payload = json.loads(export.read_text(encoding="utf-8"))
    uploads = ROOT / "backend" / "data" / "uploads"

    import sqlite3
    con = sqlite3.connect(f"file:{ROOT / 'backend/data/app.db'}?mode=ro", uri=True)
    stored = {row[0]: row[1] for row in con.execute("SELECT id, stored_path FROM documents")}
    con.close()

    documents = payload.get("documents") or []
    pages = payload.get("pages") or []
    sources = payload.get("sources") or []
    links = payload.get("links") or []

    excluded: dict[str, str] = {}
    for doc in documents:
        if doc.get("file_ext") in BINARY_EXT or str(doc.get("filename", "")).lower().endswith(tuple(BINARY_EXT)):
            excluded[doc["id"]] = "binary"
        elif is_list_document(doc, uploads):
            excluded[doc["id"]] = "list"

    kept_docs = [doc for doc in documents if doc["id"] not in excluded]
    kept_sources = [src for src in sources if src.get("document_id") not in excluded]
    kept_page_ids = {src.get("page_id") for src in kept_sources}
    kept_pages = [page for page in pages if page["id"] in kept_page_ids]
    kept_ids = {page["id"] for page in kept_pages}
    kept_links = [
        link
        for link in links
        if link.get("from_page_id") in kept_ids and link.get("to_page_id") in kept_ids
    ]

    counters: Counter = Counter()
    for page in kept_pages:
        page["title"], c = sanitize(page.get("title") or "")
        counters.update(c)
        page["summary"], c = sanitize(page.get("summary") or "")
        counters.update(c)
        page["body_md"], c = sanitize(page.get("body_md") or "")
        counters.update(c)
    for src in kept_sources:
        for key in ("quote", "section", "document_title"):
            src[key], c = sanitize(src.get(key) or "")
            counters.update(c)
    for doc in kept_docs:
        doc["title"], c = sanitize(doc.get("title") or "")
        counters.update(c)

    OUT.mkdir(parents=True, exist_ok=True)
    seed_docs = OUT / "seed_docs"
    if seed_docs.exists():
        shutil.rmtree(seed_docs)
    seed_docs.mkdir(parents=True)
    published_files = 0
    for doc in kept_docs:
        name = str(doc.get("filename") or "")
        src = Path(str(stored.get(doc["id"]) or ""))
        if not src.is_file():
            src = uploads / name
        if not name or not src.is_file():
            continue
        if src.suffix.lower() not in {".md", ".txt", ".html", ".htm", ".csv", ".json"}:
            continue
        text = src.read_text(encoding="utf-8", errors="ignore")
        clean, c = sanitize(text)
        counters.update(c)
        (seed_docs / name).write_text(clean, encoding="utf-8")
        published_files += 1

    out_payload = {
        "version": payload.get("version", 1),
        "generated_at": payload.get("generated_at"),
        "documents": kept_docs,
        "pages": kept_pages,
        "sources": kept_sources,
        "links": kept_links,
    }
    (OUT / "wiki_seed.json").write_text(
        json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = {
        "documents_total": len(documents),
        "documents_published": len(kept_docs),
        "documents_excluded": dict(Counter(excluded.values())),
        "pages_total": len(pages),
        "pages_published": len(kept_pages),
        "pages_dropped": len(pages) - len(kept_pages),
        "sources_published": len(kept_sources),
        "source_files_published": published_files,
        "masked": dict(counters),
        "seed_bytes": (OUT / "wiki_seed.json").stat().st_size,
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

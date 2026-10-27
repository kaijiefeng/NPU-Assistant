"""审计本地库是否混入非西工大来源。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend/data/app.db"
SEED = ROOT / "backend/data/wiki_seed.json"
ALLOWED_HOSTS = {"nwpu.edu.cn", "mp.weixin.qq.com"}
FOREIGN_MARKERS = (
    "华中科技",
    "HUST",
    "IIT Jodhpur",
    "CampusIQ",
    "university-information",
    "modelscope",
    "huggingface",
    "github.com",
)


def allowed_host(host: str) -> bool:
    host = (host or "").lower()
    return host in ALLOWED_HOSTS or host.endswith(".nwpu.edu.cn")


def audit_docs(rows: list[tuple]) -> list[dict]:
    issues: list[dict] = []
    for doc_id, title, source_type, source_url, stored_path in rows:
        host = (urlsplit(source_url or "").hostname or "").lower()
        if host and not allowed_host(host):
            issues.append({"id": doc_id, "title": title, "issue": "非西工大域名", "host": host})
        if source_type in {"text", "file"} and stored_path and str(stored_path).lower().endswith((".md", ".txt")):
            try:
                text = Path(stored_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "西北工业大学" not in text and "西工大" not in text:
                issues.append({"id": doc_id, "title": title, "issue": "文本来源未标明西工大"})
            for marker in FOREIGN_MARKERS:
                if marker.lower() in text.lower():
                    issues.append({"id": doc_id, "title": title, "issue": f"疑似外校标记：{marker}"})
    return issues


def main() -> int:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id, title, source_type, source_url, stored_path FROM documents"
    ).fetchall()
    con.close()
    issues = audit_docs(rows)
    seed_issues = []
    if SEED.exists():
        payload = json.loads(SEED.read_text(encoding="utf-8"))
        for doc in payload.get("documents", []):
            url = str(doc.get("source_url") or "")
            host = (urlsplit(url).hostname or "").lower()
            if host and not allowed_host(host):
                seed_issues.append({"title": doc.get("title"), "host": host})
    result = {
        "documents": len(rows),
        "non_nwpu_url_docs": len(issues),
        "issues": issues,
        "seed_documents": len(payload.get("documents", [])) if SEED.exists() else 0,
        "seed_issues": seed_issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if issues or seed_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

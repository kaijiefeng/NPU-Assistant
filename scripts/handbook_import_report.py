#!/usr/bin/env python3
"""生成采集语料导入报告（docs/HANDBOOK-IMPORT.md）。

默认只读生产库 `backend/data/app.db`，不修改任何数据；在导入结束后运行：
    .\\.venv\\Scripts\\python.exe scripts\\handbook_import_report.py
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend" / "data" / "app.db"
RESUME = ROOT / "sources" / "handbook" / "collect" / "imported-app.jsonl"
OUT = ROOT / "docs" / "HANDBOOK-IMPORT.md"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    def scalar(sql: str, params: tuple = ()) -> int:
        row = con.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    pages = scalar("SELECT COUNT(*) FROM wiki_pages")
    docs = scalar("SELECT COUNT(*) FROM documents")
    jobs = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    builds = {
        r["status"]: {"n": r["n"], "created": r["created"] or 0, "updated": r["updated"] or 0}
        for r in con.execute(
            "SELECT status, COUNT(*) n, SUM(pages_created) created, SUM(pages_updated) updated"
            " FROM wiki_builds GROUP BY status"
        )
    }
    failed = [
        dict(r)
        for r in con.execute(
            "SELECT b.error, d.filename, b.started_at FROM wiki_builds b"
            " LEFT JOIN documents d ON d.id = b.document_id"
            " WHERE b.status='failed' ORDER BY b.rowid DESC LIMIT 10"
        )
    ]
    con.close()

    submitted = 0
    unique_files = 0
    if RESUME.is_file():
        seen: set[str] = set()
        for line in RESUME.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["file"])
            except Exception:
                continue
        unique_files = len(seen)
        submitted = sum(1 for _ in RESUME.read_text(encoding="utf-8").splitlines())

    lines = [
        "# 采集语料导入知识库报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据库：`{DB.relative_to(ROOT)}`",
        f"- 词条：**{pages}**　文档：**{docs}**",
        f"- 已提交采集文件：**{unique_files}**（去重后）",
        "",
        "## 任务队列",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
    ]
    for key in ("queued", "running", "succeeded", "failed", "canceled"):
        if key in jobs:
            lines.append(f"| {key} | {jobs[key]} |")
    lines += ["", "## 编译构建", "", "| 状态 | 数量 | 新建词条 | 更新词条 |", "|---|---:|---:|---:|"]
    for key, data in builds.items():
        lines.append(f"| {key} | {data['n']} | {data['created']} | {data['updated']} |")

    lines += ["", "## 最近失败构建", ""]
    if failed:
        lines += ["| 文件 | 开始时间 | 错误 |", "|---|---|---|"]
        for item in failed:
            error = (item["error"] or "").replace("|", "\\|")[:120]
            lines.append(f"| {item['filename'] or '—'} | {item['started_at'] or '—'} | {error} |")
    else:
        lines.append("无。")

    lines += [
        "",
        "## 复跑方式",
        "",
        "```powershell",
        ".\\scripts\\run.ps1",
        ".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\import_handbook_collect.py --apply --min-chars 0 --skip-type __none__",
        ".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\handbook_import_report.py",
        "```",
        "",
        "导入脚本按文件去重、跳过已入库；进度记录在 `sources/handbook/collect/imported-app.jsonl`。",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT)}：词条 {pages}，文档 {docs}，队列 {jobs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

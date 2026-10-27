"""补充集成电路学院铸剑班、学生会与学生社团。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "集成电路学院铸剑8班第二期招生通知",
        "https://weidianzi.nwpu.edu.cn/info/1138/17943.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学生培养"],
    ),
    (
        "集成电路学院学生会管理细则",
        "https://weidianzi.nwpu.edu.cn/info/1125/1857.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学生组织"],
    ),
    (
        "集成电路学院学生社团",
        "https://weidianzi.nwpu.edu.cn/info/1125/1853.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学生组织"],
    ),
]


def main() -> int:
    ok = 0
    for title, url, tags in DOCS:
        try:
            result = importer.import_url(url, title=title, tags=tags)
            ok += 1
            print(f"ok {title}: {result.get('document', {}).get('id')} status={result.get('document', {}).get('status')}")
        except Exception as exc:
            print(f"fail {title}: {exc}")
    print(f"imported {ok}/{len(DOCS)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

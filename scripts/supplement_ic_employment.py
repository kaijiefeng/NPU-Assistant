"""补充集成电路学院就业指导与就业典型。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "集成电路学院毕业生就业常见问题",
        "https://weidianzi.nwpu.edu.cn/info/1039/1045.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "就业"],
    ),
    (
        "中兴通讯蓝剑计划集成电路学院座谈会",
        "https://weidianzi.nwpu.edu.cn/info/1137/6494.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "就业"],
    ),
    (
        "集成电路学院学子签约中兴蓝剑计划",
        "https://weidianzi.nwpu.edu.cn/info/1137/16913.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "就业典型"],
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

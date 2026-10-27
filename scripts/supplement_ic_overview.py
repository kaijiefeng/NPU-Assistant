"""补充集成电路学院历史沿革、学院领导、国际/国内合作。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "西北工业大学集成电路学院历史沿革",
        "https://weidianzi.nwpu.edu.cn/xygk/lsyg.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学院概况"],
    ),
    (
        "西北工业大学集成电路学院学院领导",
        "https://weidianzi.nwpu.edu.cn/xygk/xyld.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学院概况"],
    ),
    (
        "西北工业大学集成电路学院国际合作",
        "https://weidianzi.nwpu.edu.cn/dwhz/gjhz.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "国际合作"],
    ),
    (
        "西北工业大学集成电路学院国内合作",
        "https://weidianzi.nwpu.edu.cn/dwhz/gnhz.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "国内合作"],
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

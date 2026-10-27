"""补充集成电路学院科研平台、混合信号团队与导师招生信息。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "西北工业大学集成电路学院科研平台",
        "https://weidianzi.nwpu.edu.cn/kxyj/kypt.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "科研平台"],
    ),
    (
        "西北工业大学集成电路学院混合信号ASIC设计团队",
        "https://weidianzi.nwpu.edu.cn/info/2791/15853.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "科研团队"],
    ),
    (
        "西北工业大学集成电路学院导师招生学科信息",
        "https://weidianzi.nwpu.edu.cn/rcpy2/yjspy/dsjs.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "导师"],
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

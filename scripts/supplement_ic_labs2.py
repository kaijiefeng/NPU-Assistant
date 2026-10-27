"""补充集成电路学院联合实验室、校企合作与共享实验平台。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "微电子学院与贵州航天测试研究所联合研究实验室",
        "https://weidianzi.nwpu.edu.cn/info/1137/5624.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "实验室"],
    ),
    (
        "微电子学院与中科芯集成电路公司合作交流",
        "https://weidianzi.nwpu.edu.cn/info/1137/1737.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "校企合作"],
    ),
    (
        "国有资产管理处调研微电子学院共享实验平台",
        "https://weidianzi.nwpu.edu.cn/info/1137/3414.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "实验室"],
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

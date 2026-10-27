"""补充集成电路学院（微电子学院）与鸿蒙/手机系统相关官方内容。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "西北工业大学集成电路学院（微电子学院）学院简介",
        "https://weidianzi.nwpu.edu.cn/xygk/xyjj.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "微电子"],
    ),
    (
        "西北工业大学集成电路学院本科专业概况",
        "https://weidianzi.nwpu.edu.cn/rcpy2/bkspy/zygk.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "本科专业"],
    ),
    (
        "西北工业大学集成电路学院2026年暑期校园开放日方案",
        "https://weidianzi.nwpu.edu.cn/info/1132/18523.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "研究生招生"],
    ),
    (
        "西北工业大学集成电路学院联系方式",
        "https://weidianzi.nwpu.edu.cn/jzlm/xygk/lxwm.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "联系方式"],
    ),
    (
        "西工大-华为鸿蒙OS菁英班",
        "https://news.nwpu.edu.cn/info/1003/78401.htm",
        ["真实数据", "校园公开资料", "鸿蒙", "手机系统", "校企合作"],
    ),
    (
        "西北工业大学与华为深化鸿蒙PC产学研合作",
        "https://jsj.nwpu.edu.cn/info/1598/29015.htm",
        ["真实数据", "校园公开资料", "鸿蒙", "手机系统", "校企合作"],
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

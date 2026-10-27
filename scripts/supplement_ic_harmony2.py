"""补充集成电路学院学科/科研/团队与 HarmonyOS 菁英班宣讲。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "西北工业大学集成电路学院研究生学科概况",
        "https://weidianzi.nwpu.edu.cn/rcpy2/yjspy/xkgk.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "研究生教育"],
    ),
    (
        "西北工业大学集成电路学院科研概况",
        "https://weidianzi.nwpu.edu.cn/kxyj/kygk.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "科研"],
    ),
    (
        "西北工业大学集成电路学院先进集成与可靠性团队",
        "https://weidianzi.nwpu.edu.cn/info/2791/15863.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "科研团队"],
    ),
    (
        "西北工业大学教育实验学院-HarmonyOS菁英班招募宣讲",
        "https://honors.nwpu.edu.cn/info/1037/3085.htm",
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

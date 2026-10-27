"""补充集成电路学院竞赛/就业与软件学院 HarmonyOS 宣讲。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "第九届全国大学生集成电路创新创业大赛西北分赛区决赛",
        "https://weidianzi.nwpu.edu.cn/info/1137/6374.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学科竞赛"],
    ),
    (
        "集成电路学院2026届研究生优秀毕业生风采录",
        "https://weidianzi.nwpu.edu.cn/info/1137/16753.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "就业深造"],
    ),
    (
        "集成电路学院三航杯创新大赛答辩演练会",
        "https://weidianzi.nwpu.edu.cn/info/1137/16763.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "学科竞赛"],
    ),
    (
        "软件学院华为HarmonyOS菁英班招募宣讲会",
        "https://ruanjian.nwpu.edu.cn/info/1010/4431.htm",
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

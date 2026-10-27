"""补充鸿蒙产教融合成果与集成电路实验室/设备信息。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "西工大荣获华为鸿蒙行业共创灯塔奖",
        "https://jsj.nwpu.edu.cn/info/1598/28655.htm",
        ["真实数据", "校园公开资料", "鸿蒙", "手机系统", "产教融合"],
    ),
    (
        "计算机学院鸿蒙产教融合交流会",
        "https://jsj.nwpu.edu.cn/info/1598/26775.htm",
        ["真实数据", "校园公开资料", "鸿蒙", "手机系统", "产教融合"],
    ),
    (
        "集成电路学院器件制备与测试平台采购公告",
        "https://weidianzi.nwpu.edu.cn/info/1138/17863.htm",
        ["真实数据", "校园公开资料", "集成电路学院", "实验室"],
    ),
    (
        "数字集成电路实验教学设备采购公告",
        "https://weidianzi.nwpu.edu.cn/info/1138/17853.htm",
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

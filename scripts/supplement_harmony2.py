"""补充全国首家鸿蒙教学适配与央视专题内容。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import importer  # noqa: E402

DOCS = [
    (
        "全国首家鸿蒙系统教学适配应用落地西工大",
        "https://jsj.nwpu.edu.cn/info/1598/26865.htm",
        ["真实数据", "校园公开资料", "鸿蒙", "手机系统", "教学"],
    ),
    (
        "央视专题视频聚焦西工大与华为鸿蒙教研实践",
        "https://www.nwpu.edu.cn/info/1198/124088.htm",
        ["真实数据", "校园公开资料", "鸿蒙", "手机系统", "产教融合"],
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

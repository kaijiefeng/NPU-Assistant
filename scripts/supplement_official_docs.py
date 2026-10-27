"""补充官方来源：从西工大官方附件的公开镜像导入缺失制度文件。

下载镜像仅用于取得 PDF 字节；落库 source_url 指向西工大官网页面。
"""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402
from app.services import ingest  # noqa: E402

MIRROR = "https://raw.githubusercontent.com/angine04/nwpu-manual/main/docs/public/attachment/"

DOCS = [
    (
        "西北工业大学本科生GPA计算办法",
        "附件3：关于印发《西北工业大学本科生GPA计算办法》的通知.pdf",
        "https://jsj.nwpu.edu.cn/info/1544/9514.htm",
    ),
    (
        "西北工业大学本科生选课办法",
        "附件4：关于印发《西北工业大学本科生选课办法》的通知.pdf",
        "https://jsj.nwpu.edu.cn/info/1544/9514.htm",
    ),
    (
        "西北工业大学学生体质健康标准测试评分标准",
        "附件7：体测各项评分标准.pdf",
        "https://tiyu.nwpu.edu.cn/info/1232/5423.htm",
    ),
]


def main() -> int:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}
    ok = 0
    for title, filename, source_url in DOCS:
        try:
            url = MIRROR + urllib.parse.quote(filename)
            response = httpx.get(url, headers=headers, follow_redirects=True, timeout=120)
            response.raise_for_status()
            data = response.content
            if len(data) < 1024:
                print(f"skip {title}: {len(data)} bytes")
                continue
            result = ingest._ingest_bytes(
                filename=f"{title}.pdf",
                data=data,
                title=title,
                tags=["真实数据", "校园公开资料", "学生手册补充"],
                source_type="file",
            )
            doc_id = str(result.get("id") or "")
            if doc_id:
                db.execute("UPDATE documents SET source_url=? WHERE id=?", (source_url, doc_id))
            ok += 1
            print(f"ok {title}: {doc_id} status={result.get('status')} pages={result.get('page_count')}")
        except Exception as exc:
            print(f"fail {title}: {exc}")
    print(f"imported {ok}/{len(DOCS)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

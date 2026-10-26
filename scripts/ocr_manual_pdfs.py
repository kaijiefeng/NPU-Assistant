"""对已下载的扫描 PDF 做本地 OCR 并导入（RapidOCR + PyMuPDF）。

仅处理 manual-queue.json 中 status=downloaded_needs_ocr 且 local_file 存在的条目。
OCR 在本机完成，不把 PDF/图片上传到第三方；导入仍走现有 ingest 编译链路。
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402
from app.services import ingest  # noqa: E402

QUEUE_PATH = ROOT / "docs/campus-seed/manual-queue.json"
RESULTS_PATH = ROOT / "docs/campus-seed/manual-queue-results.json"
OCR_DIR = ROOT / "backend/data/campus_manual/ocr"
TAGS = ["真实数据", "校园公开资料", "人工队列", "OCR"]


def log(message: str) -> None:
    logging.info(message)


def ocr_pdf(path: Path, *, dpi_scale: float = 2.0) -> tuple[str, int, int]:
    """返回 (OCR 文本, 页数, 低置信度行数)。"""
    doc = pymupdf.open(path)
    engine = RapidOCR()
    sections: list[str] = []
    low_confidence = 0
    for index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi_scale, dpi_scale), alpha=False)
        image = np.array(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
        result, _ = engine(image)
        lines: list[str] = []
        for row in result or []:
            text = str(row[1]).strip()
            score = float(row[2]) if len(row) > 2 else 1.0
            if not text:
                continue
            if score < 0.45:
                low_confidence += 1
            lines.append(text)
        sections.append(f"## 第 {index} 页\n\n" + "\n".join(lines))
    return "\n\n".join(sections).strip(), len(doc), low_confidence


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 PDF 本地 OCR 并导入")
    parser.add_argument("--item", action="append", default=[], help="只处理指定 id")
    parser.add_argument("--limit", type=int, default=0, help="最多处理几条")
    parser.add_argument("--scale", type=float, default=2.0, help="PDF 渲染倍率（默认 2.0）")
    parser.add_argument("--dry-run", action="store_true", help="只 OCR，不导入/不写队列")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    selected = set(args.item)
    items = [
        item
        for item in queue["items"]
        if item.get("status") == "downloaded_needs_ocr"
        and (item.get("local_file") or (item.get("result") or {}).get("local_file"))
        and (not selected or item["id"] in selected)
    ]
    if args.limit > 0:
        items = items[: args.limit]
    results = []
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    for item in items:
        local_file = item.get("local_file") or (item.get("result") or {}).get("local_file")
        pdf_path = ROOT / local_file
        log(f"OCR {item['id']}：{pdf_path.name}")
        if not pdf_path.exists():
            log("  文件不存在，跳过")
            continue
        existing = db.query_one(
            "SELECT id FROM documents WHERE source_url = ? ORDER BY created_at LIMIT 1",
            (item["url"],),
        )
        if existing:
            item["status"] = "already_present"
            item["result"] = {"document_id": existing["id"], "detail": "OCR 前发现已有同 source_url 文档"}
            results.append({"id": item["id"], "status": "already_present", "document_id": existing["id"]})
            continue
        text, pages, low = ocr_pdf(pdf_path, dpi_scale=args.scale)
        if len(text) < 100:
            log("  OCR 文本过短，标记 needs_ocr")
            item["status"] = "needs_ocr"
            item["result"] = {"detail": f"OCR 文本仅 {len(text)} 字"}
            results.append({"id": item["id"], "status": "needs_ocr", "chars": len(text)})
            continue
        ocr_file = OCR_DIR / f"{item['id']}.md"
        ocr_file.write_text(text + "\n", encoding="utf-8")
        log(f"  OCR 完成：{pages} 页，{len(text)} 字，低置信度 {low} 行")
        if args.dry_run:
            results.append({"id": item["id"], "status": "ocr_ready", "chars": len(text), "pages": pages})
            continue
        doc = ingest.ingest_upload(
            f"{item['id']}.md",
            (text + "\n").encode("utf-8"),
            title=item["title"],
            tags=TAGS + [item.get("domain", "")],
            source_type="url",
            source_url=item["url"],
        )
        item["status"] = "imported_ocr"
        item["result"] = {
            "detail": f"本地 OCR {pages} 页，{len(text)} 字；低置信度 {low} 行",
            "document_id": doc.get("id"),
            "local_file": local_file,
            "ocr_file": str(ocr_file.relative_to(ROOT)),
        }
        results.append(
            {
                "id": item["id"],
                "status": "imported_ocr",
                "document_id": doc.get("id"),
                "pages": pages,
                "chars": len(text),
                "low_confidence": low,
            }
        )
        log(f"  导入完成：{doc.get('id')} status={doc.get('status')}")

    if not args.dry_run:
        QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dry_run": args.dry_run, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""知识库路由（LLM Wiki）：源文件上传 / 列表 / 详情 / 删除 / 重编译 / 快照 / 统计。

严格遵循 ``docs/CONTRACT.md`` §5.3 与 ``docs/WIKI-DESIGN.md`` §4.1：

* ``POST   /api/knowledge/documents``             multipart 上传，保存+解析+**同步编译**
* ``GET    /api/knowledge/documents``             列表（含 page_count）
* ``GET    /api/knowledge/documents/{id}``        详情 + 贡献的 wiki_pages
* ``DELETE /api/knowledge/documents/{id}``        删除文档 / 独占词条 / 物理文件
* ``POST   /api/knowledge/documents/{id}/reindex`` 重新解析+编译 → {document, build}
* ``POST   /api/knowledge/seed``                  导入预编译快照（幂等）
* ``GET    /api/knowledge/stats``                 文档 / 词条 / 来源统计

**已移除**（RAG 遗留）：``GET /knowledge/documents/{id}/chunks``、
``POST /knowledge/search``、chunks 表相关字段。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config, db
from ..services import ingest, wiki_builder

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _supported_ext_text() -> str:
    return "、".join(sorted(config.SUPPORTED_EXTS))


def require_document(doc_id: str) -> dict:
    """取文档行，不存在抛 404。"""
    row = db.get_document(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return row


def _document_json(row: dict) -> dict:
    return ingest.document_to_dict(row)


# ---------------------------------------------------------------------------
# 上传（同步编译）
# ---------------------------------------------------------------------------
@router.post("/knowledge/documents", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="知识库源文件"),
    title: str | None = Form(default=None),
    tags: str | None = Form(default=None),
) -> dict:
    """上传源文件并同步完成解析与 Wiki 词条编译。

    * 扩展名不支持 → 400；空文件 → 400；超过 20MB → 413；
    * 解析失败或 LLM 不可用/编译失败 → 201 + doc（``status='failed'``、带中文 ``error``），
      原始文件保留，可稍后 reindex 重试。
    """
    filename = Path((file.filename or "").strip()).name or "未命名文件"
    ext = Path(filename).suffix.lower()
    if ext not in config.SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型「{ext or '无扩展名'}」，仅支持：{_supported_ext_text()}",
        )

    try:
        data = file.file.read()
    except OSError as exc:  # pragma: no cover - 读取失败极少见
        raise HTTPException(status_code=400, detail=f"读取上传文件失败：{exc}") from exc

    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空，无法解析")
    if len(data) > config.MAX_UPLOAD_BYTES:
        max_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(data) / 1024 / 1024:.1f}MB），最大支持 {max_mb}MB",
        )

    try:
        doc = ingest.ingest_upload(
            filename=filename,
            data=data,
            title=(title or "").strip() or None,
            tags=ingest.normalize_tags(tags),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"文件保存失败：{exc}") from exc
    return doc


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------
@router.get("/knowledge/documents")
def list_documents() -> dict:
    """源文件列表（按创建时间倒序，含 ``page_count``）。

    顺带做一次幂等的种子路径修复：升级前导入的快照可能把 ``stored_path``
    指向仓库内置文件，这里迁移到 uploads 副本，避免用户删除源文件时误删仓库内容。
    """
    try:
        wiki_builder.repair_seed_document_paths()
    except Exception:  # pragma: no cover - 修复失败不影响列表返回
        logger.exception("种子文档路径修复失败（已忽略）")
    rows = db.query("SELECT * FROM documents ORDER BY created_at DESC, rowid DESC")
    return {"items": [_document_json(row) for row in rows], "total": len(rows)}


@router.get("/knowledge/documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    """源文件详情 + 该文件贡献的 Wiki 词条列表。"""
    row = require_document(doc_id)
    pages = db.query(
        "SELECT p.id, p.slug, p.title, p.status FROM wiki_pages p "
        "JOIN wiki_sources s ON s.page_id = p.id "
        "WHERE s.document_id = ? GROUP BY p.id, p.slug, p.title, p.status "
        "ORDER BY p.title",
        (doc_id,),
    )
    payload = _document_json(row)
    payload["wiki_pages"] = [
        {
            "id": page["id"],
            "slug": page["slug"],
            "title": page["title"],
            "status": page["status"],
        }
        for page in pages
    ]
    return payload


@router.delete("/knowledge/documents/{doc_id}")
def remove_document(doc_id: str) -> dict:
    """删除源文件、其独占词条与物理文件；共享词条只移除该文件来源。"""
    require_document(doc_id)
    result = ingest.delete_document(doc_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return {"ok": True, "pages_removed": int(result.get("pages_removed") or 0)}


@router.post("/knowledge/documents/{doc_id}/reindex")
def reindex(doc_id: str) -> dict:
    """重新解析并编译单篇源文件，返回 ``{document, build}``。"""
    require_document(doc_id)
    try:
        return ingest.reindex_document(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 快照 / 统计
# ---------------------------------------------------------------------------
@router.post("/knowledge/seed")
def seed() -> dict:
    """导入 ``backend/data/wiki_seed.json`` 预编译快照（按文件名/词条 slug 幂等）。"""
    try:
        return wiki_builder.load_seed_wiki()
    except Exception as exc:  # pragma: no cover - 快照损坏等极端情况
        logger.exception("导入 Wiki 快照失败")
        raise HTTPException(status_code=500, detail=f"导入 Wiki 快照失败：{exc}") from exc


@router.get("/knowledge/stats")
def knowledge_stats() -> dict:
    """知识库统计（兼容保留）：文档 / 就绪文档 / 词条 / 来源。"""
    documents = db.query_one("SELECT COUNT(*) AS n FROM documents")
    ready_documents = db.query_one("SELECT COUNT(*) AS n FROM documents WHERE status = 'ready'")
    pages = db.query_one("SELECT COUNT(*) AS n FROM wiki_pages")
    sources = db.query_one("SELECT COUNT(*) AS n FROM wiki_sources")
    return {
        "documents": int(documents["n"]) if documents else 0,
        "ready_documents": int(ready_documents["n"]) if ready_documents else 0,
        "pages": int(pages["n"]) if pages else 0,
        "sources": int(sources["n"]) if sources else 0,
    }

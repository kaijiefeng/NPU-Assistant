"""知识库路由（LLM Wiki）：上传 / 列表 / 详情 / 切片 / 检索 / 重建索引 / 示例导入 / 统计。

严格遵循 docs/CONTRACT.md §5.3。所有错误使用中文 ``detail``。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from .. import config, db
from ..services import ingest, rag

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["knowledge"])

PREVIEW_CHUNKS = 5
PREVIEW_CHARS = 300


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _supported_ext_text() -> str:
    return "、".join(sorted(config.SUPPORTED_EXTS))


def _truncate(text: str | None, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def require_document(doc_id: str) -> dict:
    """取文档行，不存在抛 404。"""
    row = db.get_document(doc_id)
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return row


def _document_json(row: dict) -> dict:
    return ingest.document_to_dict(row)


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------
@router.post("/knowledge/documents", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="知识库文件"),
    title: str | None = Form(default=None),
    tags: str | None = Form(default=None),
) -> dict:
    """上传文件并同步完成解析、切片与入库。

    * 超过 20MB → 413；
    * 扩展名不支持 → 400；
    * 解析失败 → 返回 201 + doc（``status='failed'`` 且带 ``error``）。
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
    """文档列表（按 created_at 倒序）。"""
    rows = db.query("SELECT * FROM documents ORDER BY created_at DESC, id DESC")
    return {"items": [_document_json(row) for row in rows], "total": len(rows)}


@router.get("/knowledge/documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    """文档详情 + 前 5 条切片预览（content 截断 300 字）。"""
    row = require_document(doc_id)
    previews = db.query(
        "SELECT id, seq, section, content, tokens FROM chunks "
        "WHERE document_id = ? ORDER BY seq LIMIT ?",
        (doc_id, PREVIEW_CHUNKS),
    )
    for chunk in previews:
        chunk["content"] = _truncate(chunk.get("content"), PREVIEW_CHARS)
    payload = _document_json(row)
    payload["chunks_preview"] = previews
    return payload


@router.delete("/knowledge/documents/{doc_id}")
def remove_document(doc_id: str) -> dict:
    """删除文档及其切片与物理文件。"""
    if not ingest.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return {"ok": True}


@router.post("/knowledge/documents/{doc_id}/reindex")
def reindex(doc_id: str) -> dict:
    """重新解析切片，返回最新的 doc 对象。"""
    require_document(doc_id)
    try:
        doc = ingest.reindex_document(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return doc


@router.get("/knowledge/documents/{doc_id}/chunks")
def list_chunks(
    doc_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """分页查看文档切片。"""
    require_document(doc_id)
    total_row = db.query_one("SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?", (doc_id,))
    total = int(total_row["n"]) if total_row else 0
    items = db.query(
        "SELECT id, seq, section, content, tokens FROM chunks "
        "WHERE document_id = ? ORDER BY seq LIMIT ? OFFSET ?",
        (doc_id, limit, offset),
    )
    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# 检索 / seed / 统计
# ---------------------------------------------------------------------------
@router.post("/knowledge/search")
def search_knowledge(payload: dict) -> dict:
    """检索测试接口：返回带相关度分数的切片列表（空库返回空数组）。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="检索内容不能为空")
    query = query.strip()
    if len(query) > 2000:
        raise HTTPException(status_code=400, detail="检索内容不能超过 2000 字符")

    top_k_raw = payload.get("top_k")
    top_k: int | None = None
    if top_k_raw is not None:
        try:
            top_k = int(top_k_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="top_k 必须是整数") from exc
        if top_k < 1:
            raise HTTPException(status_code=400, detail="top_k 必须大于 0")
        top_k = min(top_k, 20)  # 上限保护，避免一次返回过多切片

    started = time.perf_counter()
    items = rag.search(query, top_k=top_k)
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    return {"query": query, "latency_ms": latency_ms, "items": items}


@router.post("/knowledge/seed")
def seed() -> dict:
    """导入 backend/data/seed_docs/ 下示例文档（按文件名幂等）。"""
    try:
        result = ingest.seed_documents()
    except Exception as exc:  # pragma: no cover - 目录权限等极端情况
        logger.exception("示例资料导入失败")
        raise HTTPException(status_code=500, detail=f"示例资料导入失败：{exc}") from exc
    return result


@router.get("/knowledge/stats")
def knowledge_stats() -> dict:
    """知识库统计：文档数、切片数、总字符数、按文件类型分布。"""
    documents = db.query_one("SELECT COUNT(*) AS n FROM documents") or {"n": 0}
    chunks = db.query_one("SELECT COUNT(*) AS n FROM chunks") or {"n": 0}
    chars = db.query_one("SELECT COALESCE(SUM(char_count), 0) AS n FROM documents") or {"n": 0}
    by_file_type = {
        str(row["file_ext"]): int(row["n"])
        for row in db.query(
            "SELECT file_ext, COUNT(*) AS n FROM documents GROUP BY file_ext ORDER BY file_ext"
        )
    }
    return {
        "documents": int(documents["n"]),
        "chunks": int(chunks["n"]),
        "chars": int(chars["n"]),
        "by_file_type": by_file_type,
    }

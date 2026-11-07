"""知识库路由（LLM Wiki）：源文件上传 / 移动端快速收录 / 列表 / 详情 / 删除 / 重编译。

严格遵循 ``docs/CONTRACT.md`` §5.3、``docs/WIKI-DESIGN.md`` §4.1 与
``docs/MOBILE-OPS.md`` §2.1：

* ``POST   /api/knowledge/documents``             multipart 上传，保存+解析+**同步编译**
* ``POST   /api/knowledge/import/url``            收录网页/公众号链接（抓取+正文提取+编译）
* ``POST   /api/knowledge/import/text``           收录粘贴文本（微信/QQ 转发内容）
* ``POST   /api/knowledge/import/batch``          批量收录链接（≤5 条，逐条返回结果）
* ``GET    /api/knowledge/documents``             列表（含 page_count、source_type、source_url）
* ``GET    /api/knowledge/documents/{id}``        详情 + 贡献的 wiki_pages
* ``GET    /api/knowledge/documents/{id}/download`` 下载原始文件（附件形式）
* ``DELETE /api/knowledge/documents/{id}``        删除文档 / 独占词条 / 物理文件
* ``POST   /api/knowledge/documents/{id}/reindex`` 重新解析+编译 → {document, build}
* ``POST   /api/knowledge/seed``                  导入预编译快照（幂等）
* ``GET    /api/knowledge/stats``                 文档 / 词条 / 来源统计

管理令牌由 ``app.main.admin_guard`` 中间件统一校验，路由内不重复加依赖。
**已移除**（RAG 遗留）：``GET /knowledge/documents/{id}/chunks``、
``POST /knowledge/search``、chunks 表相关字段。
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..services import importer, ingest, jobs, wiki_builder

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
# 移动端快速收录：请求体（docs/MOBILE-OPS.md §2.1）
# ---------------------------------------------------------------------------
class ImportUrlRequest(BaseModel):
    """``POST /knowledge/import/url`` 请求体。"""

    url: str | None = None
    title: str | None = None
    tags: list[str] | str | None = None


class ImportTextRequest(BaseModel):
    """``POST /knowledge/import/text`` 请求体。"""

    title: str | None = None
    text: str | None = None
    tags: list[str] | str | None = None
    source_url: str | None = None


class ImportBatchRequest(BaseModel):
    """``POST /knowledge/import/batch`` 请求体（最多 5 条链接）。"""

    urls: list[str] | None = Field(default=None)
    tags: list[str] | str | None = None


# ---------------------------------------------------------------------------
# 上传（同步编译）
# ---------------------------------------------------------------------------
@router.post("/knowledge/documents", status_code=201)
def upload_document(
    response: Response,
    file: UploadFile = File(..., description="知识库源文件"),
    title: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    async_mode: bool = Query(default=False, alias="async", description="true=后台编译，返回 202 + job_id"),
) -> dict:
    """上传源文件并完成解析与 Wiki 词条编译。

    * 默认同步：201 + doc；
    * ``?async=1``：先保存文件并返回 202 + ``job_id``，由后台队列解析/编译，
      进度见 ``GET /api/jobs/{id}``（适合大文件或手机弱网）；
    * 扩展名不支持 → 400；空文件 → 400；超过 20MB → 413；
    * 解析失败或 LLM 不可用/编译失败 → doc ``status='failed'``、带中文 ``error``，
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

    normalized_title = (title or "").strip() or None
    normalized_tags = ingest.normalize_tags(tags)

    if async_mode:
        job = jobs.create_job(
            "ingest_upload",
            {
                "filename": filename,
                "title": normalized_title,
                "tags": normalized_tags,
            },
            created_by="admin",
        )
        try:
            staging = jobs.stage_upload(job["id"], filename, data)
        except OSError as exc:
            jobs.request_cancel(job["id"])
            raise HTTPException(status_code=500, detail=f"保存上传文件失败：{exc}") from exc
        db.execute(
            "UPDATE jobs SET payload = ? WHERE id = ?",
            (
                db.to_json(
                    {
                        "filename": filename,
                        "title": normalized_title,
                        "tags": normalized_tags,
                        "staging_path": staging,
                    }
                ),
                job["id"],
            ),
        )
        response.status_code = 202
        return {
            "job_id": job["id"],
            "status": "queued",
            "document": None,
            "message": "文件已接收，正在后台解析与编译；可在「任务与告警」查看进度。",
        }

    try:
        doc = ingest.ingest_upload(
            filename=filename,
            data=data,
            title=normalized_title,
            tags=normalized_tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"文件保存失败：{exc}") from exc
    return doc


# ---------------------------------------------------------------------------
# 移动端快速收录（链接 / 文本 / 批量）—— 见 docs/MOBILE-OPS.md §2.1
# ---------------------------------------------------------------------------
@router.post("/knowledge/import/url", status_code=201)
def import_url(payload: ImportUrlRequest, response: Response) -> dict:
    """收录网页/公众号文章链接：抓取 → 正文提取 → 查重 → 源文件 → 编译词条。

    * 新链接 → 201；同一 URL 已收录 → 200 + ``duplicated=true``（不重复编译）；
    * 抓取失败 / 非 HTML 文本 / 私网地址（SSRF 防护）等 → 400 中文 ``detail``。
    """
    try:
        result = importer.import_url(
            payload.url or "",
            title=payload.title,
            tags=payload.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=importer.error_text(exc)) from exc
    if result.get("duplicated"):
        response.status_code = 200
    return result


@router.post("/knowledge/import/text", status_code=201)
def import_text(payload: ImportTextRequest) -> dict:
    """收录一段粘贴文本（微信/QQ 转发内容），生成源文件并编译词条。

    ``text`` 需 30–200000 字；``title`` 为空时取首行（≤40 字）或「未命名资料-时间」。
    """
    try:
        return importer.import_text(
            payload.title,
            payload.text or "",
            tags=payload.tags,
            source_url=payload.source_url or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=importer.error_text(exc)) from exc


@router.post("/knowledge/import/batch")
def import_batch(payload: ImportBatchRequest) -> dict:
    """批量收录链接（≤5 条，顺序处理）；单条失败不影响其它，逐条返回 ok/error。"""
    try:
        return importer.import_batch(payload.urls, tags=payload.tags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=importer.error_text(exc)) from exc


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


@router.get("/knowledge/documents/{doc_id}/download")
def download_document(doc_id: str) -> FileResponse:
    """下载原始源文件（``Content-Disposition: attachment``）。

    文件已删除或记录指向不存在的位置 → 404；``filename`` 取上传时的原始文件名，
    保证手机/QQ 端下载后仍是可读名字。
    """
    row = require_document(doc_id)
    stored_path = Path(str(row.get("stored_path") or ""))
    if not stored_path.is_file():
        raise HTTPException(status_code=404, detail="原始文件不存在或已被删除")
    filename = str(row.get("filename") or "").strip() or stored_path.name
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        stored_path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": "no-cache"},
    )


@router.delete("/knowledge/documents/{doc_id}")
def remove_document(doc_id: str) -> dict:
    """删除源文件、其独占词条与物理文件；共享词条只移除该文件来源。"""
    require_document(doc_id)
    result = ingest.delete_document(doc_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return {"ok": True, "pages_removed": int(result.get("pages_removed") or 0)}


@router.post("/knowledge/documents/{doc_id}/reindex")
def reindex(
    doc_id: str,
    response: Response,
    async_mode: bool = Query(default=False, alias="async"),
) -> dict:
    """重新解析并编译单篇源文件，返回 ``{document, build}``；``?async=1`` 返回任务。"""
    require_document(doc_id)
    if async_mode:
        job = jobs.create_job(
            "reindex", {"document_id": doc_id}, created_by="admin"
        )
        response.status_code = 202
        return {"job_id": job["id"], "status": "queued", "document": None}
    try:
        result = ingest.reindex_document(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # wiki_builder 返回的 doc 对象不含来源字段，这里按数据库最新值补齐
    row = db.get_document(doc_id)
    if row and isinstance(result.get("document"), dict):
        result["document"].update(
            {
                "source_type": row.get("source_type") or "file",
                "source_url": row.get("source_url") or "",
            }
        )
    return result


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

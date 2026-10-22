"""Wiki 路由：词条浏览 / 详情 / 编辑 / 删除 / 导航搜索 / 统计 / 重建 / 快照导入。

严格遵循 ``docs/WIKI-DESIGN.md`` §4.2 与 ``docs/CONTRACT.md`` §5.4：

* ``GET    /api/wiki/tree``
* ``GET    /api/wiki/pages?q=&tag=&limit=&offset=``
* ``GET    /api/wiki/pages/{id}``      详情（含 body_html / sources / links）
* ``PUT    /api/wiki/pages/{id}``      手工编辑（version+1、status=ready）
* ``DELETE /api/wiki/pages/{id}``
* ``POST   /api/wiki/search``           导航搜索（method/candidates/selected）
* ``GET    /api/wiki/stats``
* ``POST   /api/wiki/rebuild``          全部重建（后台 202）/ 单篇重建（同步）
* ``GET    /api/wiki/builds/{id}``、``GET /api/wiki/builds``
* ``POST   /api/wiki/seed``             导入预编译快照（幂等）

错误统一 ``{"detail": "中文"}``。
"""
from __future__ import annotations

import html
import logging
from typing import Any

import markdown
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse

from .. import db
from ..services import text_utils, wiki_builder, wiki_nav

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["wiki"])

MARKDOWN_EXTENSIONS = ["tables", "fenced_code", "nl2br"]
MAX_QUERY_CHARS = 2000
MAX_PAGE_TITLE_CHARS = 120


# ---------------------------------------------------------------------------
# 序列化 / 渲染工具
# ---------------------------------------------------------------------------
def _render_markdown(body_md: str) -> str:
    """先转义 HTML 再渲染 Markdown，避免词条正文注入脚本（XSS）。"""
    escaped = html.escape(str(body_md or ""))
    return markdown.markdown(escaped, extensions=MARKDOWN_EXTENSIONS)


def _page_dict(row: dict | None) -> dict | None:
    if not row:
        return None
    aliases = db.from_json(row.get("aliases"), [])
    tags = db.from_json(row.get("tags"), [])
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "summary": row.get("summary") or "",
        "body_md": row.get("body_md") or "",
        "aliases": aliases if isinstance(aliases, list) else [],
        "tags": tags if isinstance(tags, list) else [],
        "status": row.get("status") or "ready",
        "version": int(row.get("version") or 1),
        "source_count": int(row.get("source_count") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _page_card(page: dict) -> dict:
    """词条列表/树使用的精简字段。"""
    return {
        "id": page["id"],
        "slug": page["slug"],
        "title": page["title"],
        "summary": page["summary"],
        "tags": page["tags"],
        "status": page["status"],
        "source_count": page["source_count"],
        "updated_at": page["updated_at"],
    }


def _build_payload(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "document_id": row.get("document_id"),
        "status": row.get("status"),
        "model": row.get("model"),
        "pages_created": int(row.get("pages_created") or 0),
        "pages_updated": int(row.get("pages_updated") or 0),
        "pages_removed": int(row.get("pages_removed") or 0),
        "error": row.get("error"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


def _require_page(page_id: str) -> dict:
    page = _page_dict(db.get_wiki_page(page_id))
    if not page:
        raise HTTPException(status_code=404, detail="Wiki 词条不存在或已被删除")
    return page


def _payload_dict(payload: Any) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return payload


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HTTPException(status_code=400, detail=f"{field} 必须是字符串数组")
    result: list[str] = []
    for item in value:
        text = item.strip()
        if text and text not in result:
            result.append(text[:60])
    return result


def _page_detail(page: dict) -> dict:
    payload = dict(page)
    payload["body_html"] = _render_markdown(page["body_md"])
    payload["sources"] = db.get_page_sources(page["id"])
    links = db.query(
        "SELECT l.to_page_id AS page_id, p.title AS title, l.relation AS relation "
        "FROM wiki_links l JOIN wiki_pages p ON p.id = l.to_page_id "
        "WHERE l.from_page_id = ? ORDER BY p.title",
        (page["id"],),
    )
    payload["links"] = [
        {
            "page_id": row["page_id"],
            "title": row["title"],
            "relation": row.get("relation") or "related",
        }
        for row in links
    ]
    return payload


# ---------------------------------------------------------------------------
# 树 / 列表 / 详情
# ---------------------------------------------------------------------------
@router.get("/wiki/tree")
def wiki_tree() -> dict:
    """按标签分组的词条树；无标签词条归入「其他」。"""
    rows = db.query("SELECT * FROM wiki_pages ORDER BY updated_at DESC, rowid DESC")
    groups: dict[str, list[dict]] = {}
    for row in rows:
        page = _page_dict(row)
        if not page:
            continue
        tags = page.get("tags") or []
        tag = str(tags[0]) if tags else "其他"
        groups.setdefault(tag, []).append(_page_card(page))

    ordered_tags = sorted(tag for tag in groups if tag != "其他")
    if "其他" in groups:
        ordered_tags.append("其他")
    return {
        "total": len(rows),
        "groups": [
            {"tag": tag, "count": len(groups[tag]), "pages": groups[tag]}
            for tag in ordered_tags
        ],
    }


@router.get("/wiki/pages")
def list_pages(
    q: str | None = None,
    tag: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """词条列表：支持关键词（标题/摘要/别名）与标签筛选、分页。"""
    needle = (q or "").strip().lower()
    tag_filter = (tag or "").strip()
    rows = db.query("SELECT * FROM wiki_pages ORDER BY updated_at DESC, rowid DESC")
    items: list[dict] = []
    for row in rows:
        page = _page_dict(row)
        if not page:
            continue
        if tag_filter and tag_filter not in (page.get("tags") or []):
            continue
        if needle:
            haystack = " ".join(
                [
                    page["title"],
                    page["summary"],
                    " ".join(str(alias) for alias in page.get("aliases") or []),
                    " ".join(str(item) for item in page.get("tags") or []),
                ]
            ).lower()
            if needle not in haystack:
                continue
        items.append(_page_card(page))
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.get("/wiki/pages/{page_id}")
def page_detail(page_id: str) -> dict:
    """词条详情：页面字段 + ``body_html`` + 来源锚点 + 相关词条。"""
    return _page_detail(_require_page(page_id))


@router.put("/wiki/pages/{page_id}")
def update_page(page_id: str, payload: dict) -> dict:
    """手工编辑词条：version+1 且 status=ready。"""
    page = _require_page(page_id)
    data = _payload_dict(payload)
    allowed = {"title", "summary", "body_md", "aliases", "tags"}
    unknown = set(data) - allowed
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"不支持的词条字段：{', '.join(sorted(unknown))}"
        )
    if not data:
        raise HTTPException(status_code=400, detail="没有可更新的字段")

    updates: list[str] = []
    params: list[Any] = []
    if "title" in data:
        title = str(data.get("title") or "").strip().strip("《》")
        if not title:
            raise HTTPException(status_code=400, detail="词条标题不能为空")
        title = title[:MAX_PAGE_TITLE_CHARS]
        slug = text_utils.slugify(title)
        if slug != page["slug"]:
            conflict = db.get_wiki_page_by_slug(slug)
            if conflict and conflict["id"] != page_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"已存在标题归一化后相同的词条「{conflict['title']}」，请先合并或修改标题",
                )
            updates.extend(["title = ?", "slug = ?"])
            params.extend([title, slug])
    if "summary" in data:
        updates.append("summary = ?")
        params.append(str(data.get("summary") or "").strip()[:500])
    if "body_md" in data:
        updates.append("body_md = ?")
        params.append(str(data.get("body_md") or ""))
    if "aliases" in data:
        updates.append("aliases = ?")
        params.append(db.to_json(_string_list(data.get("aliases"), "aliases")))
    if "tags" in data:
        updates.append("tags = ?")
        params.append(db.to_json(_string_list(data.get("tags"), "tags")))

    updates.extend(["status = 'ready'", "version = version + 1", "updated_at = ?"])
    params.extend([db.now(), page_id])
    db.execute(f"UPDATE wiki_pages SET {', '.join(updates)} WHERE id = ?", params)
    logger.info("词条已手工编辑：%s", page_id)
    return _page_detail(_require_page(page_id))


@router.delete("/wiki/pages/{page_id}")
def delete_page(page_id: str) -> dict:
    """删除词条及其来源锚点与相关链接。"""
    _require_page(page_id)
    affected_documents = [
        row["document_id"]
        for row in db.query(
            "SELECT DISTINCT document_id FROM wiki_sources WHERE page_id = ?", (page_id,)
        )
    ]
    db.execute("DELETE FROM wiki_links WHERE from_page_id = ? OR to_page_id = ?",
               (page_id, page_id))
    db.execute("DELETE FROM wiki_sources WHERE page_id = ?", (page_id,))
    db.execute("DELETE FROM wiki_pages WHERE id = ?", (page_id,))
    # 同步受影响源文件的 page_count，避免统计与实际词条数脱节
    for document_id in affected_documents:
        row = db.query_one(
            "SELECT COUNT(DISTINCT page_id) AS n FROM wiki_sources WHERE document_id = ?",
            (document_id,),
        )
        db.execute(
            "UPDATE documents SET page_count = ? WHERE id = ?",
            (int(row["n"]) if row else 0, document_id),
        )
    logger.info("词条已删除：%s", page_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 导航搜索 / 统计
# ---------------------------------------------------------------------------
@router.post("/wiki/search")
def search_wiki(payload: dict) -> dict:
    """导航测试接口：exact → keyword → LLM 选页，返回 method 与候选分数。"""
    data = _payload_dict(payload)
    query = data.get("query")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="搜索内容不能为空")
    query = query.strip()
    if len(query) > MAX_QUERY_CHARS:
        raise HTTPException(
            status_code=400, detail=f"搜索内容不能超过 {MAX_QUERY_CHARS} 字符"
        )
    limit = 5
    if data.get("limit") is not None:
        try:
            limit = int(data["limit"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="limit 必须是整数") from exc
        if limit < 1:
            raise HTTPException(status_code=400, detail="limit 必须大于 0")
        limit = min(limit, 50)
    return wiki_nav.search_pages(query, limit=limit)


@router.get("/wiki/stats")
def wiki_statistics() -> dict:
    """Wiki 统计：词条 / 来源 / 源文件 / 标签分布 / 最近构建。"""
    stats = db.wiki_stats()
    documents = db.query_one("SELECT COUNT(*) AS n FROM documents")
    last_build = db.query_one(
        "SELECT * FROM wiki_builds ORDER BY started_at DESC, rowid DESC LIMIT 1"
    )
    return {
        "pages": stats.get("pages", 0),
        "sources": stats.get("sources", 0),
        "documents": int(documents["n"]) if documents else 0,
        "by_tag": stats.get("by_tag", {}),
        "last_build": _build_payload(last_build),
    }


# ---------------------------------------------------------------------------
# 重建 / 构建记录 / 快照
# ---------------------------------------------------------------------------
def _run_rebuild_all(build_id: str) -> None:
    """后台任务：重建全部文档词条，并把结果写回 wiki_builds。"""
    try:
        result = wiki_builder.rebuild_all()
        wiki_builder.finish_build(
            build_id,
            status=str(result.get("status") or "ready"),
            model=result.get("model"),
            pages_created=int(result.get("pages_created") or 0),
            pages_updated=int(result.get("pages_updated") or 0),
            pages_removed=int(result.get("pages_removed") or 0),
            error=result.get("error"),
        )
        logger.info("全部 Wiki 重建完成：%s", result)
    except Exception as exc:  # pragma: no cover - 后台任务兜底
        logger.exception("全部 Wiki 重建失败")
        wiki_builder.finish_build(
            build_id, status="failed", error=f"重建全部词条失败：{exc}"
        )


@router.post("/wiki/rebuild", status_code=202)
def rebuild_wiki(background: BackgroundTasks, payload: dict | None = None) -> Any:
    """重建全部词条（后台 202）或同步重建单篇 ``{document_id}``。"""
    data = _payload_dict(payload)
    document_id = data.get("document_id")
    if document_id is not None:
        document_id = str(document_id).strip()
        if not document_id:
            raise HTTPException(status_code=400, detail="document_id 不能为空")
        if not db.get_document(document_id):
            raise HTTPException(status_code=404, detail="文档不存在或已被删除")
        result = wiki_builder.rebuild_document(document_id)
        build = db.query_one(
            "SELECT * FROM wiki_builds WHERE id = ?", (result.get("build_id"),)
        )
        return JSONResponse(
            status_code=200,
            content={
                "document": result.get("document"),
                "build": _build_payload(build),
                "status": result.get("status"),
                "error": result.get("error"),
            },
        )

    build_id = wiki_builder.start_build(None, None)
    background.add_task(_run_rebuild_all, build_id)
    return JSONResponse(status_code=202, content={"build_id": build_id, "status": "running"})


@router.get("/wiki/builds")
def list_builds(limit: int = Query(default=10, ge=1, le=100)) -> dict:
    """最近构建记录。"""
    rows = db.query(
        "SELECT * FROM wiki_builds ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
    )
    return {"items": [_build_payload(row) for row in rows], "total": len(rows)}


@router.get("/wiki/builds/{build_id}")
def get_build(build_id: str) -> dict:
    """构建状态。"""
    row = db.query_one("SELECT * FROM wiki_builds WHERE id = ?", (build_id,))
    if not row:
        raise HTTPException(status_code=404, detail="构建记录不存在")
    return _build_payload(row)


@router.post("/wiki/seed")
def seed_wiki() -> dict:
    """导入预编译 Wiki 快照（幂等）。"""
    try:
        return wiki_builder.load_seed_wiki()
    except Exception as exc:  # pragma: no cover - 快照损坏等极端情况
        logger.exception("导入 Wiki 快照失败")
        raise HTTPException(status_code=500, detail=f"导入 Wiki 快照失败：{exc}") from exc

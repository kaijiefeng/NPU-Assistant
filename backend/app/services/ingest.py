"""知识库入库（LLM Wiki 版）：保存源文件 → 解析 → 同步编译词条。

对外接口（见 docs/CONTRACT.md §5.3 / §6、docs/MOBILE-OPS.md §2.1）::

    document_to_dict(row) -> dict
    ingest_upload(filename, data, title=None, tags=None, *,
                  source_type="file", source_url="") -> dict
    ingest_text(title, text, *, tags=None) -> dict     # 移动端「文本收录」
    ingest_url(title, text, *, url, tags=None) -> dict # 移动端「链接收录」
    reindex_document(doc_id) -> dict          # → {document, build}
    delete_document(doc_id) -> dict            # → {ok, pages_removed}

经典 RAG 的 ``chunks`` 切片、``retrieval`` 索引与 BM25 已彻底移除：

* 所有入库路径（文件上传 / 文本 / URL）最终都走 :func:`_ingest_bytes`，即
  **只有一个编译入口**：保存 ``uploads/<doc_id>.md`` 后立即调用
  :func:`services.wiki_builder.compile_document` 同步编译词条；
* ``documents.source_type`` 记录来源（``file|url|text``），``source_url`` 记录原文链接；
* 解析/编译失败把文档标记为 ``failed`` 并写中文 error，保留文件以便重试；
* 删除文档会清理其独占词条、共享词条来源与物理文件。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .. import config, db
from . import parsing, wiki_builder

logger = logging.getLogger("npu-assistant")

MAX_TAGS = 10
MAX_TAG_CHARS = 30

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")
_TAG_SEP_RE = re.compile(r"[,，、;；]")


# ---------------------------------------------------------------------------
# 序列化 / 小工具
# ---------------------------------------------------------------------------
def document_to_dict(row: dict | None) -> dict:
    """把 documents 表行转换为契约里的 doc 对象（tags 反序列化为数组）。"""
    if not row:
        return {}
    tags = db.from_json(row.get("tags"), [])
    if not isinstance(tags, list):
        tags = []
    return {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "file_ext": row["file_ext"],
        "size_bytes": int(row.get("size_bytes") or 0),
        "status": row["status"],
        "error": row.get("error"),
        "tags": [str(tag) for tag in tags if isinstance(tag, (str, int, float))],
        "char_count": int(row.get("char_count") or 0),
        "page_count": int(row.get("page_count") or 0),
        # 移动端快速收录：来源类型（file|url|text）与原文链接
        "source_type": str(row.get("source_type") or "file"),
        "source_url": str(row.get("source_url") or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_document(doc_id: str) -> dict | None:
    """按 id 取 doc 对象，不存在返回 ``None``。"""
    return document_to_dict(db.get_document(doc_id)) or None


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文按字、英文按词），仅用于展示。"""
    return len(_TOKEN_RE.findall(text or ""))


def normalize_tags(tags: list[str] | str | None) -> list[str]:
    """把逗号分隔字符串/列表统一成去重后的标签数组。"""
    if not tags:
        return []
    raw_items = [tags] if isinstance(tags, str) else list(tags)
    result: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        for part in _TAG_SEP_RE.split(item):
            text = part.strip()[:MAX_TAG_CHARS]
            if not text:
                continue
            if text not in result:
                result.append(text)
            if len(result) >= MAX_TAGS:
                return result
    return result


def _error_text(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if len(message) > 500:
        message = message[:497] + "…"
    return message


def _stored_path(doc_id: str, ext: str) -> Path:
    config.ensure_dirs()
    return config.UPLOAD_DIR / f"{doc_id}{ext}"


def _ensure_supported(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in config.SUPPORTED_EXTS:
        supported = "、".join(sorted(config.SUPPORTED_EXTS))
        raise ValueError(f"不支持的文件类型「{ext or '无扩展名'}」，仅支持：{supported}")
    if not data:
        raise ValueError("文件内容为空，无法解析")
    if len(data) > config.MAX_UPLOAD_BYTES:
        max_mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValueError(f"文件过大（{len(data) / 1024 / 1024:.1f}MB），最大支持 {max_mb}MB")
    return ext


def _mark_failed(doc_id: str, error: str) -> None:
    db.execute(
        "UPDATE documents SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (error, db.now(), doc_id),
    )


# ---------------------------------------------------------------------------
# 入库主流程（上传 / 文本 / URL 共用：保存 → 解析 → 同步编译）
# ---------------------------------------------------------------------------
_ALLOWED_SOURCE_TYPES = {"file", "url", "text"}
_WS_RE = re.compile(r"\s+")
_FILENAME_BAD_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _normalize_source(source_type: str | None, source_url: str | None) -> tuple[str, str]:
    """规范化来源字段：非法类型回退 ``file``；非 URL 来源不保留 ``source_url``。"""
    kind = str(source_type or "file").strip().lower()
    if kind not in _ALLOWED_SOURCE_TYPES:
        kind = "file"
    url = str(source_url or "").strip()
    if kind != "url":
        url = ""
    return kind, url


def _one_line(text: str | None) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _filename_base(title: str | None, fallback: str) -> str:
    """把标题转成安全文件名（去非法字符、限长），为空时用兜底前缀。"""
    base = _FILENAME_BAD_RE.sub("-", _one_line(title)).strip(" .-")
    return base[:60].strip(" .-") or fallback


def _ingest_bytes(
    filename: str,
    data: bytes,
    title: str | None = None,
    tags: list[str] | str | None = None,
    *,
    source_type: str = "file",
    source_url: str = "",
) -> dict:
    """保存字节流并同步编译 Wiki 词条（**唯一**编译入口）。

    * 文件保存到 ``backend/data/uploads/<doc_id><ext>``；
    * 参数非法 / 扩展名不支持抛 ``ValueError``（路由层转 400/413）；
    * 解析失败或编译失败**不抛异常**：doc 置 ``failed`` 并带中文 ``error``，
      原始文件保留，可通过 reindex 重试。
    """
    original_name = Path((filename or "").strip()).name or "未命名文件"
    ext = _ensure_supported(original_name, data)
    source_kind, source_url_value = _normalize_source(source_type, source_url)

    requested_title = (title or "").strip()
    fallback_title = Path(original_name).stem or original_name
    doc_id = db.new_id()
    stored_path = _stored_path(doc_id, ext)
    try:
        stored_path.write_bytes(data)
    except OSError as exc:
        raise ValueError(f"文件保存失败：{exc}") from exc

    timestamp = db.now()
    try:
        db.execute(
            "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, status, "
            "error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, 'processing', NULL, ?, 0, 0, ?, ?, ?, ?)",
            (
                doc_id,
                requested_title or fallback_title,
                original_name,
                str(stored_path),
                ext,
                len(data),
                db.to_json(normalize_tags(tags)),
                source_kind,
                source_url_value,
                timestamp,
                timestamp,
            ),
        )
    except Exception:
        # source_url 唯一索引冲突等插入失败：回收刚写入的源文件，避免孤儿文件
        try:
            stored_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不影响原始异常
            logger.warning("清理未入库的源文件失败：%s", stored_path)
        raise

    try:
        parsed = parsing.parse_document(original_name, data)
        final_title = requested_title or parsed.title or fallback_title
        db.execute(
            "UPDATE documents SET title = ?, char_count = ?, updated_at = ? WHERE id = ?",
            (final_title, len(parsed.text), db.now(), doc_id),
        )
    except parsing.ParseError as exc:
        error = _error_text(exc)
        _mark_failed(doc_id, error)
        logger.warning("文档解析失败：%s（%s）", original_name, error)
        return document_to_dict(db.get_document(doc_id))
    except Exception as exc:  # 防御性兜底：解析层异常都转成可读错误
        error = _error_text(exc)
        _mark_failed(doc_id, error)
        logger.warning("文档解析异常：%s（%s）", original_name, error)
        return document_to_dict(db.get_document(doc_id))

    # 同步编译：失败会写入 documents.error / status=failed，不抛异常
    try:
        wiki_builder.compile_document(doc_id)
    except Exception as exc:  # 编译管线本身异常（如文档被并发删除）也不能 500
        error = _error_text(exc)
        _mark_failed(doc_id, f"编译 Wiki 词条失败：{error}")
        logger.exception("文档编译发生未预期异常：%s", original_name)

    return document_to_dict(db.get_document(doc_id))


def _ingest_markdown(
    title: str | None,
    body: str,
    tags: list[str] | str | None,
    *,
    source_type: str,
    source_url: str = "",
    filename_prefix: str,
) -> dict:
    """把「一级标题 + 正文」写成 Markdown 源文件后入库编译。"""
    clean_title = _one_line(title) or "未命名资料"
    body_text = str(body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body_text:
        raise ValueError("正文内容为空，无法收录")
    markdown = f"# {clean_title}\n\n{body_text}\n"
    filename = f"{_filename_base(clean_title, filename_prefix)}.md"
    return _ingest_bytes(
        filename,
        markdown.encode("utf-8"),
        clean_title,
        tags,
        source_type=source_type,
        source_url=source_url,
    )


def ingest_upload(
    filename: str,
    data: bytes,
    title: str | None = None,
    tags: list[str] | str | None = None,
    *,
    source_type: str = "file",
    source_url: str = "",
) -> dict:
    """保存上传文件、解析并同步编译 Wiki 词条，返回最新 doc 对象。

    ``source_type`` / ``source_url`` 供机器人或移动端标注来源（默认普通文件上传）。
    """
    return _ingest_bytes(
        filename,
        data,
        title,
        tags,
        source_type=source_type,
        source_url=source_url,
    )


def ingest_text(
    title: str | None,
    text: str,
    *,
    tags: list[str] | str | None = None,
) -> dict:
    """移动端「文本收录」：写 Markdown 源文件 → 入库 → 同步编译（``source_type=text``）。"""
    return _ingest_markdown(
        title,
        text,
        tags,
        source_type="text",
        filename_prefix="文本收录",
    )


def ingest_url(
    title: str | None,
    text: str,
    *,
    url: str,
    tags: list[str] | str | None = None,
) -> dict:
    """移动端「链接收录」：写 Markdown 源文件 → 入库 → 同步编译（``source_type=url``）。"""
    return _ingest_markdown(
        title,
        text,
        tags,
        source_type="url",
        source_url=url,
        filename_prefix="网页收录",
    )


# ---------------------------------------------------------------------------
# 重新编译 / 删除
# ---------------------------------------------------------------------------
def _build_to_dict(row: dict | None) -> dict | None:
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


def reindex_document(doc_id: str) -> dict:
    """重新解析并编译单个文档，返回 ``{document, build}``。

    文档不存在时抛 ``ValueError``（路由层负责转成 404）。
    """
    row = db.get_document(doc_id)
    if not row:
        raise ValueError("文档不存在或已被删除")
    stored_path = Path(row.get("stored_path") or "")
    if not stored_path.is_file():
        error = "原始文件已丢失，无法重新编译，请重新上传该文档"
        _mark_failed(doc_id, error)
        return {"document": document_to_dict(db.get_document(doc_id)), "build": None}

    db.execute(
        "UPDATE documents SET status = 'processing', error = NULL, updated_at = ? WHERE id = ?",
        (db.now(), doc_id),
    )
    try:
        result = wiki_builder.rebuild_document(doc_id)
    except ValueError as exc:
        raise
    except Exception as exc:  # pragma: no cover - 编译层异常不应 500
        logger.exception("文档重新编译失败：%s", row.get("filename"))
        _mark_failed(doc_id, f"重新编译失败：{_error_text(exc)}")
        return {"document": document_to_dict(db.get_document(doc_id)), "build": None}

    build = db.query_one(
        "SELECT * FROM wiki_builds WHERE id = ?", (result.get("build_id"),)
    )
    if build is None:
        build = db.query_one(
            "SELECT * FROM wiki_builds WHERE document_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (doc_id,),
        )
    return {
        "document": result.get("document") or document_to_dict(db.get_document(doc_id)),
        "build": _build_to_dict(build),
    }


def _is_inside(path: str | Path, root: str | Path) -> bool:
    """判断 ``path`` 是否位于 ``root`` 目录之内（Windows 下不区分大小写）。"""
    try:
        candidate = Path(path).resolve()
        base = Path(root).resolve()
    except OSError:  # pragma: no cover - 极端路径异常
        return False
    candidate_text = os.path.normcase(str(candidate))
    base_text = os.path.normcase(str(base))
    return candidate_text == base_text or candidate_text.startswith(base_text + os.sep)


def delete_document(doc_id: str) -> dict:
    """删除文档、其独占词条、共享词条来源与物理文件。

    返回 ``{ok, pages_removed}``；文档不存在时 ``ok=False``。

    安全防御：绝不删除仓库内置的种子原文（``config.SEED_DOCS_DIR`` 之下）。
    旧快照/手工把 ``stored_path`` 指到仓库文件时，只清理数据库记录与词条。
    """
    row = db.get_document(doc_id)
    if not row:
        return {"ok": False, "pages_removed": 0}
    try:
        pages_removed = wiki_builder.remove_pages_for_document(doc_id)
    except Exception:  # pragma: no cover - 清理失败也要保证文档可删除
        logger.exception("清理文档 %s 的词条失败", doc_id)
        pages_removed = 0
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    raw_stored_path = str(row.get("stored_path") or "").strip()
    try:
        if raw_stored_path and _is_inside(raw_stored_path, config.SEED_DOCS_DIR):
            logger.warning(
                "拒绝删除仓库内置种子文件，仅清理数据库记录与词条：%s", raw_stored_path
            )
        elif raw_stored_path:
            stored_path = Path(raw_stored_path)
            if stored_path.is_file():
                stored_path.unlink()
    except OSError as exc:  # 文件删不掉不影响数据库一致性
        logger.warning("删除物理文件失败：%s（%s）", row.get("stored_path"), exc)
    logger.info("文档已删除：%s（清理词条 %s 个）", row.get("filename"), pages_removed)
    return {"ok": True, "pages_removed": pages_removed}


# ---------------------------------------------------------------------------
# 本地文件夹扫描（管理员指定目录 → 批量收录）
# ---------------------------------------------------------------------------
_SCAN_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
_SCAN_FILE_LIMIT = 200


def resolve_scan_directory(path: str) -> Path:
    """校验并解析待扫描目录（必须存在、是目录、且不在磁盘根）。

    若配置了 ``config.SCAN_ROOTS``，目录必须位于其中之一，避免管理员误扫整盘。
    """
    raw = str(path or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("请填写要扫描的文件夹路径")
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"文件夹不存在或无法访问：{raw}") from exc
    if not candidate.is_dir():
        raise ValueError(f"不是文件夹：{candidate}")
    if candidate.parent == candidate:
        raise ValueError("不允许直接扫描磁盘根目录，请指定具体文件夹")
    if config.SCAN_ROOTS and not any(
        _is_inside(candidate, root) for root in config.SCAN_ROOTS
    ):
        raise ValueError("该文件夹不在允许扫描的目录内（可通过 NPU_SCAN_ROOTS 配置允许目录）")
    return candidate


def list_scan_files(
    path: str | Path,
    *,
    recursive: bool = True,
    max_files: int | None = None,
    max_depth: int | None = None,
) -> list[Path]:
    """列出目录下可收录的文件（按扩展名/大小过滤，目录层级有上限）。"""
    root = resolve_scan_directory(str(path))
    limit = max(1, int(max_files or config.SCAN_MAX_FILES or _SCAN_FILE_LIMIT))
    depth_limit = max(1, int(max_depth or config.SCAN_MAX_DEPTH))
    root_depth = len(root.parts)
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        if current_path == root and not recursive:
            dirnames[:] = []
        if len(current_path.parts) - root_depth >= depth_limit:
            dirnames[:] = []
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SCAN_SKIP_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith(".") or name.startswith("~$"):
                continue
            candidate = current_path / name
            if candidate.suffix.lower() not in config.SUPPORTED_EXTS:
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > config.MAX_UPLOAD_BYTES:
                continue
            files.append(candidate)
            if len(files) >= limit:
                return files
    return files


__all__ = [
    "document_to_dict",
    "get_document",
    "estimate_tokens",
    "normalize_tags",
    "ingest_upload",
    "ingest_text",
    "ingest_url",
    "reindex_document",
    "delete_document",
    "resolve_scan_directory",
    "list_scan_files",
]

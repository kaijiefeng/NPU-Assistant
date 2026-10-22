"""知识库入库：上传保存 → 解析 → 切片 → 写库（documents / chunks）。

对外接口（见 docs/CONTRACT.md §6）::

    ingest_upload(filename, data, title=None, tags=None) -> dict
    reindex_document(doc_id) -> dict
    delete_document(doc_id) -> bool
    seed_documents() -> dict

切片函数（:func:`build_chunks` / :func:`split_text`）不依赖数据库，可被单测直接调用。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .. import config, db
from . import parsing, retrieval

logger = logging.getLogger("npu-assistant")

# 切片目标：300–600 字符，相邻切片重叠约 60 字符
CHUNK_MIN_CHARS = 300
CHUNK_MAX_CHARS = 600
CHUNK_OVERLAP_CHARS = 60

MAX_TAGS = 10
MAX_TAG_CHARS = 30

# 切片内容里被视为"句子边界"的字符（优先在这些位置断开）
_SENTENCE_END_CHARS = "。！？!?；;…\n"
# 找不到句末标点时退而求其次的弱边界
_WEAK_BOUNDARY_CHARS = "，,、 "

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
        "chunk_count": int(row.get("chunk_count") or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_document(doc_id: str) -> dict | None:
    """按 id 取 doc 对象，不存在返回 ``None``。"""
    return document_to_dict(db.get_document(doc_id)) or None


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文按字、英文按词），仅用于展示，不参与检索打分。"""
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


# ---------------------------------------------------------------------------
# 文本切片（纯函数，便于单测）
# ---------------------------------------------------------------------------
def _find_split_index(text: str, start: int, limit: int) -> int:
    """在 ``[start + CHUNK_MIN_CHARS, limit)`` 内寻找最后一个自然断点。

    找不到自然断点时返回 ``limit``（硬切），保证切片长度不超过 ``limit - start``。
    """
    lower = start + CHUNK_MIN_CHARS
    if lower >= limit:
        return limit
    window = text[lower:limit]
    best = -1
    for char in _SENTENCE_END_CHARS:
        index = window.rfind(char)
        if index > best:
            best = index
    if best < 0:
        for char in _WEAK_BOUNDARY_CHARS:
            index = window.rfind(char)
            if index > best:
                best = index
    if best < 0:
        return limit
    return lower + best + 1


def split_text(
    text: str,
    *,
    min_chars: int = CHUNK_MIN_CHARS,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """把长文本切成 300–600 字符、相邻重叠约 60 字符的片段。

    * 优先在句号/分号/换行等自然边界断开；
    * 短于 ``max_chars`` 的文本原样返回一个切片；
    * 不会产生空切片。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    max_chars = max(max_chars, min_chars + 1)
    overlap = max(0, min(overlap, max_chars // 2))

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            end = _find_split_index(text, start, end)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def _iter_section_bodies(blocks: list[parsing.TextBlock]):
    """按章节把正文段落分组；标题块本身不进入正文，只更新 section。"""
    current_section: str | None = None
    parts: list[str] = []
    for block in blocks:
        if block.is_heading:
            if parts:
                yield current_section, "\n".join(parts)
                parts = []
            current_section = block.section or block.text
            continue
        section = block.section if block.section is not None else current_section
        if parts and section != current_section:
            yield current_section, "\n".join(parts)
            parts = []
        current_section = section
        parts.append(block.text)
    if parts:
        yield current_section, "\n".join(parts)


def build_chunks(
    parsed: parsing.ParsedDocument,
    *,
    min_chars: int = CHUNK_MIN_CHARS,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[dict]:
    """把解析结果切成切片：``[{seq, section, content, tokens}, ...]``。

    每个切片保留 ``section``（最近的标题）与从 0 开始的 ``seq``，并合并同章节的
    过短相邻片段，尽量让切片落在 300–600 字符区间。
    """
    blocks = list(getattr(parsed, "blocks", None) or [])
    pieces: list[dict] = []
    for section, body in _iter_section_bodies(blocks):
        for piece in split_text(body, min_chars=min_chars, max_chars=max_chars, overlap=overlap):
            pieces.append({"section": section, "content": piece})

    merged: list[dict] = []
    for piece in pieces:
        if merged:
            previous = merged[-1]
            combined_length = len(previous["content"]) + 1 + len(piece["content"])
            if (
                len(previous["content"]) < min_chars
                and previous["section"] == piece["section"]
                and combined_length <= max_chars
            ):
                previous["content"] = f"{previous['content']}\n{piece['content']}"
                continue
        merged.append(dict(piece))

    chunks: list[dict] = []
    for piece in merged:
        content = piece["content"].strip()
        if not content:
            continue
        chunks.append(
            {
                "seq": len(chunks),
                "section": piece["section"],
                "content": content,
                "tokens": estimate_tokens(content),
            }
        )

    if not chunks:
        # 兜底：文档只有标题（没有正文段落）时，用解析出的纯文本生成切片，
        # 保证任何非空文档都有内容可检索。
        fallback_text = (getattr(parsed, "text", None) or "").strip()
        for piece in split_text(
            fallback_text, min_chars=min_chars, max_chars=max_chars, overlap=overlap
        ):
            chunks.append(
                {
                    "seq": len(chunks),
                    "section": None,
                    "content": piece,
                    "tokens": estimate_tokens(piece),
                }
            )
    return chunks


# ---------------------------------------------------------------------------
# 数据库写入
# ---------------------------------------------------------------------------
def _replace_chunks(doc_id: str, chunks: list[dict]) -> None:
    db.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
    if not chunks:
        return
    timestamp = db.now()
    db.executemany(
        "INSERT INTO chunks(id, document_id, seq, section, content, tokens, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        [
            (db.new_id(), doc_id, chunk["seq"], chunk["section"], chunk["content"],
             chunk["tokens"], timestamp)
            for chunk in chunks
        ],
    )


def _mark_ready(doc_id: str, *, title: str, char_count: int, chunk_count: int) -> None:
    db.execute(
        "UPDATE documents SET title = ?, status = 'ready', error = NULL, char_count = ?, "
        "chunk_count = ?, updated_at = ? WHERE id = ?",
        (title, char_count, chunk_count, db.now(), doc_id),
    )


def _mark_failed(doc_id: str, error: str) -> None:
    db.execute(
        "UPDATE documents SET status = 'failed', error = ?, char_count = 0, chunk_count = 0, "
        "updated_at = ? WHERE id = ?",
        (error, db.now(), doc_id),
    )


def _error_text(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
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


# ---------------------------------------------------------------------------
# 上传入库
# ---------------------------------------------------------------------------
def ingest_upload(
    filename: str,
    data: bytes,
    title: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """保存上传文件、解析切片并写入数据库，返回 doc 对象。

    * 文件保存到 ``backend/data/uploads/<doc_id><ext>``；
    * documents 先写 ``processing``，解析成功置 ``ready``，失败置 ``failed`` 并写 ``error``；
    * 解析失败不抛异常（契约要求返回 doc 且 status=failed）；参数非法抛 ``ValueError``。
    """
    original_name = Path((filename or "").strip()).name or "未命名文件"
    ext = _ensure_supported(original_name, data)

    requested_title = (title or "").strip()
    fallback_title = Path(original_name).stem or original_name
    doc_id = db.new_id()
    stored_path = _stored_path(doc_id, ext)
    try:
        stored_path.write_bytes(data)
    except OSError as exc:
        raise ValueError(f"文件保存失败：{exc}") from exc

    timestamp = db.now()
    tags_json = db.to_json(normalize_tags(tags))
    db.execute(
        "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, status, "
        "error, tags, char_count, chunk_count, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, 'processing', NULL, ?, 0, 0, ?, ?)",
        (doc_id, requested_title or fallback_title, original_name, str(stored_path), ext,
         len(data), tags_json, timestamp, timestamp),
    )
    retrieval.invalidate_cache()

    try:
        parsed = parsing.parse_document(original_name, data)
        chunks = build_chunks(parsed)
        if not chunks:
            raise ValueError("未能从文档中切分出有效内容，请检查文件是否为空或格式异常")
        _replace_chunks(doc_id, chunks)
        final_title = requested_title or parsed.title or fallback_title
        _mark_ready(doc_id, title=final_title, char_count=len(parsed.text), chunk_count=len(chunks))
        logger.info("文档入库成功：%s（%s 个切片 / %s 字符）", original_name, len(chunks), len(parsed.text))
    except Exception as exc:  # 解析失败：保留 failed 记录供用户查看原因
        error = _error_text(exc)
        _mark_failed(doc_id, error)
        logger.warning("文档解析失败：%s（%s）", original_name, error)
    finally:
        retrieval.invalidate_cache()

    return document_to_dict(db.get_document(doc_id))


# ---------------------------------------------------------------------------
# 重建索引 / 删除
# ---------------------------------------------------------------------------
def reindex_document(doc_id: str) -> dict:
    """重新解析已上传的原始文件并重建切片，返回最新 doc 对象。

    文档不存在时抛 ``ValueError``（路由层负责转成 404）。
    """
    row = db.get_document(doc_id)
    if not row:
        raise ValueError("文档不存在或已被删除")
    db.execute(
        "UPDATE documents SET status = 'processing', error = NULL, updated_at = ? WHERE id = ?",
        (db.now(), doc_id),
    )
    stored_path = Path(row.get("stored_path") or "")
    try:
        if not stored_path.is_file():
            raise ValueError("原始文件已丢失，无法重建索引，请重新上传该文档")
        data = stored_path.read_bytes()
        parsed = parsing.parse_document(row.get("filename") or stored_path.name, data)
        chunks = build_chunks(parsed)
        if not chunks:
            raise ValueError("未能从文档中切分出有效内容")
        _replace_chunks(doc_id, chunks)
        _mark_ready(
            doc_id,
            title=row.get("title") or Path(row.get("filename") or "").stem,
            char_count=len(parsed.text),
            chunk_count=len(chunks),
        )
        logger.info("文档重建索引完成：%s（%s 个切片）", row.get("filename"), len(chunks))
    except Exception as exc:
        _replace_chunks(doc_id, [])
        _mark_failed(doc_id, _error_text(exc))
        logger.warning("文档重建索引失败：%s（%s）", row.get("filename"), _error_text(exc))
    finally:
        retrieval.invalidate_cache()
    return document_to_dict(db.get_document(doc_id))


def delete_document(doc_id: str) -> bool:
    """删除文档、其全部切片与物理文件；文档不存在返回 ``False``。"""
    row = db.get_document(doc_id)
    if not row:
        return False
    db.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    try:
        stored_path = Path(row.get("stored_path") or "")
        if stored_path.is_file():
            stored_path.unlink()
    except OSError as exc:  # 文件删不掉不影响数据库一致性
        logger.warning("删除物理文件失败：%s（%s）", row.get("stored_path"), exc)
    retrieval.invalidate_cache()
    logger.info("文档已删除：%s", row.get("filename"))
    return True


# ---------------------------------------------------------------------------
# 示例资料导入
# ---------------------------------------------------------------------------
def _scan_seed_files() -> list[Path]:
    """扫描示例资料文件。

    优先使用 ``config.SEED_DOCS_DIR``（默认 ``backend/data/seed_docs``）。当运行在
    自定义 ``NPU_DATA_DIR``（例如测试用的临时目录）且该目录下没有示例文件时，回退到
    仓库内置的 ``backend/data/seed_docs``，保证冷启动 seed 始终可用。
    """
    candidates = [config.SEED_DOCS_DIR]
    builtin = config.BASE_DIR / "data" / "seed_docs"
    if builtin.resolve() != config.SEED_DOCS_DIR.resolve():
        candidates.append(builtin)

    for root in candidates:
        try:
            if not root.is_dir():
                continue
            files = sorted(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in config.SUPPORTED_EXTS
            )
        except OSError as exc:
            logger.warning("扫描示例资料目录失败：%s（%s）", root, exc)
            continue
        if files:
            return files
    return []


def seed_documents() -> dict:
    """导入 ``backend/data/seed_docs/`` 下的示例文档（按文件名幂等跳过）。

    返回 ``{ingested: [filename...], skipped: [filename...], total_documents: int}``。
    """
    config.ensure_dirs()
    ingested: list[str] = []
    skipped: list[str] = []

    files = _scan_seed_files()

    existing = {
        row["filename"]
        for row in db.query("SELECT filename FROM documents")
        if row.get("filename")
    }

    for file_path in files:
        name = file_path.name
        if name in existing:
            skipped.append(name)
            continue
        try:
            data = file_path.read_bytes()
            doc = ingest_upload(name, data, title=None, tags=["示例资料"])
        except Exception as exc:
            logger.warning("导入示例文档失败：%s（%s）", name, _error_text(exc))
            skipped.append(name)
            continue
        existing.add(name)
        if doc.get("status") == "ready":
            ingested.append(name)
        else:
            logger.warning("示例文档解析失败：%s（%s）", name, doc.get("error"))
            skipped.append(name)

    row = db.query_one("SELECT COUNT(*) AS n FROM documents")
    total_documents = int(row["n"]) if row else 0
    if ingested:
        logger.info("示例资料导入完成：新增 %s 篇，跳过 %s 篇", len(ingested), len(skipped))
    return {
        "ingested": ingested,
        "skipped": skipped,
        "total_documents": total_documents,
    }


__all__ = [
    "CHUNK_MIN_CHARS",
    "CHUNK_MAX_CHARS",
    "CHUNK_OVERLAP_CHARS",
    "document_to_dict",
    "get_document",
    "estimate_tokens",
    "normalize_tags",
    "split_text",
    "build_chunks",
    "ingest_upload",
    "reindex_document",
    "delete_document",
    "seed_documents",
]

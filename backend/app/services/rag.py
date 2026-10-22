"""RAG 对外服务：检索封装 + 带编号的【知识库资料】上下文格式化。

契约（docs/CONTRACT.md §6）::

    search(query, top_k=None) -> list[dict]
    format_context(chunks, max_chars=7000) -> str

``search`` 不抛异常，无命中返回 ``[]``；``format_context`` 输出 ``[1] [2] ...``
编号与文档名/章节，超过 ``max_chars`` 时截断。
"""
from __future__ import annotations

import logging
from typing import Iterable

from . import retrieval

logger = logging.getLogger("npu-assistant")

CONTEXT_HEADER = "【知识库资料】"


def search(query: str, top_k: int | None = None) -> list[dict]:
    """检索知识库并返回 ``[{chunk_id, document_id, document_title, section, content, snippet, score}]``。"""
    return retrieval.search_chunks(query, top_k=top_k)


def build_citations(chunks: Iterable[dict] | None) -> list[dict]:
    """把检索结果转换为对话引用卡片结构（index 从 1 开始，与上下文编号一致）。"""
    citations: list[dict] = []
    for index, chunk in enumerate(chunks or [], start=1):
        citations.append(
            {
                "index": index,
                "chunk_id": chunk.get("chunk_id"),
                "document_id": chunk.get("document_id"),
                "document_title": chunk.get("document_title") or "",
                "section": chunk.get("section"),
                "snippet": chunk.get("snippet") or retrieval.make_snippet(chunk.get("content") or ""),
                "score": chunk.get("score", 0.0),
            }
        )
    return citations


def _format_entry(index: int, chunk: dict) -> str:
    title = str(chunk.get("document_title") or chunk.get("document_filename") or "未知文档").strip()
    section = str(chunk.get("section") or "").strip()
    content = str(chunk.get("content") or "").strip()
    label = f"[{index}] 来源：《{title}》"
    if section:
        label += f" · 章节：{section}"
    return f"{label}\n{content}"


def format_context(chunks: list[dict] | None, max_chars: int = 7000) -> str:
    """把检索结果格式化为带编号的知识库资料文本，超长自动截断。

    编号与 ``chunks`` 顺序一致（``[1]`` 对应第一条检索结果），供 LLM 在回答中标注
    ``[n]``；``max_chars`` 为总字符上限（含表头），为 None/非法时用默认值。
    """
    if not chunks:
        return ""
    try:
        budget = int(max_chars)
    except (TypeError, ValueError):
        budget = 7000
    if budget <= 0:
        return ""

    parts: list[str] = [CONTEXT_HEADER]

    for index, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict):
            continue
        entry = _format_entry(index, chunk)
        current = "\n\n".join(parts)
        remaining = budget - len(current) - 2  # 2 = 与上一段之间的空行
        if remaining <= 0:
            break
        if len(entry) <= remaining:
            parts.append(entry)
            continue
        # 截断最后一条：保留标题行与尽可能多的正文，并补省略号
        if remaining <= 4:
            break
        parts.append(entry[: remaining - 1].rstrip() + "…")
        break

    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)


__all__ = ["search", "format_context", "build_citations", "CONTEXT_HEADER"]

"""知识缺口 AI 备选（RSI 闭环增强）。

对服务台收集到的知识缺口，用白名单源检索（复用 :mod:`remote_search`，
4 官网 + 4 公众号站内搜索），再由 LLM 裁决相关性给出备选链接；管理员
确认后一键收录（复用 :func:`importer.import_url`，收录成功自动把缺口置为
``linked``），完成"缺口 → 找资料 → 收录 → 关联"闭环。

约束：互联网内容只有管理员确认后才入库；检索遵守 remote_sources.json
域名白名单与既有合规约定（不登录、不绕反爬、限流）。
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from .. import db
from . import importer, insights, llm, remote_search
from .wiki_builder import _extract_json

logger = logging.getLogger("npu-assistant")

_MAX_CANDIDATES = 10
_MAX_SUGGESTIONS = 5
_SNIPPET_CHARS = 200


def suggest_for_gap(gap_id: str) -> dict:
    """为缺口检索并裁决备选链接；结果写入 ``knowledge_gaps.suggestions``。

    LLM 不可用（``fallback=True``）时降级为"原始检索结果直出"，不阻塞运营。
    """
    gap = _get_gap(gap_id)
    question = str(gap.get("question") or "").strip()
    if not question:
        raise ValueError("该缺口没有可检索的问题文本")

    search = remote_search.search_all(question, limit=_MAX_CANDIDATES)
    candidates = [c for c in (search.get("results") or []) if _safe_candidate_url(c.get("url"))]
    errors = [str(e) for e in (search.get("errors") or [])][:3]

    response = llm.chat(
        _verdict_messages(question, candidates),
        temperature=0.1,
        max_tokens=900,
    )
    if response.get("fallback"):
        logger.warning("缺口 AI 备选 LLM 不可用，降级为原始候选：%s", response.get("error"))
        suggestions = [
            _plain_suggestion(c, reason="LLM 裁决不可用，以下为检索原始结果") for c in candidates
        ]
    else:
        suggestions = _rank_candidates(question, candidates, str(response.get("content") or ""))

    suggestions = suggestions[:_MAX_SUGGESTIONS]
    db.execute(
        "UPDATE knowledge_gaps SET suggestions = ?, suggested_at = ?, updated_at = ? WHERE id = ?",
        (json.dumps(suggestions, ensure_ascii=False), db.now(), db.now(), gap_id),
    )
    return {
        "gap_id": gap_id,
        "query": question,
        "candidates": len(candidates),
        "suggestions": suggestions,
        "errors": errors,
        "fallback": bool(response.get("fallback")),
    }


def collect_suggestion(gap_id: str, url: str, title: str = "") -> dict:
    """一键收录备选链接并关联缺口（status → linked）。"""
    _get_gap(gap_id)
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("缺少要收录的链接")
    result = importer.import_url(url=clean_url, title=title or None, tags=["缺口补资料"])
    document = result.get("document") or {}
    if document.get("id"):
        insights.update_gap(
            gap_id, "link", document_id=str(document["id"]), note=f"AI 备选收录：{clean_url}"
        )
    return {**result, "gap_id": gap_id}


def _get_gap(gap_id: str) -> dict:
    row = db.query_one("SELECT * FROM knowledge_gaps WHERE id = ?", (gap_id,))
    if not row:
        raise ValueError("知识缺口不存在")
    return row


def _safe_candidate_url(url: str) -> bool:
    """候选 URL 必须是 http(s)，防止 javascript: 等伪协议入库后被渲染为链接。"""
    value = str(url or "").strip()
    if not value:
        return False
    scheme = urlsplit(value).scheme.lower()
    return scheme in {"http", "https"}


def _verdict_messages(question: str, candidates: list[dict]) -> list[dict]:
    payload = [
        {
            "index": index,
            "title": (c.get("title") or "")[:80],
            "source": c.get("source") or "",
            "url": c.get("url") or "",
            "snippet": (c.get("snippet") or c.get("content") or "")[:_SNIPPET_CHARS],
        }
        for index, c in enumerate(candidates[:_MAX_CANDIDATES])
    ]
    return [
        {
            "role": "system",
            "content": (
                "你是校园知识库运营助手。判断检索候选能否作为给定问题的备选参考来源"
                "（政策/办事指南/官方通知优先），无关营销或过期内容判为不相关。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"问题": question, "候选": payload}, ensure_ascii=False)
            + '\n请只输出 JSON 数组：[{"index":0,"relevant":true,"reason":"一句话理由",'
            '"suggested_title":"建议的收录标题"}]；没有相关的候选就输出 []。',
        },
    ]


def _rank_candidates(question: str, candidates: list[dict], content: str) -> list[dict]:
    verdicts = _extract_json(content, list) or []
    by_index: dict[int, dict] = {}
    for item in verdicts:
        if isinstance(item, dict):
            try:
                by_index[int(item.get("index"))] = item
            except (TypeError, ValueError):
                continue
    suggestions: list[dict] = []
    for index, candidate in enumerate(candidates[:_MAX_CANDIDATES]):
        verdict = by_index.get(index)
        if not verdict or not verdict.get("relevant"):
            continue
        suggestions.append(
            _plain_suggestion(
                candidate,
                str(verdict.get("reason") or "")[:160],
                str(verdict.get("suggested_title") or ""),
            )
        )
    if not suggestions:
        logger.info("缺口 AI 备选：LLM 判定全部候选不相关（问题：%s）", question[:50])
    return suggestions


def _plain_suggestion(candidate: dict, reason: str = "", title: str = "") -> dict:
    return {
        "url": (candidate.get("url") or "").strip(),
        "title": (title or candidate.get("title") or "").strip()[:80],
        "snippet": (candidate.get("snippet") or candidate.get("content") or "")[:_SNIPPET_CHARS],
        "source": candidate.get("source") or "",
        "reason": reason or "检索命中",
    }

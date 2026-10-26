"""Wiki 导航：查询 → 选词条（不检索切片）→ 上下文 / 引用。

实现依据 ``docs/WIKI-DESIGN.md`` §3.2 / §3.3。定位顺序：

1. **exact**：词条标题 / 别名（长度 ≥2）出现在问题中；
2. **keyword**：``text_utils.tokenize`` 与标题(×3)/别名(×3)/标签(×2)/摘要(×1)
   的 token 重合度打分，覆盖率归一化；
3. 最佳归一化得分 ≥ 0.34 → 直接取前 ``wiki_max_pages`` 个；
4. 否则 **LLM 选页**：把紧凑目录（``[id] 标题 | 别名 | 标签 | 摘要``）交给模型，
   要求输出 ``{"page_ids":[...]}``；目录超过 12000 字时先按标签分组做两级选择；
5. LLM 不可用 / 超时 / 输出非法 → 回退 keyword；无候选 → ``method=none``。

本模块不使用 embedding / 向量库 / rerank；只有 ``ready`` 词条参与问答。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter
from typing import Any

from .. import config, db
from . import llm, text_utils

logger = logging.getLogger("npu-assistant")

# 关键词归一化得分达到该阈值即认为候选足够强，无需调用 LLM 选页
KEYWORD_STRONG_THRESHOLD = 0.34
SINGLE_PAGE_BODY_MAX_CHARS = 2500
CITATION_RE = re.compile(r"\[(\d{1,3})\]")

NAV_SYSTEM_PROMPT = """你是校园 Wiki 的导航助手。用户提出一个问题，你需要从【Wiki 目录】中挑选最相关的 1–N 个词条 id。

规则：
1. 只能从目录中挑选，禁止编造目录里不存在的 id；
2. 优先选择能直接回答问题的词条；如果目录中没有相关词条，返回空数组；
3. 只输出一个 JSON 对象：{{"page_ids": ["id1", "id2"]}}，不要解释、不要 Markdown 代码块；
4. 最多挑选 {max_pages} 个。"""

NAV_USER_TEMPLATE = """【学生问题】
{question}

【Wiki 目录】
{toc}

请输出 {{"page_ids": [...]}}（最多 {max_pages} 个，只能选目录中出现的 id；无相关词条时输出空数组）。"""

TAG_SYSTEM_PROMPT = """你是校园 Wiki 的导航助手。用户提出一个问题，你需要先从标签分组中挑选最相关的 1–4 个分组。

只输出 JSON 对象：{"tags": ["标签1", "标签2"]}，不要解释；没有相关分组时输出 {"tags": []}。"""

TAG_USER_TEMPLATE = """【学生问题】
{question}

【标签分组】（格式：标签（词条数）：词条标题列表）
{groups}

请输出 {{"tags": [...]}}（最多 4 个，只能选上面出现过的标签）。"""


# ---------------------------------------------------------------------------
# 设置 / 数据读取
# ---------------------------------------------------------------------------
def _setting_int(key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(db.get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _max_pages() -> int:
    return _setting_int("wiki_max_pages", config.WIKI_MAX_PAGES, 1, 5)


def _answer_max_chars() -> int:
    return _setting_int(
        "wiki_answer_max_chars", config.WIKI_CONTEXT_MAX_CHARS, 2000, 20000
    )


def _citations_enabled() -> bool:
    try:
        return db.get_bool_setting("citations_enabled", True)
    except Exception:  # pragma: no cover
        return True


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


def _load_ready_pages() -> list[dict]:
    rows = db.query(
        "SELECT * FROM wiki_pages WHERE status = 'ready' ORDER BY updated_at DESC, rowid DESC"
    )
    pages = [_page_dict(row) for row in rows]
    return [page for page in pages if page]


def _llm_available() -> bool:
    """导航层是否值得调用 LLM：mock 模型直接跳过。"""
    try:
        model = llm.default_model()
    except Exception:  # pragma: no cover
        return False
    if not model:
        return False
    return (model.get("provider_type") or "").strip().lower() != "mock"


def _one_line(text: Any, limit: int | None = None) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit is not None and len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


# ---------------------------------------------------------------------------
# 打分：exact / keyword
# ---------------------------------------------------------------------------
def _exact_candidates(query: str, pages: list[dict]) -> list[tuple[dict, float, str]]:
    """标题/别名（长度 ≥2）出现在问题中即视为精确命中。"""
    normalized_query = text_utils.normalize_title(query)
    if not normalized_query:
        return []
    results: list[tuple[dict, float, str]] = []
    for page in pages:
        labels = [page["title"], *(page.get("aliases") or [])]
        best: tuple[float, str] | None = None
        for label in labels:
            normalized_label = text_utils.normalize_title(label)
            if len(normalized_label) < 2:
                continue
            if normalized_label in normalized_query:
                score = min(1.0, 0.55 + 0.45 * len(normalized_label) / max(1, len(normalized_query)))
                if best is None or score > best[0]:
                    best = (round(score, 3), f"标题精确匹配：{label}")
        if best:
            results.append((page, best[0], best[1]))
    results.sort(key=lambda item: (-item[1], -len(item[2]), item[0]["title"]))
    return results


def _keyword_candidates(query: str, pages: list[dict]) -> list[dict]:
    """IDF 加权 token 重合度打分（标题/别名 ×3、标签 ×2、摘要 ×1）。

    真实 LLM 验收后增强（避免无据回答）：

    * 查询 token 过滤单字噪声（``能`` / ``用`` 等），避免覆盖率虚高；
    * 用页面语料的文档频率做 IDF 归一化：``困难`` / ``学校`` 这类高频词降权，
      ``补助`` / ``热得快`` 这类高信息词权重更高；
    * 为每个候选计算 ``strong``：必须满足「标题/别名有效命中、标签命中、
      查询内容词覆盖率 ≥0.5、标题/别名短语出现在问题中」之一，否则不进入
      keyword 通道（转 LLM 选页），从源头避免「食堂几点开门」这类问题误命中。
    """
    raw_tokens = [token for token in dict.fromkeys(text_utils.tokenize(query)) if token]
    query_tokens = [token for token in raw_tokens if len(token) > 1] or raw_tokens
    if not query_tokens:
        return []

    normalized_query = text_utils.normalize_title(query)
    fields: list[tuple[dict, set[str], set[str], set[str], set[str]]] = []
    document_frequency: Counter[str] = Counter()
    for page in pages:
        title_tokens = set(text_utils.tokenize(page.get("title") or ""))
        alias_tokens: set[str] = set()
        for alias in page.get("aliases") or []:
            alias_tokens.update(text_utils.tokenize(alias))
        tag_tokens: set[str] = set()
        for tag in page.get("tags") or []:
            tag_tokens.update(text_utils.tokenize(tag))
        summary_tokens = set(text_utils.tokenize(page.get("summary") or ""))
        fields.append((page, title_tokens, alias_tokens, tag_tokens, summary_tokens))
        document_frequency.update(title_tokens | alias_tokens | tag_tokens | summary_tokens)

    corpus_size = max(1, len(pages))
    idf = {
        token: math.log(
            1.0 + (corpus_size + 1.0) / (document_frequency.get(token, 0) + 0.5)
        )
        for token in query_tokens
    }
    max_idf = max(idf.values()) if idf else 1.0

    results: list[dict] = []
    for page, title_tokens, alias_tokens, tag_tokens, summary_tokens in fields:
        title_hits = [token for token in query_tokens if token in title_tokens]
        alias_hits = [token for token in query_tokens if token in alias_tokens]
        tag_hits = [token for token in query_tokens if token in tag_tokens]
        summary_hits = [token for token in query_tokens if token in summary_tokens]

        matched: list[str] = []
        weighted = 0.0
        for token in query_tokens:
            if token in title_hits or token in alias_hits:
                weight = 3
            elif token in tag_hits:
                weight = 2
            elif token in summary_hits:
                weight = 1
            else:
                continue
            matched.append(token)
            weighted += weight * (idf.get(token, 1.0) / max_idf)
        if not matched:
            continue

        max_weighted = 3.0 * sum(idf.get(token, 1.0) / max_idf for token in query_tokens)
        weighted_ratio = min(1.0, weighted / max_weighted) if max_weighted else 0.0
        coverage = len(matched) / len(query_tokens)
        score = round(min(1.0, weighted_ratio * 0.6 + coverage * 0.4), 3)
        if score <= 0:
            continue

        title_alias_hits = set(title_hits) | set(alias_hits)
        phrase_hit = False
        for label in [page.get("title") or "", *(page.get("aliases") or [])]:
            normalized_label = text_utils.normalize_title(label)
            if len(normalized_label) >= 3 and normalized_label in normalized_query:
                phrase_hit = True
                break

        rare_title_hit = False
        if len(title_alias_hits) == 1:
            token = next(iter(title_alias_hits))
            rare_title_hit = len(token) >= 3 or document_frequency.get(token, 0) <= max(
                2, corpus_size * 0.15
            )
        strong = bool(
            tag_hits
            or coverage >= 0.5
            or phrase_hit
            or len(title_alias_hits) >= 2
            or rare_title_hit
        )
        results.append(
            {
                "page": page,
                "score": score,
                "reason": "关键词命中：" + "、".join(matched[:4]),
                "strong": strong,
                "coverage": round(coverage, 3),
                "primary_tag": _primary_tag(page),
            }
        )
    results.sort(
        key=lambda item: (-item["score"], -item["coverage"], item["page"]["title"])
    )
    return results


def _select_keyword_candidates(
    candidates: list[dict],
    max_pages: int,
    top_score: float,
) -> list[dict]:
    """从 keyword 候选中挑选最终读入的 1–N 个词条。

    * 相对分数地板：低于 ``max(0.15, top_score * 0.4)`` 的弱候选截断；
    * 弱命中（``top_score < 0.55``）时要求主标签与最佳候选一致，避免
      「就业困难帮扶」这类跨主题词条混入「困难补助」问题。
    """
    if not candidates:
        return []
    top_tag = candidates[0]["primary_tag"]
    floor = max(0.15, float(top_score) * 0.4)
    enforce_topic = float(top_score) < 0.55 and bool(top_tag) and top_tag != "其他"
    selected: list[dict] = []
    for index, candidate in enumerate(candidates):
        if candidate["score"] < floor:
            break
        if index > 0 and enforce_topic and candidate["primary_tag"] != top_tag:
            continue
        selected.append(candidate)
        if len(selected) >= max_pages:
            break
    return selected


def _candidate_payload(page: dict, score: float, reason: str) -> dict:
    return {
        "page_id": page["id"],
        "title": page["title"],
        "summary": _one_line(page.get("summary"), 160),
        "score": round(float(score), 4),
        "reason": reason,
    }


def _merge_candidates(
    exact: list[tuple[dict, float, str]],
    keyword: list[dict],
    limit: int,
) -> list[dict]:
    merged: dict[str, dict] = {}
    for page, score, reason in exact:
        merged[page["id"]] = _candidate_payload(page, score, reason)
    for candidate in keyword:
        page = candidate["page"]
        if page["id"] in merged:
            continue
        merged[page["id"]] = _candidate_payload(
            page, candidate["score"], candidate["reason"]
        )
    ordered = sorted(
        merged.values(),
        key=lambda item: (-item["score"], item["title"]),
    )
    return ordered[:limit]


# ---------------------------------------------------------------------------
# LLM 选页
# ---------------------------------------------------------------------------
def _extract_json(text: str, expected: type) -> Any:
    value = (text or "").strip()
    if not value:
        return None
    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, re.S | re.I)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(value)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, expected):
            return data
        if expected is dict and isinstance(data, list):
            first = next((item for item in data if isinstance(item, dict)), None)
            if first is not None:
                return first
        open_char, close_char = ("[", "]") if expected is list else ("{", "}")
        start = candidate.find(open_char)
        end = candidate.rfind(close_char)
        if start != -1 and end > start:
            try:
                data = json.loads(candidate[start : end + 1])
            except (TypeError, ValueError):
                continue
            if isinstance(data, expected):
                return data
    return None


def _llm_json(messages: list[dict], expected: type, *, max_tokens: int) -> Any:
    """调用模型并解析 JSON；任何失败返回 ``None``（绝不向外抛异常）。"""
    try:
        result = llm.chat(messages, temperature=0.1, max_tokens=max_tokens)
    except Exception:
        logger.exception("Wiki 导航调用模型失败")
        return None
    if not isinstance(result, dict) or result.get("fallback"):
        if isinstance(result, dict) and result.get("error"):
            logger.warning("Wiki 导航模型不可用：%s", result.get("error"))
        return None
    return _extract_json(str(result.get("content") or ""), expected)


def _toc_line(page: dict) -> str:
    aliases = "、".join(str(alias) for alias in (page.get("aliases") or [])[:3]) or "无"
    tags = "、".join(str(tag) for tag in (page.get("tags") or [])[:3]) or "无"
    summary = _one_line(page.get("summary"), 60)
    return f"[{page['id']}] {page['title']} | 别名:{aliases} | 标签:{tags} | 摘要:{summary}"


def _build_toc(pages: list[dict]) -> str:
    return "\n".join(_toc_line(page) for page in pages)


def _primary_tag(page: dict) -> str:
    tags = page.get("tags") or []
    return str(tags[0]) if tags else "其他"


def _choose_tags(query: str, pages: list[dict], max_groups: int = 4) -> list[str]:
    """一级选择：按标签分组，让模型先选标签组。"""
    groups: dict[str, list[dict]] = {}
    for page in pages:
        groups.setdefault(_primary_tag(page), []).append(page)
    lines: list[str] = []
    for tag in sorted(groups):
        titles = "、".join(page["title"] for page in groups[tag][:12])
        lines.append(f"- {tag}（{len(groups[tag])} 个）：{titles}")
    group_text = "\n".join(lines)
    if len(group_text) > config.WIKI_TOC_MAX_CHARS:
        group_text = group_text[: config.WIKI_TOC_MAX_CHARS]
    data = _llm_json(
        [
            {"role": "system", "content": TAG_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": TAG_USER_TEMPLATE.format(question=query, groups=group_text),
            },
        ],
        dict,
        max_tokens=200,
    )
    if not isinstance(data, dict):
        return []
    candidates = data.get("tags")
    if not isinstance(candidates, list):
        return []
    selected: list[str] = []
    for item in candidates:
        tag = str(item or "").strip()
        if tag and tag in groups and tag not in selected:
            selected.append(tag)
        if len(selected) >= max_groups:
            break
    return selected


def _ask_page_ids(query: str, pages: list[dict], max_pages: int) -> list[dict]:
    """二级选择：读紧凑目录，让模型输出 ``{"page_ids":[...]}``。"""
    toc = _build_toc(pages)
    if not toc.strip():
        return []
    data = _llm_json(
        [
            {"role": "system", "content": NAV_SYSTEM_PROMPT.format(max_pages=max_pages)},
            {
                "role": "user",
                "content": NAV_USER_TEMPLATE.format(
                    question=query, toc=toc, max_pages=max_pages
                ),
            },
        ],
        dict,
        max_tokens=240,
    )
    if isinstance(data, list):
        raw_ids = data
    elif isinstance(data, dict):
        raw_ids = data.get("page_ids")
    else:
        return []
    if not isinstance(raw_ids, list):
        return []
    allowed = {page["id"]: page for page in pages}
    selected: list[dict] = []
    for item in raw_ids:
        page_id = str(item or "").strip()
        if page_id in allowed and all(page["id"] != page_id for page in selected):
            selected.append(allowed[page_id])
        if len(selected) >= max_pages:
            break
    return selected


def _llm_select(query: str, pages: list[dict], max_pages: int) -> list[dict]:
    """LLM 选页（大目录两级选择）；失败返回空列表。"""
    if not pages:
        return []
    working = pages
    toc = _build_toc(working)
    if len(toc) > config.WIKI_TOC_MAX_CHARS:
        tags = _choose_tags(query, working)
        if not tags:
            return []
        working = [page for page in working if _primary_tag(page) in tags]
        if not working:
            return []
        if len(_build_toc(working)) > config.WIKI_TOC_MAX_CHARS:
            # 过滤后仍超长：放弃 LLM，交给 keyword 回退，避免超上下文
            logger.info("Wiki 目录过滤后仍超过 %s 字，跳过 LLM 选页", config.WIKI_TOC_MAX_CHARS)
            return []
    return _ask_page_ids(query, working, max_pages)


# ---------------------------------------------------------------------------
# 对外接口（见 docs/WIKI-DESIGN.md §5）
# ---------------------------------------------------------------------------
def search_pages(query: str, *, limit: int = 5) -> dict:
    """导航核心：返回 ``{query, method, candidates, selected, latency_ms}``。"""
    started = time.perf_counter()
    query = (query or "").strip()
    try:
        limit_value = max(1, min(int(limit or 5), 50))
    except (TypeError, ValueError):
        limit_value = 5
    payload: dict[str, Any] = {
        "query": query,
        "method": "none",
        "candidates": [],
        "selected": [],
        "latency_ms": 0,
    }
    if not query:
        payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    try:
        pages = _load_ready_pages()
    except Exception:
        logger.exception("读取 Wiki 词条失败，本次按无命中处理")
        payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload
    if not pages:
        payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    max_pages = _max_pages()
    exact = _exact_candidates(query, pages)
    keyword = _keyword_candidates(query, pages)
    strong_keyword = [candidate for candidate in keyword if candidate["strong"]]
    # 只有「强命中」候选才对外展示；弱命中不进 keyword 通道
    display = _merge_candidates(exact, strong_keyword, limit_value)

    selected_pages: list[dict] = []
    method = "none"
    try:
        if exact and strong_keyword:
            exact_score = max(score for _page, score, _reason in exact)
            keyword_score = float(strong_keyword[0]["score"])
            # 精确别名可能只命中“综合测评”这类泛词；当关键词候选明显更高时，
            # 优先内容更具体的词条，避免“研究生综合测评”压过“本科生综合测评模块”。
            if keyword_score > exact_score + 0.08:
                method = "keyword"
                selected_pages = [
                    candidate["page"]
                    for candidate in _select_keyword_candidates(
                        keyword, max_pages, keyword_score
                    )
                ]
            else:
                method = "exact"
                selected_pages = [page for page, _score, _reason in exact[:max_pages]]
        elif exact:
            method = "exact"
            selected_pages = [page for page, _score, _reason in exact[:max_pages]]
        elif strong_keyword and strong_keyword[0]["score"] >= KEYWORD_STRONG_THRESHOLD:
            method = "keyword"
            selected_pages = [
                candidate["page"]
                for candidate in _select_keyword_candidates(
                    keyword, max_pages, strong_keyword[0]["score"]
                )
            ]
        else:
            llm_selected = _llm_select(query, pages, max_pages) if _llm_available() else []
            if llm_selected:
                method = "llm"
                selected_pages = llm_selected[:max_pages]
            elif strong_keyword:
                # LLM 不可用/未返回时才回退到「强命中」候选；弱相关候选绝不
                # 作为命中返回，避免无据回答仍带 citations（真实 LLM 验收问题）。
                method = "keyword"
                selected_pages = [
                    candidate["page"]
                    for candidate in _select_keyword_candidates(
                        strong_keyword, max_pages, strong_keyword[0]["score"]
                    )
                ]
            else:
                method = "none"
                selected_pages = []
    except Exception:
        logger.exception("Wiki 导航失败，回退为强命中关键词候选")
        if strong_keyword:
            method = "keyword"
            selected_pages = [
                candidate["page"]
                for candidate in _select_keyword_candidates(
                    strong_keyword, max_pages, strong_keyword[0]["score"]
                )
            ]
        else:
            method = "none"
            selected_pages = []

    candidate_map = {item["page_id"]: item for item in display}
    keyword_map = {candidate["page"]["id"]: candidate for candidate in keyword}
    exact_scores = {page["id"]: score for page, score, _reason in exact}
    selected: list[dict] = []
    for page in selected_pages:
        existing = candidate_map.get(page["id"])
        if existing:
            selected.append(dict(existing))
            continue
        # 候选列表被 limit 截断时，按实际走到的通道补全分数与原因
        if method == "exact":
            score = exact_scores.get(page["id"], 0.0)
            reason = "标题/别名精确匹配"
        elif method == "keyword":
            candidate = keyword_map.get(page["id"])
            score = candidate["score"] if candidate else 0.0
            reason = candidate["reason"] if candidate else "关键词命中"
        else:
            score = 0.0
            reason = "LLM 选页"
        selected.append(_candidate_payload(page, score, reason))

    # 让 selected 中因相对地板/LLM 选页而进入、但不在 candidates 截断范围内的词条
    # 也能在候选列表里被观察到（前端展示候选分数/理由时不会出现“选中却无候选”）。
    displayed_ids = {item["page_id"] for item in display}
    for item in selected:
        if item["page_id"] not in displayed_ids:
            display.append(dict(item))
            displayed_ids.add(item["page_id"])

    payload.update(method=method, candidates=display, selected=selected)
    payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return payload


def load_pages(page_ids: list[str]) -> list[dict]:
    """按 id 顺序加载 ``ready`` 词条（附来源锚点），用于回答问题。"""
    pages: list[dict] = []
    for page_id in page_ids:
        page = _page_dict(db.get_wiki_page(page_id))
        if not page or page["status"] != "ready":
            continue
        page["sources"] = db.get_page_sources(page["id"])
        pages.append(page)
    return pages


def select_pages_with_method(
    question: str,
    *,
    max_pages: int | None = None,
) -> tuple[list[dict], dict]:
    """选页并返回 ``(pages, navigation)``；navigation 形如
    ``{"method": "keyword", "pages": [{"id": ..., "title": ...}]}``。
    """
    question = (question or "").strip()
    limit_pages = max(1, min(int(max_pages or _max_pages()), 5))
    if not question:
        return [], {"method": "none", "pages": []}
    result = search_pages(question, limit=max(5, limit_pages * 3))
    page_ids = [item["page_id"] for item in result.get("selected") or []][:limit_pages]
    pages = load_pages(page_ids)
    navigation = {
        "method": result.get("method") or "none",
        "pages": [{"id": page["id"], "title": page["title"]} for page in pages],
    }
    return pages, navigation


def select_pages(question: str, *, max_pages: int | None = None) -> list[dict]:
    """选页的简化接口：直接返回词条行（含 sources）。"""
    pages, _navigation = select_pages_with_method(question, max_pages=max_pages)
    return pages


def build_context(pages: list[dict], max_chars: int = 9000) -> str:
    """生成带 ``[n]`` 编号的 Wiki 词条上下文（单页正文最多 2500 字）。"""
    if not pages:
        return ""
    try:
        remaining = max(200, int(max_chars))
    except (TypeError, ValueError):
        remaining = config.WIKI_CONTEXT_MAX_CHARS

    blocks: list[str] = []
    for index, page in enumerate(pages, start=1):
        header = f"【{index}】《{page.get('title') or '未命名词条'}》"
        parts = [header]
        summary = _one_line(page.get("summary"))
        if summary:
            parts.append(f"摘要：{summary}")
        body = str(page.get("body_md") or "").strip()
        if body:
            if len(body) > SINGLE_PAGE_BODY_MAX_CHARS:
                body = body[:SINGLE_PAGE_BODY_MAX_CHARS].rstrip() + "…"
            parts.append(f"正文：\n{body}")
        source_lines: list[str] = []
        for source in page.get("sources") or []:
            label = f"- {source.get('source_file') or source.get('document_title') or '未命名文件'}"
            if source.get("section"):
                label += f" · {source['section']}"
            if label not in source_lines:
                source_lines.append(label)
        if source_lines:
            parts.append("来源：\n" + "\n".join(source_lines))
        block = "\n".join(parts)

        if len(block) > remaining:
            if remaining <= len(header) + 10:
                break
            block = block[:remaining].rstrip() + "…"
        blocks.append(block)
        remaining -= len(block) + 2
        if remaining <= 0:
            break
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 引用
# ---------------------------------------------------------------------------
def _plain_text(markdown_text: str) -> str:
    """把词条 Markdown 粗略转为纯文本（用于 snippet 展示）。"""
    text = re.sub(r"```.*?```", " ", str(markdown_text or ""), flags=re.S)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)
    text = re.sub(r"(?m)^\s{0,3}[-*+]\s+", "", text)
    text = re.sub(r"(?m)^\s{0,3}\d+\.\s+", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _citation_sources(page: dict) -> list[dict]:
    sources: list[dict] = []
    seen: set[tuple] = set()
    for source in page.get("sources") or []:
        key = (
            str(source.get("document_id") or ""),
            str(source.get("section") or ""),
            str(source.get("quote") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "document_id": str(source.get("document_id") or ""),
                "document_title": str(source.get("document_title") or ""),
                "source_file": str(source.get("source_file") or ""),
                "section": str(source.get("section") or ""),
                "quote": str(source.get("quote") or ""),
            }
        )
    return sources


def _citation(index: int, page: dict) -> dict:
    return {
        "index": index,
        "page_id": page["id"],
        "page_title": page.get("title") or "",
        "summary": _one_line(page.get("summary"), 160),
        "snippet": _plain_text(page.get("body_md") or page.get("summary"))[:200],
        "sources": _citation_sources(page),
    }


def attach_citations(
    reply: str,
    pages: list[dict],
    enabled: bool = True,
) -> tuple[str, list[dict]]:
    """把回答中的 ``[n]`` 与词条对齐，生成契约 citations。

    * 模型标注了编号 → 只返回被引用的词条，index 与 ``[n]`` 严格一致；
    * 模型未标注编号 → 追加「参考词条」清单并返回全部词条引用；
    * ``enabled=False`` → 去掉编号，返回空 citations；
    * 越界编号会被移除，避免出现无法核对的引用。
    """
    reply = str(reply or "")
    if not pages:
        return reply, []
    if not enabled:
        return re.sub(r"\s*\[\d{1,3}\]", "", reply), []

    indexes = sorted({int(match) for match in CITATION_RE.findall(reply)})
    valid = [index for index in indexes if 1 <= index <= len(pages)]
    if indexes:
        reply = CITATION_RE.sub(
            lambda match: match.group(0) if 1 <= int(match.group(1)) <= len(pages) else "",
            reply,
        )
    if valid:
        return reply, [_citation(index, pages[index - 1]) for index in valid]

    lines = ["", "参考词条："]
    for index, page in enumerate(pages, start=1):
        lines.append(f"[{index}] 《{page.get('title') or '未命名词条'}》")
    citations = [_citation(index, page) for index, page in enumerate(pages, start=1)]
    return reply.rstrip() + "\n" + "\n".join(lines), citations


__all__ = [
    "search_pages",
    "select_pages",
    "select_pages_with_method",
    "load_pages",
    "build_context",
    "attach_citations",
]

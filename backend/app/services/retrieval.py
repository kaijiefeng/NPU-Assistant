"""知识库检索服务：加载切片 → BM25 排序 → 覆盖率/相对分数过滤。

* 从 ``chunks JOIN documents`` 读取 ``status='ready'`` 的切片；
* 排序委托给 :func:`app.services.retrieval_core.rank_chunks`（契约固定接口，本模块只 import）；
* 支持 settings 中的 ``retrieval_top_k`` / ``retrieval_min_score``；
* 精度保护：按查询内容词覆盖率（去停用词）与相对最高分过滤弱相关切片，
  单篇文档最多保留 2 个切片；过滤后为空时回退原 top1；
* 进程内缓存：通过 documents/chunks 的版本签名自动失效，也可显式调用
  :func:`invalidate_cache`（入库/删除/重建索引后调用）；
* 任何异常都降级返回 ``[]``，绝不把异常抛给问答链路。
"""
from __future__ import annotations

import logging
import re
import threading

from .. import config, db
from .retrieval_core import rank_chunks

try:  # tokenize 是 retrieval_core 的公开接口之一，但允许其被替换/缺失
    from .retrieval_core import tokenize as _core_tokenize
except Exception:  # pragma: no cover - 仅在检索核心接口变动时触发
    _core_tokenize = None

logger = logging.getLogger("npu-assistant")

SNIPPET_CHARS = 200

# 覆盖率感知过滤参数（见 Lead 的检索精度要求）
COVERAGE_THRESHOLD = 0.5          # 查询去重内容词命中率下限
STRONG_SCORE_RATIO = 0.6          # 分数达到最高分的 60% 可豁免覆盖率
RELATIVE_SCORE_FLOOR_RATIO = 0.25  # 相对阈值兜底：至少为最高分的 25%
MAX_CHUNKS_PER_DOCUMENT = 2       # 单篇文档最多保留的切片数

# retrieval_core.tokenize 不可用时的兜底分词（与核心口径保持一致：中文二字词 + 英文词）
_FALLBACK_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "也", "就", "都", "而", "被", "把",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "自己", "这个", "那个", "这些", "那些",
    "吗", "呢", "啊", "呀", "吧", "哦", "嗯", "么", "什么", "怎么", "怎样", "如何", "哪些",
    "请问", "一下", "可以", "能否", "是否", "需要", "应该", "一个", "现在", "然后", "以及",
    "对于", "关于", "如果", "因为", "所以", "但是", "而且", "还有", "已经", "进行", "相关",
}
_FALLBACK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"signature": None, "chunks": []}


# ---------------------------------------------------------------------------
# 切片加载与缓存
# ---------------------------------------------------------------------------
def invalidate_cache() -> None:
    """显式使检索缓存失效（文档/切片发生变化后调用）。"""
    with _CACHE_LOCK:
        _CACHE["signature"] = None
        _CACHE["chunks"] = []


def _signature() -> tuple:
    """用文档/切片计数与最新更新时间作为缓存版本号。"""
    documents = db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS latest FROM documents"
    ) or {"n": 0, "latest": ""}
    chunks = db.query_one("SELECT COUNT(*) AS n FROM chunks") or {"n": 0}
    return (int(documents["n"]), str(documents["latest"]), int(chunks["n"]))


def load_chunks(force: bool = False) -> list[dict]:
    """加载所有可检索引切片（documents.status = 'ready'）。"""
    try:
        signature = _signature()
    except Exception:
        logger.exception("读取知识库版本签名失败")
        return []
    with _CACHE_LOCK:
        cached = _CACHE.get("chunks") or []
        if not force and _CACHE.get("signature") == signature and cached:
            return cached  # type: ignore[return-value]
        rows = db.query(
            "SELECT c.id AS chunk_id, c.document_id AS document_id, c.seq AS seq, "
            "c.section AS section, c.content AS content, c.tokens AS tokens, "
            "d.title AS document_title, d.filename AS document_filename, "
            "d.file_ext AS file_ext, d.created_at AS document_created_at "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.status = 'ready' "
            "ORDER BY d.created_at, c.seq"
        )
        _CACHE["signature"] = signature
        _CACHE["chunks"] = rows
        return rows


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
def get_retrieval_settings() -> tuple[int, float]:
    """返回 ``(top_k, min_score)``，非法配置回退到默认值。"""
    try:
        top_k = int(str(db.get_setting("retrieval_top_k", str(config.DEFAULT_TOP_K))).strip())
    except (TypeError, ValueError):
        top_k = config.DEFAULT_TOP_K
    try:
        min_score = float(str(db.get_setting("retrieval_min_score", "0.05")).strip())
    except (TypeError, ValueError):
        min_score = 0.05
    return max(1, min(top_k, 50)), max(0.0, min_score)


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
def make_snippet(content: str, max_chars: int = SNIPPET_CHARS) -> str:
    """截取切片开头作为引用片段（保证是 content 的前缀，可直接核对原文）。"""
    text = (content or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _to_result(chunk: dict, score: float) -> dict:
    content = str(chunk.get("content") or "")
    return {
        "chunk_id": chunk.get("chunk_id"),
        "document_id": chunk.get("document_id"),
        "document_title": chunk.get("document_title") or chunk.get("document_filename") or "",
        "section": chunk.get("section"),
        "content": content,
        "snippet": make_snippet(content),
        "score": round(float(score), 4),
    }


# ---------------------------------------------------------------------------
# 覆盖率感知过滤（提升引用精度，避免"只共享一个泛词"的切片混入）
# ---------------------------------------------------------------------------
def tokenize_text(text: str) -> list[str]:
    """按检索核心口径分词；核心 tokenize 不可用时退回内置实现。"""
    if _core_tokenize is not None:
        try:
            return [token for token in _core_tokenize(text or "") if token]
        except Exception:  # pragma: no cover - 核心分词异常时兜底
            logger.debug("retrieval_core.tokenize 调用失败，使用内置分词", exc_info=True)
    tokens: list[str] = []
    for raw in _FALLBACK_TOKEN_RE.findall((text or "").lower()):
        if raw in _FALLBACK_STOPWORDS:
            continue
        tokens.append(raw)
        if _CJK_RE.search(raw) and len(raw) > 2:
            tokens.extend(raw[i : i + 2] for i in range(len(raw) - 1))
    return tokens


def _enrich_cjk_tokens(tokens: set[str]) -> set[str]:
    """对中文词补充字符二元组，降低 jieba 分词口径差异导致的漏匹配。

    例：查询「实验室怎么预约」→ {实验室, 实验, 验室, 预约}；只共享「预约」的无关
    切片覆盖率降到 0.25，从而被覆盖率阈值过滤掉。
    """
    enriched = set(tokens)
    for token in tokens:
        if _CJK_RE.search(token) and len(token) > 2:
            enriched.update(token[i : i + 2] for i in range(len(token) - 1))
    return enriched


def _coverage_tokens(text: str) -> set[str]:
    try:
        return _enrich_cjk_tokens(set(tokenize_text(text)))
    except Exception:  # pragma: no cover
        return set()


def _query_token_set(query: str) -> set[str]:
    return _coverage_tokens(query)


def coverage_ratio(query_tokens: set[str], content: str) -> float:
    """查询去重 token 在切片正文中的命中比例；查询无内容词时视作完全覆盖。"""
    if not query_tokens:
        return 1.0
    chunk_tokens = _coverage_tokens(content)
    if not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _select_results(
    ranked: list[tuple[dict, float]],
    query: str,
    limit: int,
    min_score: float,
) -> list[dict]:
    """对 BM25 排序结果做覆盖率/相对分数过滤 + 单文档配额，空结果回退 top1。

    过滤逻辑（同时满足才保留）：
    1. ``score >= max(settings.min_score, 0.25 * 最高分)`` 相对阈值兜底；
    2. ``coverage >= 0.5``（查询去重内容词在切片正文中的命中率）或
       ``score >= 0.6 * 最高分``（强相关切片豁免）；
    3. 仅靠 coverage 通过的切片，还要求查询内容词在标题/章节中出现过，
       避免「长正文里恰好罗列了查询词」的弱相关切片（如把「实验室门禁」
       「体育场馆预约」列在一起的校园一卡通说明）混入引用。
    """
    if not ranked:
        return []
    max_score = max(float(score) for _, score in ranked)
    relative_floor = max(float(min_score), RELATIVE_SCORE_FLOOR_RATIO * max_score)
    strong_score = STRONG_SCORE_RATIO * max_score
    query_tokens = _query_token_set(query)

    results: list[dict] = []
    per_document: dict[str, int] = {}
    for chunk, raw_score in ranked:
        score = float(raw_score)
        if score < relative_floor:
            continue
        if query_tokens and score < strong_score:
            content = str(chunk.get("content") or "")
            coverage = coverage_ratio(query_tokens, content)
            if coverage < COVERAGE_THRESHOLD:
                continue
            heading = f"{chunk.get('document_title') or ''} {chunk.get('section') or ''}"
            if not (query_tokens & _coverage_tokens(heading)):
                continue
        document_id = str(chunk.get("document_id") or "")
        if document_id and per_document.get(document_id, 0) >= MAX_CHUNKS_PER_DOCUMENT:
            continue
        results.append(_to_result(chunk, score))
        if document_id:
            per_document[document_id] = per_document.get(document_id, 0) + 1
        if len(results) >= limit:
            break

    if not results:
        # 有排序结果但全被过滤：回退原 top1，避免"有命中却返回空"
        best_chunk, best_score = ranked[0]
        results.append(_to_result(best_chunk, float(best_score)))
    return results


def search_chunks(
    query: str,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[dict]:
    """按 BM25 检索知识库切片，返回契约字段列表；无命中或异常返回 ``[]``。"""
    try:
        text = (query or "").strip()
        if not text:
            return []
        default_top_k, default_min_score = get_retrieval_settings()
        if top_k is None:
            limit = default_top_k
        else:
            try:
                limit = int(top_k)
            except (TypeError, ValueError):
                limit = default_top_k
        limit = max(1, min(limit, 50))
        threshold = default_min_score if min_score is None else float(min_score)

        candidates = load_chunks()
        if not candidates:
            return []
        # 多取一些候选（5 倍）以便过滤后仍能填满 top_k
        ranked = rank_chunks(text, candidates, top_k=max(limit * 5, 20))
        return _select_results(ranked, text, limit, threshold)
    except Exception:
        logger.exception("知识库检索失败，已降级返回空结果")
        return []


# 便捷别名（rag.search 之外也可能被直接调用）
search = search_chunks


__all__ = [
    "SNIPPET_CHARS",
    "COVERAGE_THRESHOLD",
    "STRONG_SCORE_RATIO",
    "RELATIVE_SCORE_FLOOR_RATIO",
    "MAX_CHUNKS_PER_DOCUMENT",
    "invalidate_cache",
    "load_chunks",
    "get_retrieval_settings",
    "make_snippet",
    "tokenize_text",
    "coverage_ratio",
    "search_chunks",
    "search",
]

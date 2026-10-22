"""Lexical retrieval core (BM25 over jieba tokens).

This module is intentionally dependency-light so it can be unit-tested without a
database.  ``kb-rag`` loads chunks from SQLite and delegates ranking to
:func:`rank_chunks`; the lead (and optionally a codex reviewer) improves the
scoring here without changing the public interface.

Public interface (stable, see docs/CONTRACT.md)::

    tokenize(text: str) -> list[str]
    rank_chunks(query: str, chunks: list[dict], top_k: int = 5) -> list[tuple[dict, float]]
"""
from __future__ import annotations

import math
import re
import string
from collections import Counter

import jieba

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ALNUM_RE = re.compile(r"[a-zA-Z0-9]+")
_PUNCT_TABLE = str.maketrans({c: " " for c in string.punctuation})

# Minimal stop words: function words that carry no retrieval signal in Chinese
# campus Q&A.  Content words such as 申请 / 材料 / 流程 are intentionally kept.
STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "也", "就", "都", "而", "被", "把",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "自己", "这个", "那个", "这些", "那些",
    "吗", "呢", "啊", "呀", "吧", "哦", "嗯", "么", "什么", "怎么", "怎样", "如何", "哪些",
    "请问", "一下", "可以", "能否", "是否", "需要", "应该", "一个", "现在", "然后", "以及",
    "对于", "关于", "如果", "因为", "所以", "但是", "而且", "还有", "已经", "进行", "相关",
}


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text into lowercase content tokens."""
    if not text:
        return []
    text = text.translate(_PUNCT_TABLE).lower()
    tokens: list[str] = []
    for raw in jieba.lcut(text):
        token = raw.strip()
        if not token or token in STOPWORDS:
            continue
        if _CJK_RE.search(token):
            token = token.lower()
            if len(token) <= 3:
                tokens.append(token)
                continue
            # Long jieba compounds are additionally indexed by character
            # bigrams so partial mentions ("奖学金" vs "国家奖学金") still match.
            tokens.append(token)
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))
        else:
            for word in _ALNUM_RE.findall(token):
                if len(word) > 1 or word.isdigit():
                    tokens.append(word.lower())
    return tokens


def _document_tokens(chunk: dict) -> list[str]:
    content = str(chunk.get("content") or "")
    title = str(chunk.get("document_title") or chunk.get("title") or "")
    section = str(chunk.get("section") or "")
    # Field weighting: title 2x, section 1x extra, so heading-only matches rank well.
    return tokenize(content) + tokenize(title) + tokenize(title) + tokenize(section)


def _bm25_scores(query: str, tokenized_docs: list[list[str]], k1: float = 1.5,
                 b: float = 0.75) -> list[float]:
    query_tokens = [token for token in tokenize(query) if token]
    if not query_tokens:
        return [0.0] * len(tokenized_docs)

    n_docs = len(tokenized_docs)
    doc_lens = [len(doc) for doc in tokenized_docs]
    avg_len = sum(doc_lens) / n_docs if n_docs else 0.0
    df: Counter[str] = Counter()
    for doc in tokenized_docs:
        df.update(set(doc))

    idf = {
        term: math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5))
        for term, freq in df.items()
    }
    query_counts = Counter(query_tokens)
    scores: list[float] = []
    for doc, length in zip(tokenized_docs, doc_lens):
        tf = Counter(doc)
        score = 0.0
        for term, q_count in query_counts.items():
            freq = tf.get(term, 0)
            if not freq:
                continue
            norm = 1 - b + b * (length / avg_len if avg_len else 1)
            score += q_count * idf.get(term, 0.0) * (freq * (k1 + 1)) / (freq + k1 * norm)
        scores.append(score)
    return scores


def rank_chunks(query: str, chunks: list[dict], top_k: int = 5) -> list[tuple[dict, float]]:
    """Rank chunks by BM25 relevance against ``query``.

    Args:
        query: student question.
        chunks: dicts with at least ``content``; optional ``document_title`` /
            ``section`` / ``seq`` keys improve scoring.
        top_k: number of results to keep.

    Returns:
        ``[(chunk, score), ...]`` sorted by descending score.  Chunks scoring
        zero are dropped; an empty list is returned for empty input.
    """
    if not chunks or top_k <= 0:
        return []
    tokenized_docs = [_document_tokens(chunk) for chunk in chunks]
    scores = _bm25_scores(query, tokenized_docs)
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    results: list[tuple[dict, float]] = []
    for index in order:
        if scores[index] <= 0:
            continue
        results.append((chunks[index], round(scores[index], 6)))
        if len(results) >= top_k:
            break
    return results

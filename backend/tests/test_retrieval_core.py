"""检索核心单元测试（BM25 词法检索，契约 §6 / §5.3 检索要求）。

被测模块：``backend/app/services/retrieval_core.py``（只 import，不改实现）。
本文件不依赖数据库 / 网络 / FastAPI，只依赖标准库 + pytest + jieba，因此
``conftest.py`` 设置的临时 ``NPU_DATA_DIR`` 对本模块无影响。

覆盖点：
* 中文分词与「部分匹配」（奖学金 ↔ 国家奖学金）；
* 标题加权排序、top_k 限制与分数降序；
* 空查询 / 空切片 / 非法 top_k / 无命中返回 ``[]``；
* 英文混排 tokenize 与小写匹配；
* 结果确定性、score 精度、300 切片性能。
"""
from __future__ import annotations

import time

import pytest

from app.services.retrieval_core import rank_chunks, tokenize

# ---------------------------------------------------------------------------
# 公共测试数据
# ---------------------------------------------------------------------------
SCHOLARSHIP_CHUNKS = [
    {
        "id": "a",
        "content": "国家奖学金用于奖励特别优秀的全日制本科生，申请材料包括申请表、成绩单与个人事迹。",
        "document_title": "国家奖学金评审办法",
        "section": "申请条件",
    },
    {
        "id": "b",
        "content": "校园网账号与学号绑定，连接无线网络后使用统一身份认证登录。",
        "document_title": "网络服务指南",
        "section": "校园网开户",
    },
]


# ---------------------------------------------------------------------------
# 1) tokenize 基础行为
# ---------------------------------------------------------------------------
def test_tokenize_normalizes_and_removes_stopwords() -> None:
    """中文分词保留实词、去掉停用词/标点，并统一小写。"""
    tokens = tokenize("请问国家奖学金怎么申请？")

    assert "国家" in tokens
    assert "奖学金" in tokens
    assert "申请" in tokens
    for stopword in ("请问", "怎么", "的", "了"):
        assert stopword not in tokens
    assert all(token and token == token.lower() for token in tokens)
    assert "？" not in tokens and "，" not in tokens


def test_tokenize_cjk_bigrams_and_mixed_english() -> None:
    """长中文词额外按字符 bigram 索引；英文/数字小写化且去掉连字符。"""
    tokens = tokenize("人工智能")
    assert "人工智能" in tokens
    for bigram in ("人工", "工智", "智能"):
        assert bigram in tokens

    mixed = tokenize("NPU-Assistant SCHOLARSHIP 2026")
    assert "npu" in mixed
    assert "assistant" in mixed
    assert "scholarship" in mixed
    assert "2026" in mixed
    assert all(mixed)


# ---------------------------------------------------------------------------
# 2) 中文部分匹配
# ---------------------------------------------------------------------------
def test_chinese_partial_match_hits_document() -> None:
    """查询「奖学金」应命中含「国家奖学金」的切片（bigram / 子词匹配）。"""
    results = rank_chunks("奖学金", SCHOLARSHIP_CHUNKS, top_k=5)

    assert results, "部分匹配不应丢失命中"
    top_chunk, top_score = results[0]
    assert top_chunk["document_title"] == "国家奖学金评审办法"
    assert top_score > 0

    # 完整词查询同样命中
    full = rank_chunks("国家奖学金", SCHOLARSHIP_CHUNKS, top_k=5)
    assert full and full[0][0]["document_title"] == "国家奖学金评审办法"


# ---------------------------------------------------------------------------
# 3) 标题加权
# ---------------------------------------------------------------------------
def test_title_weighting_ranks_title_match_first() -> None:
    """标题命中（2 倍加权）应排在正文命中之前。"""
    title_chunk = {
        "id": "title-hit",
        "content": "本办法适用于全校本科生，具体申请流程另行通知。",
        "document_title": "奖学金评审办法",
        "section": "",
    }
    body_chunk = {
        "id": "body-hit",
        "content": "国家奖学金评审需要提交成绩单与申请表。",
        "document_title": "学生手册",
        "section": "其他",
    }

    results = rank_chunks("奖学金", [title_chunk, body_chunk], top_k=5)

    assert len(results) == 2
    assert results[0][0]["id"] == "title-hit"
    assert results[0][1] > results[1][1]


def test_section_text_contributes_to_scoring() -> None:
    """章节（section）文本参与打分：相同正文时章节相关的切片排前。"""
    with_section = {
        "id": "section-hit",
        "content": "详见正文说明。",
        "document_title": "办事指南",
        "section": "奖学金申请流程",
    }
    without_section = {
        "id": "plain",
        "content": "详见正文说明。",
        "document_title": "办事指南",
        "section": "其他事项",
    }

    results = rank_chunks("奖学金", [with_section, without_section], top_k=5)
    assert results and results[0][0]["id"] == "section-hit"


# ---------------------------------------------------------------------------
# 4) 无命中 / 空输入 / 非法 top_k
# ---------------------------------------------------------------------------
def test_no_hit_returns_empty_list() -> None:
    """与知识库完全无关的查询返回空列表（不报错、不返回零分项）。"""
    assert rank_chunks("xyzzy quux frobnicate", SCHOLARSHIP_CHUNKS, top_k=5) == []
    # 中文但知识库未覆盖的主题同样返回 []
    assert rank_chunks("量子引力波观测", SCHOLARSHIP_CHUNKS, top_k=5) == []


@pytest.mark.parametrize(
    ("query", "chunks", "top_k"),
    [
        ("", SCHOLARSHIP_CHUNKS, 5),          # 空查询
        ("   ", SCHOLARSHIP_CHUNKS, 5),       # 纯空白
        ("！？。", SCHOLARSHIP_CHUNKS, 5),      # 纯标点（分词后为空）
        ("奖学金", [], 5),                     # 空切片列表
        ("奖学金", SCHOLARSHIP_CHUNKS, 0),     # top_k = 0
        ("奖学金", SCHOLARSHIP_CHUNKS, -3),    # 非法 top_k
    ],
)
def test_invalid_or_empty_inputs_return_empty_list(query, chunks, top_k) -> None:
    assert rank_chunks(query, chunks, top_k=top_k) == []


# ---------------------------------------------------------------------------
# 5) top_k 与分数降序
# ---------------------------------------------------------------------------
def test_top_k_limit_and_descending_scores() -> None:
    """结果数量受 top_k 限制，且按 score 严格降序。"""
    chunks = [
        {
            "id": "high",
            "content": "奖学金 奖学金 奖学金 申请条件与材料说明。",
            "document_title": "评审办法",
            "section": "申请",
        },
        {
            "id": "mid",
            "content": "奖学金申请需要提交成绩单。",
            "document_title": "学生手册",
            "section": "奖助",
        },
        {
            "id": "low",
            "content": "校园网账号与学号绑定。",
            "document_title": "网络指南",
            "section": "网络",
        },
    ]

    top2 = rank_chunks("奖学金", chunks, top_k=2)
    assert len(top2) == 2
    assert [chunk["id"] for chunk, _ in top2] == ["high", "mid"]
    scores = [score for _, score in top2]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[1]

    assert len(rank_chunks("奖学金", chunks, top_k=1)) == 1
    # top_k 大于命中数时只返回正分切片（low 不命中，不应出现）
    assert {chunk["id"] for chunk, _ in rank_chunks("奖学金", chunks, top_k=99)} == {"high", "mid"}
    # 返回的 chunk 应保持输入对象的引用（分值附带在元组里）
    assert top2[0][0] is chunks[0]


# ---------------------------------------------------------------------------
# 6) 英文混排
# ---------------------------------------------------------------------------
def test_english_mixed_query_matches() -> None:
    """英文大小写不敏感；与中文知识库无关的英文查询返回 []。"""
    chunks = [
        {
            "id": "en",
            "content": "NPU scholarship application requires transcript and form.",
            "document_title": "Scholarship Guide",
            "section": "Application",
        },
        {
            "id": "net",
            "content": "Campus network account is bound to student id.",
            "document_title": "Network Guide",
            "section": "Network",
        },
    ]

    results = rank_chunks("scholarship NPU", chunks, top_k=5)
    assert results and results[0][0]["id"] == "en"
    # 大写查询同样命中
    assert rank_chunks("SCHOLARSHIP", chunks, top_k=5)[0][0]["id"] == "en"
    # 中文查询不会误命中英文文档
    assert rank_chunks("奖学金", chunks, top_k=5) == []


# ---------------------------------------------------------------------------
# 7) 确定性与分数精度
# ---------------------------------------------------------------------------
def test_results_are_deterministic_and_scores_rounded() -> None:
    """相同输入两次调用结果完全一致；score 为正的 6 位小数以内的 float。"""
    first = rank_chunks("国家奖学金 申请材料", SCHOLARSHIP_CHUNKS, top_k=5)
    second = rank_chunks("国家奖学金 申请材料", SCHOLARSHIP_CHUNKS, top_k=5)

    assert [(chunk["id"], score) for chunk, score in first] == [
        (chunk["id"], score) for chunk, score in second
    ]
    for _, score in first:
        assert isinstance(score, float)
        assert score > 0
        assert round(score, 6) == score


# ---------------------------------------------------------------------------
# 8) 性能
# ---------------------------------------------------------------------------
def test_rank_300_chunks_under_two_seconds() -> None:
    """300 条切片的一次检索应在 2 秒内完成（MVP 本地知识库规模）。"""
    chunks = [
        {
            "id": f"chunk-{index}",
            "content": (
                f"通知第 {index} 条：国家奖学金与助学金申请材料说明，"
                "包含成绩单、申请表与个人事迹材料，具体时间以学校通知为准。"
            ),
            "document_title": f"奖助政策汇编 第{index % 7 + 1}册",
            "section": "申请材料",
        }
        for index in range(300)
    ]

    # 预热（首次调用含 jieba 词典加载，不计入性能预算）
    rank_chunks("奖学金", chunks[:3], top_k=1)

    started = time.perf_counter()
    results = rank_chunks("奖学金 申请材料", chunks, top_k=5)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"300 条切片检索耗时 {elapsed:.3f}s，超过 2s 预算"
    assert len(results) == 5
    assert all(score > 0 for _, score in results)

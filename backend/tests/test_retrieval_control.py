"""端到端测试：Wiki 回答完整性判断 + 官网/公众号联网兜底。

覆盖：
* 规则判断：完整回答不联网、明确拒答判为不完整；
* LLM 裁判：结构化 JSON 解析；
* API 链路：Wiki 命中但不完整 → 补充联网引用；
* 联网无结果 → 保持“暂未收录”，不硬塞无关引用。
"""
from __future__ import annotations

from app.services import counselor


def _chat(client, message: str):
    return client.post(
        "/api/chat",
        json={"message": message, "channel": "web", "session_key": "web:e2e"},
    )


def _fake_remote(query, **kwargs):
    return {
        "query": query,
        "results": [
            {
                "source": "西北工业大学集成电路学院",
                "title": "国家奖学金申请条件与材料",
                "url": "https://weidianzi.nwpu.edu.cn/info/9/9.htm",
                "snippet": "国家奖学金申请条件、材料清单与评审办法。",
                "date": "2026-10-01",
                "account": "",
                "origin": "remote",
            }
        ],
        "errors": [],
    }


def test_rule_marks_generic_answer_incomplete():
    decision = counselor._rule_completeness(
        "国家奖学金申请条件是什么？",
        "国家奖学金需要提交申请材料，具体以学院通知为准。",
        [
            {
                "index": 1,
                "page_id": "p1",
                "page_title": "国家奖学金申请",
                "summary": "国家奖学金面向优秀本科生。",
                "snippet": "申请材料包括申请表。",
                "sources": [],
            }
        ],
    )
    assert decision["complete"] is False
    assert decision["need_remote"] is True
    assert decision["missing"]


def test_rule_marks_specific_answer_complete():
    decision = counselor._rule_completeness(
        "国家奖学金申请条件是什么？",
        "国家奖学金申请条件为学习成绩排名前10%，并通过家庭经济困难认定。",
        [
            {
                "index": 1,
                "page_id": "p1",
                "page_title": "国家奖学金申请",
                "summary": "申请条件：学习成绩排名前10%，通过家庭经济困难认定。",
                "snippet": "申请条件：学习成绩排名前10%。",
                "sources": [],
            }
        ],
    )
    assert decision["complete"] is True
    assert decision["need_remote"] is False


def test_llm_judge_parses_structured_json(monkeypatch):
    from app import config
    from app.services import llm

    monkeypatch.setattr(config, "LLM_COMPLETENESS_JUDGE_ENABLED", True)
    monkeypatch.setattr(
        llm,
        "default_model",
        lambda: {"provider_type": "openai", "display_name": "judge"},
    )
    monkeypatch.setattr(
        llm,
        "chat",
        lambda *args, **kwargs: {
            "content": '{"complete": false, "confidence": 0.88, "missing": ["具体分值"], "need_remote": true, "reason": "缺少分值"}',
            "fallback": False,
        },
    )
    decision = counselor._llm_judge_completeness(
        "本科生综测分怎么计算？",
        "综合测评由各学院制定细则。",
        [
            {
                "index": 1,
                "page_id": "p2",
                "page_title": "综合测评总则",
                "summary": "综合测评包括五个模块。",
                "snippet": "具体分值由学院制定。",
                "sources": [],
            }
        ],
    )
    assert decision is not None
    assert decision["complete"] is False
    assert decision["need_remote"] is True
    assert decision["judge"] == "llm"


def test_api_incomplete_answer_appends_remote_citations(
    client, seeded_wiki, fake_llm, real_model, monkeypatch
):
    monkeypatch.setattr(
        counselor,
        "_judge_answer_completeness",
        lambda question, reply, citations: {
            "complete": False,
            "confidence": 0.9,
            "missing": ["具体细则"],
            "need_remote": True,
            "reason": "e2e:incomplete",
            "judge": "rule",
        },
    )
    from app import config
    monkeypatch.setattr(config, "REMOTE_FALLBACK_ENABLED", True)
    monkeypatch.setattr(counselor.remote_search, "search_all", _fake_remote)
    data = _chat(client, "国家奖学金怎么申请？").json()

    assert data["navigation"]["method"] == "remote"
    assert data["retrieval"]["wiki_used"] is True
    assert data["retrieval"]["remote_used"] is True
    assert "补充检索" in data["reply"]
    assert any(
        "国家奖学金申请条件与材料" == citation["page_title"]
        for citation in data["citations"]
    )


def test_api_remote_empty_keeps_no_hit(
    client, seeded_wiki, fake_llm, real_model, monkeypatch
):
    monkeypatch.setattr(
        counselor,
        "_judge_answer_completeness",
        lambda question, reply, citations: {
            "complete": False,
            "confidence": 0.9,
            "missing": ["具体细则"],
            "need_remote": True,
            "reason": "e2e:incomplete",
            "judge": "rule",
        },
    )
    from app import config
    monkeypatch.setattr(config, "REMOTE_FALLBACK_ENABLED", True)
    monkeypatch.setattr(
        counselor.remote_search,
        "search_all",
        lambda *args, **kwargs: {"query": "", "results": [], "errors": []},
    )
    data = _chat(client, "国家奖学金怎么申请？").json()

    assert data["retrieval"]["remote_used"] is False
    assert "暂未收录" in data["reply"] or "补充检索" not in data["reply"]

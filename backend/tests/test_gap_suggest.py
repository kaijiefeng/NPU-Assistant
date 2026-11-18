"""知识缺口 AI 备选：白名单检索 + LLM 裁决 + 一键收录。"""
from __future__ import annotations

import time


def _make_gap(question: str = "校园卡如何在寒假期间充值？") -> str:
    from app import db

    gap_id = db.new_id()
    now = db.now()
    db.execute(
        "INSERT INTO knowledge_gaps(id, question, normalized_key, channel, count, reasons, "
        "first_seen_at, last_seen_at, status, created_at, updated_at) "
        "VALUES(?, ?, ?, 'web', 1, '[]', ?, ?, 'open', ?, ?)",
        (gap_id, question, f"key-{gap_id[:8]}", now, now, now, now),
    )
    return gap_id


def _wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    return job


def _patch_search(monkeypatch) -> None:
    from app.services import remote_search

    fake = {
        "results": [
            {
                "source": "集成电路学院官网",
                "title": "校园卡服务指南",
                "url": "https://ic.nwpu.edu.cn/card-guide",
                "snippet": "充值与挂失流程",
            },
            {
                "source": "推广页",
                "title": "无关促销",
                "url": "https://example.com/ad",
                "snippet": "促销信息",
            },
        ],
        "errors": [],
    }
    monkeypatch.setattr(remote_search, "search_all", lambda query, **kwargs: fake)


def test_suggest_filters_candidates_and_persists(client, monkeypatch):
    from app.services import gap_suggest

    gap_id = _make_gap()
    _patch_search(monkeypatch)

    def fake_chat(messages, **kwargs):
        return {
            "content": (
                '[{"index":0,"relevant":true,"reason":"官方指南","suggested_title":"校园卡服务指南（官方）"},'
                '{"index":1,"relevant":false,"reason":"无关广告"}]'
            ),
            "fallback": False,
        }

    monkeypatch.setattr(gap_suggest.llm, "chat", fake_chat)
    result = gap_suggest.suggest_for_gap(gap_id)
    assert result["candidates"] == 2, result
    assert len(result["suggestions"]) == 1, result
    suggestion = result["suggestions"][0]
    assert suggestion["url"] == "https://ic.nwpu.edu.cn/card-guide"
    assert suggestion["title"] == "校园卡服务指南（官方）"
    assert suggestion["reason"] == "官方指南"

    gap = client.get(f"/api/insights/gaps/{gap_id}").json()
    assert gap["suggestions"] and gap["suggestions"][0]["url"].endswith("card-guide")
    assert gap["suggested_at"], gap


def test_suggest_falls_back_to_raw_candidates_when_llm_down(client, monkeypatch):
    from app.services import gap_suggest

    gap_id = _make_gap()
    _patch_search(monkeypatch)
    monkeypatch.setattr(
        gap_suggest.llm, "chat", lambda messages, **kwargs: {"content": "", "fallback": True}
    )
    result = gap_suggest.suggest_for_gap(gap_id)
    assert result["fallback"] is True, result
    assert len(result["suggestions"]) == 2, result
    assert all("原始结果" in s["reason"] for s in result["suggestions"])


def test_collect_suggestion_links_gap(client, monkeypatch):
    from app import db
    from app.services import gap_suggest, importer

    gap_id = _make_gap()

    def fake_import_url(url, title=None, tags=None):
        doc_id = db.new_id()
        now = db.now()
        db.execute(
            "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, "
            "status, error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
            "VALUES(?, '校园卡指南', '校园卡指南.md', 'unused.md', '.md', 10, 'ready', NULL, '[]', "
            "100, 1, 'url', ?, ?, ?)",
            (doc_id, url, now, now),
        )
        return {"duplicated": False, "document": db.get_document(doc_id), "pages": 1}

    monkeypatch.setattr(importer, "import_url", fake_import_url)
    result = gap_suggest.collect_suggestion(gap_id, "https://ic.nwpu.edu.cn/card-guide")
    assert result["duplicated"] is False, result

    gap = client.get(f"/api/insights/gaps/{gap_id}").json()
    assert gap["status"] == "linked", gap
    assert gap["linked_document_id"], gap


def test_suggest_endpoint_runs_job(client, monkeypatch):
    gap_id = _make_gap()
    _patch_search(monkeypatch)
    from app.services import gap_suggest

    monkeypatch.setattr(
        gap_suggest.llm,
        "chat",
        lambda messages, **kwargs: {"content": "[]", "fallback": False},
    )
    response = client.post(f"/api/insights/gaps/{gap_id}/suggest", json={})
    assert response.status_code == 202, response.text
    job = _wait_job(client, response.json()["job_id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["candidates"] == 2, job

    missing = client.post("/api/insights/gaps/不存在/suggest", json={})
    assert missing.status_code == 404

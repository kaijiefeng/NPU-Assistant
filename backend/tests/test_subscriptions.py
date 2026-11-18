"""订阅网址发现：CRUD、新内容发现去重、一键收录与忽略。"""
from __future__ import annotations

import time


def _wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    return job


LIST_HTML = """
<html><body>
<a href="/info/2026/1118/a1.htm">关于寒假校园卡服务的通知</a>
<a href="/info/2026/1118/a2.htm">奖学金评审结果公示</a>
</body></html>
"""

ARTICLE_HTML = "<html><body><h1>标题</h1><p>{}</p></body></html>".format("正文内容。" * 50)


def _patch_fetch(monkeypatch, seen: set[str] | None = None):
    from app.services import importer

    def fake_fetch_url(url):
        if seen is not None:
            seen.add(url)
        if url.endswith("a1.htm") or url.endswith("a2.htm"):
            return {
                "requested_url": url,
                "final_url": url,
                "status_code": 200,
                "content_type": "text/html",
                "charset": "utf-8",
                "content": ARTICLE_HTML,
                "is_html": True,
            }
        return {
            "requested_url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html",
            "charset": "utf-8",
            "content": LIST_HTML,
            "is_html": True,
        }

    monkeypatch.setattr(importer, "fetch_url", fake_fetch_url)


def _patch_llm_summary(monkeypatch):
    from app.services import discovery

    monkeypatch.setattr(
        discovery.llm,
        "chat",
        lambda messages, **kwargs: {
            "content": '[{"url":"https://example.edu/info/2026/1118/a1.htm","summary":"寒假校园卡服务安排"},'
            '{"url":"https://example.edu/info/2026/1118/a2.htm","summary":"奖学金评审结果"}]',
            "fallback": False,
        },
    )


def test_subscription_crud_and_validation(client):
    created = client.post(
        "/api/insights/subscriptions", json={"name": "学院通知", "url": "https://example.edu/notify/"}
    )
    assert created.status_code == 201, created.text
    sub = created.json()
    assert sub["enabled"] is True and sub["discoveries"] == []

    duplicate = client.post(
        "/api/insights/subscriptions", json={"name": "重复", "url": "https://example.edu/notify"}
    )
    assert duplicate.status_code == 400

    listed = client.get("/api/insights/subscriptions").json()
    assert any(item["id"] == sub["id"] for item in listed["items"])

    deleted = client.delete(f"/api/insights/subscriptions/{sub['id']}")
    assert deleted.status_code == 200
    again = client.delete(f"/api/insights/subscriptions/{sub['id']}")
    assert again.status_code == 404


def test_check_discovers_new_items_and_dedupes(client, monkeypatch):
    created = client.post(
        "/api/insights/subscriptions", json={"name": "通知列表", "url": "https://example.edu/notify/"}
    )
    sub_id = created.json()["id"]
    _patch_fetch(monkeypatch)
    _patch_llm_summary(monkeypatch)

    first = client.post(f"/api/insights/subscriptions/{sub_id}/check", json={})
    assert first.status_code == 202, first.text
    job = _wait_job(client, first.json()["job_id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["status"] == "ok", job
    assert len(job["result"]["discoveries"]) == 2, job

    detail = client.get(f"/api/insights/subscriptions").json()["items"]
    sub = next(item for item in detail if item["id"] == sub_id)
    assert sub["last_status"] == "ok"
    assert sub["discoveries"][0]["summary"] == "寒假校园卡服务安排"

    # 模拟 a1 已收录（加入 source_url）→ 下一轮只剩 a2
    from app import db

    now = db.now()
    db.execute(
        "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, "
        "status, error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
        "VALUES('doc-a1', '校园卡通知', 'a.md', 'a.md', '.md', 1, 'ready', NULL, '[]', 1, 1, "
        "'url', 'https://example.edu/info/2026/1118/a1.htm', ?, ?)",
        (now, now),
    )
    second = client.post(f"/api/insights/subscriptions/{sub_id}/check", json={})
    job = _wait_job(client, second.json()["job_id"])
    urls = {item["url"] for item in job["result"]["discoveries"]}
    assert urls == {"https://example.edu/info/2026/1118/a2.htm"}, job


def test_collect_and_ignore_discovery(client, monkeypatch):
    from app import db
    from app.services import importer

    created = client.post(
        "/api/insights/subscriptions", json={"name": "通知列表", "url": "https://example.edu/notify/"}
    )
    sub_id = created.json()["id"]
    db.execute(
        "UPDATE subscriptions SET discoveries = ? WHERE id = ?",
        (
            '[{"url":"https://example.edu/info/2026/1118/a1.htm","title":"通知A","summary":"","date":""},'
            '{"url":"https://example.edu/info/2026/1118/a2.htm","title":"通知B","summary":"","date":""}]',
            sub_id,
        ),
    )

    def fake_import_url(url, title=None, tags=None):
        doc_id = db.new_id()
        now = db.now()
        db.execute(
            "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, "
            "status, error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
            "VALUES(?, '通知A', 'a.md', 'a.md', '.md', 1, 'ready', NULL, '[]', 1, 1, 'url', ?, ?, ?)",
            (doc_id, url, now, now),
        )
        return {"duplicated": False, "document": db.get_document(doc_id), "pages": 1}

    monkeypatch.setattr(importer, "import_url", fake_import_url)
    collected = client.post(
        f"/api/insights/subscriptions/{sub_id}/collect",
        json={"url": "https://example.edu/info/2026/1118/a1.htm", "title": "通知A"},
    )
    assert collected.status_code == 200, collected.text
    assert collected.json()["document"]["source_url"].endswith("a1.htm")

    sub = next(
        item
        for item in client.get("/api/insights/subscriptions").json()["items"]
        if item["id"] == sub_id
    )
    assert {d["url"] for d in sub["discoveries"]} == {
        "https://example.edu/info/2026/1118/a2.htm"
    }

    ignored = client.post(
        f"/api/insights/subscriptions/{sub_id}/ignore",
        json={"url": "https://example.edu/info/2026/1118/a2.htm"},
    )
    assert ignored.status_code == 200
    sub = next(
        item
        for item in client.get("/api/insights/subscriptions").json()["items"]
        if item["id"] == sub_id
    )
    assert sub["discoveries"] == []
    assert "https://example.edu/info/2026/1118/a2.htm" in sub["ignored_urls"]


def test_check_reports_robots_block(client, monkeypatch):
    created = client.post(
        "/api/insights/subscriptions", json={"name": "受限站", "url": "https://example.edu/notify/"}
    )
    sub_id = created.json()["id"]

    class DenyAll:
        def allowed(self, url):
            return False

    from app.services import discovery

    monkeypatch.setattr(discovery, "_robots", DenyAll())
    response = client.post(f"/api/insights/subscriptions/{sub_id}/check", json={})
    job = _wait_job(client, response.json()["job_id"])
    assert job["result"]["status"] == "robots-disallowed", job
    sub = next(
        item
        for item in client.get("/api/insights/subscriptions").json()["items"]
        if item["id"] == sub_id
    )
    assert sub["last_status"] == "robots-disallowed"

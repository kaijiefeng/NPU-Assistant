"""第三轮审阅修复的回归：设置脱敏、限流分桶、processing 回收、reindex 回滚、异步上传 payload。"""
from __future__ import annotations

import time


def test_settings_redacts_secrets_for_anonymous(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "secret-token")
    anonymous = client.get("/api/settings")
    assert anonymous.status_code == 200
    payload = anonymous.json()
    assert "crisis_notify_webhook" not in payload
    assert "system_prompt" not in payload
    assert "welcome_message" in payload

    full = client.get("/api/settings", headers={"X-Admin-Token": "secret-token"})
    assert full.status_code == 200
    assert "system_prompt" in full.json()


def test_login_rate_limit_has_separate_bucket():
    from app import main

    main._chat_hits.clear()
    host = "203.0.113.9"
    for _ in range(main.CHAT_RATE_LIMIT + 2):
        main._chat_rate_limited(host)
    # 聊天桶打满后，登录桶仍应放行
    assert main._login_rate_limited(host) is False


def _insert_document(doc_id: str, status: str, stored_path: str = "") -> None:
    from app import db

    timestamp = db.now()
    db.execute(
        "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, status, "
        "error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, '.txt', 5, ?, NULL, '[]', 5, 0, 'file', '', ?, ?)",
        (doc_id, "测试文档", "test.txt", stored_path, status, timestamp, timestamp),
    )


def test_recover_interrupted_resets_processing_documents(client):
    from app import db
    from app.services import jobs

    doc_id = db.new_id()
    _insert_document(doc_id, "processing")
    jobs.recover_interrupted()
    row = db.get_document(doc_id)
    assert row["status"] == "failed"
    assert "中断" in (row.get("error") or "")


def test_async_upload_payload_contains_staging_path(client, compile_ready):
    response = client.post(
        "/api/knowledge/documents?async=1",
        files={"file": ("异步.md", "# 通知\n\n" + "选课调整说明。" * 30, "text/markdown")},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = {}
    for _ in range(50):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    assert job.get("payload", {}).get("staging_path"), job


def test_health_reports_degraded_without_model(client, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_default_model", lambda: None)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded", body
    assert any("模型" in issue for issue in body["issues"])


def test_wiki_edit_version_conflict_returns_409(client, compile_ready):
    upload = client.post(
        "/api/knowledge/documents",
        files={"file": ("版本.md", "# 版本测试\n\n" + "版本冲突说明。" * 30, "text/markdown")},
    )
    assert upload.status_code == 201, upload.text
    pages = client.get("/api/wiki/pages").json()["items"]
    listed = next(item for item in pages if "版本" in item["title"])
    page = client.get(f"/api/wiki/pages/{listed['id']}").json()
    assert "version" in page, page
    first = client.put(
        f"/api/wiki/pages/{page['id']}",
        json={"body_md": "第一版正文", "expected_version": page["version"]},
    )
    assert first.status_code == 200, first.text
    stale = client.put(
        f"/api/wiki/pages/{page['id']}",
        json={"body_md": "旧版本覆盖", "expected_version": page["version"]},
    )
    assert stale.status_code == 409, stale.text


def test_reindex_failure_restores_previous_pages(client, compile_ready, monkeypatch):
    from app import db
    from app.services import wiki_builder

    upload = client.post(
        "/api/knowledge/documents",
        files={"file": ("通知.md", "# 关于选课的通知\n\n" + "选课调整说明。" * 30, "text/markdown")},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()["id"]
    before = db.query_one("SELECT COUNT(*) AS n FROM wiki_pages")
    before_count = int(before["n"])
    assert before_count >= 1

    def fail_compile(document_id: str, force: bool = False) -> dict:
        return {
            "document": wiki_builder._document_json(db.get_document(document_id)),
            "status": "failed",
            "pages_created": 0,
            "pages_updated": 0,
            "pages_removed": 0,
            "error": "模拟模型不可用",
        }

    monkeypatch.setattr(wiki_builder, "compile_document", fail_compile)
    response = client.post(f"/api/knowledge/documents/{doc_id}/reindex")
    assert response.status_code == 200, response.text
    after = db.query_one("SELECT COUNT(*) AS n FROM wiki_pages")
    assert int(after["n"]) >= before_count, "重建失败后应回滚原有词条"

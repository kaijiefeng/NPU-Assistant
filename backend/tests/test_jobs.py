"""M4: background jobs, retry/cancel and alerts."""
from __future__ import annotations

import time


def _wait_job(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    data = {}
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.2)
    raise AssertionError(f"job did not finish: {data}")


def test_async_upload_creates_and_completes_job(client, fake_llm, real_model, seeded_wiki):
    response = client.post(
        "/api/knowledge/documents?async=1",
        files={
            "file": (
                "异步测试.md",
                "# 异步测试\n\n这是一段用于异步编译测试的校园说明文字。".encode(),
                "text/markdown",
            )
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "queued"
    assert job["job_id"]

    done = _wait_job(client, job["job_id"])
    assert done["status"] == "succeeded", done
    assert done["progress"] == 100
    assert done["result"]["document"]["status"] in {"ready", "failed"}
    assert done["result"]["document"]["id"]

    listed = client.get("/api/jobs").json()
    assert listed["total"] >= 1
    assert any(item["id"] == job["job_id"] for item in listed["items"])


def test_failed_job_alerts_and_retry_limit(client):
    from app.services import jobs

    job = jobs.create_job("reindex", {"document_id": "missing-doc"}, created_by="test")
    failed = _wait_job(client, job["id"])
    assert failed["status"] == "failed"
    assert "document" in (failed["error"] + failed["message"]).lower() or failed["error"]

    alerts = client.get("/api/alerts").json()
    assert alerts["open"] >= 1
    alert = next(item for item in alerts["items"] if item["category"] == "job")
    assert alert["status"] == "open"
    assert client.post(f"/api/alerts/{alert['id']}/ack").json()["status"] == "acknowledged"
    assert client.post(f"/api/alerts/{alert['id']}/resolve").json()["status"] == "resolved"

    retried = client.post(f"/api/jobs/{job['id']}/retry")
    assert retried.status_code == 200, retried.text
    again = _wait_job(client, job["id"])
    assert again["status"] == "failed"
    assert again["attempts"] == 2

    third = client.post(f"/api/jobs/{job['id']}/retry")
    assert third.status_code == 400
    assert "最大重试次数" in third.json()["detail"]


def test_cancel_finished_job_is_rejected(client):
    from app.services import jobs

    job = jobs.create_job("reindex", {"document_id": "missing-doc"}, created_by="test")
    done = _wait_job(client, job["id"])
    assert done["status"] == "failed"
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 400


def test_health_exposes_queue_metrics(client):
    health = client.get("/api/health").json()
    assert "jobs" in health
    assert "queued" in health["jobs"] and "running" in health["jobs"]
    assert "alerts_open" in health

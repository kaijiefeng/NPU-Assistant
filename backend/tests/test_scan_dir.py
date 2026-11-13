"""本地文件夹扫描收录 + 模型词条不合法的兜底回归。"""
from __future__ import annotations

import time
from pathlib import Path

from docx import Document


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _wait_job(client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    return job


def test_scan_dir_imports_supported_files(client, compile_ready, tmp_path):
    folder = tmp_path / "学生资料"
    folder.mkdir()
    _write(folder / "通知.md", "# 关于选课的说明\n\n" + "选课调整说明，请同学们注意时间安排。" * 30)
    _write(folder / "说明.txt", "关于宿舍报修的说明\n\n" + "报修流程说明，请按步骤提交。" * 30)
    nested = folder / "子目录"
    nested.mkdir()
    doc = Document()
    doc.add_heading("奖学金评审办法", 1)
    doc.add_paragraph("评审办法说明，按综合测评结果排序。" * 30)
    doc.save(nested / "奖学金.docx")
    (folder / "忽略.png").write_bytes(b"not-a-doc")

    response = client.post(
        "/api/knowledge/scan-dir", json={"path": str(folder), "recursive": True}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body.get("files") == 3, body
    job = _wait_job(client, body["job_id"])
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["imported_count"] == 3, result
    assert result["failed_count"] == 0, result
    names = {item["filename"] for item in client.get("/api/knowledge/documents").json()["items"]}
    assert {"通知.md", "说明.txt", "奖学金.docx"} <= names


def test_scan_dir_rejects_bad_path(client, compile_ready, tmp_path):
    missing = client.post("/api/knowledge/scan-dir", json={"path": str(tmp_path / "不存在")})
    assert missing.status_code == 400
    file_path = tmp_path / "单个文件.md"
    file_path.write_text("x", encoding="utf-8")
    not_dir = client.post("/api/knowledge/scan-dir", json={"path": str(file_path)})
    assert not_dir.status_code == 400


def test_scan_dir_requires_admin_when_token_mode(client, compile_ready, monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "scan-secret")
    folder = tmp_path / "资料"
    folder.mkdir()
    _write(folder / "通知.md", "# 通知\n\n" + "说明。" * 60)
    denied = client.post("/api/knowledge/scan-dir", json={"path": str(folder)})
    assert denied.status_code == 401
    allowed = client.post(
        "/api/knowledge/scan-dir",
        json={"path": str(folder)},
        headers={"X-Admin-Token": "scan-secret"},
    )
    assert allowed.status_code == 202


def test_invalid_model_entries_fall_back_to_raw_excerpt(client, compile_ready, monkeypatch):
    """模型返回 JSON 但每个词条都缺正文时，文档仍应 ready（兜底为原文摘录）。"""
    from app.services import wiki_builder

    def fake_call(messages, expected_type, *, max_tokens=0, error_prefix=""):
        return [{"title": "只有标题没有正文"}], "", False

    monkeypatch.setattr(wiki_builder, "_call_json", fake_call)
    response = client.post(
        "/api/knowledge/documents",
        files={
            "file": (
                "通知.md",
                "# 关于选课的说明\n\n" + "选课调整说明，请同学们注意时间安排。" * 30,
                "text/markdown",
            )
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready", body
    assert body["page_count"] >= 1, body

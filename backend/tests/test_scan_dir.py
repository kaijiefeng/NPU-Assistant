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


def test_scan_dir_skip_mode_avoids_duplicates(client, compile_ready, tmp_path):
    """重复扫描同一目录：skip 模式不再重复入库，已收录文件计入 skipped。"""
    folder = tmp_path / "资料"
    folder.mkdir()
    _write(folder / "通知.md", "# 关于选课的说明\n\n" + "选课调整说明，请同学们注意时间安排。" * 30)

    first = client.post(
        "/api/knowledge/scan-dir", json={"path": str(folder), "recursive": True}
    )
    assert first.status_code == 202, first.text
    job = _wait_job(client, first.json()["job_id"])
    assert job["result"]["imported_count"] == 1, job

    second = client.post(
        "/api/knowledge/scan-dir",
        json={"path": str(folder), "recursive": True, "mode": "skip"},
    )
    assert second.status_code == 202, second.text
    job = _wait_job(client, second.json()["job_id"])
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["imported_count"] == 0, result
    assert result["skipped_count"] == 1, result
    assert result["skipped"][0]["reason"] in {"unchanged", "name-exists"}, result
    docs = client.get("/api/knowledge/documents").json()["items"]
    assert len([d for d in docs if d["filename"] == "通知.md"]) == 1


def test_scan_dir_update_mode_recompiles_changed_file(client, compile_ready, tmp_path):
    """同名但内容变化的文件：skip 模式跳过；update 模式覆盖并重新编译。"""
    folder = tmp_path / "资料"
    folder.mkdir()
    target = folder / "奖学金.md"
    _write(target, "# 奖学金评审办法\n\n" + "旧的评审办法说明。" * 40)

    first = client.post(
        "/api/knowledge/scan-dir", json={"path": str(folder), "recursive": True}
    )
    job = _wait_job(client, first.json()["job_id"])
    assert job["result"]["imported_count"] == 1, job

    _write(target, "# 奖学金评审办法（2026 修订版）\n\n" + "全新的评审办法说明。" * 40)

    skipped_scan = client.post(
        "/api/knowledge/scan-dir",
        json={"path": str(folder), "recursive": True, "mode": "skip"},
    )
    job = _wait_job(client, skipped_scan.json()["job_id"])
    assert job["result"]["skipped_count"] == 1, job
    assert job["result"]["updated_count"] == 0, job

    update_scan = client.post(
        "/api/knowledge/scan-dir",
        json={"path": str(folder), "recursive": True, "mode": "update"},
    )
    job = _wait_job(client, update_scan.json()["job_id"])
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["updated_count"] == 1, result
    assert result["skipped_count"] == 0, result
    assert result["imported_count"] == 0, result

    docs = client.get("/api/knowledge/documents").json()["items"]
    same_name = [d for d in docs if d["filename"] == "奖学金.md"]
    assert len(same_name) == 1, docs
    assert "2026" in (same_name[0].get("title") or "") or same_name[0]["page_count"] >= 1


def test_scan_dir_content_hash_recorded(client, compile_ready, tmp_path):
    """新入库文档应记录源内容 sha256（查重依据）。"""
    from app import db as app_db

    folder = tmp_path / "资料"
    folder.mkdir()
    _write(folder / "通知.md", "# 关于选课的说明\n\n" + "选课调整说明。" * 40)
    scan = client.post(
        "/api/knowledge/scan-dir", json={"path": str(folder), "recursive": True}
    )
    _wait_job(client, scan.json()["job_id"])
    row = app_db.query_one(
        "SELECT content_hash FROM documents WHERE filename = ?", ("通知.md",)
    )
    assert row is not None and len(row["content_hash"]) == 64, row


def test_scan_dir_empty_path_uses_saved_or_default_dir(client, compile_ready, monkeypatch, tmp_path):
    """path 留空：优先 settings 的 knowledge_scan_dir，否则仓库默认扫描目录。"""
    from app import config

    saved_dir = tmp_path / "默认目录"
    saved_dir.mkdir()
    _write(saved_dir / "已保存目录.md", "# 已保存目录的说明\n\n" + "说明内容。" * 40)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    put = client.put("/api/settings", json={"knowledge_scan_dir": str(saved_dir)})
    assert put.status_code == 200, put.text

    response = client.post("/api/knowledge/scan-dir", json={"path": "", "recursive": True})
    assert response.status_code == 202, response.text
    job = _wait_job(client, response.json()["job_id"])
    assert job["status"] == "succeeded", job
    names = {item["filename"] for item in client.get("/api/knowledge/documents").json()["items"]}
    assert "已保存目录.md" in names

    # 清空保存目录后，回退到 config.SCAN_DEFAULT_DIR（monkeypatch 到独立目录验证）
    default_dir = tmp_path / "仓库默认"
    default_dir.mkdir()
    _write(default_dir / "仓库默认目录.md", "# 仓库默认目录\n\n" + "说明内容。" * 40)
    monkeypatch.setattr(config, "SCAN_DEFAULT_DIR", default_dir)
    client.put("/api/settings", json={"knowledge_scan_dir": ""})
    response = client.post("/api/knowledge/scan-dir", json={"recursive": True})
    assert response.status_code == 202, response.text
    job = _wait_job(client, response.json()["job_id"])
    assert job["status"] == "succeeded", job
    names = {item["filename"] for item in client.get("/api/knowledge/documents").json()["items"]}
    assert "仓库默认目录.md" in names


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

"""文件链路回归：UTF-16 文本/CSV 解码、删除不越界、扫描不跟随链接。"""
from __future__ import annotations

import time

import pytest

from app import db
from app.services import parsing


def test_utf16_bom_text_decodes():
    raw = "关于选课的通知\n\n请同学们注意时间安排。".encode("utf-16")
    doc = parsing.parse_bytes("通知.txt", raw)
    assert "关于选课的通知" in doc.text
    assert "\x00" not in doc.text


def test_utf16_bom_csv_decodes():
    raw = "姓名,学号\r\n张三,2023001\r\n".encode("utf-16")
    doc = parsing.parse_bytes("名单.csv", raw)
    assert "姓名" in doc.text and "张三" in doc.text


def test_utf16_without_bom_ascii_text_decodes():
    # 无 BOM 的中文 UTF-16 与 GBK 在字节层难以可靠区分，这里只保证 ASCII 为主的
    # 无 BOM UTF-16（NUL 字节比例高）能被识别；中文请使用带 BOM 的导出。
    raw = "Notice\n\nBody text".encode("utf-16-le")
    doc = parsing.parse_bytes("notice.txt", raw)
    assert "Notice" in doc.text
    assert "\x00" not in doc.text


def test_utf8_csv_with_crlf_parses():
    raw = "项目,说明\r\n选课,调整\r\n".encode("utf-8")
    doc = parsing.parse_bytes("数据.csv", raw)
    assert "选课 | 调整" in doc.text


def test_delete_refuses_file_outside_upload_dir(client, tmp_path):
    target = tmp_path / "important.txt"
    target.write_text("不要删除我", encoding="utf-8")
    doc_id = db.new_id()
    timestamp = db.now()
    db.execute(
        "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, status, "
        "error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, '.txt', ?, 'ready', NULL, '[]', 5, 0, 'file', '', ?, ?)",
        (doc_id, "越界文件", "important.txt", str(target), target.stat().st_size, timestamp, timestamp),
    )
    response = client.delete(f"/api/knowledge/documents/{doc_id}")
    assert response.status_code == 200, response.text
    assert target.exists(), "上传目录之外的文件不应被删除"
    assert db.get_document(doc_id) is None


def _wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    job: dict = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    return job


def test_scan_skips_symlink_outside_root(client, compile_ready, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# 外部文档\n\n" + "外部内容。" * 30, encoding="utf-8")
    root = tmp_path / "扫描目录"
    root.mkdir()
    (root / "内部.md").write_text("# 内部文档\n\n" + "内部内容。" * 30, encoding="utf-8")
    link = root / "链接.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    response = client.post("/api/knowledge/scan-dir", json={"path": str(root)})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["files"] == 1, body
    job = _wait_job(client, body["job_id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["imported_count"] == 1, job
    assert job["result"]["imported"][0]["file"] == "内部.md"

"""扫描目录配置/预览权限、固定任务路径以及管理助手工具次数回归。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config, db
from app.services import agent_chat, agent_tools, auth, ingest, jobs

ADMIN = {"role": "admin", "username": "scan-admin"}
VIEWER = {"role": "viewer", "username": "scan-viewer"}


def _folder(tmp_path: Path) -> Path:
    root = tmp_path / "资料目录"
    root.mkdir()
    (root / "通知.md").write_text("# 通知\n\n扫描预览样例。", encoding="utf-8")
    (root / "忽略.png").write_bytes(b"image")
    nested = root / "子目录"
    nested.mkdir()
    (nested / "说明.txt").write_text("资料说明", encoding="utf-8")
    return root


def test_directory_preview_and_default_persistence(client, monkeypatch, tmp_path):
    root = _folder(tmp_path)
    fallback = tmp_path / "默认"
    fallback.mkdir()
    monkeypatch.setattr(config, "SCAN_DEFAULT_DIR", fallback)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])
    db.set_setting("knowledge_scan_dir", "")
    current = client.get("/api/knowledge/scan-directory")
    assert current.status_code == 200
    assert current.json()["path"] == str(fallback.resolve())
    assert current.json()["source"] == "default"
    saved = client.put("/api/knowledge/scan-directory", json={"path": str(root)})
    assert saved.status_code == 200
    assert saved.json()["source"] == "saved"
    assert db.get_setting("knowledge_scan_dir") == str(root.resolve())
    preview = client.post("/api/knowledge/scan-directory/preview", json={"recursive": False})
    assert preview.status_code == 200
    assert preview.json()["files"] == 1
    assert preview.json()["samples"][0]["name"] == "通知.md"
    recursive = client.post("/api/knowledge/scan-directory/preview", json={"recursive": True}).json()
    assert recursive["files"] == 2
    assert {row["name"] for row in recursive["samples"]} == {"通知.md", str(Path("子目录") / "说明.txt")}
    assert not db.query("SELECT id FROM jobs")
    reset = client.put("/api/knowledge/scan-directory", json={"path": ""})
    assert reset.status_code == 200 and reset.json()["path"] == str(fallback.resolve())
    assert db.get_setting("knowledge_scan_dir") == ""


def test_relative_directory_uses_project_root(monkeypatch, tmp_path):
    root = _folder(tmp_path)
    elsewhere = tmp_path / "working"
    elsewhere.mkdir()
    monkeypatch.setattr(config, "REPO_DIR", tmp_path)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])
    monkeypatch.chdir(elsewhere)
    assert ingest.resolve_scan_directory("资料目录") == root.resolve()
    assert ingest.preview_scan_directory("资料目录")["files"] == 2


def test_preview_rejects_missing_file_root_and_outside_allowed(client, monkeypatch, tmp_path):
    root = _folder(tmp_path)
    monkeypatch.setattr(config, "SCAN_ROOTS", [str(root)])
    for path in [str(tmp_path / "missing"), str(root / "通知.md"), str(tmp_path), str(Path(tmp_path.anchor))]:
        response = client.post("/api/knowledge/scan-directory/preview", json={"path": path})
        assert response.status_code == 400, (path, response.text)
    assert client.put("/api/knowledge/scan-directory", json={"path": str(tmp_path)}).status_code == 400


def test_directory_info_reports_invalid_saved_config_without_hiding_diagnostic(client, monkeypatch, tmp_path):
    saved = str(tmp_path / "不存在的旧目录")
    db.set_setting("knowledge_scan_dir", saved)
    response = client.get("/api/knowledge/scan-directory")
    assert response.status_code == 200
    data = response.json()
    assert data["accessible"] is False and data["source"] == "saved"
    assert data["saved_path"] == saved and "不存在" in data["error"]


def test_scan_preview_reports_limit_and_empty_directory(client, monkeypatch, tmp_path):
    root = _folder(tmp_path)
    monkeypatch.setattr(config, "SCAN_MAX_FILES", 1)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])
    result = client.post("/api/knowledge/scan-directory/preview", json={"path": str(root)}).json()
    assert result["files"] == 1 and result["truncated"] is True
    empty = tmp_path / "empty"
    empty.mkdir()
    result = client.post("/api/knowledge/scan-directory/preview", json={"path": str(empty)})
    assert result.status_code == 200 and result.json()["files"] == 0


def test_directory_configuration_private_to_content_writers(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "scan-config-test-token")
    assert client.get("/api/knowledge/scan-directory").status_code == 401
    token = {"X-Admin-Token": "scan-config-test-token"}
    assert client.get("/api/knowledge/scan-directory", headers=token).status_code == 200
    auth.create_user("scan-viewer", "ScanViewer#2026", role="viewer")
    login = client.post("/api/auth/login", json={"username": "scan-viewer", "password": "ScanViewer#2026"})
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert client.get("/api/knowledge/scan-directory").status_code == 403
    assert client.put("/api/knowledge/scan-directory", json={"path": str(tmp_path)}, headers=csrf).status_code == 403
    assert client.post("/api/knowledge/scan-directory/preview", json={}, headers=csrf).status_code == 403
    assert "get_scan_directory" not in {spec.name for spec in agent_tools.tools_for("viewer")}
    assert agent_tools.call_tool("get_scan_directory", {}, VIEWER).get("error")
    assert agent_tools.call_tool("set_scan_directory", {"path": str(tmp_path)}, VIEWER).get("error")


def test_route_and_tool_pin_absolute_directory_before_queue(client, monkeypatch, tmp_path):
    root = _folder(tmp_path)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])
    db.set_setting("knowledge_scan_dir", str(root))
    captured = []
    def create(kind, payload, **kwargs):
        captured.append((kind, payload.copy()))
        return {"id": "scan-test-job"}
    monkeypatch.setattr(jobs, "create_job", create)
    response = client.post("/api/knowledge/scan-dir", json={"path": "", "recursive": True})
    assert response.status_code == 202 and response.json()["path"] == str(root.resolve())
    result = agent_tools.call_tool("scan_dir", {}, ADMIN)
    assert result["path"] == str(root.resolve())
    db.set_setting("knowledge_scan_dir", str(tmp_path / "changed"))
    assert len(captured) == 2
    assert all(kind == "scan_dir" and payload["path"] == str(root.resolve()) for kind, payload in captured)


def test_agent_can_discover_and_save_scan_directory(client, monkeypatch, tmp_path):
    root = _folder(tmp_path)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])
    saved = agent_tools.call_tool("set_scan_directory", {"path": str(root)}, ADMIN)
    assert saved["accessible"] is True
    result = agent_tools.call_tool("get_scan_directory", {"preview": True}, ADMIN)
    assert result["directory"]["path"] == str(root.resolve())
    assert result["preview"]["files"] == 2
    assert db.query("SELECT id FROM audit_log WHERE action='agent.tool.set_scan_directory'")
    assert agent_tools.call_tool("set_scan_directory", {}, ADMIN).get("error")


@pytest.mark.parametrize("last_is_reply", [True, False])
def test_agent_executes_at_most_200_tools(monkeypatch, last_is_reply):
    calls, executed = [], []
    def fake_chat(messages, **kwargs):
        calls.append(messages.copy())
        if len(calls) == 201 and last_is_reply:
            return {"content": json.dumps({"reply": "已执行200步，扫描任务已提交。"}), "fallback": False}
        return {"content": json.dumps({"tool": "get_job", "args": {"job_id": f"step-{len(calls)}"}}), "fallback": False}
    monkeypatch.setattr(agent_chat.llm, "chat", fake_chat)
    monkeypatch.setattr(agent_chat, "call_tool", lambda name, args, principal: executed.append(name) or {"ok": True})
    result = agent_chat.run_agent_chat([], "检查后提交扫描", ADMIN)
    assert len(executed) == 200 and len(result["steps"]) == 200
    assert len(calls) == 201
    assert any("只输出 reply" in item["content"] for item in calls[-1])
    assert "200步" in result["reply"] if last_is_reply else "上限" in result["reply"]

"""系统接口测试（LLM Wiki）：健康检查、运行信息、统计、设置读写（契约 §5.1 / WIKI-DESIGN §2）。"""
from __future__ import annotations

import json

from app import config


def test_health_returns_ok_wiki_mode_and_default_model(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["app"] == "NPU-Assistant"
    assert data["version"] == config.APP_VERSION
    assert data["knowledge_mode"] == "llm-wiki"
    assert data["wiki_pages"] == 0
    assert data["time"]

    model = data["default_model"]
    assert model is not None
    assert model["provider_type"] == "mock"


def test_health_wiki_pages_after_seed(client, seeded_wiki):
    data = client.get("/api/health").json()
    assert data["knowledge_mode"] == "llm-wiki"
    assert data["wiki_pages"] == 3


def test_system_info_exposes_safe_configuration(client):
    data = client.get("/api/system/info").json()

    for key in (
        "app",
        "version",
        "knowledge_mode",
        "wiki_seed_available",
        "frontend_ready",
        "supported_exts",
        "max_upload_mb",
        "url_import_max_mb",
        "url_import_max_urls",
        "admin_token_required",
        "auto_seed",
        "cors_enabled",
        "insecure_bind",
    ):
        assert key in data, f"缺少字段 {key}"

    assert data["knowledge_mode"] == "llm-wiki"
    assert ".md" in data["supported_exts"]
    assert ".pdf" in data["supported_exts"]
    assert ".doc" in data["supported_exts"]
    assert data["max_upload_mb"] == 20
    assert data["url_import_max_mb"] == 8.0
    assert data["url_import_max_urls"] == 5
    assert data["admin_token_required"] is False
    assert data["cors_enabled"] is False
    assert data["insecure_bind"] is False

    # 安全：公开接口不得泄露绝对路径 / 部署目录
    for forbidden in ("data_dir", "upload_dir", "db_path", "wiki_seed_path"):
        assert forbidden not in data, f"system/info 不应返回 {forbidden}"
    serialized = json.dumps(data, ensure_ascii=False)
    assert ":\\" not in serialized and "npu_assistant_tests_" not in serialized


def test_security_headers_and_no_default_cors(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    # 默认同源部署：不应自动返回 CORS 放行头
    assert "access-control-allow-origin" not in response.headers


def test_system_stats_use_wiki_tables(client, seeded_wiki):
    data = client.get("/api/system/stats").json()

    assert data["documents"] == 1
    assert data["ready_documents"] == 1
    assert data["wiki_pages"] == 3
    assert data["wiki_sources"] == 3
    assert data["messages"] == 0
    assert data["conversations"] == 0
    assert data["providers"] >= 1
    assert data["models"] >= 1
    assert data["bot_channels"] >= 1
    assert data["db_size_bytes"] > 0
    # 旧 RAG 字段已移除
    assert "chunks" not in data


def test_settings_defaults_are_wiki_based(client):
    data = client.get("/api/settings").json()

    assert data["counselor_name"] == "小南"
    assert isinstance(data["quick_questions"], list) and data["quick_questions"]
    assert data["wiki_max_pages"] == 3
    assert data["wiki_answer_max_chars"] == 9000
    assert data["citations_enabled"] is True
    assert data["system_prompt"].strip()
    assert data["welcome_message"].strip()
    assert data["bot_reply_footer"].strip()
    # 旧检索参数已移除
    assert "retrieval_top_k" not in data
    assert "retrieval_min_score" not in data


def test_settings_partial_update_persists(client):
    response = client.put(
        "/api/settings",
        json={
            "counselor_name": "测试老师",
            "wiki_max_pages": 4,
            "wiki_answer_max_chars": 8000,
            "citations_enabled": False,
            "quick_questions": ["问题A？", "问题B？"],
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["counselor_name"] == "测试老师"
    assert updated["wiki_max_pages"] == 4
    assert updated["wiki_answer_max_chars"] == 8000
    assert updated["citations_enabled"] is False
    assert updated["quick_questions"] == ["问题A？", "问题B？"]

    fresh = client.get("/api/settings").json()
    assert fresh["counselor_name"] == "测试老师"
    assert fresh["wiki_max_pages"] == 4
    assert fresh["welcome_message"].strip()


def test_settings_reject_invalid_values(client):
    assert client.put("/api/settings", json={"unknown_key": "x"}).status_code == 400
    assert client.put("/api/settings", json={"quick_questions": "不是数组"}).status_code == 400
    assert client.put("/api/settings", json={"counselor_name": 123}).status_code == 400
    assert client.put("/api/settings", json={"wiki_max_pages": "abc"}).status_code == 400
    assert client.put("/api/settings", json={"wiki_max_pages": 0}).status_code == 400
    assert client.put("/api/settings", json={"wiki_answer_max_chars": "abc"}).status_code == 400
    # 旧 RAG 设置项不再可编辑
    assert client.put("/api/settings", json={"retrieval_top_k": 4}).status_code == 400


def test_system_info_reflects_admin_token_requirement(client, monkeypatch):
    from app import config

    assert client.get("/api/system/info").json()["admin_token_required"] is False

    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-secret-token")
    assert client.get("/api/system/info").json()["admin_token_required"] is True
    # 只读接口与设置读取不受影响（写操作鉴权由 middleware 负责）
    assert client.get("/api/settings").status_code == 200


def test_admin_token_can_be_initialized_from_loopback(client, monkeypatch, tmp_path):
    token = "local-init-token-1234567890"
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    response = client.post("/api/system/admin-token/init", json={"token": token})
    assert response.status_code == 200
    assert response.json()["admin_token_required"] is True
    assert config.ADMIN_TOKEN == token
    assert (tmp_path / "admin_token.txt").read_text(encoding="utf-8").strip() == token

    # The token takes effect immediately for protected writes.
    assert client.post("/api/knowledge/import/text", json={"title": "x", "text": "y" * 60}).status_code == 401
    allowed = client.post(
        "/api/knowledge/import/text",
        json={"title": "x", "text": "y" * 60},
        headers={"X-Admin-Token": token},
    )
    assert allowed.status_code in {200, 201}


def test_admin_token_init_rejects_short_token(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    response = client.post("/api/system/admin-token/init", json={"token": "short"})
    assert response.status_code == 400
    assert "16" in response.json()["detail"]


def test_admin_token_can_be_rotated_with_current_token(
    client, monkeypatch, tmp_path
):
    """已启用后修改令牌：新令牌立即生效，旧令牌失效（此前只能改浏览器本地副本）。"""
    old_token = "old-admin-token-1234567890"
    new_token = "new-admin-token-abcdefghij"
    monkeypatch.setattr(config, "ADMIN_TOKEN", old_token)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    response = client.post(
        "/api/system/admin-token/init",
        json={"token": new_token},
        headers={"X-Admin-Token": old_token},
    )
    assert response.status_code == 200
    assert response.json()["rotated"] is True
    assert config.ADMIN_TOKEN == new_token
    assert (tmp_path / "admin_token.txt").read_text(encoding="utf-8").strip() == new_token

    # 旧令牌立即失效，新令牌可执行受保护写操作
    assert (
        client.post(
            "/api/knowledge/import/text",
            json={"title": "x", "text": "y" * 60},
            headers={"X-Admin-Token": old_token},
        ).status_code
        == 401
    )
    assert client.post(
        "/api/knowledge/import/text",
        json={"title": "x", "text": "y" * 60},
        headers={"X-Admin-Token": new_token},
    ).status_code in {200, 201}


def test_admin_token_rotation_from_remote_requires_current_token(
    client, monkeypatch, tmp_path
):
    """非本机请求轮换令牌时必须携带当前令牌，避免仅凭新值覆盖服务端。"""
    from app.routers import system as system_router

    old_token = "old-admin-token-1234567890"
    new_token = "new-admin-token-abcdefghij"
    monkeypatch.setattr(config, "ADMIN_TOKEN", old_token)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(system_router, "_is_loopback_client", lambda request: False)

    denied = client.post("/api/system/admin-token/init", json={"token": new_token})
    assert denied.status_code == 401
    assert config.ADMIN_TOKEN == old_token

    wrong = client.post(
        "/api/system/admin-token/init",
        json={"token": new_token},
        headers={"X-Admin-Token": "wrong-token"},
    )
    assert wrong.status_code == 401

    allowed = client.post(
        "/api/system/admin-token/init",
        json={"token": new_token},
        headers={"X-Admin-Token": old_token},
    )
    assert allowed.status_code == 200
    assert config.ADMIN_TOKEN == new_token


def test_admin_token_rotation_rejected_when_env_configured(
    client, monkeypatch, tmp_path
):
    """环境变量优先：UI 轮换会重启后失效，因此直接拒绝并提示改部署配置。"""
    monkeypatch.setenv("NPU_ADMIN_TOKEN", "env-admin-token-1234567890")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "env-admin-token-1234567890")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    response = client.post(
        "/api/system/admin-token/init",
        json={"token": "new-admin-token-abcdefghij"},
        headers={"X-Admin-Token": "env-admin-token-1234567890"},
    )
    assert response.status_code == 409
    assert "环境变量" in response.json()["detail"]

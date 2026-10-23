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

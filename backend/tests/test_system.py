"""系统接口测试：健康检查、运行信息、统计、设置读写（契约 §5.1）。"""
from __future__ import annotations


def test_health_returns_ok_and_default_mock_model(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "ok"
    assert data["app"] == "NPU-Assistant"
    assert data["version"]
    assert data["time"]

    model = data["default_model"]
    assert model is not None
    assert model["provider_type"] == "mock"
    assert model["display_name"]
    assert model["id"] and model["model_name"]


def test_system_info_exposes_runtime_configuration(client):
    data = client.get("/api/system/info").json()

    for key in (
        "app",
        "version",
        "data_dir",
        "upload_dir",
        "db_path",
        "frontend_ready",
        "supported_exts",
        "max_upload_mb",
        "admin_token_required",
        "auto_seed",
    ):
        assert key in data, f"缺少字段 {key}"

    assert data["app"] == "NPU-Assistant"
    assert ".md" in data["supported_exts"]
    assert ".pdf" in data["supported_exts"]
    assert ".docx" in data["supported_exts"]
    assert data["max_upload_mb"] == 20
    assert data["admin_token_required"] is False
    # 测试环境必须使用临时目录，避免污染开发库
    assert "npu_assistant_tests_" in data["data_dir"].replace("\\", "/")


def test_system_stats_counts_seeded_knowledge(client, seeded):
    data = client.get("/api/system/stats").json()

    assert data["documents"] >= 6
    assert data["ready_documents"] >= 6
    assert data["chunks"] > 0
    assert data["messages"] == 0
    assert data["conversations"] == 0
    assert data["providers"] >= 1
    assert data["models"] >= 1
    assert data["bot_channels"] >= 1
    assert data["db_size_bytes"] > 0


def test_settings_read_defaults(client):
    data = client.get("/api/settings").json()

    assert data["counselor_name"] == "小南"
    assert isinstance(data["quick_questions"], list)
    assert len(data["quick_questions"]) >= 1
    assert data["retrieval_top_k"] == 4
    assert 0 <= float(data["retrieval_min_score"]) <= 1
    assert data["citations_enabled"] is True
    assert data["system_prompt"].strip()
    assert data["welcome_message"].strip()
    assert data["bot_reply_footer"].strip()


def test_settings_partial_update_persists(client):
    payload = {
        "counselor_name": "测试老师",
        "retrieval_top_k": 3,
        "retrieval_min_score": 0.2,
        "citations_enabled": False,
        "quick_questions": ["问题A？", "问题B？"],
    }
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    updated = response.json()
    assert updated["counselor_name"] == "测试老师"
    assert updated["retrieval_top_k"] == 3
    assert updated["retrieval_min_score"] == 0.2
    assert updated["citations_enabled"] is False
    assert updated["quick_questions"] == ["问题A？", "问题B？"]

    # 再次读取确认已持久化，且局部更新不会覆盖未提交字段
    fresh = client.get("/api/settings").json()
    assert fresh["counselor_name"] == "测试老师"
    assert fresh["retrieval_top_k"] == 3
    assert fresh["welcome_message"].strip()


def test_settings_rejects_unknown_and_malformed_values(client):
    assert client.put("/api/settings", json={"unknown_key": "x"}).status_code == 400
    assert (
        client.put("/api/settings", json={"quick_questions": "不是数组"}).status_code == 400
    )
    assert client.put("/api/settings", json={"counselor_name": 123}).status_code == 400

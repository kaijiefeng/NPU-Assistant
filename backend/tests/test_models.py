"""模型管理测试：Provider / 模型 CRUD、Key 脱敏、默认模型切换与回退、连通性测试（契约 §5.4）。"""
from __future__ import annotations


def _providers(client) -> list[dict]:
    return client.get("/api/models/providers").json()["items"]


def _mock_provider(client) -> dict:
    return next(item for item in _providers(client) if item["type"] == "mock")


def test_bootstrap_provider_and_default_model(client):
    providers = _providers(client)
    assert providers
    mock = _mock_provider(client)
    assert mock["name"]
    assert mock["model_count"] >= 1
    assert "api_key" not in mock  # 只返回脱敏字段
    assert mock["api_key_masked"] == ""

    models = client.get("/api/models").json()["items"]
    assert models
    defaults = [item for item in models if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["provider_type"] == "mock"
    assert defaults[0]["provider_name"] == mock["name"]


def test_provider_crud_masks_key_and_validates(client):
    created = client.post(
        "/api/models/providers",
        json={
            "name": "测试 OpenAI",
            "type": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test-1234567890abcd",
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()
    assert provider["type"] == "openai"
    assert provider["base_url"] == "https://api.example.com/v1"
    assert provider["api_key_masked"] == "sk-t****abcd"
    assert "api_key" not in provider

    # api_key 传空串表示不修改，name 可局部更新
    updated = client.put(
        f"/api/models/providers/{provider['id']}",
        json={"name": "改名后的 Provider", "api_key": ""},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "改名后的 Provider"
    assert updated.json()["api_key_masked"] == "sk-t****abcd"

    # 非法类型 / 缺 base_url / 缺 api_key 均 400
    assert (
        client.post("/api/models/providers", json={"name": "x", "type": "anthropic"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/models/providers",
            json={"name": "缺地址", "type": "openai", "api_key": "sk-1"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/models/providers",
            json={"name": "缺Key", "type": "openai", "base_url": "https://x.example/v1"},
        ).status_code
        == 400
    )

    deleted = client.delete(f"/api/models/providers/{provider['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert all(item["id"] != provider["id"] for item in _providers(client))
    assert client.put("/api/models/providers/not-exist", json={"name": "x"}).status_code == 404


def test_provider_test_endpoint_returns_structured_result(client):
    mock = _mock_provider(client)
    tested = client.post(f"/api/models/providers/{mock['id']}/test")
    assert tested.status_code == 200
    data = tested.json()
    assert data["ok"] is True
    assert isinstance(data["latency_ms"], int)
    assert isinstance(data["models"], list) and data["models"]

    assert client.post("/api/models/providers/not-exist/test").status_code == 404


def test_model_crud_default_switch_and_fallback(client):
    provider = _mock_provider(client)
    created = client.post(
        "/api/models",
        json={
            "provider_id": provider["id"],
            "model_name": "test-model",
            "display_name": "测试模型",
            "temperature": 0.5,
            "max_tokens": 800,
        },
    )
    assert created.status_code == 201, created.text
    model = created.json()
    assert model["display_name"] == "测试模型"
    assert model["is_default"] is False
    assert model["enabled"] is True
    assert model["temperature"] == 0.5
    assert model["max_tokens"] == 800
    model_id = model["id"]

    # 切换默认模型：全表只允许一个默认，/health 同步体现
    switched = client.post(f"/api/models/{model_id}/default")
    assert switched.status_code == 200
    assert switched.json()["is_default"] is True

    models = client.get("/api/models").json()["items"]
    defaults = [item for item in models if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == model_id
    assert client.get("/api/health").json()["default_model"]["id"] == model_id

    # 测试模型（mock 直接可用）
    tested = client.post(f"/api/models/{model_id}/test").json()
    assert tested["ok"] is True
    assert tested["sample"].strip()

    # 停用默认模型 → 自动回退到其它启用模型
    updated = client.put(f"/api/models/{model_id}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    health = client.get("/api/health").json()
    assert health["default_model"] is not None
    assert health["default_model"]["id"] != model_id

    # 删除模型后默认模型仍存在且唯一
    assert client.delete(f"/api/models/{model_id}").json() == {"ok": True}
    models = client.get("/api/models").json()["items"]
    defaults = [item for item in models if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["provider_type"] == "mock"


def test_model_validation_and_missing_provider(client):
    provider = _mock_provider(client)
    assert (
        client.post(
            "/api/models", json={"provider_id": "not-exist", "model_name": "m"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/models", json={"provider_id": provider["id"], "model_name": "  "}
        ).status_code
        == 400
    )
    assert client.post("/api/models", json={"model_name": "m"}).status_code == 400
    assert client.delete("/api/models/providers/not-exist").status_code == 404
    assert client.post("/api/models/not-exist/default").status_code == 404
    assert client.post("/api/models/not-exist/test").status_code == 404

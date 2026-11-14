"""LLM Provider / 模型管理路由（backend-core）。

接口契约见 ``docs/CONTRACT.md`` §5.4：

* ``GET/POST/PUT/DELETE /api/models/providers[...]``
* ``POST /api/models/providers/{id}/test``
* ``GET/POST/PUT/DELETE /api/models[...]``
* ``POST /api/models/{id}/default``、``POST /api/models/{id}/test``

约定：``api_key`` 只在服务端使用，返回给前端时一律脱敏；写操作在启用
``NPU_ADMIN_TOKEN`` 时要求 ``X-Admin-Token`` 请求头（见 ``app/security.py``）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..security import require_admin_token
from ..services import llm

logger = logging.getLogger("npu-assistant")

router = APIRouter(tags=["models"])


# ---------------------------------------------------------------------------
# 校验工具
# ---------------------------------------------------------------------------
def _require_text(payload: dict, key: str, label: str) -> str:
    value = payload.get(key)
    text = str(value).strip() if value is not None else ""
    if not text:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    return text


def _optional_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}


def _parse_float(value: Any, default: float, label: str, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label} 必须是数字")
    if number < minimum or number > maximum:
        raise HTTPException(
            status_code=400, detail=f"{label} 必须在 {minimum} 到 {maximum} 之间"
        )
    return round(number, 3)


def _parse_int(value: Any, default: int, label: str, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{label} 必须是整数")
    if number < minimum or number > maximum:
        raise HTTPException(
            status_code=400, detail=f"{label} 必须在 {minimum} 到 {maximum} 之间"
        )
    return number


def _normalize_provider_type(value: Any, default: str = "") -> str:
    provider_type = str(value if value is not None else default).strip().lower()
    if provider_type not in llm.PROVIDER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 Provider 类型：{provider_type or '空'}（可选 openai/ollama/mock）",
        )
    return provider_type


def _clean_base_url(value: Any, *, required: bool = False) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        if required:
            raise HTTPException(status_code=400, detail="base_url 不能为空")
        return ""
    if not (text.startswith("http://") or text.startswith("https://")):
        raise HTTPException(
            status_code=400, detail="base_url 必须以 http:// 或 https:// 开头"
        )
    return text


def _validate_openai_config(provider_type: str, base_url: str, api_key: str) -> None:
    if provider_type != "openai":
        return
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail="OpenAI 兼容 Provider 需要填写 base_url（例如 https://api.openai.com/v1）",
        )
    if not api_key:
        raise HTTPException(
            status_code=400, detail="OpenAI 兼容 Provider 需要填写 api_key"
        )


# ---------------------------------------------------------------------------
# 序列化 & 默认模型维护
# ---------------------------------------------------------------------------
def _get_provider(provider_id: str) -> dict | None:
    return db.query_one(
        "SELECT p.*, (SELECT COUNT(*) FROM models_registry m WHERE m.provider_id = p.id) "
        "AS model_count FROM providers p WHERE p.id = ?",
        (provider_id,),
    )


def _get_model(model_id: str) -> dict | None:
    return db.query_one(
        "SELECT m.*, p.name AS provider_name, p.type AS provider_type, p.base_url, p.api_key "
        "FROM models_registry m JOIN providers p ON p.id = m.provider_id WHERE m.id = ?",
        (model_id,),
    )


def _provider_obj(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "base_url": row.get("base_url") or "",
        "api_key_masked": llm.mask_api_key(row.get("api_key")),
        "embedding_model": row.get("embedding_model") or "",
        "model_count": int(row.get("model_count") or 0),
        "created_at": row["created_at"],
    }


def _model_obj(row: dict) -> dict:
    return {
        "id": row["id"],
        "provider_id": row["provider_id"],
        "provider_name": row.get("provider_name") or "",
        "provider_type": row.get("provider_type") or "",
        "model_name": row["model_name"],
        "display_name": row.get("display_name") or row["model_name"],
        "temperature": float(row.get("temperature") if row.get("temperature") is not None else 0.3),
        "max_tokens": int(row.get("max_tokens") or 0),
        "is_default": bool(row.get("is_default")),
        "enabled": bool(row.get("enabled")),
        "created_at": row["created_at"],
    }


def _set_default(model_id: str) -> None:
    """事务内清除其它默认模型，并把目标模型设为默认。"""
    with db.connect() as conn:
        conn.execute(
            "UPDATE models_registry SET is_default = 0 WHERE is_default = 1 AND id <> ?",
            (model_id,),
        )
        conn.execute(
            "UPDATE models_registry SET is_default = 1 WHERE id = ?", (model_id,)
        )
        conn.commit()


def _ensure_default_model() -> str | None:
    """保证「至多一个默认模型」；没有默认时回退到最早创建的启用模型。"""
    rows = db.query(
        "SELECT id, is_default FROM models_registry WHERE enabled = 1 "
        "ORDER BY is_default DESC, created_at ASC, rowid ASC"
    )
    if not rows:
        db.execute("UPDATE models_registry SET is_default = 0 WHERE is_default = 1")
        return None
    chosen = rows[0]["id"]
    _set_default(chosen)
    return chosen


# ---------------------------------------------------------------------------
# Provider 管理
# ---------------------------------------------------------------------------
@router.get("/models/providers")
def list_providers() -> dict:
    """Provider 列表（含脱敏后的 api_key 与模型数量）。"""
    rows = db.query(
        "SELECT p.*, (SELECT COUNT(*) FROM models_registry m WHERE m.provider_id = p.id) "
        "AS model_count FROM providers p ORDER BY p.created_at ASC, p.rowid ASC"
    )
    return {"items": [_provider_obj(row) for row in rows]}


@router.post(
    "/models/providers",
    status_code=201,
    dependencies=[Depends(require_admin_token)],
)
def create_provider(payload: dict) -> dict:
    """新增 Provider；``openai`` 必须提供 base_url 与 api_key。"""
    name = _require_text(payload, "name", "Provider 名称")
    provider_type = _normalize_provider_type(payload.get("type"))
    base_url = _clean_base_url(payload.get("base_url"))
    api_key = str(payload.get("api_key") or "").strip()
    embedding_model = _optional_text(payload, "embedding_model")

    if provider_type == "openai":
        _validate_openai_config(provider_type, base_url, api_key)
    elif provider_type == "ollama" and not base_url:
        base_url = llm.DEFAULT_OLLAMA_BASE_URL
    elif provider_type == "mock":
        base_url = ""
        api_key = ""

    provider_id = db.new_id()
    db.execute(
        "INSERT INTO providers(id, name, type, base_url, api_key, embedding_model, extra, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, '{}', ?)",
        (provider_id, name, provider_type, base_url, api_key, embedding_model, db.now()),
    )
    return _provider_obj(_get_provider(provider_id))


@router.put(
    "/models/providers/{provider_id}",
    dependencies=[Depends(require_admin_token)],
)
def update_provider(provider_id: str, payload: dict) -> dict:
    """局部更新 Provider；``api_key`` 传空串表示不修改。"""
    provider = db.query_one("SELECT * FROM providers WHERE id = ?", (provider_id,))
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")

    provider_type = _normalize_provider_type(
        payload.get("type") if payload.get("type") is not None else provider["type"]
    )

    name = provider["name"]
    if "name" in payload and payload.get("name") is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Provider 名称不能为空")

    base_url = provider.get("base_url") or ""
    if "base_url" in payload:
        base_url = _clean_base_url(payload.get("base_url"))

    api_key = provider.get("api_key") or ""
    if "api_key" in payload:
        new_key = str(payload.get("api_key") or "").strip()
        if new_key:  # 空串表示不修改
            api_key = new_key

    embedding_model = provider.get("embedding_model") or ""
    if "embedding_model" in payload:
        embedding_model = _optional_text(payload, "embedding_model")

    if provider_type == "openai":
        _validate_openai_config(provider_type, base_url, api_key)
    elif provider_type == "ollama" and not base_url:
        base_url = llm.DEFAULT_OLLAMA_BASE_URL
    elif provider_type == "mock":
        base_url = ""
        api_key = ""

    db.execute(
        "UPDATE providers SET name = ?, type = ?, base_url = ?, api_key = ?, embedding_model = ? "
        "WHERE id = ?",
        (name, provider_type, base_url, api_key, embedding_model, provider_id),
    )
    llm.reset_circuit()  # 凭据/地址改过后，旧的熔断状态不应继续拦截
    return _provider_obj(_get_provider(provider_id))


@router.delete(
    "/models/providers/{provider_id}",
    dependencies=[Depends(require_admin_token)],
)
def delete_provider(provider_id: str) -> dict:
    """删除 Provider 及其下所有模型（契约允许删除 mock Provider）。"""
    if db.query_one("SELECT id FROM providers WHERE id = ?", (provider_id,)) is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    with db.connect() as conn:
        conn.execute("DELETE FROM models_registry WHERE provider_id = ?", (provider_id,))
        conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        conn.commit()
    _ensure_default_model()
    return {"ok": True}


@router.post("/models/providers/{provider_id}/test")
def test_provider(provider_id: str) -> dict:
    """测试 Provider 连通性；失败也返回 HTTP 200 + ``ok=false`` 的可读信息。"""
    if db.query_one("SELECT id FROM providers WHERE id = ?", (provider_id,)) is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    result = llm.test_provider(provider_id)
    if result.get("ok"):
        llm.reset_circuit()
    return result


# ---------------------------------------------------------------------------
# 模型管理
# ---------------------------------------------------------------------------
@router.get("/models")
def list_models() -> dict:
    """模型列表（默认模型排在最前）。"""
    rows = db.query(
        "SELECT m.*, p.name AS provider_name, p.type AS provider_type "
        "FROM models_registry m JOIN providers p ON p.id = m.provider_id "
        "ORDER BY m.is_default DESC, m.created_at ASC, m.rowid ASC"
    )
    return {"items": [_model_obj(row) for row in rows]}


@router.post("/models", status_code=201, dependencies=[Depends(require_admin_token)])
def create_model(payload: dict) -> dict:
    """在指定 Provider 下新增模型。"""
    provider_id = str(payload.get("provider_id") or "").strip()
    if not provider_id:
        raise HTTPException(status_code=400, detail="请选择 Provider（provider_id 不能为空）")
    if db.query_one("SELECT id FROM providers WHERE id = ?", (provider_id,)) is None:
        raise HTTPException(status_code=400, detail="Provider 不存在")

    model_name = _require_text(payload, "model_name", "模型名称")
    display_name = _optional_text(payload, "display_name") or model_name
    temperature = _parse_float(payload.get("temperature"), 0.3, "temperature", 0.0, 2.0)
    max_tokens = _parse_int(payload.get("max_tokens"), 1200, "max_tokens", 1, 32768)
    is_default = _as_bool(payload.get("is_default"), False)

    model_id = db.new_id()
    db.execute(
        "INSERT INTO models_registry(id, provider_id, model_name, display_name, temperature, "
        "max_tokens, is_default, enabled, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (
            model_id,
            provider_id,
            model_name,
            display_name,
            temperature,
            max_tokens,
            1 if is_default else 0,
            db.now(),
        ),
    )
    if is_default:
        _set_default(model_id)
    else:
        _ensure_default_model()
    return _model_obj(_get_model(model_id))  # type: ignore[arg-type]


@router.put("/models/{model_id}", dependencies=[Depends(require_admin_token)])
def update_model(model_id: str, payload: dict) -> dict:
    """局部更新模型配置。"""
    model = _get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="模型不存在")

    fields: list[str] = []
    params: list[Any] = []

    model_name = model["model_name"]
    if "model_name" in payload and payload.get("model_name") is not None:
        model_name = str(payload["model_name"]).strip()
        if not model_name:
            raise HTTPException(status_code=400, detail="模型名称不能为空")
        fields.append("model_name = ?")
        params.append(model_name)

    if "display_name" in payload:
        display_name = _optional_text(payload, "display_name") or model_name
        fields.append("display_name = ?")
        params.append(display_name)

    if "temperature" in payload:
        fields.append("temperature = ?")
        params.append(_parse_float(payload.get("temperature"), 0.3, "temperature", 0.0, 2.0))

    if "provider_id" in payload and payload.get("provider_id") is not None:
        provider_id = str(payload.get("provider_id")).strip()
        if provider_id and db.query_one("SELECT id FROM providers WHERE id = ?", (provider_id,)) is None:
            raise HTTPException(status_code=400, detail="Provider 不存在")
        fields.append("provider_id = ?")
        params.append(provider_id or None)

    if "max_tokens" in payload:
        fields.append("max_tokens = ?")
        params.append(_parse_int(payload.get("max_tokens"), 1200, "max_tokens", 1, 32768))

    if "enabled" in payload:
        fields.append("enabled = ?")
        params.append(1 if _as_bool(payload.get("enabled"), True) else 0)

    if "is_default" in payload:
        fields.append("is_default = ?")
        params.append(1 if _as_bool(payload.get("is_default"), False) else 0)

    if fields:
        db.execute(
            f"UPDATE models_registry SET {', '.join(fields)} WHERE id = ?", params + [model_id]
        )

    if "is_default" in payload and _as_bool(payload.get("is_default"), False):
        _set_default(model_id)
    elif "enabled" in payload or "is_default" in payload:
        _ensure_default_model()

    return _model_obj(_get_model(model_id))  # type: ignore[arg-type]


@router.delete("/models/{model_id}", dependencies=[Depends(require_admin_token)])
def delete_model(model_id: str) -> dict:
    """删除模型；若删除的是默认模型，自动回退到最早创建的启用模型。"""
    if db.query_one("SELECT id FROM models_registry WHERE id = ?", (model_id,)) is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    db.execute("DELETE FROM models_registry WHERE id = ?", (model_id,))
    _ensure_default_model()
    return {"ok": True}


@router.post("/models/{model_id}/default", dependencies=[Depends(require_admin_token)])
def set_default_model(model_id: str) -> dict:
    """设为默认模型（事务内清除其它默认标记）。"""
    row = db.query_one(
        "SELECT id, enabled FROM models_registry WHERE id = ?", (model_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    if not int(row.get("enabled") or 0):
        raise HTTPException(status_code=400, detail="该模型已停用，请先启用再设为默认")
    _set_default(model_id)
    return _model_obj(_get_model(model_id))  # type: ignore[arg-type]


@router.post("/models/{model_id}/test")
def test_model(model_id: str) -> dict:
    """真实发送一条「你好」消息测试模型可用性。"""
    if db.query_one("SELECT id FROM models_registry WHERE id = ?", (model_id,)) is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    return llm.test_model(model_id)

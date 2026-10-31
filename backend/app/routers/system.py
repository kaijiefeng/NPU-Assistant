"""System routes: health, statistics and agent settings (Lead-owned)."""
from __future__ import annotations

import ipaddress
import json
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config, db
from ..security import ADMIN_TOKEN_HEADER, verify_admin_token

router = APIRouter(tags=["system"])


class AdminTokenInit(BaseModel):
    token: str


def _is_loopback_client(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "testclient", "testserver"}

EDITABLE_SETTINGS = {
    "counselor_name",
    "system_prompt",
    "welcome_message",
    "quick_questions",
    "wiki_max_pages",
    "wiki_answer_max_chars",
    "citations_enabled",
    "bot_reply_footer",
}

INT_SETTINGS = ("wiki_max_pages", "wiki_answer_max_chars")


def current_settings() -> dict:
    settings = db.all_settings()
    try:
        settings["quick_questions"] = json.loads(settings.get("quick_questions", "[]"))
    except json.JSONDecodeError:
        settings["quick_questions"] = []
    for key in INT_SETTINGS:
        try:
            settings[key] = int(settings.get(key, 0))
        except (TypeError, ValueError):
            settings[key] = 0
    settings["citations_enabled"] = db.get_bool_setting("citations_enabled", True)
    return settings


@router.get("/health")
def health() -> dict:
    model = db.get_default_model()
    return {
        "status": "ok",
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "time": datetime.now().isoformat(timespec="seconds"),
        "knowledge_mode": "llm-wiki",
        "wiki_pages": db.wiki_page_count(),
        "default_model": (
            {
                "id": model["id"],
                "display_name": model["display_name"],
                "model_name": model["model_name"],
                "provider_type": model["provider_type"],
            }
            if model
            else None
        ),
    }


@router.post("/system/admin-token/init")
def init_admin_token(payload: AdminTokenInit, request: Request) -> dict:
    """首次启用或轮换服务端管理令牌。

    * 未启用时：仅允许本机请求初始化；
    * 已启用时：允许本机请求，或携带「当前」``X-Admin-Token`` 的请求轮换
      （避免只能在浏览器保存新令牌、服务端仍校验旧令牌）；
    * 令牌持久化到 ``backend/data/admin_token.txt``（gitignored）以便重启后生效；
      显式设置 ``NPU_ADMIN_TOKEN`` 环境变量时始终以环境变量为准，不允许在 UI 轮换。
    """
    if os.environ.get("NPU_ADMIN_TOKEN", "").strip():
        raise HTTPException(
            status_code=409,
            detail="管理令牌由环境变量 NPU_ADMIN_TOKEN 控制，请在部署配置中修改并重启服务",
        )
    token = (payload.token or "").strip()
    if len(token) < 16:
        raise HTTPException(status_code=400, detail="管理令牌至少 16 位，建议 24 位以上随机串")

    loopback = _is_loopback_client(request)
    rotated = bool(config.ADMIN_TOKEN)
    if rotated and not loopback:
        # 已启用时，远程轮换必须证明自己知道当前令牌
        verify_admin_token(request.headers.get(ADMIN_TOKEN_HEADER))
    elif not rotated and not loopback:
        raise HTTPException(status_code=403, detail="只能在本机初始化管理令牌")

    config.ADMIN_TOKEN = token
    path = config.DATA_DIR / "admin_token.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"令牌已生效，但写入本地文件失败：{exc}") from exc
    return {
        "ok": True,
        "admin_token_required": True,
        "rotated": rotated,
        "persisted": str(path.name),
    }


@router.get("/system/info")
def system_info() -> dict:
    # Deliberately no absolute filesystem paths here: this endpoint is public
    # (the SPA needs it before login) and must not leak deployment layout.
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "knowledge_mode": "llm-wiki",
        "wiki_seed_available": config.WIKI_SEED_PATH.is_file(),
        "frontend_ready": (config.FRONTEND_DIR / "index.html").is_file(),
        "supported_exts": sorted(config.SUPPORTED_EXTS),
        "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
        "url_import_max_mb": round(config.URL_IMPORT_MAX_BYTES / (1024 * 1024), 1),
        "url_import_max_urls": config.URL_IMPORT_MAX_URLS,
        "admin_token_required": bool(config.ADMIN_TOKEN),
        "auto_seed": config.AUTO_SEED,
        "cors_enabled": bool(config.CORS_ORIGINS),
        "insecure_bind": (config.HOST or "").strip().lower()
        not in {"127.0.0.1", "localhost", "::1"}
        and not bool(config.ADMIN_TOKEN),
    }


@router.get("/system/stats")
def system_stats() -> dict:
    def count(table: str, where: str = "") -> int:
        row = db.query_one(f"SELECT COUNT(*) AS n FROM {table} {where}")
        return int(row["n"]) if row else 0

    db_size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
    return {
        "documents": count("documents"),
        "ready_documents": count("documents", "WHERE status = 'ready'"),
        "wiki_pages": count("wiki_pages"),
        "wiki_sources": count("wiki_sources"),
        "messages": count("messages"),
        "conversations": count("conversations"),
        "providers": count("providers"),
        "models": count("models_registry"),
        "bot_channels": count("bot_channels"),
        "db_size_bytes": db_size,
    }


@router.get("/settings")
def read_settings() -> dict:
    return current_settings()


@router.put("/settings")
def update_settings(payload: dict) -> dict:
    unknown = set(payload) - EDITABLE_SETTINGS
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的设置项：{', '.join(sorted(unknown))}")
    for key, value in payload.items():
        if key == "quick_questions":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise HTTPException(status_code=400, detail="quick_questions 必须是字符串数组")
            value = json.dumps(value, ensure_ascii=False)
        elif key == "citations_enabled":
            value = "true" if value else "false"
        elif key in INT_SETTINGS:
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} 必须是整数")
            if number <= 0:
                raise HTTPException(status_code=400, detail=f"{key} 必须大于 0")
            value = str(number)
        elif not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{key} 必须是字符串")
        db.set_setting(key, value)
    return current_settings()

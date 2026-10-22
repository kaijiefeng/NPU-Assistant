"""System routes: health, statistics and agent settings (Lead-owned)."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException

from .. import config, db

router = APIRouter(tags=["system"])

EDITABLE_SETTINGS = {
    "counselor_name",
    "system_prompt",
    "welcome_message",
    "quick_questions",
    "retrieval_top_k",
    "retrieval_min_score",
    "citations_enabled",
    "bot_reply_footer",
}


def current_settings() -> dict:
    settings = db.all_settings()
    try:
        settings["quick_questions"] = json.loads(settings.get("quick_questions", "[]"))
    except json.JSONDecodeError:
        settings["quick_questions"] = []
    for key in ("retrieval_top_k",):
        try:
            settings[key] = int(settings.get(key, config.DEFAULT_TOP_K))
        except (TypeError, ValueError):
            settings[key] = config.DEFAULT_TOP_K
    try:
        settings["retrieval_min_score"] = float(settings.get("retrieval_min_score", 0))
    except (TypeError, ValueError):
        settings["retrieval_min_score"] = 0.0
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


@router.get("/system/info")
def system_info() -> dict:
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "data_dir": str(config.DATA_DIR),
        "upload_dir": str(config.UPLOAD_DIR),
        "db_path": str(config.DB_PATH),
        "frontend_ready": (config.FRONTEND_DIR / "index.html").is_file(),
        "supported_exts": sorted(config.SUPPORTED_EXTS),
        "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
        "admin_token_required": bool(config.ADMIN_TOKEN),
        "auto_seed": config.AUTO_SEED,
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
        "chunks": count("chunks"),
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
        elif key in {"retrieval_top_k", "retrieval_min_score"}:
            value = str(value)
        elif not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{key} 必须是字符串")
        db.set_setting(key, value)
    return current_settings()

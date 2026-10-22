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


@router.get("/system/info")
def system_info() -> dict:
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "knowledge_mode": "llm-wiki",
        "data_dir": str(config.DATA_DIR),
        "upload_dir": str(config.UPLOAD_DIR),
        "db_path": str(config.DB_PATH),
        "wiki_seed_path": str(config.WIKI_SEED_PATH),
        "wiki_seed_available": config.WIKI_SEED_PATH.is_file(),
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

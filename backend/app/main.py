"""FastAPI application entrypoint.

The app is assembled here (Lead-owned file).  Each feature area lives in its own
router module and is mounted under ``/api``:

* ``system``    – health, stats, settings          (Lead)
* ``chat``      – counselor chat + history         (backend-core)
* ``knowledge`` – LLM Wiki upload / search          (kb-rag)
* ``models``    – LLM providers & model registry    (backend-core)
* ``bots``      – WeChat / QQ bot gateway           (bot-gateway)
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request

from . import config, db
from .routers import bots, chat, knowledge, models, system

logger = logging.getLogger("npu-assistant")

API_PREFIX = "/api"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    if config.AUTO_SEED:
        try:
            from .services.ingest import seed_documents

            result = seed_documents()
            if result.get("ingested"):
                logger.info("已自动导入示例知识库文档：%s", result["ingested"])
        except Exception:  # pragma: no cover - optional convenience
            logger.exception("示例知识库自动导入失败（已忽略，可手动触发 /api/knowledge/seed）")
    logger.info("NPU-Assistant 已启动 | data=%s", config.DATA_DIR)
    yield


def create_app() -> FastAPI:
    config.ensure_dirs()
    db.init_db()
    app = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        description=config.APP_DESCRIPTION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(knowledge.router, prefix=API_PREFIX)
    app.include_router(models.router, prefix=API_PREFIX)
    app.include_router(bots.router, prefix=API_PREFIX)

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        """Serve the single-page frontend, falling back to index.html."""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        index = config.FRONTEND_DIR / "index.html"
        target = (config.FRONTEND_DIR / (full_path or "index.html")).resolve()
        try:
            target.relative_to(config.FRONTEND_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found")

        if target.is_file():
            media_type, _ = mimetypes.guess_type(str(target))
            return FileResponse(target, media_type=media_type)
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return JSONResponse(
            status_code=503,
            content={"detail": "前端资源尚未就绪：请确认 frontend/index.html 存在"},
        )

    return app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

app = create_app()

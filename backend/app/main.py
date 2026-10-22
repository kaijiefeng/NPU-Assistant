"""FastAPI application entrypoint (LLM Wiki edition).

The app is assembled here (Lead-owned file).  Each feature area lives in its own
router module and is mounted under ``/api``:

* ``system``    – health, stats, settings          (Lead)
* ``chat``      – counselor chat + history         (backend-core)
* ``knowledge`` – source documents (upload/parse)   (kb-rag)
* ``wiki``      – compiled wiki pages + navigation  (wiki-core)
* ``models``    – LLM providers & model registry    (backend-core)
* ``bots``      – WeChat / QQ bot gateway           (bot-gateway)
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request

from . import config, db
from .routers import bots, chat, knowledge, models, system, wiki
from .security import verify_admin_token

logger = logging.getLogger("npu-assistant")

API_PREFIX = "/api"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Student-facing Q&A stays public; bot webhooks authenticate with their own
# channel tokens; every other write requires the admin token when configured.
PUBLIC_POST_PATHS = {"/api/chat"}
BOT_WEBHOOK_PREFIXES = ("/api/bots/onebot/", "/api/bots/wechat/")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    if config.AUTO_SEED and db.wiki_page_count() == 0:
        try:
            from .services.wiki_builder import load_seed_wiki

            result = load_seed_wiki()
            if result.get("imported"):
                logger.info(
                    "已导入预编译 Wiki 快照：%s 个词条 / %s 个来源",
                    result.get("pages"),
                    result.get("sources"),
                )
        except Exception:  # pragma: no cover - optional convenience
            logger.exception("预编译 Wiki 快照导入失败（已忽略，可在知识库页手动导入）")
    logger.info("NPU-Assistant 已启动 | data=%s", config.DATA_DIR)
    yield


def create_app() -> FastAPI:
    config.ensure_dirs()
    db.init_db()
    mimetypes.add_type("application/manifest+json", ".webmanifest")
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

    @app.middleware("http")
    async def admin_guard(request: Request, call_next):
        """Protect all admin/write APIs when NPU_ADMIN_TOKEN is configured.

        Public: GET/HEAD/OPTIONS, student Q&A (POST /api/chat), bot webhooks
        (they use their own channel token / signature). Everything else
        (uploads, imports, wiki edits, model/bot management, settings, rebuild)
        requires ``X-Admin-Token``.
        """
        if config.ADMIN_TOKEN and request.method not in SAFE_METHODS:
            path = request.url.path
            if path not in PUBLIC_POST_PATHS and not path.startswith(BOT_WEBHOOK_PREFIXES):
                try:
                    verify_admin_token(request.headers.get("X-Admin-Token"))
                except HTTPException as exc:
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(knowledge.router, prefix=API_PREFIX)
    app.include_router(wiki.router, prefix=API_PREFIX)
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
# httpx INFO logs full request URLs (including sensitive query strings) — keep
# them at WARNING so imported links / provider keys never leak into logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = create_app()

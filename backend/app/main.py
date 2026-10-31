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
import re
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request

from . import config, db
from .routers import bots, chat, knowledge, mobile, models, system, wiki
from .security import verify_admin_token
from .services import qq_gateway

logger = logging.getLogger("npu-assistant")

API_PREFIX = "/api"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Student-facing Q&A stays public; bot webhooks authenticate with their own
# channel tokens; every other write requires the admin token when configured.
PUBLIC_POST_PATHS = {"/api/chat"}
BOT_WEBHOOK_PREFIXES = ("/api/bots/onebot/", "/api/bots/wechat/")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
# Requests carrying payloads get a hard Content-Length cap (defense in depth;
# reverse proxies should also limit body size).
BODY_LIMITS = {
    "/api/chat": 1 * 1024 * 1024,
    "/api/bots/onebot": 2 * 1024 * 1024,
    "/api/bots/wechat": 2 * 1024 * 1024,
}
DEFAULT_BODY_LIMIT = config.MAX_UPLOAD_BYTES + 2 * 1024 * 1024
CHAT_RATE_LIMIT = 30  # requests per minute per client IP
_chat_hits: dict[str, list[float]] = {}
_chat_hits_lock = threading.Lock()
INTERNAL_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
}


def _body_limit_for(path: str) -> int:
    for prefix, limit in BODY_LIMITS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return limit
    return DEFAULT_BODY_LIMIT


def _chat_rate_limited(client_host: str | None) -> bool:
    key = client_host or "unknown"
    if key in {"testclient", "testserver"}:  # TestClient / pytest
        return False
    now = time.monotonic()
    with _chat_hits_lock:
        hits = [t for t in _chat_hits.get(key, []) if now - t < 60]
        if len(hits) >= CHAT_RATE_LIMIT:
            _chat_hits[key] = hits
            return True
        hits.append(now)
        _chat_hits[key] = hits
        return False


def _check_bind_security() -> None:
    """Refuse to expose an unauthenticated instance on a non-loopback address."""
    host = (config.HOST or "").strip().lower()
    if host in LOOPBACK_HOSTS:
        return
    if config.ADMIN_TOKEN:
        return
    if config.ALLOW_INSECURE_LAN:
        logger.warning(
            "对外监听 %s 且未设置 NPU_ADMIN_TOKEN（NPU_ALLOW_INSECURE_LAN=1）："
            "仅限可信内网临时演示，任何能访问该地址的人都可以管理知识库。",
            host,
        )
        return
    raise RuntimeError(
        "检测到对外监听（NPU_HOST=%s）但未设置 NPU_ADMIN_TOKEN："
        "为防止知识库被任意访问/篡改，已拒绝启动。请设置 NPU_ADMIN_TOKEN（推荐 ≥24 位随机串）"
        "后重启；仅可信内网临时演示可设置 NPU_ALLOW_INSECURE_LAN=1。" % host
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _check_bind_security()
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
    # QQ 开放平台官方机器人：主动建立 WebSocket 网关连接（无需公网回调）。
    await qq_gateway.gateway.startup()
    try:
        yield
    finally:
        await qq_gateway.gateway.shutdown()


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
    # Same-origin SPA needs no CORS. Only enable it for explicitly configured
    # origins (never "*": that would let any website drive the local API).
    if "*" in config.CORS_ORIGINS:
        raise RuntimeError("NPU_CORS_ORIGINS 不允许使用 *；请填写明确的来源白名单")
    if config.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.CORS_ORIGINS,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["X-Admin-Token", "Content-Type"],
        )
        logger.info("已启用 CORS 白名单：%s", config.CORS_ORIGINS)

    @app.middleware("http")
    async def admin_guard(request: Request, call_next):
        """Protect all admin/write APIs when NPU_ADMIN_TOKEN is configured.

        Public: GET/HEAD/OPTIONS, student Q&A (POST /api/chat), bot webhooks
        (they use their own channel token / signature). Everything else
        (uploads, imports, wiki edits, model/bot management, settings, rebuild)
        requires ``X-Admin-Token``.

        When the instance is reachable through a non-loopback Host header and no
        token is configured, fail closed (protects against uvicorn ``--host``
        overrides that bypass the startup check).
        """
        path = request.url.path
        host = (request.url.hostname or "").strip().lower().strip("[]")
        if (
            not config.ADMIN_TOKEN
            and not config.ALLOW_INSECURE_LAN
            and host
            and host not in LOOPBACK_HOSTS
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "该服务仅允许本机访问；对外提供访问前请设置 NPU_ADMIN_TOKEN 后重启"
                },
            )
        if config.ADMIN_TOKEN and request.method not in SAFE_METHODS:
            if path not in PUBLIC_POST_PATHS and not path.startswith(BOT_WEBHOOK_PREFIXES):
                try:
                    verify_admin_token(request.headers.get("X-Admin-Token"))
                except HTTPException as exc:
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        length = (request.headers.get("content-length") or "").strip()
        if length.isdigit() and int(length) > _body_limit_for(path):
            return JSONResponse(
                status_code=413, content={"detail": "请求内容过大，已拒绝（请缩小上传/请求体）"}
            )
        if path == "/api/chat" and request.method == "POST" and _chat_rate_limited(
            request.client.host if request.client else None
        ):
            return JSONResponse(
                status_code=429, content={"detail": "提问过于频繁，请稍后再试"}
            )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(knowledge.router, prefix=API_PREFIX)
    app.include_router(wiki.router, prefix=API_PREFIX)
    app.include_router(models.router, prefix=API_PREFIX)
    app.include_router(bots.router, prefix=API_PREFIX)
    app.include_router(mobile.router, prefix=API_PREFIX)

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, exc: ValueError):
        # Business errors (importer/ingest) raise ValueError with a user-facing
        # Chinese message; strip filesystem paths and cap the length so internal
        # details never leak through the public API.
        message = INTERNAL_PATH_RE.sub("<path>", str(exc))[:300]
        return JSONResponse(status_code=400, content={"detail": message})

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        """Serve the single-page frontend, falling back to index.html."""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        index = config.FRONTEND_DIR / "index.html"
        target = (config.FRONTEND_DIR / (full_path or "index.html")).resolve()
        try:
            target.relative_to(config.FRONTEND_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found")

        if target.is_file():
            media_type, _ = mimetypes.guess_type(str(target))
            headers = {}
            if target.name in {"index.html", "service-worker.js", "manifest.webmanifest"}:
                headers["Cache-Control"] = "no-cache, must-revalidate"
            return FileResponse(target, media_type=media_type, headers=headers)
        if index.is_file():
            return FileResponse(
                index, media_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"}
            )
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

"""Authentication / authorization helpers.

Authentication sources (in order):

1. HttpOnly session cookie ``npu_admin_session`` (browser login, v0.2.0 M3);
2. legacy ``X-Admin-Token`` header (maps to ``super_admin``);
3. open mode — no accounts and no token configured (local demo only).

``require_admin_token`` keeps its historical name so existing routers work, but
now also accepts a logged-in session.  New code should use
``require_permission("content:write")`` when a specific role matters.

The global HTTP guard in ``main.py`` uses :func:`authenticate_request` for every
mutating request, which also enforces CSRF for cookie sessions.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from . import config
from .services import auth

ADMIN_TOKEN_HEADER = "X-Admin-Token"
CSRF_HEADER = "X-CSRF-Token"
OPEN_PRINCIPAL = {
    "user_id": "",
    "username": "local",
    "display_name": "本机演示",
    "role": "super_admin",
    "auth": "open",
    "csrf_token": "",
}


def admin_token_required() -> bool:
    """当前部署是否启用了管理员令牌。"""
    return bool(config.ADMIN_TOKEN)


def verify_admin_token(token: str | None) -> None:
    """严格校验 ``X-Admin-Token``；未配置令牌时直接放行。"""
    expected = config.ADMIN_TOKEN
    if not expected:
        return
    if not token:
        raise HTTPException(
            status_code=401,
            detail=f"该操作需要管理员令牌：请在请求头 {ADMIN_TOKEN_HEADER} 中提供",
        )
    if not secrets.compare_digest(str(token), expected):
        raise HTTPException(
            status_code=401,
            detail=f"管理员令牌不正确，请检查请求头 {ADMIN_TOKEN_HEADER}",
        )


def _session_token(request: Request) -> str:
    return (request.cookies.get(config.ADMIN_COOKIE_NAME) or "").strip()


def resolve_principal(request: Request) -> dict | None:
    """Return the authenticated principal, or ``None``."""
    principal = auth.resolve_session(_session_token(request))
    if principal:
        return principal
    token = (request.headers.get(ADMIN_TOKEN_HEADER) or "").strip()
    if config.ADMIN_TOKEN and token and secrets.compare_digest(token, config.ADMIN_TOKEN):
        return auth.token_principal()
    return None


def csrf_valid(request: Request, principal: dict) -> bool:
    """Cookie sessions must echo their CSRF token on writes."""
    if principal.get("auth") != "session":
        return True
    expected = str(principal.get("csrf_token") or "")
    provided = (request.headers.get(CSRF_HEADER) or "").strip()
    return bool(expected) and secrets.compare_digest(provided, expected)


def authenticate_request(request: Request, *, permission: str = "") -> dict:
    """Authenticate a request and (optionally) enforce a permission.

    Raises ``HTTPException`` 401/403 with a user-facing Chinese message.
    In open mode a synthetic super-admin principal is returned.
    """
    principal = resolve_principal(request)
    if not principal:
        if auth.auth_required():
            raise HTTPException(
                status_code=401,
                detail="请先登录管理后台（或提供管理员令牌）",
            )
        return OPEN_PRINCIPAL
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if principal.get("auth") == "session" and not csrf_valid(request, principal):
            raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面后重试")
    if permission and not auth.has_permission(principal.get("role") or "", permission):
        raise HTTPException(status_code=403, detail="当前账号没有执行该操作的权限")
    return principal


def require_admin_token(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> dict:
    """Backwards-compatible dependency: any signed-in account is accepted."""
    # Keep the header parameter so OpenAPI still advertises it.
    _ = x_admin_token
    return authenticate_request(request)


def require_permission(permission: str):
    """FastAPI dependency factory for role checks (e.g. ``admin:manage``)."""

    def _dependency(request: Request) -> dict:
        return authenticate_request(request, permission=permission)

    return _dependency


__all__ = [
    "ADMIN_TOKEN_HEADER",
    "CSRF_HEADER",
    "OPEN_PRINCIPAL",
    "admin_token_required",
    "verify_admin_token",
    "resolve_principal",
    "csrf_valid",
    "authenticate_request",
    "require_admin_token",
    "require_permission",
]

"""Admin authentication, users, roles and audit routes (v0.2.0 M3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import config
from ..security import (
    ADMIN_TOKEN_HEADER,
    authenticate_request,
    require_admin_token,
    require_permission,
    resolve_principal,
)
from ..services import auth

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = ""


class PasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str = Field(min_length=8, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = ""
    role: str = "viewer"


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    active: bool | None = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    must_change: bool = True


def _cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.ADMIN_COOKIE_NAME,
        value=token,
        max_age=max(1, int(config.ADMIN_SESSION_DAYS)) * 86400,
        httponly=True,
        samesite="lax",
        secure=bool(config.ADMIN_COOKIE_SECURE),
        path="/",
    )


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _me_payload(request: Request, principal: dict) -> dict:
    return {
        "authenticated": True,
        "user": {
            "id": principal.get("user_id"),
            "username": principal.get("username"),
            "display_name": principal.get("display_name") or principal.get("username"),
            "role": principal.get("role"),
            "role_label": auth.ROLES.get(principal.get("role") or "", ""),
        },
        "permissions": sorted(auth.permissions_for(principal.get("role") or "")),
        "csrf_token": principal.get("csrf_token") or "",
        "auth_mode": principal.get("auth") or "",
        "auth_required": auth.auth_required(),
        "setup_required": auth.user_count() == 0,
        "roles": [{"key": key, "label": label} for key, label in auth.ROLES.items()],
    }


@router.get("/auth/me")
def me(request: Request) -> dict:
    principal = resolve_principal(request)
    if principal:
        return _me_payload(request, principal)
    return {
        "authenticated": False,
        "user": None,
        "permissions": [],
        "csrf_token": "",
        "auth_mode": "",
        "auth_required": auth.auth_required(),
        "setup_required": auth.user_count() == 0,
        "open_mode": not auth.auth_required(),
        "roles": [{"key": key, "label": label} for key, label in auth.ROLES.items()],
    }


@router.post("/auth/setup")
def setup(payload: SetupRequest, request: Request, response: Response) -> dict:
    """Create the first super admin.

    Only possible while no account exists.  If an deployment token is
    configured, proving knowledge of it is also required (the path is public so
    the bootstrap form works before login).
    """
    if auth.user_count() > 0:
        raise HTTPException(status_code=409, detail="系统已初始化，请直接登录")
    if config.ADMIN_TOKEN:
        from ..security import verify_admin_token

        verify_admin_token(request.headers.get(ADMIN_TOKEN_HEADER))
    try:
        user = auth.create_user(
            payload.username,
            payload.password,
            role="super_admin",
            display_name=payload.display_name or "系统管理员",
            created_by="setup",
            must_change_password=False,
        )
        result = auth.login(
            payload.username, payload.password, ip=_ip(request), user_agent=request.headers.get("user-agent", "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _cookie(response, result["token"])
    auth.record_audit(
        result["user"], "auth.setup", target_type="user", target_id=user.get("id") or "", ip=_ip(request)
    )
    return {**_me_payload(request, auth.resolve_session(result["token"]) or {}), **_login_extra(result)}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    try:
        result = auth.login(
            payload.username, payload.password, ip=_ip(request), user_agent=request.headers.get("user-agent", "")
        )
    except ValueError as exc:
        auth.record_audit(
            None,
            "auth.login_failed",
            target_type="user",
            target_id=payload.username,
            detail="用户名或密码不正确",
            ip=_ip(request),
            status_code=401,
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _cookie(response, result["token"])
    auth.record_audit(
        result["user"], "auth.login", target_type="user", target_id=result["user"].get("id") or "", ip=_ip(request)
    )
    return {**_me_payload(request, auth.resolve_session(result["token"]) or {}), **_login_extra(result)}


def _login_extra(result: dict) -> dict:
    return {"expires_at": result.get("expires_at")}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = (request.cookies.get(config.ADMIN_COOKIE_NAME) or "").strip()
    principal = auth.resolve_session(token)
    if token:
        auth.logout(token)
    response.delete_cookie(config.ADMIN_COOKIE_NAME, path="/")
    if principal:
        auth.record_audit(principal, "auth.logout", target_type="user", target_id=principal.get("user_id") or "", ip=_ip(request))
    return {"ok": True}


@router.post("/auth/password")
def change_password(
    payload: PasswordRequest,
    request: Request,
    principal: dict = Depends(require_admin_token),
) -> dict:
    if principal.get("auth") != "session" or not principal.get("user_id"):
        raise HTTPException(status_code=400, detail="当前使用管理令牌，请在账号登录后修改密码")
    try:
        current_token = (request.cookies.get(config.ADMIN_COOKIE_NAME) or "").strip()
        auth.change_password(
            principal["user_id"],
            payload.old_password,
            payload.new_password,
            keep_token=current_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.record_audit(principal, "auth.password_change", target_type="user", target_id=principal["user_id"], ip=_ip(request))
    return {"ok": True}


@router.get("/admin/users", dependencies=[Depends(require_permission("admin:manage"))])
def list_users() -> dict:
    items = auth.list_users()
    return {"items": items, "total": len(items)}


@router.post("/admin/users", status_code=201)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    principal: dict = Depends(require_permission("admin:manage")),
) -> dict:
    try:
        user = auth.create_user(
            payload.username,
            payload.password,
            role=payload.role,
            display_name=payload.display_name,
            created_by=principal.get("username") or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.record_audit(
        principal,
        "admin.user_create",
        target_type="user",
        target_id=user.get("id") or "",
        detail=f"{user.get('username')} · {user.get('role')}",
        ip=_ip(request),
    )
    return user


@router.patch("/admin/users/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    request: Request,
    principal: dict = Depends(require_permission("admin:manage")),
) -> dict:
    try:
        user = auth.update_user(
            user_id,
            display_name=payload.display_name,
            role=payload.role,
            active=payload.active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.record_audit(
        principal,
        "admin.user_update",
        target_type="user",
        target_id=user_id,
        detail=f"{user.get('username')} · {user.get('role')} · active={user.get('active')}",
        ip=_ip(request),
    )
    return user


@router.delete("/admin/users/{user_id}")
def delete_user(
    user_id: str,
    request: Request,
    principal: dict = Depends(require_permission("admin:manage")),
) -> dict:
    try:
        auth.delete_user(user_id, actor_id=principal.get("user_id") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.record_audit(principal, "admin.user_delete", target_type="user", target_id=user_id, ip=_ip(request))
    return {"ok": True}


@router.post("/admin/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    request: Request,
    principal: dict = Depends(require_permission("admin:manage")),
) -> dict:
    try:
        auth.reset_password(user_id, payload.password, must_change=payload.must_change)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.record_audit(
        principal, "admin.password_reset", target_type="user", target_id=user_id, ip=_ip(request)
    )
    return {"ok": True}


@router.get("/admin/audit")
def audit_log(
    request: Request,
    days: int = 30,
    username: str = "",
    action: str = "",
    limit: int = 100,
    offset: int = 0,
    principal: dict = Depends(require_permission("audit:read")),
) -> dict:
    _ = principal
    return auth.list_audit(
        days=days, username=username, action=action, limit=limit, offset=offset
    )


__all__ = ["router"]

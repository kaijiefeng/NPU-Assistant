"""Admin accounts, sessions, roles and audit log (v0.2.0 M3).

Design
------
* Passwords are stored as ``pbkdf2_sha256$iterations$salt$hash`` (stdlib only).
* Browsers authenticate with an HttpOnly session cookie; the raw token is never
  stored server-side (only its SHA-256 digest).  A CSRF token travels in the
  login response / ``/auth/me`` and must be echoed in ``X-CSRF-Token`` for
  cookie-authenticated writes.
* The legacy ``X-Admin-Token`` remains supported and maps to ``super_admin`` so
  existing deployments and scripts keep working.
* Every authenticated write is audited by the HTTP guard in ``main.py``; login,
  logout, user management and password changes add explicit entries.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from .. import config, db

ROLES = {
    "super_admin": "超级管理员",
    "admin": "管理员",
    "counselor": "辅导员",
    "viewer": "只读",
}
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": {"*"},
    "admin": {
        "content:read",
        "content:write",
        "settings:write",
        "model:write",
        "bot:write",
        "insights:read",
        "insights:write",
        "handoff:read",
        "handoff:write",
        "audit:read",
    },
    "counselor": {
        "content:read",
        "insights:read",
        "insights:write",
        "handoff:read",
        "handoff:write",
    },
    "viewer": {"content:read", "insights:read", "handoff:read"},
}
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, *, iterations: int | None = None) -> str:
    value = str(password or "")
    rounds = int(iterations or config.PASSWORD_HASH_ITERATIONS or 260000)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, rounds, dklen=32)
    return "pbkdf2_sha256${}${}${}".format(
        rounds, salt.hex(), digest.hex()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, hash_hex = str(stored or "").split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(rounds),
            dklen=len(bytes.fromhex(hash_hex)),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def validate_password(password: str, username: str = "") -> None:
    value = str(password or "")
    if len(value) < 8:
        raise ValueError("密码至少 8 位")
    if len(value) > 128:
        raise ValueError("密码过长（最多 128 位）")
    if username and value.lower() == str(username).lower():
        raise ValueError("密码不能与用户名相同")


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Roles / permissions
# ---------------------------------------------------------------------------
def permissions_for(role: str) -> set[str]:
    return set(ROLE_PERMISSIONS.get(str(role or ""), set()))


def has_permission(role: str, permission: str) -> bool:
    granted = permissions_for(role)
    return "*" in granted or permission in granted


def permission_for_request(method: str, path: str) -> str:
    """Map a mutating request to the permission it needs."""
    if path.startswith("/api/auth"):
        return ""
    if path.startswith("/api/jobs"):
        return "content:read" if method in SAFE_METHODS else "content:write"
    if path.startswith("/api/alerts"):
        return "insights:read" if method in SAFE_METHODS else "insights:write"
    if path.startswith("/api/admin"):
        return "admin:manage"
    if path.startswith(("/api/handoff", "/api/crisis")):
        return "handoff:read" if method in SAFE_METHODS else "handoff:write"
    if path.startswith("/api/insights"):
        return "insights:read" if method in SAFE_METHODS else "insights:write"
    if path.startswith("/api/models"):
        return "model:write"
    if path.startswith("/api/bots"):
        return "bot:write"
    if path.startswith(("/api/settings", "/api/system")):
        return "settings:write"
    if path.startswith(("/api/chat/conversations", "/api/chat/messages")):
        return "insights:write"
    return "content:write"


def user_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM admin_users")
    return int((row or {}).get("n") or 0)


def auth_required() -> bool:
    """Auth is enforced once a token or at least one account exists."""
    return bool(config.ADMIN_TOKEN) or user_count() > 0


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _public(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "display_name": row.get("display_name") or row.get("username"),
        "role": row.get("role"),
        "role_label": ROLES.get(row.get("role") or "", "只读"),
        "active": bool(row.get("active")),
        "must_change_password": bool(row.get("must_change_password")),
        "created_at": row.get("created_at"),
        "last_login_at": row.get("last_login_at"),
        "password_updated_at": row.get("password_updated_at"),
    }


def list_users() -> list[dict]:
    rows = db.query(
        "SELECT * FROM admin_users ORDER BY CASE role "
        "WHEN 'super_admin' THEN 0 WHEN 'admin' THEN 1 WHEN 'counselor' THEN 2 ELSE 3 END, "
        "created_at ASC"
    )
    return [_public(row) for row in rows]


def get_user(user_id: str) -> dict | None:
    return _public(db.query_one("SELECT * FROM admin_users WHERE id = ?", (user_id,)))


def _find_username(username: str) -> dict | None:
    return db.query_one("SELECT * FROM admin_users WHERE username = ?", (username,))


def create_user(
    username: str,
    password: str,
    *,
    role: str = "viewer",
    display_name: str = "",
    created_by: str = "",
    must_change_password: bool = True,
) -> dict:
    name = str(username or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise ValueError("用户名需为 3-32 位字母、数字、点、下划线或短横线")
    if role not in ROLES:
        raise ValueError("不支持的角色")
    validate_password(password, name)
    if _find_username(name):
        raise ValueError("用户名已存在")
    timestamp = db.now()
    user_id = db.new_id()
    db.execute(
        "INSERT INTO admin_users(id, username, display_name, role, password_hash, active, "
        "must_change_password, created_by, created_at, updated_at, password_updated_at) "
        "VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
        (
            user_id,
            name,
            str(display_name or name).strip()[:60],
            role,
            hash_password(password),
            1 if must_change_password else 0,
            str(created_by or "")[:64],
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    return get_user(user_id) or {}


def update_user(
    user_id: str, *, display_name: str | None = None, role: str | None = None, active: bool | None = None
) -> dict:
    row = db.query_one("SELECT * FROM admin_users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("用户不存在")
    next_role = str(role or row.get("role") or "viewer")
    if next_role not in ROLES:
        raise ValueError("不支持的角色")
    next_active = bool(row.get("active")) if active is None else bool(active)
    if row.get("role") == "super_admin" and (next_role != "super_admin" or not next_active):
        if _active_super_admins() <= 1:
            raise ValueError("至少保留一名启用的超级管理员")
    fields = ["display_name = ?", "role = ?", "active = ?", "updated_at = ?"]
    params: list[Any] = [
        (
            str(display_name).strip()[:60]
            if display_name is not None
            else (row.get("display_name") or row.get("username"))
        ),
        next_role,
        1 if next_active else 0,
        db.now(),
    ]
    if not next_active:
        db.execute(
            "UPDATE admin_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (db.now(), user_id),
        )
    db.execute(
        f"UPDATE admin_users SET {', '.join(fields)} WHERE id = ?", [*params, user_id]
    )
    return get_user(user_id) or {}


def _active_super_admins() -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM admin_users WHERE role = 'super_admin' AND active = 1"
    )
    return int((row or {}).get("n") or 0)


def delete_user(user_id: str, *, actor_id: str = "") -> None:
    row = db.query_one("SELECT * FROM admin_users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("用户不存在")
    if actor_id and actor_id == user_id:
        raise ValueError("不能删除当前登录账号")
    if row.get("role") == "super_admin" and _active_super_admins() <= 1:
        raise ValueError("至少保留一名启用的超级管理员")
    db.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))


def reset_password(user_id: str, password: str, *, must_change: bool = True) -> None:
    row = db.query_one("SELECT * FROM admin_users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("用户不存在")
    validate_password(password, row.get("username") or "")
    timestamp = db.now()
    db.execute(
        "UPDATE admin_users SET password_hash = ?, password_updated_at = ?, updated_at = ?, "
        "must_change_password = ? WHERE id = ?",
        (hash_password(password), timestamp, timestamp, 1 if must_change else 0, user_id),
    )
    db.execute(
        "UPDATE admin_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (timestamp, user_id),
    )


def change_password(
    user_id: str, old_password: str, new_password: str, keep_token: str = ""
) -> None:
    row = db.query_one("SELECT * FROM admin_users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("用户不存在")
    if not verify_password(old_password, row.get("password_hash") or ""):
        raise ValueError("当前密码不正确")
    validate_password(new_password, row.get("username") or "")
    if hmac.compare_digest(str(old_password), str(new_password)):
        raise ValueError("新密码不能与当前密码相同")
    timestamp = db.now()
    db.execute(
        "UPDATE admin_users SET password_hash = ?, password_updated_at = ?, updated_at = ?, "
        "must_change_password = 0 WHERE id = ?",
        (hash_password(new_password), timestamp, timestamp, user_id),
    )
    # 改密后让其它会话立即失效（保留当前会话，避免用户被自己踢下线）
    if keep_token:
        db.execute(
            "UPDATE admin_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL "
            "AND token_hash != ?",
            (timestamp, user_id, _token_hash(keep_token)),
        )
    else:
        revoke_user_sessions(user_id)


def ensure_bootstrap() -> None:
    """Create the first super admin from env when the user table is empty."""
    username = (getattr(config, "ADMIN_BOOTSTRAP_USERNAME", "") or "admin").strip().lower()
    password = getattr(config, "ADMIN_BOOTSTRAP_PASSWORD", "") or ""
    if not password or user_count() > 0:
        return
    try:
        create_user(
            username,
            password,
            role="super_admin",
            display_name="系统管理员",
            created_by="bootstrap",
            must_change_password=False,
        )
    except ValueError:
        return


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _session_payload(row: dict) -> dict:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row.get("display_name") or row["username"],
        "role": row["role"],
        "auth": "session",
        "csrf_token": row["csrf_token"],
    }


def login(
    username: str,
    password: str,
    *,
    ip: str = "",
    user_agent: str = "",
) -> dict:
    name = str(username or "").strip().lower()
    row = _find_username(name)
    if not row or not row.get("active") or not verify_password(password, row.get("password_hash") or ""):
        raise ValueError("用户名或密码不正确")
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now()
    session_id = db.new_id()
    expires_at = (now + timedelta(days=max(1, int(config.ADMIN_SESSION_DAYS)))).isoformat(
        timespec="seconds"
    )
    timestamp = now.isoformat(timespec="seconds")
    db.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (timestamp,))
    db.execute(
        "INSERT INTO admin_sessions(id, user_id, token_hash, csrf_token, ip, user_agent, "
        "created_at, expires_at, last_seen_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            row["id"],
            _token_hash(token),
            csrf,
            str(ip or "")[:64],
            str(user_agent or "")[:240],
            timestamp,
            expires_at,
            timestamp,
        ),
    )
    db.execute("UPDATE admin_users SET last_login_at = ? WHERE id = ?", (timestamp, row["id"]))
    principal = {
        "user_id": row["id"],
        "username": row["username"],
        "display_name": row.get("display_name") or row["username"],
        "role": row["role"],
        "auth": "session",
        "csrf_token": csrf,
    }
    return {"token": token, "csrf_token": csrf, "expires_at": expires_at, "user": _public({**row, "last_login_at": timestamp})}


def resolve_session(token: str) -> dict | None:
    if not token:
        return None
    row = db.query_one(
        "SELECT s.*, u.username, u.display_name, u.role, u.active "
        "FROM admin_sessions s JOIN admin_users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.revoked_at IS NULL",
        (_token_hash(token),),
    )
    if not row or not row.get("active"):
        return None
    if str(row.get("expires_at") or "") <= db.now():
        return None
    return _session_payload(row)


def logout(token: str) -> None:
    if token:
        db.execute(
            "UPDATE admin_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (db.now(), _token_hash(token)),
        )


def revoke_user_sessions(user_id: str) -> None:
    db.execute(
        "UPDATE admin_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (db.now(), user_id),
    )


def token_principal() -> dict:
    return {
        "user_id": "",
        "username": "admin-token",
        "display_name": "管理令牌",
        "role": "super_admin",
        "auth": "token",
        "csrf_token": "",
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def record_audit(
    principal: dict | None,
    action: str,
    *,
    target_type: str = "",
    target_id: str = "",
    detail: str = "",
    ip: str = "",
    status_code: int = 0,
) -> None:
    principal = principal or {}
    try:
        db.execute(
            "INSERT INTO audit_log(id, user_id, username, role, action, target_type, "
            "target_id, detail, ip, status_code, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                db.new_id(),
                principal.get("user_id") or "",
                principal.get("username") or "",
                principal.get("role") or "",
                str(action or "")[:80],
                str(target_type or "")[:40],
                str(target_id or "")[:80],
                str(detail or "")[:500],
                str(ip or "")[:64],
                int(status_code or 0),
                db.now(),
            ),
        )
    except Exception:  # pragma: no cover - auditing must never break a request
        pass


def list_audit(
    *, days: int = 30, username: str = "", action: str = "", limit: int = 100, offset: int = 0
) -> dict:
    since = (datetime.now() - timedelta(days=max(0, int(days)))).isoformat(timespec="seconds")
    where = ["created_at >= ?"]
    params: list[Any] = [since]
    if username:
        where.append("username = ?")
        params.append(str(username).strip().lower())
    if action:
        where.append("action = ?")
        params.append(str(action).strip())
    clause = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM audit_log WHERE {clause}", params)
    rows = db.query(
        f"SELECT * FROM audit_log WHERE {clause} ORDER BY created_at DESC, rowid DESC "
        "LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))],
    )
    return {
        "items": rows,
        "total": int((total or {}).get("n") or 0),
        "actions": [
            row["action"]
            for row in db.query(
                "SELECT action, COUNT(*) AS n FROM audit_log WHERE created_at >= ? "
                "GROUP BY action ORDER BY n DESC LIMIT 30",
                (since,),
            )
        ],
    }

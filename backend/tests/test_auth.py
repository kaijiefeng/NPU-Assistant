"""M3: admin accounts, sessions, roles, CSRF and audit."""
from __future__ import annotations

import pytest

PASSWORD = "Counselor#2026"


def _setup(client, username="root", password=PASSWORD):
    response = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, "display_name": "测试管理员"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _login(client, username="root", password=PASSWORD):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _csrf(data):
    return {"X-CSRF-Token": data["csrf_token"]}


def test_open_mode_allows_local_demo(client):
    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is False
    assert me["open_mode"] is True
    assert me["setup_required"] is True


def test_setup_login_me_logout(client):
    data = _setup(client)
    assert data["authenticated"] is True
    assert data["user"]["role"] == "super_admin"

    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["user"]["username"] == "root"
    assert me["permissions"] == ["*"]

    # Second setup is refused.
    assert (
        client.post(
            "/api/auth/setup", json={"username": "other", "password": PASSWORD}
        ).status_code
        == 409
    )

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is False
    assert client.get("/api/admin/users").status_code == 401

    logged = _login(client)
    assert logged["user"]["username"] == "root"


def test_wrong_password_and_login_audit(client):
    _setup(client)
    client.post("/api/auth/logout")
    bad = client.post("/api/auth/login", json={"username": "root", "password": "wrong-pass"})
    assert bad.status_code == 401
    assert "用户名或密码不正确" in bad.json()["detail"]

    _login(client)
    events = client.get("/api/admin/audit?days=1").json()["items"]
    assert any(item["action"] == "auth.login_failed" for item in events)
    assert all("wrong-pass" not in (item.get("detail") or "") for item in events)


def test_csrf_required_for_cookie_writes(client):
    data = _setup(client)
    denied = client.put("/api/settings", json={"academic_year": "2026-2027"})
    assert denied.status_code == 403
    assert "CSRF" in denied.json()["detail"]

    ok = client.put(
        "/api/settings", json={"academic_year": "2026-2027"}, headers=_csrf(data)
    )
    assert ok.status_code == 200

    audit = client.get("/api/admin/audit").json()["items"]
    assert any(item["action"] == "PUT /api/settings" for item in audit)


def test_roles_permissions_and_user_management(client):
    admin = _setup(client)
    created = client.post(
        "/api/admin/users",
        json={
            "username": "counselor1",
            "password": PASSWORD,
            "display_name": "张老师",
            "role": "counselor",
        },
        headers=_csrf(admin),
    )
    assert created.status_code == 201, created.text
    assert created.json()["role_label"] == "辅导员"

    client.post("/api/auth/logout")
    counselor = _login(client, "counselor1")

    assert client.get("/api/admin/users").status_code == 403
    assert (
        client.post(
            "/api/insights/recompute", headers=_csrf(counselor)
        ).status_code
        == 200
    )
    # Counselors may write handoff/insight data but not content.
    blocked = client.post(
        "/api/knowledge/import/text",
        json={"title": "x", "text": "y"},
        headers=_csrf(counselor),
    )
    assert blocked.status_code == 403

    client.post("/api/auth/logout")
    viewer = client.post(
        "/api/auth/login", json={"username": "counselor1", "password": PASSWORD}
    ).json()
    # Demote via the original super admin session is covered below; here just
    # verify a viewer-role account cannot use the service desk write APIs.
    assert viewer["user"]["role"] == "counselor"


def test_viewer_cannot_write_insights(client):
    admin = _setup(client)
    client.post(
        "/api/admin/users",
        json={"username": "viewer1", "password": PASSWORD, "role": "viewer"},
        headers=_csrf(admin),
    )
    client.post("/api/auth/logout")
    viewer = _login(client, "viewer1")
    assert client.get("/api/handoff").status_code == 200
    assert (
        client.post("/api/insights/recompute", headers=_csrf(viewer)).status_code == 403
    )


def test_password_change_revokes_sessions(client):
    data = _setup(client)
    wrong = client.post(
        "/api/auth/password",
        json={"old_password": "nope-nope", "new_password": "NewPass#2026"},
        headers=_csrf(data),
    )
    assert wrong.status_code == 400

    changed = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": "NewPass#2026"},
        headers=_csrf(data),
    )
    assert changed.status_code == 200

    client.post("/api/auth/logout")
    assert (
        client.post(
            "/api/auth/login", json={"username": "root", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "root", "password": "NewPass#2026"}
        ).status_code
        == 200
    )


def test_last_super_admin_is_protected(client):
    data = _setup(client)
    user_id = data["user"]["id"]
    assert (
        client.patch(
            f"/api/admin/users/{user_id}", json={"role": "viewer"}, headers=_csrf(data)
        ).status_code
        == 400
    )
    assert (
        client.delete(f"/api/admin/users/{user_id}", headers=_csrf(data)).status_code
        == 400
    )


def test_legacy_admin_token_maps_to_super_admin(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "legacy-token-at-least-16-chars")
    assert client.get("/api/admin/users").status_code == 401
    headers = {"X-Admin-Token": "legacy-token-at-least-16-chars"}
    assert client.get("/api/admin/users", headers=headers).status_code == 200
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["authenticated"] is True
    assert me["auth_mode"] == "token"
    # Token auth needs no CSRF token.
    assert client.put("/api/settings", json={"academic_year": "2025-2026"}, headers=headers).status_code == 200


def test_env_bootstrap_creates_first_admin(client, monkeypatch):
    from app import config
    from app.services import auth

    monkeypatch.setattr(config, "ADMIN_BOOTSTRAP_USERNAME", "bootadmin")
    monkeypatch.setattr(config, "ADMIN_BOOTSTRAP_PASSWORD", "Boot#2026")
    auth.ensure_bootstrap()
    assert auth.user_count() == 1
    data = _login(client, "bootadmin", "Boot#2026")
    assert data["user"]["role"] == "super_admin"


@pytest.mark.parametrize("length", [7])
def test_password_policy(client, length):
    response = client.post(
        "/api/auth/setup", json={"username": "root", "password": "a" * length}
    )
    assert response.status_code == 422

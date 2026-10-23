"""v0.1.1 手机扫码访问 / 一键公网隧道 API 测试。"""

from __future__ import annotations

import io

from PIL import Image

from app import config
from app.services import tunnel as tunnel_module
from app.services.tunnel import tunnel_manager


def test_mobile_access_info_has_urls_and_qr(client):
    response = client.get("/api/mobile/access")
    assert response.status_code == 200
    data = response.json()

    assert data["app_version"] == config.APP_VERSION
    assert isinstance(data["token_required"], bool)
    assert isinstance(data["urls"], list) and data["urls"]
    item = data["urls"][0]
    assert {"url", "kind", "label", "secure", "pwa"} <= set(item)
    assert item["url"].startswith(("http://", "https://"))
    assert data["preferred_url"] == item["url"]
    assert data["qr"].startswith("data:image/png;base64,")
    assert set(data["tunnel"]) >= {"available", "running", "public_url", "install_hint"}


def test_mobile_qr_png_and_validation(client):
    response = client.get("/api/mobile/qr.png", params={"url": "http://192.168.1.5:8000"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(response.content))
    assert image.width > 50 and image.height > 50

    assert client.get("/api/mobile/qr.png", params={"url": "file:///etc/passwd"}).status_code == 400
    assert client.get("/api/mobile/qr.png", params={"url": "not-a-url"}).status_code == 400


def test_tunnel_unavailable_reports_install_hint(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-test-token")
    monkeypatch.setattr(config, "CLOUDFLARED_PATH", "definitely-not-installed-cloudflared")
    response = client.post(
        "/api/mobile/tunnel/start", headers={"X-Admin-Token": "mobile-test-token"}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "cloudflared" in detail
    assert "安装" in detail or "winget" in detail


class _FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.returncode = None
        self.stdout = iter(
            [
                "2026-10-23 INF Requesting new quick Tunnel on trycloudflare.com...\n",
                "2026-10-23 INF |  https://npu-demo.trycloudflare.com  |\n",
            ]
        )

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


def test_tunnel_start_and_stop_with_fake_cloudflared(client, monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARED_PATH", "cloudflared")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-test-token")
    monkeypatch.setattr(tunnel_manager, "available", lambda: True)
    monkeypatch.setattr(tunnel_module.subprocess, "Popen", _FakeProcess)
    headers = {"X-Admin-Token": "mobile-test-token"}
    try:
        response = client.post("/api/mobile/tunnel/start", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["tunnel"]["public_url"] == "https://npu-demo.trycloudflare.com"
        assert data["preferred_url"] == "https://npu-demo.trycloudflare.com"
        assert data["urls"][0]["kind"] in {"tunnel", "public"}

        stopped = client.post("/api/mobile/tunnel/stop", headers=headers)
        assert stopped.status_code == 200
        assert stopped.json()["tunnel"]["running"] is False
    finally:
        tunnel_manager.stop()

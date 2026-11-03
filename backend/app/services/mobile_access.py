"""手机扫码访问信息：局域网/公网地址探测 + 二维码生成。"""

from __future__ import annotations

import base64
import io
import ipaddress
import socket
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from .. import config

QR_CACHE: dict[str, str] = {}


def _valid_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if ip.version != 4:
        return False
    # 198.18.0.0/15 是网络基准测试保留段，常被代理软件（Clash/Mihomo TUN）用作
    # fake-IP，手机无法访问，不能当作局域网地址展示。
    if int(ip) & 0xFFFE0000 == 0xC6120000:  # 198.18.0.0/15
        return False
    return not (
        ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    )


def detect_lan_ips() -> list[str]:
    """Best-effort LAN IPv4 detection (no third-party deps)."""
    candidates: list[str] = []
    # Default-route trick: connect() on UDP does not send packets.
    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.3)
                sock.connect((probe, 80))
                candidates.append(sock.getsockname()[0])
                break
        except OSError:
            continue
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidates.append(item[4][0])
    except OSError:
        pass

    seen: set[str] = set()
    result: list[str] = []
    for value in candidates:
        value = value.strip()
        if not _valid_ip(value) or value in seen:
            continue
        seen.add(value)
        result.append(value)
    # Prefer typical home/campus Wi-Fi ranges over virtual adapters
    # （172.16/12 常被 Hyper-V / Docker / VirtualBox 的虚拟网卡占用，排在最后）。
    def score(item: str) -> tuple[int, str]:
        ip = ipaddress.ip_address(item)
        if int(ip) & 0xFFFF0000 == 0xC0A80000:  # 192.168.0.0/16
            return (0, item)
        if int(ip) & 0xFF000000 == 0x0A000000:  # 10.0.0.0/8
            return (1, item)
        if int(ip) & 0xFFC00000 == 0x64400000:  # 100.64.0.0/10（校园/运营商 CGNAT）
            return (2, item)
        if int(ip) & 0xFFF00000 == 0xAC100000:  # 172.16.0.0/12
            return (4, item)
        return (3, item)

    result.sort(key=score)
    return result


def access_urls() -> list[dict[str, Any]]:
    urls: list[dict[str, Any]] = []
    public = (config.PUBLIC_URL or "").strip().rstrip("/")
    if public.startswith(("http://", "https://")):
        urls.append(
            {
                "url": public,
                "kind": "public",
                "label": "公网 HTTPS",
                "secure": public.startswith("https://"),
                "pwa": public.startswith("https://"),
            }
        )
    for index, ip in enumerate(detect_lan_ips()):
        url = f"http://{ip}:{config.PORT}"
        urls.append(
            {
                "url": url,
                "kind": "lan",
                "label": "局域网" if index == 0 else "局域网（备用网卡）",
                "secure": False,
                "pwa": False,
            }
        )
    return urls


def build_access_info(request_host: str) -> dict[str, Any]:
    from .tunnel import tunnel_manager

    tunnel = tunnel_manager.status()
    urls = access_urls()
    if tunnel.get("public_url"):
        urls.insert(
            0,
            {
                "url": tunnel["public_url"],
                "kind": "tunnel",
                "label": "公网 HTTPS（临时隧道）",
                "secure": True,
                "pwa": True,
            },
        )
    host = (request_host or "").strip().lstrip("[").rstrip("]")
    if host and host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0", "testserver", "testclient"}:
        current = f"http://{host}:{config.PORT}"
        if not any(item["url"] == current for item in urls):
            urls.append(
                {
                    "url": current,
                    "kind": "current",
                    "label": "当前访问地址",
                    "secure": False,
                    "pwa": False,
                }
            )
    preferred = urls[0]["url"] if urls else ""
    return {
        "app_version": config.APP_VERSION,
        "token_required": bool(config.ADMIN_TOKEN),
        "preferred_url": preferred,
        "urls": urls,
        "qr": qr_data_url(preferred) if preferred else "",
        "tunnel": tunnel,
        "hint": ""
        if preferred
        else "当前只监听本机。请用 .\\scripts\\run.ps1 -Lan 启动，或在下方开启公网访问。",
    }


def qr_data_url(text: str) -> str:
    if not text:
        return ""
    cached = QR_CACHE.get(text)
    if cached:
        return cached
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=6, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b3d91", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    if len(QR_CACHE) > 200:
        QR_CACHE.clear()
    QR_CACHE[text] = data
    return data


def qr_png(text: str) -> bytes:
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b3d91", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

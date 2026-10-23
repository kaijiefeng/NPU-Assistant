"""手机扫码访问与一键公网隧道 API（v0.1.1）。"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, HTTPException, Request, Response

from .. import config
from ..services import mobile_access
from ..services.tunnel import TunnelError, tunnel_manager

router = APIRouter(tags=["mobile"])


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "testclient", "testserver"}


def _require_local_or_admin(request: Request) -> None:
    """Tunnel start/stop changes the public exposure of the instance."""
    if config.ADMIN_TOKEN:
        return  # the admin-token middleware already verified the header
    if not _is_loopback(request):
        raise HTTPException(
            status_code=403,
            detail="开启/关闭公网访问需要在服务器本机操作（或先配置 NPU_ADMIN_TOKEN）",
        )


@router.get("/mobile/access")
def mobile_access_info(request: Request) -> dict:
    host = request.url.hostname or ""
    return mobile_access.build_access_info(host)


@router.get("/mobile/qr.png")
def mobile_qr(url: str) -> Response:
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")) or len(target) > 500:
        raise HTTPException(status_code=400, detail="二维码内容必须是 http/https 地址")
    return Response(content=mobile_access.qr_png(target), media_type="image/png")


@router.post("/mobile/tunnel/start")
def mobile_tunnel_start(request: Request) -> dict:
    _require_local_or_admin(request)
    if not config.ADMIN_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="开启公网访问前请先设置 NPU_ADMIN_TOKEN（用 .\\scripts\\run.ps1 -Lan 启动会自动生成），否则公网访问会被安全策略拒绝。",
        )
    try:
        tunnel_manager.start()
    except TunnelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return mobile_access.build_access_info(request.url.hostname or "")


@router.post("/mobile/tunnel/stop")
def mobile_tunnel_stop(request: Request) -> dict:
    _require_local_or_admin(request)
    tunnel_manager.stop()
    return mobile_access.build_access_info(request.url.hostname or "")

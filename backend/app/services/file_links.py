"""源文件下载链接拼装。

引用（citations）里的来源可能是本地上传的源文件，也可能是收录时的原网页。
本模块负责把它们统一转换成可点击的链接：

* 本地上传文件 → ``/api/knowledge/documents/{id}/download``（相对路径，供网页使用）；
* 原网页 → 直接使用 ``source_url``；
* 机器人渠道需要**绝对**地址（手机上的 QQ 无法解析相对路径），因此额外提供
  :func:`absolute_url`：优先 ``NPU_PUBLIC_URL`` / 临时隧道地址，其次局域网 IP。
"""
from __future__ import annotations

from .. import config

# 下载端点（与 ``routers/knowledge.py`` 保持一致）
DOWNLOAD_PATH_TEMPLATE = "/api/knowledge/documents/{doc_id}/download"


def download_path(document_id: str) -> str:
    """本地源文件的**相对**下载路径；文档 id 为空时返回空字符串。"""
    doc_id = str(document_id or "").strip()
    if not doc_id:
        return ""
    return DOWNLOAD_PATH_TEMPLATE.format(doc_id=doc_id)


def base_url() -> str:
    """推断机器人可用的绝对访问地址（公网 → 隧道 → 局域网 → 本机）。"""
    public = (config.PUBLIC_URL or "").strip().rstrip("/")
    if public.startswith(("http://", "https://")):
        return public
    try:
        from .tunnel import tunnel_manager  # 惰性导入：避免启动期依赖

        tunnel_url = str(tunnel_manager.status().get("public_url") or "").strip().rstrip("/")
        if tunnel_url.startswith(("http://", "https://")):
            return tunnel_url
    except Exception:  # noqa: BLE001 - 隧道状态失败不影响降级
        pass
    try:
        from .mobile_access import detect_lan_ips

        ips = detect_lan_ips()
    except Exception:  # noqa: BLE001 - 网卡探测失败时退回本机地址
        ips = []
    host = ips[0] if ips else "127.0.0.1"
    return f"http://{host}:{config.PORT}"


def absolute_url(path_or_url: str) -> str:
    """把相对路径补全为绝对地址；已是 http(s) 链接则原样返回。"""
    value = str(path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if not value.startswith("/"):
        value = "/" + value
    return base_url() + value


def bot_links_reachable() -> bool:
    """机器人回复里的链接能否被手机打开（公网地址 / 隧道 / 对外监听之一即可）。"""
    if (config.PUBLIC_URL or "").strip().startswith(("http://", "https://")):
        return True
    if (config.HOST or "").strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
        return True
    try:
        from .tunnel import tunnel_manager  # 惰性导入：避免启动期依赖

        if str(tunnel_manager.status().get("public_url") or "").strip():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def source_links(citations: list[dict] | None, *, limit: int = 3) -> list[dict]:
    """从 citations 提取「相关文件」链接，按文档去重并保留引用顺序。

    返回 ``[{"title": 文档标题, "url": 绝对链接, "kind": "file"|"web"}]``；
    信息源为链接时给原网页地址，本地上传文件则给下载地址。
    """
    links: list[dict] = []
    seen: set[str] = set()
    for citation in citations or []:
        if not isinstance(citation, dict):
            continue
        for source in citation.get("sources") or []:
            if not isinstance(source, dict):
                continue
            document_id = str(source.get("document_id") or "")
            source_url = str(source.get("source_url") or "")
            key = document_id or source_url
            if not key or key in seen:
                continue
            title = str(
                source.get("document_title") or source.get("source_file") or "源文件"
            ).strip()
            if source_url:
                links.append({"title": title, "url": source_url, "kind": "web"})
            else:
                path = str(source.get("download_url") or download_path(document_id))
                if not path:
                    continue
                links.append({"title": title, "url": absolute_url(path), "kind": "file"})
            seen.add(key)
            if len(links) >= limit:
                return links
    return links


__all__ = ["download_path", "base_url", "absolute_url", "bot_links_reachable", "source_links"]

"""移动端快速收录：网页链接抓取 / 正文提取 / 纯文本收录（LLM Wiki 版）。

实现契约见 ``docs/MOBILE-OPS.md`` §2.1 与 §4.5：

* ``fetch_url(url)``：仅 http/https；手动跟随重定向（每跳都校验主机）；
  默认拒绝私网/回环/链路本地/保留地址（``NPU_URL_IMPORT_ALLOW_PRIVATE=1`` 放行）；
  超时 ``URL_IMPORT_TIMEOUT_SECONDS``；流式读取 ≤ ``URL_IMPORT_MAX_BYTES``；
  只接受 HTML/纯文本，其余 Content-Type 直接报错。
* ``extract_article(content, ...)``：bs4 提取正文（优先公众号 ``#js_content`` /
  ``.rich_media_content``、``article/main/[role=main]``，否则 body 最大文本块）；
  标题优先级 ``og:title`` > ``<title>`` > ``h1``；正文 <100 字报错。
* ``import_url`` / ``import_text`` / ``import_batch``：查重、保存源文件并调用
  :mod:`services.ingest` 完成入库 + LLM 编译（**全项目只有这一个编译入口**）。

安全约定：

1. SSRF：先校验协议/账号密码，再解析域名并逐个检查解析结果，有任何非公网 IP
   即拒绝；重定向每一跳都重新校验。DNS 在真正连接前会再次解析，本模块通过
   “解析即校验” 降低（MVP 范围内不消除）DNS rebinding 风险。
2. 资源限制：超时、重定向次数、响应体大小、正文长度均有上限。
3. 日志/错误信息不包含完整 query 字符串，也不回显请求头等敏感信息。
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import SplitResult, unquote, unquote_plus, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .. import config, db
from . import ingest

logger = logging.getLogger("npu-assistant")

# 文本收录长度限制（docs/MOBILE-OPS.md §2.1）
MIN_IMPORT_TEXT_CHARS = 30
MAX_IMPORT_TEXT_CHARS = 200_000
# 网页正文少于该字数视为提取失败
MIN_EXTRACTED_CHARS = 100
MAX_TITLE_CHARS = 120
UNTITLED_PREFIX = "未命名资料"
# 单次网络读操作超时（慢速滴水由总 deadline 兜底）
READ_TIMEOUT_SECONDS = 10.0
# 正文定位：嵌套超过该深度直接降级为整页 get_text；单层候选容器数量上限
MAX_BLOCK_DEPTH = 256
MAX_BLOCK_CANDIDATES = 2000

# 只接受网页/纯文本
_ALLOWED_MIME_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_NOISE_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    # 注意：不要整块删除 form。部分高校 CMS 把正文放在 <form> 内，
    # 删除 form 会把 #vsb_content 一起删掉；表单噪声交给链接密度清理。
    "iframe",
    "svg",
    "canvas",
    "template",
)
_CONTAINER_TAGS = ("article", "main", "section", "div", "td")
_NOISE_TAG_SET = frozenset(_NOISE_TAGS)
_LINE_BREAK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "html",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_WHITESPACE_RE = re.compile(r"\s+")
_LINE_WS_RE = re.compile(r"[ \t\f\v\u00a0\u3000]+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2028\u2029\ufeff]")
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.IGNORECASE)
_NOISE_CONTAINER_RE = re.compile(
    r"nav|menu|footer|header|sidebar|comment|share|social|advert|breadcrumb|related|recommend|copyright",
    re.IGNORECASE,
)
# 解析前先剔除非正文大块内容（脚本/样式/内联 SVG/超长 base64 data URI），
# 显著降低 bs4 内存占用与解析耗时。
_PRE_TAG_SCAN_RE = re.compile(
    r"<\s*(/)?\s*(script|style|svg)\b[^>]*>", re.IGNORECASE
)
_PRE_STRIP_DATA_URI_RE = re.compile(
    r"data:[^\"'\s<>]{0,200};base64,[A-Za-z0-9+/=\s]{20,}", re.IGNORECASE
)
# URL 归一化时丢弃的跟踪参数（保留可能影响内容的业务参数）
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "yclid",
    "msclkid",
    "spm",
    "scm",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "igshid",
    "isappinstalled",
}
# 私网/特殊用途 IPv6 前缀：NAT64、6to4、文档示例地址
_IPV6_FORBIDDEN_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001:db8::/32"),
)


# 同一 URL 的进程内串行锁：先查后插阶段避免并发重复抓取/编译；
# 多进程/多实例场景由 documents.source_url 唯一索引 + IntegrityError 兜底。
_URL_IMPORT_LOCKS: dict[str, threading.Lock] = {}
_URL_IMPORT_LOCKS_GUARD = threading.Lock()


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def error_text(exc: BaseException) -> str:
    """把异常转成面向用户的中文错误文本（截断到 500 字）。"""
    message = str(exc).strip() or exc.__class__.__name__
    if len(message) > 500:
        message = message[:497] + "…"
    return message


def _safe_url_for_log(url: str) -> str:
    """日志用脱敏 URL：只保留 scheme://host[:port]/path，丢弃 query/fragment。"""
    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return "（无效链接）"
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port not in (80, 443):
        host = f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 60:
        path = path[:57] + "…"
    return f"{parts.scheme or '?'}://{host}{path}"


def _size_label(byte_count: int) -> str:
    if byte_count >= 1024 * 1024 and byte_count % (1024 * 1024) == 0:
        return f"{byte_count // (1024 * 1024)}MB"
    if byte_count >= 1024 and byte_count % 1024 == 0:
        return f"{byte_count // 1024}KB"
    return f"{byte_count} 字节"


def _oversize_message(current_bytes: int, max_bytes: int) -> str:
    """响应体超限的统一中文提示（含可操作建议，避免用户只看到“超过 3MB”）。"""
    return (
        f"网页内容过大（当前 {_size_label(current_bytes)}，上限 {_size_label(max_bytes)}），"
        "无法收录；可改用『文本收录』粘贴正文，或请管理员调大 NPU_URL_IMPORT_MAX_BYTES"
    )


@contextmanager
def _quiet_httpx_logging():
    """抓取期间压低 httpx 的 INFO 请求日志（它会打印含 query 的完整 URL）。"""
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    if previous_level < logging.WARNING:
        httpx_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        httpx_logger.setLevel(previous_level)


def _compact_len(text: str) -> int:
    """去掉所有空白后的字符数（中文场景下近似“字数”）。"""
    return len(_WHITESPACE_RE.sub("", text or ""))


def _clean_title(value: str | None) -> str:
    """清理标题：去零宽字符、压缩空白、限长。"""
    text = _ZERO_WIDTH_RE.sub("", str(value or ""))
    text = text.replace("\r", " ").replace("\n", " ")
    text = _LINE_WS_RE.sub(" ", text).strip().strip("\"'“”‘’").strip()
    return text[:MAX_TITLE_CHARS]


def _clean_line(value: str) -> str:
    text = _ZERO_WIDTH_RE.sub("", str(value or ""))
    return _LINE_WS_RE.sub(" ", text).strip()


def _fallback_title_from_url(url: str) -> str:
    """从 URL 猜一个兜底标题（路径最后一段或域名）。"""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    path = unquote(parts.path or "").strip("/")
    if path:
        base = path.rsplit("/", 1)[-1]
        base = re.sub(r"\.(?:s?html?|php|aspx?|jsp)$", "", base, flags=re.IGNORECASE)
        base = _clean_title(base)
        if base and len(base) >= 2:
            return base
    return _clean_title(parts.hostname or "")


def _fallback_untitled() -> str:
    return f"{UNTITLED_PREFIX}-{datetime.now():%Y%m%d-%H%M}"


# ---------------------------------------------------------------------------
# URL 解析 / SSRF 防护
# ---------------------------------------------------------------------------
def _parse_http_url(url: str) -> SplitResult:
    """校验并解析 URL（只允许 http/https、不允许账号密码、端口必须合法）。"""
    value = str(url or "").strip()
    if not value:
        raise ValueError("链接不能为空")
    if any(ch in value for ch in "\r\n\t"):
        raise ValueError("链接包含非法控制字符，无法收录")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValueError("链接格式不正确，无法收录") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(
            f"仅支持 http/https 链接（当前为「{scheme or '空'}」），无法收录该地址"
        )
    if parts.username or parts.password:
        raise ValueError("链接不能包含用户名或密码，无法收录")
    if not parts.hostname:
        raise ValueError("链接缺少域名，无法收录")
    try:
        parts.port  # noqa: B018 - 仅用于触发端口格式校验
    except ValueError as exc:
        raise ValueError("链接端口不合法，无法收录") from exc
    return parts


def _socket_addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """解析域名得到全部 IP（失败转中文 ValueError）。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"无法解析域名「{host}」，请检查链接是否正确") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw_ip = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise ValueError(f"无法解析域名「{host}」，请检查链接是否正确")
    return addresses


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """白名单式公网地址判定。

    * IPv4-mapped IPv6 递归按其 IPv4 地址判断；
    * IPv4：必须 ``is_global``，并显式排除组播/保留/回环/链路本地/未指定；
    * IPv6：显式排除组播/回环/链路本地/site-local/保留/未指定，以及
      NAT64（64:ff9b::/96、64:ff9b:1::/48）、6to4（2002::/16）、文档前缀（2001:db8::/32）。
    """
    if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return _is_public_ip(mapped)
        if (
            ip.is_multicast
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_site_local
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        ):
            return False
        return not any(ip in network for network in _IPV6_FORBIDDEN_NETWORKS)
    if (
        ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
    ):
        return False
    return bool(ip.is_global)


def _normalize_ip_text(raw_ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """把 socket/peer 返回的 IP 字符串（可能带 IPv6 scope id）转成 ipaddress 对象。"""
    value = str(raw_ip or "").strip().split("%", 1)[0]
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _assert_public_url(url: str) -> SplitResult:
    """SSRF 防护：解析主机并拒绝私网/本机/保留地址（可用环境变量放行）。"""
    parts = _parse_http_url(url)
    if config.URL_IMPORT_ALLOW_PRIVATE:
        return parts
    host = (parts.hostname or "").strip()
    port = parts.port or (443 if (parts.scheme or "").lower() == "https" else 80)
    try:
        literal_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    addresses = [literal_ip] if literal_ip is not None else _socket_addresses(host, port)
    for ip in addresses:
        if not _is_public_ip(ip):
            raise ValueError(
                "出于安全考虑，不允许收录私网/本机/保留地址的链接"
                "（如需收录内网资料，请管理员设置 NPU_URL_IMPORT_ALLOW_PRIVATE=1）"
            )
    return parts


def _response_peer_addr(response: httpx.Response) -> str:
    """读取本次连接的真实对端地址（httpx/httpcore 的 ``network_stream``）。

    DNS 校验与实际建连之间存在时间差（DNS rebinding / TOCTOU）：解析时是公网 IP，
    建连时可能已切到内网。这里在建连后、读 body 前取真实 peer 再校验一次。
    """
    network_stream = response.extensions.get("network_stream")
    if network_stream is None:
        raise ValueError("无法校验目标服务器的真实地址，已拒绝本次抓取")
    try:
        server_addr = network_stream.get_extra_info("server_addr")
    except Exception as exc:  # pragma: no cover - httpcore 版本差异
        raise ValueError("无法校验目标服务器的真实地址，已拒绝本次抓取") from exc
    if isinstance(server_addr, (tuple, list)):
        server_addr = server_addr[0] if server_addr else None
    host = str(server_addr or "").strip()
    if not host:
        raise ValueError("无法校验目标服务器的真实地址，已拒绝本次抓取")
    return host


def _assert_public_peer(response: httpx.Response) -> None:
    """校验实际连接的 peer 仍是公网地址（每次重定向都要调用）。"""
    if config.URL_IMPORT_ALLOW_PRIVATE:
        return
    peer_ip = _normalize_ip_text(_response_peer_addr(response))
    if peer_ip is None:
        raise ValueError("目标服务器返回的地址异常，已拒绝本次抓取")
    if not _is_public_ip(peer_ip):
        raise ValueError("目标地址在连接时发生变化（疑似 DNS rebinding），已拒绝本次抓取")


def _check_deadline(deadline: float, timeout: float) -> None:
    """总时限检查（慢速滴水攻击时每 chunk 都要调用）。"""
    if time.monotonic() > deadline:
        raise ValueError(
            f"抓取网页超时（总耗时超过 {int(timeout)} 秒），请稍后重试或改用文本收录"
        )


def _filter_tracking_query(query: str) -> str:
    """去掉 utm_* 等跟踪参数，保留业务参数原文与顺序。"""
    if not query:
        return ""
    kept: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = unquote_plus(pair.split("=", 1)[0]).strip().lower()
        if not key:
            continue
        if key.startswith("utm_") or key in _TRACKING_QUERY_KEYS:
            continue
        kept.append(pair)
    return "&".join(kept)


def _normalize_url(url: str) -> str:
    """规范化 URL，用于查重与落库：

    * scheme/host 小写、去默认端口、去 fragment；
    * 去掉 ``utm_*`` 等跟踪参数；
    * 非根路径去掉结尾斜杠（``/a/`` 与 ``/a`` 视为同一资源）。
    """
    parts = _parse_http_url(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("链接缺少域名，无法收录")
    netloc = f"[{host}]" if ":" in host else host
    try:
        port = parts.port
    except ValueError as exc:  # pragma: no cover - _parse_http_url 已校验
        raise ValueError("链接端口不合法，无法收录") from exc
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    path = parts.path or ""
    if path == "/":
        path = ""
    elif path:
        path = path.rstrip("/")
    query = _filter_tracking_query(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def _parse_content_type(raw_value: str | None) -> tuple[str, str]:
    """解析 Content-Type → ``(mime, charset)``；缺失时 mime 为空字符串。"""
    raw = (raw_value or "").strip()
    if not raw:
        return "", ""
    mime = raw.split(";", 1)[0].strip().lower()
    match = _CHARSET_RE.search(raw)
    charset = (match.group(1) if match else "").strip()
    return mime, charset


def _decode_body(raw: bytes, charset: str) -> str:
    """按 charset / UTF-8 / GB18030 依次解码响应体。"""
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_url(url: str) -> dict:
    """抓取网页并返回原始内容。

    返回 ``{requested_url, final_url, status_code, content_type, charset, content, is_html}``；
    任何不合法/不安全/超限/非 HTML 文本的情况都抛 ``ValueError``（中文原因）。
    """
    normalized = _normalize_url(url)
    _assert_public_url(normalized)

    timeout = max(1.0, float(config.URL_IMPORT_TIMEOUT_SECONDS))
    max_bytes = max(1024, int(config.URL_IMPORT_MAX_BYTES))
    max_redirects = max(0, int(config.URL_IMPORT_MAX_REDIRECTS))
    # 总时限：除 httpx 单次 read 超时外，防止「每 9 秒 1 字节」的慢速滴水攻击
    deadline = time.monotonic() + timeout

    requested_url = normalized
    current = normalized
    seen = {current}
    headers = {
        "User-Agent": config.URL_IMPORT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        # 禁止服务器自动压缩，避免解压开销与压缩炸弹；若服务器仍返回压缩内容，
        # 下方按解压后累计字节数限流（见 iter_bytes 循环）。
        "Accept-Encoding": "identity",
    }

    with _quiet_httpx_logging(), httpx.Client(
        trust_env=False,
        timeout=httpx.Timeout(
            timeout,
            connect=min(READ_TIMEOUT_SECONDS, timeout),
            read=min(READ_TIMEOUT_SECONDS, timeout),
        ),
        headers=headers,
        follow_redirects=False,
    ) as client:
        for _hop in range(max_redirects + 1):
            _check_deadline(deadline, timeout)
            try:
                with client.stream("GET", current) as response:
                    # P0：DNS 解析与真正建连之间存在 TOCTOU；建连后、读 body 前校验真实 peer。
                    _assert_public_peer(response)
                    _check_deadline(deadline, timeout)
                    status = int(response.status_code)
                    if status in _REDIRECT_STATUSES:
                        location = (response.headers.get("location") or "").strip()
                        if not location:
                            raise ValueError("网页重定向缺少目标地址，无法收录")
                        target = _normalize_url(urljoin(current, location))
                        _assert_public_url(target)
                        if target in seen:
                            raise ValueError("网页重定向陷入循环，无法收录")
                        seen.add(target)
                        current = target
                        continue
                    if status >= 400:
                        logger.warning(
                            "抓取网页失败（%s）：HTTP %s", _safe_url_for_log(current), status
                        )
                        raise ValueError(
                            f"网页访问失败（HTTP {status}），请确认链接可公开访问或稍后重试"
                        )
                    if status >= 300:
                        raise ValueError(f"网页返回异常状态（HTTP {status}），无法收录")

                    mime, charset = _parse_content_type(response.headers.get("content-type"))
                    if mime not in _ALLOWED_MIME_TYPES:
                        raise ValueError(
                            "该链接返回的不是网页或纯文本"
                            f"（Content-Type：{mime or '未提供'}），无法收录"
                        )

                    # Content-Length 与「解压后大小」口径：
                    # * 无 Content-Encoding 时，Content-Length 就是实际大小，超限可提前拒绝；
                    # * gzip/br/deflate 时它是压缩后大小，直接与 max_bytes 比较可能误拒，
                    #   因此只有明显超过上限（>2 倍）才提前拒绝，其余交给下方解压后的
                    #   流式累计校验兜底（压缩炸弹无法绕过）。
                    declared = (response.headers.get("content-length") or "").strip()
                    encoding = (
                        (response.headers.get("content-encoding") or "").strip().lower()
                    )
                    compressed = bool(encoding) and encoding != "identity"
                    if declared.isdigit():
                        declared_bytes = int(declared)
                        precheck_limit = max_bytes * 2 if compressed else max_bytes
                        if declared_bytes > precheck_limit:
                            raise ValueError(_oversize_message(declared_bytes, max_bytes))

                    body = bytearray()
                    try:
                        for chunk in response.iter_bytes(chunk_size=64 * 1024):
                            _check_deadline(deadline, timeout)
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise ValueError(_oversize_message(len(body), max_bytes))
                            # 服务器无视 identity 仍返回压缩内容时，额外限制原始下载量，
                            # 防止「压缩后很小/解压后也能绕过」的异常情况占用带宽。
                            if (
                                compressed
                                and response.num_bytes_downloaded > max_bytes * 2
                            ):
                                raise ValueError(
                                    _oversize_message(response.num_bytes_downloaded, max_bytes)
                                )
                    except httpx.DecodingError as exc:
                        raise ValueError(
                            "服务器返回了无法解码的压缩内容，请稍后重试或改用文本收录"
                        ) from exc
                    if not body:
                        raise ValueError("网页内容为空，无法收录")
                    if b"\x00" in bytes(body[:4096]):
                        raise ValueError("网页内容不是文本，无法收录")
                    text = _decode_body(bytes(body), charset)
                    if not text.strip():
                        raise ValueError("网页内容为空，无法收录")
                    return {
                        "requested_url": requested_url,
                        "final_url": current,
                        "status_code": status,
                        "content_type": mime,
                        "charset": charset,
                        "content": text,
                        "is_html": mime in {"text/html", "application/xhtml+xml"},
                    }
            except httpx.TimeoutException as exc:
                logger.warning("抓取网页超时（%s）", _safe_url_for_log(current))
                raise ValueError(
                    f"抓取网页超时（超过 {int(timeout)} 秒），请稍后重试或改用文本收录"
                ) from exc
            except httpx.HTTPError as exc:
                logger.warning(
                    "抓取网页网络错误（%s）：%s",
                    _safe_url_for_log(current),
                    exc.__class__.__name__,
                )
                raise ValueError("无法访问该链接（网络错误），请检查链接或稍后重试") from exc
        raise ValueError(f"网页重定向次数过多（最多 {max_redirects} 次），无法收录")


# ---------------------------------------------------------------------------
# 正文提取（HTML → 标题 + 纯文本）
# ---------------------------------------------------------------------------
def _make_soup(html: str) -> BeautifulSoup:
    """创建 BeautifulSoup：优先 lxml（python-docx 依赖已带入，8MB 页面更快），
    不可用或解析失败时回退到纯标准库 html.parser。"""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml 缺失/极端畸形输入
        return BeautifulSoup(html, "html.parser")


def _meta_content(soup: BeautifulSoup, *keys: str) -> str:
    """按 og:title / twitter:title 取 meta content（优先 <head>，避免整树 find）。"""
    wanted = {key.lower() for key in keys}
    head = soup.head
    if head is not None:
        for meta in head.find_all("meta"):
            key = str(
                meta.get("property") or meta.get("name") or meta.get("itemprop") or ""
            ).strip().lower()
            if key in wanted:
                value = _clean_title(meta.get("content"))  # type: ignore[arg-type]
                if value:
                    return value
        return ""
    # 畸形页面可能没有 head：限制扫描数量，避免超大页面重复整树遍历
    for meta in soup.find_all("meta", limit=50):
        key = str(
            meta.get("property") or meta.get("name") or meta.get("itemprop") or ""
        ).strip().lower()
        if key in wanted:
            value = _clean_title(meta.get("content"))  # type: ignore[arg-type]
            if value:
                return value
    return ""


def _html_title(soup: BeautifulSoup, fallback_title: str | None) -> str:
    """标题优先级：og:title > <title> > h1 > 兜底标题。"""
    title = _meta_content(soup, "og:title", "twitter:title")
    if not title:
        head = soup.head
        title_tag = head.find("title") if head is not None else None
        if title_tag is None:
            title_tag = soup.find("title")
        if title_tag is not None:
            title = _clean_title(title_tag.get_text(" ", strip=True))
    if not title:
        heading = soup.find("h1")
        if heading is not None:
            title = _clean_title(heading.get_text(" ", strip=True))
    return title or _clean_title(fallback_title)


def _looks_like_noise_container(node: Tag) -> bool:
    classes = node.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    marker = " ".join([str(node.get("id") or ""), *[str(item) for item in classes]])
    return bool(_NOISE_CONTAINER_RE.search(marker))


def _strip_tag_blocks(html: str) -> str:
    """线性扫描删除 script/style/svg 整段内容（单次 finditer，无回溯风险）。

    不能用 ``<(script|style|svg).*?</\\1>`` 这类正则：畸形页面里大量未闭合
    ``<script`` 会让每次查找都扫描到文末，形成 O(n²) CPU DoS；这里用状态机
    单次扫描，未闭合标签只丢弃开标签本身，其余内容保留。
    """
    if not html:
        return ""
    parts: list[str] = []
    position = 0
    active_tag: str | None = None
    for match in _PRE_TAG_SCAN_RE.finditer(html):
        closing = bool(match.group(1))
        name = (match.group(2) or "").lower()
        if active_tag is None:
            if closing:
                continue
            parts.append(html[position:match.start()])
            parts.append(" ")
            active_tag = name
            position = match.end()
        elif closing and name == active_tag:
            active_tag = None
            position = match.end()
    if active_tag is None:
        parts.append(html[position:])
    else:
        # 未闭合的 script/style/svg：整段丢弃，避免把脚本内容当正文
        parts.append(" ")
    return "".join(parts)


def _prestrip_html(html: str) -> str:
    """交给 bs4 之前先剔除脚本/样式/SVG 与超长 base64 data URI。

    这些内容与正文无关却常占页面体积大头；提前剔除可显著降低解析内存与耗时，
    也避免 ``<script>`` 里的伪 HTML 干扰结构判断。
    """
    if not html:
        return ""
    text = _strip_tag_blocks(html)
    return _PRE_STRIP_DATA_URI_RE.sub("data:,", text)


def _compute_text_metrics(
    root: Tag | BeautifulSoup,
) -> tuple[
    dict[int, int],
    dict[int, int],
    int,
    list[Tag],
    list[Tag],
    list[Tag],
]:
    """一次后序遍历算出每个节点的正文长度/链接文字长度、最大深度与各类候选节点。

    旧实现对各候选容器反复 ``get_text`` 整树遍历（O(n²)），并用 CSS 选择器
    多轮匹配（8MB 页面可达数秒）；这里改为一次线性遍历：
    节点长度后序聚合，同时收集 ``#js_content`` / ``.rich_media_content``、
    ``article/main/[role=main]`` 与「类名像导航/页脚」的 div 候选，后续只查表。
    """
    text_lengths: dict[int, int] = {}
    link_lengths: dict[int, int] = {}
    order: list[Tag | BeautifulSoup] = []
    media_candidates: list[Tag] = []
    structural_candidates: list[Tag] = []
    noise_candidates: list[Tag] = []
    max_depth = 0
    stack: list[tuple[Tag | BeautifulSoup, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            max_depth = depth
        order.append(node)
        if isinstance(node, Tag):
            node_id = str(node.get("id") or "")
            classes = node.get("class") or []
            if not isinstance(classes, str):
                classes = [str(item) for item in classes]
            else:
                classes = [classes]
            if (
                node_id in {"js_content", "vsb_content", "vsb_content_100", "vsb_content_2"}
                or "rich_media_content" in classes
                or "v_news_content" in classes
            ):
                media_candidates.append(node)
            role = str(node.get("role") or "").strip().lower()
            if node.name in ("article", "main") or role == "main":
                structural_candidates.append(node)
            if node.name in ("div", "section", "ul") and _looks_like_noise_container(node):
                noise_candidates.append(node)
            children = [child for child in node.children if isinstance(child, Tag)]
            for child in reversed(children):
                stack.append((child, depth + 1))
    for node in reversed(order):
        direct_len = 0
        child_text_len = 0
        child_link_len = 0
        is_link = isinstance(node, Tag) and node.name == "a"
        for child in node.children:
            if isinstance(child, Tag):
                child_text_len += text_lengths.get(id(child), 0)
                if not is_link:
                    child_link_len += link_lengths.get(id(child), 0)
            elif isinstance(child, NavigableString) and not isinstance(child, Comment):
                direct_len += _compact_len(str(child))
        total = direct_len + child_text_len
        text_lengths[id(node)] = total
        link_lengths[id(node)] = total if is_link else child_link_len
    return (
        text_lengths,
        link_lengths,
        max_depth,
        media_candidates,
        structural_candidates,
        noise_candidates,
    )


def _strip_link_dense_noise(
    soup: BeautifulSoup,
    text_lengths: dict[int, int],
    link_lengths: dict[int, int],
    noise_candidates: list[Tag],
) -> bool:
    """删除 div 实现的导航/页头/页脚等：class/id 命中噪声词且链接文字占比 ≥50%。

    规范只要求移除 ``nav``/``header``/``footer`` 等语义标签；国内不少站点用
    ``<div id="header">`` 实现，这里按「链接密度」保守清理，正文几乎不会全是链接。
    候选节点已在 :func:`_compute_text_metrics` 中一次遍历收集，避免额外整树扫描。
    返回是否发生过删除（调用方据此决定要不要重算节点长度）。
    """
    if soup.body is None:
        return False
    removed = False
    for node in noise_candidates:
        if node.parent is None:  # 已被上层删除
            continue
        text_len = text_lengths.get(id(node), 0)
        if text_len < 40:
            continue
        if link_lengths.get(id(node), 0) >= text_len * 0.5:
            node.decompose()
            removed = True
    return removed


def _html_to_text(node: Tag | BeautifulSoup | None) -> str:
    """把 HTML 节点转成保留段落的纯文本（实体由 bs4 自动解码）。"""
    if node is None:
        return ""
    pieces: list[str] = []
    for item in node.descendants:
        if isinstance(item, Comment):
            continue
        if isinstance(item, NavigableString):
            text = str(item)
            if text.strip():
                pieces.append(text)
            continue
        if isinstance(item, Tag) and item.name in _LINE_BREAK_TAGS:
            pieces.append("\n")
    lines: list[str] = []
    for line in "".join(pieces).split("\n"):
        cleaned = _clean_line(line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _largest_text_block(
    root: Tag | BeautifulSoup,
    text_lengths: dict[int, int],
) -> Tag | BeautifulSoup:
    """从 body 逐层收敛到「最大文本块」：子容器文本占比 ≥90% 时下沉，最多 8 层。

    ``text_lengths`` 已由 :func:`_compute_text_metrics` 一次算好，本函数只查表，
    不再对每个候选重复整树遍历；单层候选数量有上限，避免异常页面放大开销。
    """
    current: Tag | BeautifulSoup = root
    current_len = text_lengths.get(id(root), 0)
    for _ in range(8):
        best: Tag | None = None
        best_len = 0
        scanned = 0
        for child in current.children:
            if not isinstance(child, Tag) or child.name not in _CONTAINER_TAGS:
                continue
            scanned += 1
            if scanned > MAX_BLOCK_CANDIDATES:
                break
            if _looks_like_noise_container(child):
                continue
            length = text_lengths.get(id(child), 0)
            if length > best_len:
                best, best_len = child, length
        if best is None or best_len < max(MIN_EXTRACTED_CHARS, int(current_len * 0.9)):
            break
        current, current_len = best, best_len
    return current


def _pick_content_root(
    soup: BeautifulSoup,
    text_lengths: dict[int, int],
    max_depth: int,
    media_candidates: list[Tag],
    structural_candidates: list[Tag],
) -> Tag | BeautifulSoup:
    """选择正文节点：公众号容器 > article/main/[role=main] > body 最大文本块。

    候选节点已由 :func:`_compute_text_metrics` 一次遍历收集，避免 soupsieve
    在 8MB/十万级节点页面上反复做 CSS 选择器匹配（实测可占数秒）。
    """
    for node in media_candidates:
        if text_lengths.get(id(node), 0) >= MIN_EXTRACTED_CHARS:
            return node
    for node in structural_candidates:
        if text_lengths.get(id(node), 0) >= MIN_EXTRACTED_CHARS:
            return node
    body = soup.body or soup
    if max_depth > MAX_BLOCK_DEPTH:
        return body  # 深层嵌套：调用方降级为整页 get_text
    return _largest_text_block(body, text_lengths)


def _normalize_plain_body(text: str) -> str:
    """纯文本归一化：统一换行、去行尾空白、压缩空行。"""
    value = _ZERO_WIDTH_RE.sub("", str(text or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(line) for line in value.split("\n")]
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _truncate_body(text: str, limit: int | None = None) -> str:
    max_chars = max(0, int(limit if limit is not None else config.URL_IMPORT_MAX_TEXT_CHARS))
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def _is_wechat_url(url: str) -> bool:
    """判断是否为微信公众号文章域名（mp.weixin.qq.com）。"""
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return False
    return host == "mp.weixin.qq.com" or host.endswith(".mp.weixin.qq.com")


def _ensure_body(text: str, *, source_label: str = "网页", url: str = "") -> None:
    if _compact_len(text) >= MIN_EXTRACTED_CHARS:
        return
    if _is_wechat_url(url):
        hint = (
            "可能是微信验证/反爬页面，请用『文本收录』粘贴正文"
            "（可先在微信中打开文章复制全文）"
        )
    else:
        hint = "请确认链接是文章正文页，或改用『文本收录』粘贴内容"
    raise ValueError(
        f"未从{source_label}中提取到正文（少于 {MIN_EXTRACTED_CHARS} 字）：{hint}"
    )


def extract_article(
    content: str,
    *,
    content_type: str = "text/html",
    url: str = "",
    fallback_title: str | None = None,
) -> dict:
    """从 HTML / 纯文本中提取标题与正文，返回 ``{title, text}``。

    正文不足 100 字、内容为空或 HTML 结构异常时抛 ``ValueError``。
    """
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime == "text/plain":
        body = _normalize_plain_body(content)
        _ensure_body(body, source_label="网页文本", url=url)
        first_line = next((line for line in body.split("\n") if line.strip()), "")
        title = (
            _clean_title(first_line)
            or _clean_title(fallback_title)
            or _fallback_title_from_url(url)
            or _fallback_untitled()
        )
        return {"title": title, "text": _truncate_body(body)}

    try:
        soup = _make_soup(_prestrip_html(content or ""))
        # 单次 find_all(True) + 集合过滤，避免对 15 个噪声标签分别整树过滤
        noise_nodes = [
            tag for tag in soup.find_all(True) if tag.name in _NOISE_TAG_SET
        ]
        for tag in noise_nodes:
            tag.decompose()
        # 注释无需显式删除：metrics 与 _html_to_text 都显式跳过 Comment，
        # get_text() 也不包含注释，省去一次全量字符串遍历。
        (
            text_lengths,
            link_lengths,
            max_depth,
            media_candidates,
            structural_candidates,
            noise_candidates,
        ) = _compute_text_metrics(soup)
        if _strip_link_dense_noise(
            soup, text_lengths, link_lengths, noise_candidates
        ):
            (
                text_lengths,
                _,
                max_depth,
                media_candidates,
                structural_candidates,
                _,
            ) = _compute_text_metrics(soup)
        title = _html_title(soup, fallback_title) or _fallback_title_from_url(url)
        if max_depth > MAX_BLOCK_DEPTH:
            # 深层嵌套页面降级为整页 get_text，避免任何候选遍历放大开销
            root = soup.body or soup
            body = root.get_text("\n", strip=True)
        else:
            body = _html_to_text(
                _pick_content_root(
                    soup,
                    text_lengths,
                    max_depth,
                    media_candidates,
                    structural_candidates,
                )
            )
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover - bs4 对畸形 HTML 一般不会抛异常
        raise ValueError("网页正文解析失败，无法收录该链接") from exc

    # 正文长度是产品配额（URL_IMPORT_MAX_TEXT_CHARS）：超出只截断，不报 400
    _ensure_body(body, url=url)
    return {"title": title or _fallback_untitled(), "text": _truncate_body(body)}


# ---------------------------------------------------------------------------
# 查重 / 结果组装
# ---------------------------------------------------------------------------
def _find_document_by_source_url(source_url: str) -> dict | None:
    if not source_url:
        return None
    return db.query_one(
        "SELECT * FROM documents WHERE source_url = ? ORDER BY created_at LIMIT 1",
        (source_url,),
    )


def _pages_for_document(document_id: str | None) -> list[dict]:
    """文档贡献的 Wiki 词条（id/title/summary），供导入接口回显。"""
    if not document_id:
        return []
    rows = db.query(
        "SELECT p.id AS id, p.title AS title, p.summary AS summary "
        "FROM wiki_pages p JOIN wiki_sources s ON s.page_id = p.id "
        "WHERE s.document_id = ? GROUP BY p.id, p.title, p.summary ORDER BY p.title",
        (document_id,),
    )
    return [
        {"id": row["id"], "title": row["title"], "summary": row.get("summary") or ""}
        for row in rows
    ]


def _duplicated_result(row: dict) -> dict:
    return {
        "duplicated": True,
        "document": ingest.document_to_dict(row),
        "pages": _pages_for_document(row.get("id")),
    }


@contextmanager
def _url_import_lock(normalized_url: str):
    """同一规范化 URL 的进程内互斥锁。

    避免两个并发请求都通过「先查」后同时抓取/编译/插库；多进程或多实例场景
    仍由 ``documents.source_url`` 唯一索引 + :class:`sqlite3.IntegrityError` 兜底。
    """
    with _URL_IMPORT_LOCKS_GUARD:
        lock = _URL_IMPORT_LOCKS.get(normalized_url)
        if lock is None:
            lock = threading.Lock()
            _URL_IMPORT_LOCKS[normalized_url] = lock
    with lock:
        yield


# ---------------------------------------------------------------------------
# 公开接口：URL / 文本 / 批量
# ---------------------------------------------------------------------------
def import_url(
    url: str,
    title: str | None = None,
    tags: list[str] | str | None = None,
) -> dict:
    """收录网页/公众号文章链接。

    返回 ``{duplicated, document, pages}``；重复 URL（``documents.source_url``
    规范化后相同）直接返回已有文档且不重新编译。抓取/提取失败抛中文 ``ValueError``。
    """
    normalized = _normalize_url(url)
    with _url_import_lock(normalized):
        existing = _find_document_by_source_url(normalized)
        if existing is not None:
            logger.info("URL 已收录，跳过重复编译：%s", _safe_url_for_log(normalized))
            return _duplicated_result(existing)

        fetched = fetch_url(normalized)
        article = extract_article(
            fetched.get("content") or "",
            content_type=fetched.get("content_type") or "",
            url=fetched.get("final_url") or normalized,
            fallback_title=_fallback_title_from_url(fetched.get("final_url") or normalized),
        )
        final_title = (
            _clean_title(title)
            or _clean_title(article.get("title"))
            or _fallback_title_from_url(fetched.get("final_url") or normalized)
            or _fallback_untitled()
        )
        try:
            document = ingest.ingest_url(
                final_title,
                article.get("text") or "",
                url=normalized,
                tags=ingest.normalize_tags(tags),
            )
        except sqlite3.IntegrityError:
            # 唯一索引冲突：其它进程/实例已抢先插入同一 source_url。
            existing = _find_document_by_source_url(normalized)
            if existing is not None:
                logger.info(
                    "URL 并发收录冲突，返回既有文档：%s", _safe_url_for_log(normalized)
                )
                return _duplicated_result(existing)
            raise ValueError("该链接正在被收录，请稍后刷新查看") from None

        pages = _pages_for_document(document.get("id"))
        logger.info(
            "URL 收录完成：%s → 《%s》（%s 个词条）",
            _safe_url_for_log(fetched.get("final_url") or normalized),
            final_title,
            len(pages),
        )
        return {"duplicated": False, "document": document, "pages": pages}


def import_text(
    title: str | None,
    text: str,
    tags: list[str] | str | None = None,
    source_url: str = "",
) -> dict:
    """收录一段纯文本（微信/QQ 转发的通知等），返回结构与 :func:`import_url` 相同。"""
    body = _normalize_plain_body(text)
    length = len(body)
    if not body:
        raise ValueError(f"文字内容为空，至少需要 {MIN_IMPORT_TEXT_CHARS} 字")
    if length < MIN_IMPORT_TEXT_CHARS:
        raise ValueError(f"文字内容太短（{length} 字），至少需要 {MIN_IMPORT_TEXT_CHARS} 字")
    if length > MAX_IMPORT_TEXT_CHARS:
        raise ValueError(
            f"文字内容过长（{length} 字），最多支持 {MAX_IMPORT_TEXT_CHARS} 字"
        )

    first_line = next((line for line in body.split("\n") if line.strip()), "")
    final_title = _clean_title(title) or _clean_title(first_line[:40]) or _fallback_untitled()
    if source_url:
        document = ingest.ingest_url(
            final_title,
            body,
            url=source_url,
            tags=ingest.normalize_tags(tags),
        )
    else:
        document = ingest.ingest_text(
            final_title,
            body,
            tags=ingest.normalize_tags(tags),
        )
    pages = _pages_for_document(document.get("id"))
    logger.info("文本收录完成：《%s》（%s 字，%s 个词条）", final_title, length, len(pages))
    return {"duplicated": False, "document": document, "pages": pages}


def import_batch(
    urls: list[str] | tuple[str, ...] | None,
    tags: list[str] | str | None = None,
) -> dict:
    """批量收录链接（≤ ``URL_IMPORT_MAX_URLS`` 条，顺序处理，单条失败不影响其它）。

    返回 ``{items:[{url, ok, duplicated, document_id, pages, error}]}``。
    """
    items_in = list(urls or [])
    if not items_in:
        raise ValueError("请至少提供一个要收录的链接")
    max_urls = max(1, int(config.URL_IMPORT_MAX_URLS))
    if len(items_in) > max_urls:
        raise ValueError(f"一次最多收录 {max_urls} 个链接，请分批提交")

    items: list[dict] = []
    for raw_url in items_in:
        url_text = str(raw_url or "").strip()
        try:
            if not url_text:
                raise ValueError("链接不能为空")
            result = import_url(url_text, tags=tags)
            document = result.get("document") or {}
            items.append(
                {
                    "url": url_text,
                    "ok": True,
                    "duplicated": bool(result.get("duplicated")),
                    "document_id": document.get("id"),
                    "pages": result.get("pages") or [],
                    "error": None,
                }
            )
        except Exception as exc:  # 单条失败不影响其它链接
            error = error_text(exc)
            logger.warning(
                "批量收录失败（%s）：%s", _safe_url_for_log(url_text), error
            )
            items.append(
                {
                    "url": url_text,
                    "ok": False,
                    "duplicated": False,
                    "document_id": None,
                    "pages": [],
                    "error": error,
                }
            )
    return {"items": items}


__all__ = [
    "fetch_url",
    "extract_article",
    "import_url",
    "import_text",
    "import_batch",
    "error_text",
]

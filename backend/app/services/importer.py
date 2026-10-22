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
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import SplitResult, unquote, urljoin, urlsplit, urlunsplit

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
    "form",
    "iframe",
    "svg",
    "canvas",
    "template",
)
_CONTAINER_TAGS = ("article", "main", "section", "div", "td")
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
    """公网地址判定：任何非 global 地址（私网/回环/链路本地/保留/组播/未指定）都拒绝。"""
    return bool(ip.is_global)


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


def _normalize_url(url: str) -> str:
    """规范化 URL：小写协议/域名、去默认端口、去 fragment；保留 path 与 query 原文。"""
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
    return urlunsplit((scheme, netloc, parts.path or "", parts.query, ""))


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

    requested_url = normalized
    current = normalized
    seen = {current}
    headers = {
        "User-Agent": config.URL_IMPORT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    }

    with _quiet_httpx_logging(), httpx.Client(
        trust_env=False,
        timeout=httpx.Timeout(timeout),
        headers=headers,
        follow_redirects=False,
    ) as client:
        for _hop in range(max_redirects + 1):
            try:
                with client.stream("GET", current) as response:
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

                    declared = (response.headers.get("content-length") or "").strip()
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise ValueError(
                            f"网页内容过大（超过 {_size_label(max_bytes)}），无法收录"
                        )

                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise ValueError(
                                f"网页内容过大（超过 {_size_label(max_bytes)}），无法收录"
                            )
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
def _meta_content(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        for attrs in ({"property": key}, {"name": key}, {"itemprop": key}):
            tag = soup.find("meta", attrs=attrs)
            if tag is None:
                continue
            value = _clean_title(tag.get("content"))  # type: ignore[arg-type]
            if value:
                return value
    return ""


def _html_title(soup: BeautifulSoup, fallback_title: str | None) -> str:
    """标题优先级：og:title > <title> > h1 > 兜底标题。"""
    title = _meta_content(soup, "og:title", "twitter:title")
    if not title:
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


def _compact_link_text(node: Tag) -> int:
    total = 0
    for link in node.find_all("a"):
        total += _compact_len(link.get_text(" ", strip=True))
    return total


def _strip_link_dense_noise(soup: BeautifulSoup) -> None:
    """删除 div 实现的导航/页头/页脚等：class/id 命中噪声词且链接文字占比 ≥50%。

    规范只要求移除 ``nav``/``header``/``footer`` 等语义标签；国内不少站点用
    ``<div id="header">`` 实现，这里按「链接密度」保守清理，正文几乎不会全是链接。
    """
    body = soup.body
    if body is None:
        return
    for node in body.find_all(["div", "section", "ul"]):
        if node.parent is None:  # 已被上层删除
            continue
        if not _looks_like_noise_container(node):
            continue
        text_len = _compact_len(_html_to_text(node))
        if text_len < 40:
            continue
        if _compact_link_text(node) >= text_len * 0.5:
            node.decompose()


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


def _largest_text_block(root: Tag | BeautifulSoup) -> Tag | BeautifulSoup:
    """从 body 逐层收敛到「最大文本块」：子容器文本占比 ≥90% 时下沉，最多 8 层。"""
    current: Tag | BeautifulSoup = root
    current_len = _compact_len(_html_to_text(root))
    for _ in range(8):
        best: Tag | None = None
        best_len = 0
        for child in current.find_all(_CONTAINER_TAGS):
            if _looks_like_noise_container(child):
                continue
            length = _compact_len(_html_to_text(child))
            if length > best_len:
                best, best_len = child, length
        if best is None or best_len < max(MIN_EXTRACTED_CHARS, int(current_len * 0.9)):
            break
        current, current_len = best, best_len
    return current


def _pick_content_root(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    """选择正文节点：公众号容器 > article/main/[role=main] > body 最大文本块。"""
    for selector in ("#js_content", ".rich_media_content"):
        node = soup.select_one(selector)
        if node is not None and _compact_len(_html_to_text(node)) >= MIN_EXTRACTED_CHARS:
            return node
    for selector in ("article", "main", "[role=main]"):
        node = soup.select_one(selector)
        if node is not None and _compact_len(_html_to_text(node)) >= MIN_EXTRACTED_CHARS:
            return node
    return _largest_text_block(soup.body or soup)


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


def _ensure_body(text: str, *, source_label: str = "网页") -> None:
    if _compact_len(text) < MIN_EXTRACTED_CHARS:
        raise ValueError(
            f"未从{source_label}中提取到正文（少于 {MIN_EXTRACTED_CHARS} 字），"
            "请确认链接是文章正文页，或改用「文本收录」粘贴内容"
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
        _ensure_body(body, source_label="网页文本")
        first_line = next((line for line in body.split("\n") if line.strip()), "")
        title = (
            _clean_title(first_line)
            or _clean_title(fallback_title)
            or _fallback_title_from_url(url)
            or _fallback_untitled()
        )
        return {"title": title, "text": _truncate_body(body)}

    try:
        soup = BeautifulSoup(content or "", "html.parser")
        for tag in soup.find_all(_NOISE_TAGS):
            tag.decompose()
        for comment in soup.find_all(string=lambda item: isinstance(item, Comment)):
            comment.extract()
        _strip_link_dense_noise(soup)
        title = _html_title(soup, fallback_title) or _fallback_title_from_url(url)
        body = _html_to_text(_pick_content_root(soup))
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover - bs4 对畸形 HTML 一般不会抛异常
        raise ValueError("网页正文解析失败，无法收录该链接") from exc

    _ensure_body(body)
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
    相同）直接返回已有文档且不重新编译。抓取/提取失败抛中文 ``ValueError``。
    """
    normalized = _normalize_url(url)
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
    document = ingest.ingest_url(
        final_title,
        article.get("text") or "",
        url=normalized,
        tags=ingest.normalize_tags(tags),
    )
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

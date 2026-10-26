"""受控校园栏目同步：列表发现 → 正文导入 → 去重。

设计原则（docs/CAMPUS-CONTENT-PLAN.md）：
* 只读取公开栏目列表页，不登录、不绕过验证码、不递归全站；
* 每个源限速、限条数；导入失败只记录，不影响其它源；
* 以 ``documents.source_url`` 去重，重复 URL 不重新编译；
* 附件默认不抓取（部分附件有验证码/WAF），只导入页面正文；
* 可由 Windows 任务计划定时执行 ``scripts/campus_sync.py``。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .. import db
from . import importer

logger = logging.getLogger("npu-assistant.campus")

SOURCES_PATH = Path(__file__).resolve().parent.parent / "campus_sources.json"
_DETAIL_RE = re.compile(r"/info/\d+/\d+\.(?:htm|html)$", re.I)
_DATE_PATTERNS = (
    re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})"),
    re.compile(r"(\d{1,2})[./](\d{1,2})\s+(\d{4})"),
)


@dataclass
class CampusSource:
    id: str
    name: str
    list_url: str
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    interval_hours: int = 6
    max_items: int = 5
    exclude_keywords: list[str] = field(default_factory=list)
    legal_note: str = ""


@dataclass
class ListItem:
    url: str
    title: str
    date: str = ""


def load_config(path: Path | None = None) -> dict:
    data = json.loads((path or SOURCES_PATH).read_text(encoding="utf-8"))
    return data


def load_sources(path: Path | None = None) -> list[CampusSource]:
    data = load_config(path)
    result = []
    for raw in data.get("sources", []):
        result.append(
            CampusSource(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                list_url=str(raw["list_url"]),
                tags=[str(tag) for tag in raw.get("tags", [])],
                enabled=bool(raw.get("enabled", True)),
                interval_hours=int(raw.get("interval_hours", 6)),
                max_items=int(raw.get("max_items", 5)),
                exclude_keywords=[str(word) for word in raw.get("exclude_keywords", [])],
                legal_note=str(raw.get("legal_note") or ""),
            )
        )
    return result


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_date(container) -> str:
    text = _clean_text(container.get_text(" ", strip=True)) if container else ""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if len(match.groups()) == 3 and len(match.group(1)) == 4:
            year, month, day = match.groups()
        else:
            month, day, year = match.groups()
        try:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        except ValueError:
            continue
    return ""


def parse_list_page(
    html: str,
    list_url: str,
    *,
    exclude_keywords: list[str] | tuple[str, ...] = (),
    include_keywords: list[str] | tuple[str, ...] = (),
) -> list[ListItem]:
    """从 CMS 列表页提取详情页链接；纯函数，便于离线测试。"""
    soup = BeautifulSoup(html or "", "html.parser")
    list_parts = urlsplit(list_url)
    excluded = [word.lower() for word in exclude_keywords if word]
    included = [word.lower() for word in include_keywords if word]
    items: list[ListItem] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(list_url, anchor["href"].strip())
        parts = urlsplit(href)
        if parts.scheme not in {"http", "https"} or parts.netloc != list_parts.netloc:
            continue
        if not _DETAIL_RE.search(parts.path):
            continue
        url = href.split("#", 1)[0]
        if url in seen:
            continue
        title = _clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
        if not title or len(title) < 4:
            continue
        lowered = title.lower()
        if excluded and any(word in lowered for word in excluded):
            continue
        if included and not any(word in lowered for word in included):
            continue
        container = anchor.find_parent(["li", "tr", "div"])
        date = _extract_date(container)
        seen.add(url)
        items.append(ListItem(url=url, title=title, date=date))
    return items


class RobotsCache:
    """按站点缓存 robots.txt 规则。

    按 RFC 9309：200 正常解析；4xx（404/403 等，429 除外）视为未提供
    robots.txt，保守按公开页面处理；5xx/网络错误按不可用处理，禁止自动抓取。
    """

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._cache: dict[str, RobotFileParser | bool] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            try:
                response = httpx.get(
                    origin + "/robots.txt",
                    headers={"User-Agent": self.user_agent},
                    timeout=15,
                    follow_redirects=True,
                )
                if response.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
                    self._cache[origin] = parser
                elif 400 <= response.status_code < 500 and response.status_code != 429:
                    # 未提供 robots.txt（含 WAF 对 robots 路径返回 403）：公开页面可访问
                    self._cache[origin] = True
                else:
                    logger.warning("robots.txt 状态 %s，暂停自动抓取：%s", response.status_code, origin)
                    self._cache[origin] = False
            except Exception as exc:
                logger.warning("robots.txt 读取失败，暂停自动抓取：%s (%s)", origin, exc)
                self._cache[origin] = False
        cached = self._cache[origin]
        if isinstance(cached, bool):
            return cached
        return cached.can_fetch(self.user_agent, url)


def fetch_list_page(url: str, user_agent: str, timeout: float = 30.0) -> str:
    response = httpx.get(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    content_type = (response.headers.get("content-type") or "").lower()
    if "html" not in content_type and "text" not in content_type:
        raise ValueError(f"列表页不是 HTML（{content_type or 'unknown'}）")
    return response.text


def existing_source_urls() -> set[str]:
    rows = db.query("SELECT source_url FROM documents WHERE source_url <> ''")
    return {str(row["source_url"]) for row in rows if row.get("source_url")}


def run_source(
    source: CampusSource,
    *,
    user_agent: str,
    delay_seconds: float = 3.0,
    limit: int | None = None,
    dry_run: bool = False,
    robots: RobotsCache | None = None,
) -> dict:
    robots = robots or RobotsCache(user_agent)
    summary = {
        "source": source.id,
        "name": source.name,
        "list_url": source.list_url,
        "candidates": 0,
        "new": 0,
        "imported": [],
        "skipped": [],
        "errors": [],
    }
    if not robots.allowed(source.list_url):
        summary["errors"].append("robots.txt 不允许抓取列表页")
        return summary
    time.sleep(max(0.0, delay_seconds))
    try:
        html = fetch_list_page(source.list_url, user_agent)
    except Exception as exc:
        summary["errors"].append(f"列表页抓取失败：{exc}")
        return summary
    items = parse_list_page(html, source.list_url, exclude_keywords=source.exclude_keywords)
    summary["candidates"] = len(items)
    existing = existing_source_urls()
    max_items = max(1, int(limit if limit is not None else source.max_items))
    for item in items:
        if item.url in existing:
            continue
        if summary["new"] >= max_items:
            summary["skipped"].append({"url": item.url, "reason": f"超过本次上限 {max_items}"})
            continue
        if not robots.allowed(item.url):
            summary["skipped"].append({"url": item.url, "reason": "robots.txt 不允许"})
            continue
        summary["new"] += 1
        if dry_run:
            summary["imported"].append({"url": item.url, "title": item.title, "date": item.date, "dry_run": True})
            continue
        time.sleep(max(0.0, delay_seconds))
        try:
            result = importer.import_url(item.url, title=item.title, tags=source.tags)
            doc = result.get("document") or {}
            summary["imported"].append(
                {
                    "url": item.url,
                    "title": doc.get("title") or item.title,
                    "date": item.date,
                    "document_id": doc.get("id"),
                    "status": doc.get("status"),
                    "duplicated": bool(result.get("duplicated")),
                }
            )
            existing.add(item.url)
        except Exception as exc:
            summary["errors"].append({"url": item.url, "title": item.title, "error": str(exc)})
    return summary


def run_sync(
    *,
    source_ids: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    delay_seconds: float | None = None,
    config_path: Path | None = None,
) -> dict:
    config = load_config(config_path)
    user_agent = str(config.get("user_agent") or "NPU-Assistant campus-sync/0.1")
    delay = float(config.get("request_delay_seconds", 3) if delay_seconds is None else delay_seconds)
    robots = RobotsCache(user_agent)
    selected = set(source_ids or [])
    results = []
    for source in load_sources(config_path):
        if not source.enabled:
            continue
        if selected and source.id not in selected:
            continue
        logger.info("同步源 %s：%s", source.id, source.list_url)
        results.append(
            run_source(
                source,
                user_agent=user_agent,
                delay_seconds=delay,
                limit=limit,
                dry_run=dry_run,
                robots=robots,
            )
        )
    return {"dry_run": dry_run, "results": results}

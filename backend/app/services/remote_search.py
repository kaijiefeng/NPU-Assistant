"""限定范围远程检索（不写数据库）。

* 网站：调用指定站点的公开站内搜索接口（VSB AOP），只接受配置的域名；
* 公众号：通过搜狗微信搜索发现公开文章，再还原 mp.weixin.qq.com 原文并抓正文；
* 结果只做临时上下文/引用，内存 TTL 缓存，不落库；
* 不登录、不绕验证码；触发验证码/反爬时返回明确错误，交由人工。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("npu-assistant.remote")

SOURCES_PATH = Path(__file__).resolve().parent.parent / "remote_sources.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
CACHE_TTL_SECONDS = 15 * 60
_CACHE: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}


@dataclass
class SiteSource:
    id: str
    name: str
    host: str
    owner: str
    enabled: bool = True
    max_results: int = 5
    allowed_domains: list[str] | None = None


@dataclass
class WechatSource:
    id: str
    name: str
    account: str
    aliases: list[str]
    enabled: bool = True
    max_results: int = 3


def load_sources(path: Path | None = None) -> dict:
    return json.loads((path or SOURCES_PATH).read_text(encoding="utf-8"))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)).strip()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _cache_get(key: tuple[str, str, str]) -> list[dict] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    created, value = item
    if time.time() - created > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: tuple[str, str, str], value: list[dict]) -> None:
    if len(_CACHE) > 200:
        oldest = sorted(_CACHE.items(), key=lambda pair: pair[1][0])[:50]
        for old_key, _ in oldest:
            _CACHE.pop(old_key, None)
    _CACHE[key] = (time.time(), value)


def parse_aop_records(data: dict, source_name: str) -> list[dict]:
    if data.get("code") != "0000":
        raise ValueError(str(data.get("msg") or "站内搜索失败"))
    records = ((data.get("data") or {}).get("page") or {}).get("records") or []
    results = []
    for row in records:
        url = str(row.get("url") or "")
        if url.startswith("//"):
            url = "https:" + url
        results.append(
            {
                "source": source_name,
                "title": _clean(row.get("title") or ""),
                "url": url,
                "snippet": _clean(row.get("intro") or ""),
                "date": row.get("createtime") or row.get("createTime") or "",
                "origin": "remote",
                "fetched_at": _now(),
            }
        )
    return results


def parse_sogou_results(html: str, aliases: list[str]) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select("ul.news-list li"):
        title_node = item.select_one("h3 a")
        if not title_node:
            continue
        account_node = item.select_one(".s-p .all-time-y2") or item.select_one(".s-p")
        account = _clean(account_node.get_text(" ", strip=True)) if account_node else ""
        if aliases and account and not any(alias in account or account in alias for alias in aliases):
            continue
        href = title_node.get("href") or ""
        if not href:
            continue
        results.append(
            {
                "title": _clean(title_node.get_text(" ", strip=True)),
                "snippet": _clean((item.select_one(".txt-info") or item).get_text(" ", strip=True))[:300],
                "account": account,
                "link": urljoin("https://weixin.sogou.com", href),
            }
        )
    return results


def parse_sogou_redirect(html: str) -> str:
    parts = re.findall(r"url \+= '([^']*)'", html or "")
    url = "".join(parts).replace("@", "")
    if not url.startswith("http"):
        raise ValueError("未能从搜狗跳转页还原原文 URL")
    return url


def parse_wechat_article(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("#js_content")
    title = soup.select_one("#activity-name")
    account = soup.select_one("#js_name")
    date = soup.select_one("#publish_time")
    return {
        "title": _clean(title.get_text(" ", strip=True) if title else ""),
        "account": _clean(account.get_text(" ", strip=True) if account else ""),
        "date": _clean(date.get_text(" ", strip=True) if date else ""),
        "content": _clean(content.get_text(" ", strip=True) if content else ""),
    }


def search_site(
    client: httpx.Client,
    source: SiteSource,
    query: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    key = ("site", source.id, query)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    size = max(1, int(limit or source.max_results))
    payload = {
        "aliasName": "article_data,open_data",
        "keyWord": query,
        "lastkeyWord": query,
        "searchKeyWord": False,
        "orderType": "date",
        "searchType": "text",
        "searchScope": "1",
        "searchOperator": 1,
        "searchDateType": "",
        "searchDateName": "time.any_time",
        "beginDate": "",
        "endDate": datetime.now().strftime("%Y-%m-%d"),
        "language": "chinese",
        "showId": "",
        "auditing": ["1", "5"],
        "owner": source.owner,
        "token": "tourist",
        "urlPrefix": "/aop_component/",
        "filterTerm": {"wbsourceid": ["0", "6"]},
        "page": {"current": 0, "size": size, "pageSizes": [5, 10, 20], "total": None, "totalPage": 0, "indexs": []},
        "advance": False,
        "advanceKeyWord": "",
        "jumpPageNum": "",
        "lang": "i18n_zh_CN",
    }
    headers = {
        "Authorization": "tourist",
        "owner": source.owner,
        "Referer": source.host + "/",
        "Origin": source.host,
        "Content-Type": "application/json;charset=utf-8",
        "Accept": "application/json, text/plain, */*",
    }
    response = client.post(
        source.host + "/aop_component//webber/search/search/search/queryPage",
        headers=headers,
        content=json.dumps(payload),
        timeout=20,
    )
    response.raise_for_status()
    results = parse_aop_records(response.json(), source.name)
    if source.allowed_domains:
        allowed = {domain.lower() for domain in source.allowed_domains}
        results = [
            row
            for row in results
            if (urlsplit(row["url"]).hostname or "").lower() in allowed
        ]
    for row in results:
        row["source_id"] = source.id
    _cache_set(key, results)
    return results


def _resolve_sogou_link(client: httpx.Client, link: str, referer: str) -> str:
    response = client.get(link, headers={"Referer": referer}, timeout=20)
    response.raise_for_status()
    if "antispider" in str(response.url) or "请输入验证码" in response.text:
        raise ValueError("搜狗微信触发验证码/反爬，停止")
    return parse_sogou_redirect(response.text)


def search_wechat(
    client: httpx.Client,
    source: WechatSource,
    query: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    key = ("wechat", source.id, query)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    search_url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(f"{source.account} {query}")
    response = client.get(search_url, timeout=20)
    response.raise_for_status()
    if "antispider" in str(response.url) or "请输入验证码" in response.text:
        raise ValueError("搜狗微信触发验证码/反爬，停止")
    discovered = parse_sogou_results(response.text, source.aliases)[: max(1, int(limit or source.max_results))]
    results = []
    for item in discovered:
        time.sleep(1)
        try:
            article_url = _resolve_sogou_link(client, item["link"], search_url)
            article_response = client.get(article_url, timeout=25)
            article_response.raise_for_status()
            article = parse_wechat_article(article_response.text)
            article_account = article.get("account") or item["account"]
            if source.aliases and article_account and not any(
                alias in article_account or article_account in alias for alias in source.aliases
            ):
                continue
        except Exception as exc:
            results.append(
                {
                    "source": source.name,
                    "title": item["title"],
                    "url": "",
                    "snippet": item["snippet"],
                    "account": item["account"],
                    "origin": "remote",
                    "fetched_at": _now(),
                    "error": str(exc),
                }
            )
            continue
        results.append(
            {
                "source": source.name,
                "title": article.get("title") or item["title"],
                "url": article_url,
                "snippet": item["snippet"],
                "account": article.get("account") or item["account"],
                "date": article.get("date", ""),
                "content": (article.get("content") or "")[:4000],
                "origin": "remote",
                "fetched_at": _now(),
            }
        )
    for row in results:
        row["source_id"] = source.id
    _cache_set(key, results)
    return results


def search_all(
    query: str,
    *,
    source_ids: list[str] | None = None,
    limit: int | None = None,
    config: dict | None = None,
) -> dict:
    """顺序检索所有启用源；返回 {results, errors}，不写数据库。"""
    data = config or load_sources()
    selected = set(source_ids or [])
    results: list[dict] = []
    errors: list[dict] = []
    with httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True, timeout=25) as client:
        for raw in data.get("sites", []):
            if not raw.get("enabled", True) or (selected and raw["id"] not in selected):
                continue
            source = SiteSource(
                id=str(raw["id"]),
                name=str(raw["name"]),
                host=str(raw["host"]).rstrip("/"),
                owner=str(raw["owner"]),
                enabled=True,
                max_results=int(raw.get("max_results", 5)),
                allowed_domains=[str(item) for item in raw.get("allowed_domains", [])],
            )
            try:
                results.extend(search_site(client, source, query, limit=limit))
            except Exception as exc:
                logger.warning("站点检索失败 %s：%s", source.id, exc)
                errors.append({"source": source.name, "error": str(exc)})
        for raw in data.get("wechat", []):
            if not raw.get("enabled", True) or (selected and raw["id"] not in selected):
                continue
            source = WechatSource(
                id=str(raw["id"]),
                name=str(raw["name"]),
                account=str(raw["account"]),
                aliases=[str(item) for item in raw.get("aliases", [])],
                enabled=True,
                max_results=int(raw.get("max_results", 3)),
            )
            try:
                results.extend(search_wechat(client, source, query, limit=limit))
            except Exception as exc:
                logger.warning("公众号检索失败 %s：%s", source.id, exc)
                errors.append({"source": source.name, "error": str(exc)})
    return {"query": query, "results": results, "errors": errors}

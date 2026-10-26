"""限定范围联网检索探针（不写数据库）。

验证两条路线：
1. 指定网站的站内搜索（西工大官网/研究生院/教务处/集成电路学院 VSB AOP 接口）；
2. 非自有公众号：搜狗微信搜索 → 解析结果 → 还原 mp.weixin 原文链接 → 抓正文。

仅用于公开内容、低频率试验；不登录、不绕验证码。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
WEBSITE_SOURCES = {
    "www": {
        "name": "西北工业大学官网",
        "host": "https://www.nwpu.edu.cn",
        "owner": "1328288089",
    },
    "gs": {
        "name": "西北工业大学研究生院",
        "host": "https://gs.nwpu.edu.cn",
        "owner": "1096673008",
    },
    "jiaowu": {
        "name": "西北工业大学教务处",
        "host": "https://jiaowu.nwpu.edu.cn",
        "owner": "866967863",
    },
    "ic": {
        "name": "西北工业大学集成电路学院",
        "host": "https://weidianzi.nwpu.edu.cn",
        "owner": "2124751840",
    },
}
WECHAT_ACCOUNTS = {
    "nwpu": "西北工业大学",
    "gs": "西北工业大学研究生院",
    "jiaowu": "西北工业大学教务处",
    "ic": "西工大集成电路学院",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)).strip()


def website_search(client: httpx.Client, source_id: str, query: str, size: int = 5) -> list[dict]:
    cfg = WEBSITE_SOURCES[source_id]
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
        "owner": cfg["owner"],
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
        "owner": cfg["owner"],
        "Referer": cfg["host"] + "/",
        "Origin": cfg["host"],
        "Content-Type": "application/json;charset=utf-8",
        "Accept": "application/json, text/plain, */*",
    }
    response = client.post(
        cfg["host"] + "/aop_component//webber/search/search/search/queryPage",
        headers=headers,
        content=json.dumps(payload),
        timeout=20,
    )
    if response.status_code != 200:
        return [{"source": cfg["name"], "error": f"HTTP {response.status_code}"}]
    data = response.json()
    if data.get("code") != "0000":
        return [{"source": cfg["name"], "error": data.get("msg") or "search error"}]
    records = ((data.get("data") or {}).get("page") or {}).get("records") or []
    results = []
    for row in records:
        url = row.get("url") or ""
        if url.startswith("//"):
            url = "https:" + url
        results.append(
            {
                "source": cfg["name"],
                "title": _clean(row.get("title") or ""),
                "url": url,
                "snippet": _clean(row.get("intro") or ""),
                "date": row.get("createtime") or row.get("createTime") or "",
            }
        )
    return results


def _resolve_sogou_link(client: httpx.Client, link: str, referer: str) -> str:
    response = client.get(link, headers={"Referer": referer}, timeout=20)
    if response.status_code != 200:
        raise ValueError(f"Sogou link HTTP {response.status_code}")
    if "antispider" in str(response.url) or "请输入验证码" in response.text:
        raise ValueError("Sogou 触发验证码/反爬，停止")
    parts = re.findall(r"url \+= '([^']*)'", response.text)
    url = "".join(parts).replace("@", "")
    if not url.startswith("http"):
        raise ValueError("未能从 Sogou 跳转页还原原文 URL")
    return url


def _wechat_article(client: httpx.Client, url: str) -> dict:
    response = client.get(url, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
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


def wechat_search(client: httpx.Client, account: str, query: str, limit: int = 3) -> list[dict]:
    search_url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(f"{account} {query}")
    response = client.get(search_url, timeout=20)
    if response.status_code != 200:
        return [{"source": f"公众号:{account}", "error": f"HTTP {response.status_code}"}]
    if "antispider" in str(response.url) or "请输入验证码" in response.text:
        return [{"source": f"公众号:{account}", "error": "Sogou 触发验证码/反爬，停止"}]
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict] = []
    for item in soup.select("ul.news-list li"):
        if len(results) >= limit:
            break
        title_node = item.select_one("h3 a")
        if not title_node:
            continue
        item_account = _clean(
            (item.select_one(".s-p .all-time-y2") or item.select_one(".s-p")).get_text(" ", strip=True)
            if item.select_one(".s-p")
            else ""
        )
        if account and item_account and account not in item_account and item_account not in account:
            continue
        href = title_node.get("href") or ""
        if not href:
            continue
        link = urljoin("https://weixin.sogou.com", href)
        try:
            article_url = _resolve_sogou_link(client, link, search_url)
            article = _wechat_article(client, article_url)
        except Exception as exc:
            results.append(
                {
                    "source": f"公众号:{item_account or account}",
                    "title": _clean(title_node.get_text(" ", strip=True)),
                    "url": "",
                    "snippet": _clean((item.select_one(".txt-info") or item).get_text(" ", strip=True))[:160],
                    "error": str(exc),
                }
            )
            continue
        results.append(
            {
                "source": f"公众号:{article.get('account') or item_account or account}",
                "title": article.get("title") or _clean(title_node.get_text(" ", strip=True)),
                "url": article_url,
                "snippet": _clean((item.select_one(".txt-info") or item).get_text(" ", strip=True))[:160],
                "date": article.get("date", ""),
                "content_chars": len(article.get("content") or ""),
            }
        )
        time.sleep(1)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="集成电路 研究生")
    parser.add_argument("--site", action="append", default=[], help="www / gs / jiaowu / ic")
    parser.add_argument("--wechat", action="append", default=[], help="nwpu / gs / jiaowu / ic")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    client = httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True, timeout=25)
    out = {"query": args.query, "sites": {}, "wechat": {}}
    for site in args.site or ["ic"]:
        out["sites"][site] = website_search(client, site, args.query, args.limit)
    for key in args.wechat:
        out["wechat"][key] = wechat_search(client, WECHAT_ACCOUNTS.get(key, key), args.query, args.limit)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

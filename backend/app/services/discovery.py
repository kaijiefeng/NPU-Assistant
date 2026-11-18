"""订阅网址发现（RSI）。

管理员订阅列表页 URL → 一键检查：抓列表页 → 提取同源文章链接 → 与已入库
``documents.source_url`` 及上次发现对比出新内容 → LLM 逐条生成一句话摘要
供挑选 → 确认后走 :func:`importer.import_url` 一键收录。

合规约束（承袭 docs/CAMPUS-CONTENT-PLAN.md）：仅公开免登录页面；robots.txt
禁止抓取的站点直接跳过；每源单次最多 20 条候选；内容只有管理员确认后才入库。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .. import db
from . import importer, llm
from .campus_sync import RobotsCache, existing_source_urls

logger = logging.getLogger("npu-assistant")

_USER_AGENT = "NPU-Assistant-Discovery/0.1 (+repo: NPU-Assistant)"
_MAX_DISCOVERIES = 20
_SUMMARY_BATCH = 5  # 单次检查最多抓正文做 LLM 摘要的条数（限速防反爬）

# 通用详情页链接：同源 .htm/.html/.shtml 结尾（避免列表页/目录/锚点）
_DETAIL_SUFFIX_RE = re.compile(r"\.(?:shtml|html|htm)$", re.I)


@dataclass
class LinkItem:
    url: str
    title: str
    date: str = ""


def _extract_links(html: str, list_url: str) -> list[LinkItem]:
    """从列表页提取同源详情页链接（通用规则，不限 CMS 结构）。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    base = urlsplit(list_url)
    seen: set[str] = set()
    items: list[LinkItem] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(list_url, anchor["href"].strip())
        parts = urlsplit(href)
        if parts.scheme not in {"http", "https"} or parts.netloc != base.netloc:
            continue
        path = parts.path or ""
        if not _DETAIL_SUFFIX_RE.search(path):
            continue
        url = href.split("#", 1)[0]
        if url in seen:
            continue
        title = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").strip()
        if len(title) < 4:
            continue
        seen.add(url)
        items.append(LinkItem(url=url, title=title[:120]))
    return items


_robots = RobotsCache(_USER_AGENT)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _row_to_subscription(row: dict) -> dict:
    discoveries = db.from_json(row.get("discoveries"), [])
    ignored = db.from_json(row.get("ignored_urls"), [])
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "url": row.get("url") or "",
        "enabled": bool(row.get("enabled")),
        "last_checked_at": row.get("last_checked_at"),
        "last_status": row.get("last_status") or "",
        "discoveries": discoveries if isinstance(discoveries, list) else [],
        "ignored_urls": ignored if isinstance(ignored, list) else [],
        "created_at": row.get("created_at"),
    }


def _get_subscription(sub_id: str) -> dict:
    row = db.query_one("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
    if not row:
        raise ValueError("订阅不存在或已被删除")
    return _row_to_subscription(row)


def get_subscription(sub_id: str) -> dict:
    """公共读取（路由 404 判定用）。"""
    return _get_subscription(sub_id)


def list_subscriptions() -> dict:
    rows = db.query("SELECT * FROM subscriptions ORDER BY created_at, rowid")
    return {"items": [_row_to_subscription(row) for row in rows]}


def create_subscription(name: str, url: str) -> dict:
    clean_name = str(name or "").strip()[:60]
    clean_url = importer._normalize_url(str(url or ""))
    importer._assert_public_url(clean_url)
    if not clean_name:
        clean_name = urlsplit(clean_url).netloc
    duplicate = db.query_one("SELECT id FROM subscriptions WHERE url = ?", (clean_url,))
    if duplicate:
        raise ValueError("该 URL 已在订阅列表中")
    sub_id = db.new_id()
    db.execute(
        "INSERT INTO subscriptions(id, name, url, enabled, discoveries, ignored_urls, created_at) "
        "VALUES(?, ?, ?, 1, '[]', '[]', ?)",
        (sub_id, clean_name, clean_url, db.now()),
    )
    return _get_subscription(sub_id)


def delete_subscription(sub_id: str) -> dict:
    _get_subscription(sub_id)
    db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    return {"ok": True, "id": sub_id}


def toggle_subscription(sub_id: str, enabled: bool) -> dict:
    _get_subscription(sub_id)
    db.execute(
        "UPDATE subscriptions SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, db.now(), sub_id),
    )
    return _get_subscription(sub_id)


# ---------------------------------------------------------------------------
# 检查与收录
# ---------------------------------------------------------------------------
def check_subscription(sub_id: str) -> dict:
    """检查一个订阅：返回并持久化本轮发现的新内容（不自动入库）。"""
    sub = _get_subscription(sub_id)
    if not sub["enabled"]:
        raise ValueError("订阅已停用，请先启用")

    def finish(status: str, discoveries: list[dict], message: str) -> dict:
        db.execute(
            "UPDATE subscriptions SET last_checked_at = ?, last_status = ?, "
            "discoveries = ?, updated_at = ? WHERE id = ?",
            (db.now(), status, json.dumps(discoveries, ensure_ascii=False), db.now(), sub_id),
        )
        return {"subscription_id": sub_id, "status": status, "message": message,
                "discoveries": discoveries}

    if not _robots.allowed(sub["url"]):
        logger.warning("订阅 %s 被 robots.txt 禁止抓取，跳过检查", sub["url"])
        return finish("robots-disallowed", [], "robots.txt 禁止抓取该站点，已跳过检查")

    try:
        fetched = importer.fetch_url(sub["url"])
    except ValueError as exc:
        return finish("fetch-failed", [], f"抓取失败：{exc}")
    if not fetched.get("is_html"):
        return finish("fetch-failed", [], "订阅地址不是 HTML 页面，无法提取链接")

    try:
        items = _extract_links(fetched.get("content") or "", fetched["final_url"] or sub["url"])
    except Exception as exc:  # noqa: BLE001 - 解析异常不阻塞订阅
        logger.exception("订阅列表页解析失败：%s", sub["url"])
        return finish("parse-failed", [], f"列表页解析失败：{exc}")

    known = existing_source_urls()
    ignored = set(sub["ignored_urls"])
    previous = {
        item.get("url"): item for item in sub["discoveries"] if isinstance(item, dict)
    }
    # 上一轮遗留：已收录（出现在 source_url）的条目清掉，未处理的保留
    carried = [d for url, d in previous.items() if url and url not in known]

    fresh = [
        item
        for item in items
        if item.url not in known and item.url not in ignored and item.url not in previous
    ][:_MAX_DISCOVERIES]
    if not fresh:
        merged = carried[:_MAX_DISCOVERIES]
        return finish(
            "ok", merged, f"本轮没有新内容；待挑选 {len(merged)} 条" if merged else "本轮没有发现新内容"
        )

    summaries = _summarize(fresh)
    new_entries = [
        {
            "url": item.url,
            "title": summaries.get(item.url, {}).get("title") or item.title or item.url,
            "date": item.date or "",
            "summary": summaries.get(item.url, {}).get("summary") or "",
            "collected": False,
        }
        for item in fresh
    ]
    merged = (carried + new_entries)[:_MAX_DISCOVERIES]
    result = finish(
        "ok",
        merged,
        f"发现 {len(new_entries)} 条新内容；待挑选 {len(merged)} 条",
    )
    logger.info("订阅 %s 发现 %d 条新内容", sub["name"] or sub["url"], len(new_entries))
    return result


def collect_discovery(sub_id: str, url: str, title: str = "") -> dict:
    """一键收录一条发现（import_url），成功后从发现列表移除。"""
    sub = _get_subscription(sub_id)
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("缺少要收录的链接")
    result = importer.import_url(url=clean_url, title=title or None, tags=["订阅发现"])
    _drop_discovery(sub_id, clean_url)
    return {**result, "subscription_id": sub_id}


def ignore_discovery(sub_id: str, url: str) -> dict:
    """忽略一条发现：本轮不再展示，且后续检查不再重复提示。"""
    sub = _get_subscription(sub_id)
    clean_url = str(url or "").strip()
    _drop_discovery(sub_id, clean_url)
    ignored = list(sub["ignored_urls"])
    if clean_url and clean_url not in ignored:
        ignored.append(clean_url)
        db.execute(
            "UPDATE subscriptions SET ignored_urls = ?, updated_at = ? WHERE id = ?",
            (json.dumps(ignored[:200], ensure_ascii=False), db.now(), sub_id),
        )
    return {"ok": True, "subscription_id": sub_id}


# ---------------------------------------------------------------------------
def _drop_discovery(sub_id: str, url: str) -> None:
    row = db.query_one("SELECT discoveries FROM subscriptions WHERE id = ?", (sub_id,))
    if not row:
        return
    discoveries = db.from_json(row.get("discoveries"), [])
    remaining = [item for item in discoveries if isinstance(item, dict) and item.get("url") != url]
    db.execute(
        "UPDATE subscriptions SET discoveries = ?, updated_at = ? WHERE id = ?",
        (json.dumps(remaining, ensure_ascii=False), db.now(), sub_id),
    )


def _summarize(items: list) -> dict[str, dict]:
    """对前若干条抓正文并让 LLM 概括；失败条目回退为标题即摘要。"""
    summaries: dict[str, dict] = {}
    corpus: list[dict] = []
    for item in items[:_SUMMARY_BATCH]:
        try:
            fetched = importer.fetch_url(item.url)
            article = importer.extract_article(
                fetched.get("content") or "",
                content_type=fetched.get("content_type") or "text/html",
                url=item.url,
                fallback_title=item.title or None,
            )
            corpus.append(
                {
                    "url": item.url,
                    "title": article.get("title") or item.title,
                    "excerpt": (article.get("text") or "")[:300],
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单条正文抓取失败不影响整体
            logger.info("订阅发现正文抓取失败（%s）：%s", item.url, exc)

    if corpus:
        response = llm.chat(
            [
                {
                    "role": "system",
                    "content": "你是校园知识库运营助手，为管理员概括网页内容，便于判断是否收录。",
                },
                {
                    "role": "user",
                    "content": json.dumps(corpus, ensure_ascii=False)
                    + '\n请只输出 JSON 数组：[{"url":"...","summary":"一句话概括内容与收录价值（40 字内）"}]，'
                    "url 原样返回。",
                },
            ],
            temperature=0.1,
            max_tokens=600,
        )
        if not response.get("fallback"):
            from .wiki_builder import _extract_json

            for item in _extract_json(str(response.get("content") or ""), list) or []:
                if isinstance(item, dict) and item.get("url"):
                    summaries[item["url"]] = {
                        "summary": str(item.get("summary") or "")[:120],
                    }
    return summaries

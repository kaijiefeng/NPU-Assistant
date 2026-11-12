#!/usr/bin/env python3
"""Wiki 导航的规模压测（只读，不调用 LLM）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\bench_wiki_scale.py
    .\\.venv\\Scripts\\python.exe scripts\\bench_wiki_scale.py --json

脚本读取 ``backend/data/app.db`` 中 ``status='ready'`` 的词条，按倍数复制成
1x / 2x / 4x / 8x / 16x 规模，测量：

* 目录（TOC）字符数与估算 token 数；
* 关键词候选扫描耗时；
* 标签分组数与最大分组规模；
* 两级 LLM 选页是否还能把目录压到 12000 字以内。

复制出的词条只改 id、不改标题，因此目录长度严格线性增长；这不是线上数据，
只用于观察查询侧成本的量级。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.services import wiki_nav  # noqa: E402

QUERIES = [
    "国家奖学金怎么申请？需要哪些材料？",
    "请假流程是什么？病假需要什么证明？",
    "心理中心怎么预约？",
    "宿舍安全管理规定",
    "转专业需要什么条件？",
    "毕业生三方协议怎么签？",
]


def load_pages() -> list[dict]:
    # 只读打开：库不存在时直接报错，避免 sqlite3 顺手创建空库。
    uri = f"file:{config.DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM wiki_pages WHERE status = 'ready' ORDER BY title"
            )
        ]
    finally:
        conn.close()
    pages = []
    for row in rows:
        page = wiki_nav._page_dict(row)
        if page:
            pages.append(page)
    return pages


def clone_pages(pages: list[dict], copies: int) -> list[dict]:
    if copies <= 1:
        return pages
    cloned: list[dict] = []
    for copy in range(copies):
        for page in pages:
            item = dict(page)
            item["id"] = f"{page['id']}-x{copy}"
            cloned.append(item)
    return cloned


def time_keyword(pages: list[dict], repeats: int = 8) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        for query in QUERIES:
            wiki_nav._keyword_candidates(query, pages)
        samples.append((time.perf_counter() - started) * 1000 / len(QUERIES))
    return round(statistics.median(samples), 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wiki navigation scale benchmark")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--max-copies", type=int, default=16)
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    try:
        base = load_pages()
    except sqlite3.Error as exc:
        print(f"无法读取 {config.DB_PATH}：{exc}", file=sys.stderr)
        return 1
    if not base:
        print(f"{config.DB_PATH} 中没有 ready 词条", file=sys.stderr)
        return 1

    results = []
    copies = 1
    while copies <= args.max_copies:
        pages = clone_pages(base, copies)
        toc = wiki_nav._build_toc(pages)
        tags: dict[str, int] = {}
        for page in pages:
            tag = wiki_nav._primary_tag(page)
            tags[tag] = tags.get(tag, 0) + 1
        largest_tag, largest_tag_count = max(tags.items(), key=lambda item: item[1])
        # 两级选页：先选 4 个标签组，再把这些组的目录交给模型。
        top_groups = sorted(tags.items(), key=lambda item: -item[1])[:4]
        top_group_pages = sum(count for _tag, count in top_groups)
        # 近似该组过滤后的目录长度（按平均每页目录字符数）。
        avg_line = len(toc) / len(pages)
        results.append(
            {
                "copies": copies,
                "pages": len(pages),
                "tags": len(tags),
                "largest_tag": largest_tag,
                "largest_tag_pages": largest_tag_count,
                "top4_group_pages": top_group_pages,
                "toc_chars": len(toc),
                "toc_tokens_est": round(len(toc) / 1.6),
                "avg_toc_line_chars": round(avg_line, 1),
                "top4_toc_chars_est": round(top_group_pages * avg_line),
                "llm_nav_toc_over_limit": len(toc) > config.WIKI_TOC_MAX_CHARS,
                "keyword_ms": time_keyword(pages),
            }
        )
        copies *= 2

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    print(
        f"当前词条 {len(base)} 条；WIKI_TOC_MAX_CHARS={config.WIKI_TOC_MAX_CHARS}，"
        f"WIKI_MAX_PAGES={config.WIKI_MAX_PAGES}"
    )
    header = (
        f"{'pages':>6} {'tags':>5} {'max组':>6} {'目录字符':>9} "
        f"{'≈tokens':>8} {'前4组≈字符':>11} {'keyword ms':>11} {'LLM目录':>8}"
    )
    print(header)
    print("-" * len(header))
    for row in results:
        nav = "超限" if row["llm_nav_toc_over_limit"] else "可直读"
        print(
            f"{row['pages']:>6} {row['tags']:>5} {row['largest_tag_pages']:>6} "
            f"{row['toc_chars']:>9} {row['toc_tokens_est']:>8} "
            f"{row['top4_toc_chars_est']:>11} {row['keyword_ms']:>11.2f} {nav:>8}"
        )
    print(
        "\n注：目录按“每页一行”线性增长；keyword 为纯 CPU 扫描耗时；"
        "LLM 选页的两级过滤只在目录超过上限时触发。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

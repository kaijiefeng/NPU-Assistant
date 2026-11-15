#!/usr/bin/env python3
"""把 `docs/npu-handbook/collect/` 采集语料批量导入应用知识库。

采集语料共 2500+ 篇官方公开文档，默认**不混入**精选 Wiki 快照；本脚本提供可控的
分批导入：按文档类型/主题筛选、跳过已入库、限流、断点续传，导入后由应用异步编译成词条。

用法：
    # 1) 先看会导入多少（不写任何数据）
    .\\.venv\\Scripts\\python.exe scripts\\import_handbook_collect.py --dry-run

    # 2) 只导入制度/办事指南类，最多 200 篇，导入到正在运行的本机应用
    .\\.venv\\Scripts\\python.exe scripts\\import_handbook_collect.py --apply --limit 200

    # 3) 指定服务地址与管理令牌（启用令牌时）
    .\\.venv\\Scripts\\python.exe scripts\\import_handbook_collect.py --apply --base-url http://127.0.0.1:8000 --token ***

产物：`docs/npu-handbook/collect/imported-app.jsonl`（每行一个已提交文件，便于断点续传）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
COLLECT = ROOT / "docs" / "npu-handbook" / "collect"
MANIFEST = COLLECT / "manifest.jsonl"
RESUME = COLLECT / "imported-app.jsonl"

SKIP_TYPES = {"新闻动态", "其他"}


def load_manifest() -> list[dict]:
    items: list[dict] = []
    with MANIFEST.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def load_resume() -> set[str]:
    done: set[str] = set()
    if RESUME.is_file():
        for line in RESUME.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["file"])
            except Exception:
                continue
    return done


def existing_documents(client: httpx.Client) -> set[str]:
    names: set[str] = set()
    offset = 0
    while True:
        resp = client.get("/api/knowledge/documents", params={"limit": 200, "offset": offset})
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("items") or []
        names.update(item.get("filename", "") for item in items)
        total = payload.get("total") or 0
        offset += len(items)
        if not items or offset >= total:
            break
    return names


def select(items: list[dict], args: argparse.Namespace, skip_files: set[str]) -> list[dict]:
    picked: list[dict] = []
    for item in items:
        if item.get("doc_type") in args.skip_type:
            continue
        if args.doc_type and item.get("doc_type") not in args.doc_type:
            continue
        if args.category and item.get("category") not in args.category:
            continue
        if item.get("chars", 0) < args.min_chars:
            continue
        file_name = str(item.get("file") or "")
        if not file_name or file_name in skip_files:
            continue
        source = COLLECT / file_name
        if not source.is_file():
            continue
        item["_path"] = source
        picked.append(item)
        if args.limit and len(picked) >= args.limit:
            break
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入采集语料到知识库")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不导入")
    parser.add_argument("--apply", action="store_true", help="实际提交导入")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    parser.add_argument("--doc-type", action="append", default=[], help="只导入这些文档类型，可多次")
    parser.add_argument("--category", action="append", default=[], help="只导入这些主题，可多次")
    parser.add_argument("--skip-type", action="append", default=sorted(SKIP_TYPES))
    parser.add_argument("--min-chars", type=int, default=400)
    parser.add_argument("--limit", type=int, default=0, help="最多导入多少篇，0 表示不限")
    parser.add_argument("--delay", type=float, default=0.4, help="两次提交间隔秒")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not args.dry_run and not args.apply:
        parser.error("请指定 --dry-run 或 --apply")

    items = load_manifest()
    done = load_resume()
    headers = {"X-Admin-Token": args.token} if args.token else {}

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=args.timeout, trust_env=False) as client:
        if args.apply:
            try:
                existing = existing_documents(client)
            except Exception as exc:  # noqa: BLE001
                raise SystemExit(f"读取已入库文档失败（服务是否已启动？）：{exc}") from exc
            if not args.token:
                # 快速探活：未启用令牌时 /api/health 应可访问
                try:
                    client.get("/api/health").raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    raise SystemExit(f"服务不可用：{exc}") from exc
        else:
            existing = set()

        picked = select(items, args, done | existing)
        by_type = Counter(item.get("doc_type") for item in picked)
        by_cat = Counter(item.get("category") for item in picked)
        print(f"清单 {len(items)} 篇；已导入/已提交 {len(done)} 篇；本次候选 {len(picked)} 篇")
        print("文档类型：", dict(by_type))
        print("主题 Top10：", by_cat.most_common(10))
        if args.dry_run:
            return 0

        ok = failed = 0
        for index, item in enumerate(picked, 1):
            path: Path = item["_path"]
            data = path.read_bytes()
            try:
                resp = client.post(
                    "/api/knowledge/documents",
                    params={"async": "1"},
                    files={"file": (path.name, data, "text/markdown")},
                )
                resp.raise_for_status()
                ok += 1
                with RESUME.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "file": item["file"],
                                "title": item.get("title"),
                                "status": resp.status_code,
                                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                print(f"[{index}/{len(picked)}] ok {item.get('title', '')[:40]}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[{index}/{len(picked)}] FAIL {item.get('title', '')[:40]}: {exc}")
            time.sleep(args.delay)
        print(f"完成：提交成功 {ok} 篇，失败 {failed} 篇；进度记录 {RESUME.relative_to(ROOT)}")
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

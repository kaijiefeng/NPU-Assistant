#!/usr/bin/env python3
"""E2E 交互验收入口（沙箱隔离，computer use 风格）。

用法：
    python scripts/e2e_verify.py --headless              # 全量沙箱运行
    python scripts/e2e_verify.py --scenario knowledge.*  # 按 glob 选场景
    python scripts/e2e_verify.py --tag smoke --headed    # 指定标签、可见浏览器
    python scripts/e2e_verify.py --self-test             # 引擎自检（不启浏览器）
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from e2e import engine  # noqa: E402
from e2e import scenarios as _scenarios  # noqa: E402,F401  （注册场景）
from e2e.engine import REGISTRY, run_selected  # noqa: E402
from e2e.sandbox import RunSandbox  # noqa: E402


def select(pattern: str, tag: str, profile: str) -> list:
    items = list(REGISTRY)
    if pattern:
        items = [item for item in items if fnmatch.fnmatch(item.id, pattern)]
    if tag:
        items = [item for item in items if tag in item.tags]
    if profile:
        items = [item for item in items if item.profile == profile]
    return items


def self_test() -> int:
    inventory = engine.inventory_actions()
    total = sum(len(value) for value in inventory.values())
    problems: list[str] = []
    if total < 20:
        problems.append(f"可交互点盘点过少：{total}")
    if not scenario_ids_ok():
        problems.append("场景 id 重复或为空")
    print(f"[self-test] 场景 {len(REGISTRY)} 个 / 可交互点 {total} 个 / view {len(inventory)} 个")
    for item in REGISTRY:
        print(f"  - {item.id} profile={item.profile} covers={len(item.covers)}")
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1
    print("[PASS] 引擎自检通过")
    return 0


def scenario_ids_ok() -> bool:
    ids = [item.id for item in REGISTRY]
    return bool(ids) and len(ids) == len(set(ids)) and all(ids)


async def main_async(args: argparse.Namespace) -> int:
    items = select(args.scenario or "", args.tag or "", args.profile or "")
    if not items:
        print("没有匹配的场景，请检查 --scenario/--tag")
        return 2
    print(f"沙箱运行 {len(items)} 个场景（headless={not args.headed}）", flush=True)
    results, sandbox = await run_selected(items, headless=not args.headed, keep=args.keep)
    counts = {
        status: sum(1 for item in results if item.status == status)
        for status in ("PASS", "FAIL", "KNOWN")
    }
    coverage = engine.coverage_report()
    print(
        f"\n结果：PASS {counts['PASS']} / FAIL {counts['FAIL']} / KNOWN {counts['KNOWN']}"
        f" | 覆盖率 {coverage['percent']}% ({coverage['covered']}/{coverage['total']})"
    )
    print(f"报告：{sandbox.report_dir}")

    if args.publish:
        engine.publish_evidence(
            sandbox,
            results,
            coverage,
            shots_dir=ROOT / "docs" / "screenshots" / "e2e",
            doc_path=ROOT / "docs" / "E2E-COVERAGE.md",
        )
        print(f"证据已发布：{ROOT / 'docs' / 'E2E-COVERAGE.md'}")

    if args.junit:
        source = sandbox.report_dir / "junit.xml"
        if source.is_file():
            Path(args.junit).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, args.junit)

    keep = args.keep or counts["FAIL"]
    sandbox.cleanup(keep=keep)
    if keep:
        print(f"已保留沙箱现场：{sandbox.root}")

    if args.min_coverage and coverage["percent"] < args.min_coverage:
        print(f"[FAIL] 覆盖率 {coverage['percent']}% 低于阈值 {args.min_coverage}%")
        return 1
    return 1 if counts["FAIL"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E 交互验收（沙箱隔离）")
    parser.add_argument("--scenario", default="", help="场景 id glob，如 knowledge.*")
    parser.add_argument("--tag", default="", help="按标签筛选")
    parser.add_argument("--profile", default="", choices=["", "open", "account", "token"])
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--headless", action="store_true", help="无头模式（默认，显式声明便于 CI 阅读）")
    parser.add_argument("--keep", action="store_true", help="保留沙箱目录")
    parser.add_argument("--junit", default="", help="额外输出 JUnit XML 路径")
    parser.add_argument("--min-coverage", type=float, default=0.0, help="覆盖率下限（百分比）")
    parser.add_argument("--self-test", action="store_true", help="只做引擎自检")
    parser.add_argument("--ci", action="store_true", help="CI 模式（等价 headless + 保留失败现场）")
    parser.add_argument("--publish", action="store_true", help="把截图与覆盖文档发布到 docs/（作为验证证据）")
    args = parser.parse_args()
    if args.ci:
        args.headed = False
    if args.self_test:
        return self_test()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

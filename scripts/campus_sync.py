"""定时/手动同步西工大公开校园栏目（受控、限速、去重）。

示例：
    .\.venv\Scripts\python.exe scripts\campus_sync.py --dry-run
    .\.venv\Scripts\python.exe scripts\campus_sync.py --source xgb-zdgk --limit 3
    .\.venv\Scripts\python.exe scripts\campus_sync.py

建议由 Windows 任务计划每 6 小时运行一次；详见 docs/CAMPUS-CONTENT-PLAN.md。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import campus_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="同步公开校园栏目到 NPU-Assistant 知识库")
    parser.add_argument("--source", action="append", default=[], help="只同步指定源 id，可重复")
    parser.add_argument("--limit", type=int, default=None, help="每个源本次最多导入几条")
    parser.add_argument("--dry-run", action="store_true", help="只列出候选，不抓取详情/不编译")
    parser.add_argument("--delay", type=float, default=None, help="请求间隔秒数（默认 3）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 结果")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = campus_sync.run_sync(
        source_ids=args.source or None,
        limit=args.limit,
        dry_run=args.dry_run,
        delay_seconds=args.delay,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["results"]:
            print(f"\n[{item['source']}] {item['name']}")
            print(f"  候选 {item['candidates']}，新增 {item['new']}")
            for row in item["imported"]:
                print(f"  + {row.get('title')}  {row.get('url')}")
            for row in item["errors"]:
                print(f"  ! {row}")
            for row in item["skipped"][:5]:
                print(f"  - {row}")
    has_error = any(item["errors"] for item in result["results"])
    return 1 if has_error and not args.dry_run else 0


if __name__ == "__main__":
    raise SystemExit(main())

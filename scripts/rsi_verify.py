#!/usr/bin/env python3
"""RSI 端到端质量门禁：把 E2E（含移动端）结果固化为自进化流程的一部分。

RSI 闭环 = 反馈 → 知识缺口 → 补料 → 重编译 → 时效治理。任何自进化改动在合入前
必须通过本门禁：跑 `rsi` 标签的关键场景（含移动端），产出可审计证据。

用法：
    # 独立跑 RSI 关键场景（自举沙箱，约 2 分钟）
    python scripts/rsi_verify.py --headless

    # 复用最近一次 E2E 产物（CI 中先跑全量 E2E，再固化结果）
    python scripts/rsi_verify.py --from-artifacts

产物：
    docs/rsi/e2e-latest.json        # 机器可读门禁结果（提交进仓库，供 RSI 循环读取）
    docs/rsi/latest/                # 本次关键场景报告 + 截图（证据）
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from e2e import engine  # noqa: E402
from e2e import scenarios as _scenarios  # noqa: E402,F401
from e2e.engine import REGISTRY  # noqa: E402

RSI_TAG = "rsi"
DEFAULT_MIN_COVERAGE = 90.0
OUT_DIR = ROOT / "docs" / "rsi"


def rsi_scenario_ids() -> list[str]:
    return [item.id for item in REGISTRY if RSI_TAG in item.tags]


def _latest_report() -> Path:
    reports = sorted(
        (ROOT / ".artifacts" / "e2e").glob("*/report.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not reports:
        raise SystemExit("未找到 E2E 产物，请先运行 scripts/e2e_verify.py --publish")
    return reports[-1]


def _copy_evidence(report: dict, report_path: Path, target: Path) -> list[str]:
    shots: list[str] = []
    keep = set(rsi_scenario_ids())
    for item in report.get("scenarios", []):
        if item["id"] not in keep:
            continue
        for step in item.get("steps", []):
            shot = step.get("screenshot") or ""
            if not shot:
                continue
            source = report_path.parent / shot
            if not source.is_file():
                continue
            dest = target / "screenshots" / item["id"] / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            shots.append(str(dest.relative_to(OUT_DIR)))
    return shots


def verify_from_artifacts(min_coverage: float) -> int:
    report_path = _latest_report()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    keep = set(rsi_scenario_ids())
    scenarios = [item for item in report.get("scenarios", []) if item["id"] in keep]
    failures = [item for item in scenarios if item["status"] == "FAIL"]
    skipped = [item for item in scenarios if item["status"] == "SKIP"]
    coverage = float((report.get("coverage") or {}).get("percent") or 0)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    latest = OUT_DIR / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    latest.mkdir(parents=True)
    shutil.copyfile(report_path, latest / "report.json")
    junit = report_path.parent / "junit.xml"
    if junit.is_file():
        shutil.copyfile(junit, latest / "junit.xml")
    screenshots = _copy_evidence(report, report_path, latest)

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": commit,
        "source_report": str(report_path.relative_to(ROOT)),
        "coverage_percent": coverage,
        "min_coverage": min_coverage,
        "rsi_scenarios": [
            {"id": item["id"], "status": item["status"], "duration": item.get("duration")}
            for item in scenarios
        ],
        "counts": {
            "pass": sum(1 for item in scenarios if item["status"] == "PASS"),
            "fail": len(failures),
            "skip": len(skipped),
            "total": len(scenarios),
        },
        "screenshots": screenshots,
        "passed": not failures and coverage >= min_coverage,
        "evidence_dir": "docs/rsi/latest",
    }
    out = OUT_DIR / "e2e-latest.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"RSI 门禁：{summary['counts']['pass']}/{summary['counts']['total']} 通过，"
        f"覆盖率 {coverage}%（门槛 {min_coverage}%），提交 {commit}"
    )
    print(f"证据：{out.relative_to(ROOT)} / {summary['evidence_dir']}")
    return 0 if summary["passed"] else 1


def verify_live(headless: bool, min_coverage: float) -> int:
    scenarios = [item for item in REGISTRY if RSI_TAG in item.tags]
    if not scenarios:
        raise SystemExit("没有 rsi 标签的场景")
    import asyncio

    results, sandbox = asyncio.run(
        engine.run_selected(scenarios, headless=headless, keep=False)
    )
    coverage = engine.coverage_report({item.id: item.status for item in results})
    report = {
        "coverage": coverage,
        "scenarios": [
            {
                "id": item.id,
                "status": item.status,
                "duration": item.duration,
                "steps": [
                    {"name": step.name, "status": step.status, "screenshot": step.screenshot}
                    for step in item.steps
                ],
            }
            for item in results
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    latest = OUT_DIR / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    latest.mkdir(parents=True)
    (latest / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    screenshots: list[str] = []
    for result in results:
        for shot in result.screenshots:
            source = sandbox.report_dir / shot
            if not source.is_file():
                continue
            dest = latest / "screenshots" / result.id / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            screenshots.append(str(dest.relative_to(OUT_DIR)))
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    failures = [item for item in results if item.status == "FAIL"]
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": commit,
        "source_report": "docs/rsi/latest/report.json",
        "coverage_percent": coverage["percent"],
        "min_coverage": min_coverage,
        "rsi_scenarios": [
            {"id": item.id, "status": item.status, "duration": round(item.duration, 2)}
            for item in results
        ],
        "counts": {
            "pass": sum(1 for item in results if item.status == "PASS"),
            "fail": len(failures),
            "skip": 0,
            "total": len(results),
        },
        "screenshots": screenshots,
        "passed": not failures and coverage["percent"] >= min_coverage,
        "evidence_dir": "docs/rsi/latest",
    }
    (OUT_DIR / "e2e-latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"RSI 门禁：{summary['counts']['pass']}/{summary['counts']['total']} 通过，"
        f"覆盖率 {coverage['percent']}%（门槛 {min_coverage}%）"
    )
    sandbox.cleanup()
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="RSI 端到端质量门禁")
    parser.add_argument("--from-artifacts", action="store_true", help="复用最近一次 E2E 产物")
    parser.add_argument("--headless", action="store_true", help="无头浏览器（默认）")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument("--list", action="store_true", help="只列出 RSI 关键场景")
    args = parser.parse_args()
    if args.list:
        for item in REGISTRY:
            if RSI_TAG in item.tags:
                print(item.id, item.profile)
        return 0
    if args.from_artifacts:
        return verify_from_artifacts(args.min_coverage)
    return verify_live(not args.headed, args.min_coverage)


if __name__ == "__main__":
    raise SystemExit(main())

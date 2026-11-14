"""E2E 引擎单测：沙箱、覆盖统计、报告、场景注册（不启动浏览器/服务）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

from e2e import engine  # noqa: E402
from e2e.sandbox import RunSandbox, fingerprint_production, free_port  # noqa: E402


def test_free_port_distinct():
    ports = {free_port() for _ in range(3)}
    assert len(ports) == 3


def test_fingerprint_has_expected_keys():
    snapshot = fingerprint_production()
    assert {"db", "uploads", "admin_token"} <= set(snapshot)


def test_sandbox_is_temp_and_cleans_up():
    sandbox = RunSandbox.create()
    try:
        assert sandbox.data_dir.is_relative_to(sandbox.root)
        assert sandbox.root.name.startswith("npu-e2e-")
        assert sandbox.port != 8000
        assert sandbox.data_dir.is_dir()
    finally:
        sandbox.cleanup()
    assert not sandbox.root.exists()


def test_action_inventory_is_not_empty():
    inventory = engine.inventory_actions()
    assert sum(len(items) for items in inventory.values()) >= 20


def test_coverage_report_shape():
    coverage = engine.coverage_report()
    assert 0 <= coverage["percent"] <= 100
    assert coverage["total"] >= coverage["covered"]
    assert "views" in coverage


def test_write_reports(tmp_path):
    sandbox = RunSandbox.create(report_root=tmp_path)
    try:
        results = [
            engine.ScenarioResult(
                id="demo.pass",
                profile="open",
                status="PASS",
                duration=1.2,
                steps=[engine.StepResult(name="打开", status="PASS", duration=1.2)],
            ),
            engine.ScenarioResult(
                id="demo.fail",
                profile="open",
                status="FAIL",
                duration=0.5,
                error="AssertionError: boom",
            ),
        ]
        engine.write_reports(sandbox, results, engine.coverage_report())
        payload = json.loads((sandbox.report_dir / "report.json").read_text(encoding="utf-8"))
        assert payload["counts"]["pass"] == 1
        assert payload["counts"]["fail"] == 1
        junit = (sandbox.report_dir / "junit.xml").read_text(encoding="utf-8")
        assert "tests='2'" in junit and "failures='1'" in junit
        assert (sandbox.report_dir / "report.html").is_file()
    finally:
        sandbox.cleanup()

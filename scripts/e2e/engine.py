"""E2E 引擎：场景注册、步骤执行、断言、覆盖率、报告。"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from xml.sax.saxutils import escape as xml_escape

from .browser import Browser
from .sandbox import REPO, RunSandbox
from .server import AppServer

# ---------------------------------------------------------------------------
# 场景注册
# ---------------------------------------------------------------------------
@dataclass
class Scenario:
    id: str
    fn: Callable[["Context"], Awaitable[None]]
    tags: tuple[str, ...] = ()
    profile: str = "open"
    covers: tuple[str, ...] = ()
    known_issues: tuple[str, ...] = ()


REGISTRY: list[Scenario] = []


def scenario(
    id: str,
    *,
    tags: tuple[str, ...] = (),
    profile: str = "open",
    covers: tuple[str, ...] = (),
    known_issues: tuple[str, ...] = (),
):
    def decorator(fn):
        REGISTRY.append(
            Scenario(
                id=id,
                fn=fn,
                tags=tags,
                profile=profile,
                covers=covers,
                known_issues=known_issues,
            )
        )
        return fn

    return decorator


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    name: str
    status: str
    duration: float
    error: str = ""
    screenshot: str = ""


@dataclass
class ScenarioResult:
    id: str
    profile: str
    status: str
    duration: float
    steps: list[StepResult] = field(default_factory=list)
    error: str = ""
    known_issues: tuple[str, ...] = ()
    screenshots: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------
class Context:
    def __init__(
        self,
        sandbox: RunSandbox,
        server: AppServer,
        browser: Browser,
        scenario: Scenario,
    ) -> None:
        self.sandbox = sandbox
        self.server = server
        self.browser = browser
        self.scenario = scenario
        self.steps: list[StepResult] = []
        self.screenshots: list[str] = []
        self._step_start = 0.0

    # -- 步骤 -----------------------------------------------------------
    @asynccontextmanager
    async def step(self, name: str):
        started = time.time()
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            shot = await self._screenshot(f"FAIL-{name}")
            self.steps.append(
                StepResult(name=name, status="FAIL", duration=time.time() - started, error=str(exc)[:800], screenshot=shot)
            )
            raise
        else:
            shot = await self._screenshot(name)
            self.steps.append(
                StepResult(name=name, status="PASS", duration=time.time() - started, screenshot=shot)
            )

    async def _screenshot(self, label: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", label)[:60]
        path = self.sandbox.report_dir / "screenshots" / self.scenario.id / f"{len(self.steps):02d}-{safe}.png"
        try:
            await self.browser.screenshot(path)
            rel = str(path.relative_to(self.sandbox.report_dir))
            self.screenshots.append(rel)
            return rel
        except Exception:  # noqa: BLE001
            return ""

    # -- 断言 -----------------------------------------------------------
    def expect(self, condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    async def expect_text(self, text: str, timeout: float = 15) -> None:
        found = await self.browser.wait_for_text(text, timeout=timeout)
        self.expect(found, f"页面未出现文字：{text}")

    async def expect_selector(self, selector: str, timeout: float = 15) -> None:
        found = await self.browser.wait_for_selector(selector, timeout=timeout)
        self.expect(found, f"页面未出现元素：{selector}")

    def api_get(self, path: str) -> Any:
        response = self.server.api.get(path)
        response.raise_for_status()
        return response.json()

    def api_post(self, path: str, payload: dict | None = None) -> Any:
        response = self.server.api.post(path, json=payload or {})
        response.raise_for_status()
        return response.json()

    # -- 收尾 -----------------------------------------------------------
    def gate(self) -> None:
        errors = self.browser.console_errors()
        failures = [item for item in self.browser.network_failures() if item.get("status")]
        if errors:
            raise AssertionError(f"控制台错误：{errors[:3]}")
        if failures:
            raise AssertionError(f"网络 5xx：{failures[:3]}")


# ---------------------------------------------------------------------------
# 覆盖率
# ---------------------------------------------------------------------------
def inventory_actions() -> dict[str, set[str]]:
    """静态盘点前端可交互点：按 view 文件名收集 data-action 值。"""
    views = REPO / "frontend" / "assets" / "js" / "views"
    result: dict[str, set[str]] = {}
    for path in views.glob("*.js"):
        text = path.read_text(encoding="utf-8", errors="replace")
        actions = set(re.findall(r'data-action="([a-z0-9-]+)"', text))
        if actions:
            result[path.stem] = actions
    return result


def coverage_report() -> dict:
    inventory = inventory_actions()
    covered: set[str] = set()
    for item in REGISTRY:
        covered.update(item.covers)
    total = sum(len(items) for items in inventory.values())
    hit = sum(1 for items in inventory.values() for action in items if action in covered)
    per_view = {
        view: {
            "total": len(actions),
            "covered": len([action for action in actions if action in covered]),
            "missing": sorted(action for action in actions if action not in covered),
        }
        for view, actions in sorted(inventory.items())
    }
    return {
        "total": total,
        "covered": hit,
        "percent": round(hit * 100 / total, 1) if total else 0.0,
        "views": per_view,
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def write_reports(sandbox: RunSandbox, results: list[ScenarioResult], coverage: dict) -> None:
    out = sandbox.report_dir
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "sandbox": sandbox.manifest(),
        "counts": {
            "pass": sum(1 for r in results if r.status == "PASS"),
            "fail": sum(1 for r in results if r.status == "FAIL"),
            "known": sum(1 for r in results if r.status == "KNOWN"),
            "skip": sum(1 for r in results if r.status == "SKIP"),
        },
        "coverage": coverage,
        "scenarios": [
            {
                "id": r.id,
                "profile": r.profile,
                "status": r.status,
                "duration": round(r.duration, 2),
                "known_issues": list(r.known_issues),
                "error": r.error,
                "steps": [
                    {
                        "name": s.name,
                        "status": s.status,
                        "duration": round(s.duration, 2),
                        "error": s.error,
                        "screenshot": s.screenshot,
                    }
                    for s in r.steps
                ],
            }
            for r in results
        ],
    }
    (out / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = "\n".join(
        "<tr class='{cls}'><td>{id}</td><td>{status}</td><td>{duration}s</td><td>{error}</td></tr>".format(
            cls=r.status.lower(),
            id=html.escape(r.id),
            status=r.status,
            duration=f"{r.duration:.1f}",
            error=html.escape((r.error or "")[:300]),
        )
        for r in results
    )
    (out / "report.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>E2E report</title>"
        "<style>body{font-family:system-ui;margin:24px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ddd;padding:6px;font-size:13px}.fail{background:#fde8e8}"
        ".known{background:#fff7e0}.pass{background:#eaf7ea}</style>"
        f"<h1>E2E {html.escape(sandbox.root.name)}</h1><p>覆盖率 {coverage['percent']}% "
        f"({coverage['covered']}/{coverage['total']})</p>"
        f"<table><tr><th>场景</th><th>状态</th><th>耗时</th><th>错误</th></tr>{rows}</table>",
        encoding="utf-8",
    )

    cases = []
    for r in results:
        body = xml_escape(r.error or "")
        if r.status == "FAIL":
            cases.append(
                f"<testcase classname='e2e.{xml_escape(r.profile)}' name='{xml_escape(r.id)}' "
                f"time='{r.duration:.2f}'><failure message='{body[:300]}'>{body}</failure></testcase>"
            )
        else:
            note = "KNOWN: " + ",".join(r.known_issues) if r.status == "KNOWN" else r.status
            cases.append(
                f"<testcase classname='e2e.{xml_escape(r.profile)}' name='{xml_escape(r.id)}' "
                f"time='{r.duration:.2f}'><system-out>{xml_escape(note)}</system-out></testcase>"
            )
    (out / "junit.xml").write_text(
        "<?xml version='1.0' encoding='utf-8'?><testsuite name='e2e' tests='%d' failures='%d'>%s</testsuite>"
        % (len(results), sum(1 for r in results if r.status == "FAIL"), "".join(cases)),
        encoding="utf-8",
    )
    (out / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "sandbox.json").write_text(json.dumps(sandbox.manifest(), ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------
async def run_scenario(scenario_obj: Scenario, sandbox: RunSandbox, server: AppServer, browser: Browser) -> ScenarioResult:
    started = time.time()
    context = Context(sandbox, server, browser, scenario_obj)
    result = ScenarioResult(
        id=scenario_obj.id,
        profile=scenario_obj.profile,
        status="PASS",
        duration=0.0,
        known_issues=scenario_obj.known_issues,
    )
    try:
        await asyncio.wait_for(scenario_obj.fn(context), timeout=90)
        context.gate()
    except asyncio.TimeoutError:
        result.status = "FAIL"
        result.error = "场景超时（90s）"
    except Exception as exc:  # noqa: BLE001
        result.status = "FAIL"
        result.error = f"{type(exc).__name__}: {exc}"[:800]
        # 失败现场：DOM + console + 网络
        try:
            failure_dir = sandbox.report_dir / "dom"
            failure_dir.mkdir(parents=True, exist_ok=True)
            (failure_dir / f"{scenario_obj.id}-failure.html").write_text(
                await browser.body_html(), encoding="utf-8"
            )
            (sandbox.report_dir / "console.log").write_text(
                "\n".join(browser.console_errors()), encoding="utf-8"
            )
            (sandbox.report_dir / "network-failures.jsonl").write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in browser.network_failures()),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        result.steps = context.steps
        result.screenshots = context.screenshots
        result.duration = time.time() - started
        if result.status == "FAIL" and scenario_obj.known_issues:
            result.status = "KNOWN"
    return result


async def run_selected(
    scenarios: list[Scenario],
    *,
    headless: bool,
    keep: bool,
    verbose: bool = True,
) -> tuple[list[ScenarioResult], RunSandbox]:
    sandbox = RunSandbox.create()
    results: list[ScenarioResult] = []
    mock = None
    profiles: dict[str, list[Scenario]] = {}
    for item in scenarios:
        profiles.setdefault(item.profile, []).append(item)

    from .mock_llm import MockLLM

    mock = MockLLM(sandbox.mock_port).start()
    try:
        for profile, items in profiles.items():
            server = AppServer(sandbox, profile).start()
            browser = Browser(profile_dir=sandbox.chrome_profile, headless=headless)
            try:
                server.bootstrap(mock.base_url())
                await browser.start()
                for item in items:
                    if verbose:
                        print(f"[RUN ] {item.id}", flush=True)
                    result = await run_scenario(item, sandbox, server, browser)
                    results.append(result)
                    line = f"[{result.status:5}] {result.id} ({result.duration:.1f}s)"
                    if result.error:
                        line += f" -> {result.error[:160]}"
                    print(line, flush=True)
            finally:
                await browser.close()
                server.stop()
    finally:
        mock.stop()

    sandbox.verify_production_untouched()
    coverage = coverage_report()
    write_reports(sandbox, results, coverage)
    return results, sandbox

"""运维 Agent 状态机：分析 / 许可 / 执行 / 清理与权限边界。

git 操作跑在 tmp_path 的独立沙箱仓库；LLM 与 pytest 全部 monkeypatch，
不触发真实模型与真实测试。
"""
from __future__ import annotations

import json
import subprocess
import time

import pytest

ERROR_INPUT = "pytest backend/tests/test_example.py\nE   AssertionError: assert 1 == 2\nE   +  where 1 = 校园卡充值计算()" * 2

ANALYSIS_JSON = json.dumps(
    {
        "title": "修复充值计算断言失败",
        "root_cause": "充值计算函数返回值与预期断言不一致",
        "affected_files": ["backend/app/services/wallet.py"],
        "fix_plan": ["修正充值计算返回值"],
        "test_plan": ["tests/test_wallet.py", "全量 pytest"],
        "risk": "低；仅调整计算逻辑",
    },
    ensure_ascii=False,
)

PLAN_JSON = json.dumps(
    [
        {
            "path": "backend/app/services/wallet.py",
            "action": "modify",
            "content": "# 已由运维 Agent 修复\nWALLET_FIXED = True\n",
        },
        {
            "path": "backend/tests/test_wallet.py",
            "action": "create",
            "content": "def test_wallet_fixed():\n    assert True\n",
        },
    ],
    ensure_ascii=False,
)


def _run(cmd: list[str], cwd) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def sandbox_repo(tmp_path, monkeypatch):
    """独立 git 沙箱仓库 + 受控工作树目录；隔离主仓。"""
    from app import config
    from app import db as app_db
    from app.services import ops_agent

    repo = tmp_path / "sandbox-repo"
    (repo / "backend" / "app" / "services").mkdir(parents=True)
    (repo / "backend" / "tests").mkdir()
    (repo / "scripts").mkdir()
    (repo / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
    _run(["git", "init", "-q"], repo)
    _run(["git", "config", "user.email", "ops@test.local"], repo)
    _run(["git", "config", "user.name", "ops-test"], repo)
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", "init"], repo)

    monkeypatch.setattr(config, "REPO_DIR", repo)
    monkeypatch.setattr(config, "WORKTREE_DIR", tmp_path / "worktrees")
    monkeypatch.setattr(config, "GIT_PROXY", "")
    monkeypatch.setattr(ops_agent, "_venv_python", lambda: "python")
    # execute 内部读取 config.REPO_DIR / WORKTREE_DIR，均已被替换
    assert app_db is not None
    return repo


def _patch_llm(monkeypatch, *, analyze_ok=True, fix_ok=True):
    from app.services import ops_agent

    def fake_chat(messages, **kwargs):
        content = str(messages[-1]["content"])
        if "报错/问题描述" in content:
            return {"content": ANALYSIS_JSON if analyze_ok else "不是 JSON", "fallback": not analyze_ok}
        return {"content": PLAN_JSON if fix_ok else "{{{", "fallback": not fix_ok}

    monkeypatch.setattr(ops_agent.llm, "chat", fake_chat)


def _patch_pytest(monkeypatch, *, fail_full=False):
    from app.services import ops_agent

    real_run = ops_agent._run

    def fake_run(cmd, *, cwd=None, timeout=600):
        # 只拦截 pytest；git / gh 等真实执行（沙箱仓库内）
        if "pytest" not in cmd:
            return real_run(cmd, cwd=cwd, timeout=timeout)
        is_full = cmd[-1] == "-q" and len(cmd) == 4
        failed = fail_full and is_full
        return subprocess.CompletedProcess(
            cmd, 1 if failed else 0, "" if failed else "1 passed", "" if not failed else "1 failed"
        )

    monkeypatch.setattr(ops_agent, "_run", fake_run)


def _wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    return job


def test_full_lifecycle_creates_worktree_fix_and_cleanup(client, monkeypatch, sandbox_repo):
    from app import config

    _patch_llm(monkeypatch)
    _patch_pytest(monkeypatch)

    created = client.post("/api/ops/agent/tasks", json={"error_input": ERROR_INPUT})
    assert created.status_code == 202, created.text
    task_id = created.json()["task_id"]

    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job
    detail = client.get(f"/api/ops/agent/tasks/{task_id}").json()
    assert detail["status"] == "planned", detail
    assert detail["analysis"]["root_cause"], detail
    assert detail["branch"].startswith("ops/"), detail

    approved = client.post(f"/api/ops/agent/tasks/{task_id}/approve", json={"run_e2e": False})
    assert approved.status_code == 202, approved.text
    job = _wait_job(client, approved.json()["job_id"])
    assert job["status"] == "succeeded", job

    detail = client.get(f"/api/ops/agent/tasks/{task_id}").json()
    assert detail["status"] == "done", detail
    assert not detail["worktree_dir"], detail

    # 工作树已清理；修改保留在分支上
    from pathlib import Path

    worktrees = list(Path(config.WORKTREE_DIR).glob("*")) if Path(config.WORKTREE_DIR).exists() else []
    assert not worktrees, worktrees
    branch = detail["branch"]
    show = subprocess.run(
        ["git", "show", f"{branch}:backend/app/services/wallet.py"],
        cwd=str(sandbox_repo),
        capture_output=True,
        text=True,
    )
    assert show.returncode == 0 and "WALLET_FIXED" in show.stdout, show.stdout


def test_analyze_failure_marks_task_failed(client, monkeypatch, sandbox_repo):
    from app import db as app_db

    _patch_llm(monkeypatch, analyze_ok=False)
    created = client.post("/api/ops/agent/tasks", json={"error_input": ERROR_INPUT})
    task_id = created.json()["task_id"]
    job = _wait_job(client, created.json()["job_id"])
    assert job["status"] == "failed", job
    detail = client.get(f"/api/ops/agent/tasks/{task_id}").json()
    assert detail["status"] == "failed", detail
    alert = app_db.query_one(
        "SELECT * FROM system_alerts WHERE category = 'ops' AND resource_id = ?", (task_id,)
    )
    assert alert is not None


def test_execute_failure_keeps_worktree(client, monkeypatch, sandbox_repo):
    _patch_llm(monkeypatch)
    _patch_pytest(monkeypatch, fail_full=True)
    created = client.post("/api/ops/agent/tasks", json={"error_input": ERROR_INPUT})
    task_id = created.json()["task_id"]
    _wait_job(client, created.json()["job_id"])
    approved = client.post(f"/api/ops/agent/tasks/{task_id}/approve", json={"run_e2e": False})
    job = _wait_job(client, approved.json()["job_id"], timeout=60)
    # execute 内部失败被捕获置 failed，job 以 failed 结束（max_attempts=1）
    detail = client.get(f"/api/ops/agent/tasks/{task_id}").json()
    assert detail["status"] == "failed", detail
    assert detail["worktree_dir"], "失败应保留工作树供人工接管"

    cleaned = client.post(f"/api/ops/agent/tasks/{task_id}/cleanup")
    assert cleaned.status_code == 200
    detail = client.get(f"/api/ops/agent/tasks/{task_id}").json()
    assert detail["worktree_dir"] == ""


def test_token_mode_requires_admin_header(client, monkeypatch, sandbox_repo):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "ops-secret")
    denied = client.post("/api/ops/agent/tasks", json={"error_input": ERROR_INPUT})
    assert denied.status_code == 401
    allowed = client.post(
        "/api/ops/agent/tasks",
        json={"error_input": ERROR_INPUT},
        headers={"X-Admin-Token": "ops-secret"},
    )
    assert allowed.status_code == 202


def test_short_error_input_rejected(client):
    response = client.post("/api/ops/agent/tasks", json={"error_input": "太短"})
    assert response.status_code == 400


def test_analyze_rejects_terminal_states(client, monkeypatch, sandbox_repo):
    """done/cancelled 任务不允许被迟到的分析 job 复活。"""
    from app.services import ops_agent

    task = ops_agent.create_task(ERROR_INPUT)
    for status in ("done", "cancelled"):
        ops_agent._save(task["id"], status=status)
        with pytest.raises(ValueError, match="不允许"):
            ops_agent.analyze(task["id"])
        assert ops_agent.get_task(task["id"])["status"] == status


def test_safe_path_rejects_escape(client, monkeypatch, sandbox_repo):
    from app.services import ops_agent

    worktree = sandbox_repo
    for bad in ("../../etc/passwd", "/etc/passwd", "a/../../b.md"):
        with pytest.raises(ops_agent.OpsError, match="非法路径|越出工作树"):
            ops_agent._safe_path(worktree, bad)
    assert ops_agent._safe_path(worktree, "backend/app/x.py").is_absolute()


def test_recover_interrupted_recovers_executing_tasks(client, monkeypatch, sandbox_repo):
    """服务重启回收：executing 的运维任务置 failed 并保留工作树信息。"""
    from app import db as app_db
    from app.services import jobs, ops_agent

    task = ops_agent.create_task(ERROR_INPUT)
    ops_agent._save(task["id"], status="executing", worktree_dir=r"E:\tmp\wt-placeholder")
    ops_agent._log(task["id"], "执行中…")
    recovered = jobs.recover_interrupted()
    assert recovered >= 0
    detail = ops_agent.get_task(task["id"])
    assert detail["status"] == "failed", detail
    assert "服务重启" in detail["test_summary"]
    assert "人工接管" in detail["log"]


def test_targeted_args_extracts_paths_from_chinese_plan(client, monkeypatch, sandbox_repo):
    from app.services import ops_agent

    task = {"analysis": {"test_plan": ["运行 tests/test_wallet.py 验证充值", "跑全量回归"]}}
    args = ops_agent._targeted_args(task)
    assert args == ["tests/test_wallet.py"], args

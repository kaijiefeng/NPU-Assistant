"""运维 Agent（仅超级管理员）：报错驱动的自动化修复工作流。

流程：粘贴报错 → LLM 定位问题/给出修复与测试方案（``planned``，等待许可）→
管理员批准 → 统一目录 ``git worktree`` 内 LLM 生成修改清单并写盘 → 定向 +
全量单元测试（可选 RSI 端到端）→ 推送分支 / 建 PR → 清理工作树 → 站内告警
通知管理员。

安全边界：
* 路由层仅 ``ops:manage``（超级管理员 ``*`` 命中）；
* subprocess 白名单：``git`` / ``pytest``（固定参数）/ ``gh``；
* LLM 修改清单路径必须相对路径且解析后位于工作树内；
* 一切写操作只发生在一次性工作树（``.worktrees/<task_id>``），主仓不动；
  失败时保留工作树供人工接管。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from .. import config, db
from . import alerts, llm
from .wiki_builder import _extract_json

logger = logging.getLogger("npu-assistant")

_STATUSES = {"analyzing", "planned", "executing", "done", "failed", "cancelled"}
_BRANCH_PREFIX = "ops/"
_MAX_LOG_LINES = 400


class OpsError(RuntimeError):
    """执行期错误：保留工作树、置 failed，供人工接管。"""


# ---------------------------------------------------------------------------
# 行存取
# ---------------------------------------------------------------------------
def _row_to_task(row: dict) -> dict:
    analysis = db.from_json(row.get("analysis"), {})
    plan = db.from_json(row.get("plan"), [])
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "error_input": row.get("error_input") or "",
        "status": row.get("status") or "analyzing",
        "analysis": analysis if isinstance(analysis, dict) else {},
        "plan": plan if isinstance(plan, list) else [],
        "worktree_dir": row.get("worktree_dir") or "",
        "branch": row.get("branch") or "",
        "pr_url": row.get("pr_url") or "",
        "log": row.get("log") or "",
        "test_summary": row.get("test_summary") or "",
        "created_by": row.get("created_by") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _get_task(task_id: str) -> dict:
    row = db.query_one("SELECT * FROM ops_tasks WHERE id = ?", (task_id,))
    if not row:
        raise ValueError("运维任务不存在")
    return _row_to_task(row)


def get_task(task_id: str) -> dict:
    return _get_task(task_id)


def list_tasks(status: str = "", limit: int = 50) -> dict:
    if status and status in _STATUSES:
        rows = db.query(
            "SELECT * FROM ops_tasks WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (status, max(1, min(int(limit), 200))),
        )
    else:
        rows = db.query(
            "SELECT * FROM ops_tasks ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        )
    return {"items": [_row_to_task(row) for row in rows]}


def _save(task_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{key} = ?" for key in fields)
    db.execute(
        f"UPDATE ops_tasks SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), db.now(), task_id),
    )


def _log(task_id: str, line: str) -> None:
    row = db.query_one("SELECT log FROM ops_tasks WHERE id = ?", (task_id,))
    existing = (row or {}).get("log") or ""
    lines = existing.splitlines() if existing else []
    lines.append(f"[{db.now()}] {line}")
    db.execute(
        "UPDATE ops_tasks SET log = ?, updated_at = ? WHERE id = ?",
        ("\n".join(lines[-_MAX_LOG_LINES:]), db.now(), task_id),
    )


# ---------------------------------------------------------------------------
# 创建 / 分析 / 许可 / 取消
# ---------------------------------------------------------------------------
def create_task(error_input: str, title: str = "", created_by: str = "") -> dict:
    text = str(error_input or "").strip()
    if len(text) < 20:
        raise ValueError("请粘贴完整的报错信息（至少 20 个字符），便于定位问题")
    task_id = db.new_id()
    now = db.now()
    clean_title = str(title or "").strip()[:80] or text.splitlines()[0][:60]
    db.execute(
        "INSERT INTO ops_tasks(id, title, error_input, status, analysis, plan, log, "
        "created_by, created_at, updated_at) VALUES(?, ?, ?, 'analyzing', '{}', '[]', "
        "'', ?, ?, ?)",
        (task_id, clean_title, text[:20000], created_by, now, now),
    )
    return _get_task(task_id)


def analyze(task_id: str) -> dict:
    """LLM 定位问题并生成修复/测试方案；结束进入 ``planned`` 等待许可。"""
    task = _get_task(task_id)
    _save(task_id, status="analyzing")
    _log(task_id, "LLM 正在分析报错…")
    messages = [
        {
            "role": "system",
            "content": (
                "你是资深 Python/FastAPI 工程师，负责分析项目报错并给出最小修复方案。"
                "只依据报错信息推断，不臆造不存在的文件。"
            ),
        },
        {
            "role": "user",
            "content": "项目：FastAPI + SQLite + 原生 ES Module 前端的校园问答系统（仓库 NPU-Assistant）。\n报错/问题描述：\n"
            + task["error_input"][:8000]
            + '\n请只输出 JSON 对象：{"title":"一句话任务标题","root_cause":"根因分析",'
            '"affected_files":["相对路径"],"fix_plan":["步骤1","步骤2"],'
            '"test_plan":["如何验证"],"risk":"风险与回滚说明"}。',
        },
    ]
    response = llm.chat(messages, temperature=0.2, max_tokens=1500)
    data = _extract_json(str(response.get("content") or ""), dict)
    if response.get("fallback") or not data:
        detail = str(response.get("error") or "输出无法解析为 JSON")
        _log(task_id, f"LLM 分析失败：{detail[:200]}")
        _save(task_id, status="failed")
        alerts.create_alert(
            f"运维任务分析失败：{task['title'][:60]}",
            level="warning",
            category="ops",
            detail=detail[:500],
            resource_id=task_id,
        )
        raise OpsError(f"LLM 分析失败：{detail[:200]}")

    analysis = {
        "root_cause": str(data.get("root_cause") or "")[:1000],
        "affected_files": [str(f) for f in (data.get("affected_files") or []) if f][:10],
        "fix_plan": [str(s) for s in (data.get("fix_plan") or []) if s][:10],
        "test_plan": [str(s) for s in (data.get("test_plan") or []) if s][:10],
        "risk": str(data.get("risk") or "")[:500],
    }
    branch = f"{_BRANCH_PREFIX}{task_id[:12]}"
    _save(
        task_id,
        status="planned",
        analysis=json.dumps(analysis, ensure_ascii=False),
        branch=branch,
        title=str(data.get("title") or task["title"])[:80],
    )
    _log(task_id, f"分析完成：{analysis['root_cause'][:120]}")
    _log(task_id, f"已进入待许可状态（分支 {branch}），等待管理员批准执行。")
    return _get_task(task_id)


def approve(task_id: str) -> dict:
    """许可门：``planned`` → ``executing``（由 ops_execute 任务驱动执行）。"""
    task = _get_task(task_id)
    if task["status"] != "planned":
        raise ValueError(f"只有待许可（planned）状态可以批准执行，当前：{task['status']}")
    _save(task_id, status="executing")
    _log(task_id, "管理员已批准，开始执行修复流水线。")
    return _get_task(task_id)


def cancel(task_id: str) -> dict:
    task = _get_task(task_id)
    if task["status"] in {"executing"}:
        raise ValueError("任务正在执行，请等待完成或失败后再操作")
    if task["status"] in {"done", "cancelled"}:
        raise ValueError("任务已结束，无需取消")
    if task["worktree_dir"]:
        _remove_worktree(task_id, task["worktree_dir"])
    _save(task_id, status="cancelled")
    _log(task_id, "任务已取消，工作树已清理。")
    return _get_task(task_id)


def cleanup(task_id: str) -> dict:
    """人工清理 failed 任务残留的工作树（保留分支与记录）。"""
    task = _get_task(task_id)
    if task["status"] not in {"failed", "done", "cancelled"}:
        raise ValueError("只能清理已结束的任务")
    if task["worktree_dir"]:
        _remove_worktree(task_id, task["worktree_dir"])
        _save(task_id, worktree_dir="")
    _log(task_id, "工作树已人工清理。")
    return _get_task(task_id)


# ---------------------------------------------------------------------------
# 执行流水线（approve 之后由 jobs 驱动）
# ---------------------------------------------------------------------------
def execute(task_id: str, *, run_e2e: bool = False) -> dict:
    task = _get_task(task_id)
    if task["status"] not in {"executing"}:
        raise ValueError(f"任务状态不允许执行（当前：{task['status']}）")

    try:
        worktree = _ensure_worktree(task)
        task = _get_task(task_id)

        _log(task_id, "步骤 1/5：LLM 生成修改清单…")
        plan = _apply_fix(task, worktree)
        _log(task_id, f"修改清单：{len(plan)} 个文件；已写入工作树并提交。")

        _log(task_id, "步骤 2/5：定向单元测试…")
        code, output = _run_pytest(worktree, _targeted_args(task))
        if code != 0:
            raise OpsError(f"定向测试失败：{output[-800:]}")
        _log(task_id, "定向测试通过。")

        _log(task_id, "步骤 3/5：全量单元测试…")
        code, output = _run_pytest(worktree, [])
        summary = _tail_line(output) or ("exit=" + str(code))
        if code != 0:
            _save(task_id, test_summary=f"全量测试失败：{summary}")
            raise OpsError(f"全量测试失败：{output[-800:]}")
        _log(task_id, f"全量测试通过（{summary}）。")

        if run_e2e:
            _log(task_id, "步骤 4/5：端到端验证（rsi_verify）…")
            code, output = _run_e2e(worktree)
            if code != 0:
                _save(task_id, test_summary=f"端到端失败：{_tail_line(output)}")
                raise OpsError(f"端到端验证失败：{output[-600:]}")
            _log(task_id, "端到端验证通过。")
        else:
            _log(task_id, "步骤 4/5：跳过端到端（未勾选）。")

        _log(task_id, "步骤 5/5：推送分支并创建 PR…")
        pr_url = _ship(task, worktree)
        _remove_worktree(task_id, worktree)
        _save(
            task_id,
            status="done",
            pr_url=pr_url,
            worktree_dir="",
            test_summary=f"单测通过；{summary}",
            plan=json.dumps(plan, ensure_ascii=False),
        )
        _log(task_id, f"完成：{pr_url or '分支已就绪（未配置远程，未推送）'}；工作树已清理。")
        alerts.create_alert(
            f"运维修复完成：{task['title'][:60]}",
            level="info",
            category="ops",
            detail=pr_url or f"分支 {task['branch']}（无远程，未推送）",
            resource_id=task_id,
        )
    except OpsError as exc:
        _save(task_id, status="failed")
        _log(task_id, f"失败：{exc}")
        alerts.create_alert(
            f"运维修复失败：{task['title'][:60]}",
            level="warning",
            category="ops",
            detail=str(exc)[:500],
            resource_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001 - 防御兜底：执行异常不能拖垮 worker
        logger.exception("运维任务执行异常：%s", task_id)
        _save(task_id, status="failed")
        _log(task_id, f"执行异常：{exc}")
    return _get_task(task_id)


# ---------------------------------------------------------------------------
# 内部步骤
# ---------------------------------------------------------------------------
def _ensure_worktree(task: dict) -> Path:
    repo = Path(config.REPO_DIR)
    base = Path(config.WORKTREE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    worktree = base / task["id"]
    branch = task["branch"] or f"{_BRANCH_PREFIX}{task['id'][:12]}"
    if worktree.exists():
        _git(["worktree", "remove", "--force", str(worktree)], cwd=repo)
    exists_branch = _git(["rev-parse", "--verify", branch], cwd=repo)
    args = (
        ["worktree", "add", str(worktree), branch]
        if exists_branch.code == 0
        else ["worktree", "add", "-b", branch, str(worktree)]
    )
    result = _git(args, cwd=repo)
    if result.code != 0:
        raise OpsError(f"创建工作树失败：{result.output[-400:]}")
    _save(task["id"], worktree_dir=str(worktree), branch=branch)
    _log(task["id"], f"工作树就绪：{worktree}（分支 {branch}）")
    return worktree


def _remove_worktree(task_id: str, worktree: str) -> None:
    result = _git(["worktree", "remove", "--force", worktree], cwd=config.REPO_DIR)
    if result.code != 0:
        logger.warning("清理工作树失败（%s）：%s", worktree, result.output[-200:])


def _apply_fix(task: dict, worktree: Path) -> list[dict]:
    analysis = task.get("analysis") or {}
    affected = [f for f in (analysis.get("affected_files") or []) if f]
    context_files: list[dict] = []
    for rel in affected[:6]:
        candidate = _safe_path(worktree, rel)
        if candidate.is_file():
            content = candidate.read_text(encoding="utf-8", errors="replace")
            context_files.append({"path": rel, "content": content[:4000]})
    messages = [
        {
            "role": "system",
            "content": (
                "你是资深工程师，按既定修复方案修改代码。只输出 JSON 数组，每项 "
                '{"path":"相对路径","action":"create|modify","content":"完整文件内容"}；'
                "路径必须相对仓库根且不使用 ..；content 为修改后的完整文件。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "报错": task["error_input"][:4000],
                    "方案": analysis,
                    "相关文件现状": context_files,
                },
                ensure_ascii=False,
            )
            + "\n请输出修复所需的完整 JSON 数组。测试文件如需新增也一并给出（tests/ 下）。",
        },
    ]
    response = llm.chat(messages, temperature=0.2, max_tokens=8000)
    plan = _extract_json(str(response.get("content") or ""), list)
    if response.get("fallback") or not plan:
        raise OpsError("LLM 修复输出无法解析为修改清单")
    applied: list[dict] = []
    for item in plan[:12]:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").strip()
        action = str(item.get("action") or "modify")
        content = item.get("content")
        if not rel or not isinstance(content, str):
            continue
        target = _safe_path(worktree, rel)
        if action == "delete":
            if target.is_file():
                target.unlink()
            applied.append({"path": rel, "action": "delete"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        applied.append({"path": rel, "action": action})
    if not applied:
        raise OpsError("LLM 修改清单为空")
    _git(["add", "-A"], cwd=worktree)
    commit = _git(
        ["commit", "-m", f"fix(ops): {task['title'][:60]}（运维任务 {task['id'][:8]}）"],
        cwd=worktree,
    )
    if commit.code != 0 and "nothing to commit" not in commit.output:
        raise OpsError(f"提交修改失败：{commit.output[-300:]}")
    return applied


def _targeted_args(task: dict) -> list[str]:
    test_plan = (task.get("analysis") or {}).get("test_plan") or []
    files = [
        token.replace("\\", "/").split(" ")[0].strip()
        for token in test_plan
        if "tests/" in token
    ]
    return [f for f in files if f.endswith(".py")][:6]


def _run_pytest(worktree: Path, args: list[str]) -> tuple[int, str]:
    python = _venv_python()
    cmd = [python, "-m", "pytest", *(args or []), "-q"]
    proc = _run(cmd, cwd=worktree, timeout=900)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _run_e2e(worktree: Path) -> tuple[int, str]:
    script = worktree / "scripts" / "rsi_verify.py"
    if not script.is_file():
        return 0, "（工作树无 rsi_verify，跳过）"
    proc = _run([_venv_python(), str(script)], cwd=worktree, timeout=1200)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _ship(task: dict, worktree: Path) -> str:
    origin = _git(["remote", "get-url", "origin"], cwd=worktree)
    origin_url = origin.output.strip() if origin.code == 0 else ""
    if not origin_url:
        _log(task["id"], "未配置 origin 远程，跳过推送；修改保留在分支上。")
        return ""
    push_cmd = ["git"]
    if config.GIT_PROXY:
        push_cmd += ["-c", f"http.proxy={config.GIT_PROXY}", "-c", f"https.proxy={config.GIT_PROXY}"]
    push_cmd += ["push", "-u", "origin", task["branch"]]
    code, output = _run(push_cmd, cwd=worktree, timeout=180)
    if code != 0:
        raise OpsError(f"推送失败（可配置 NPU_GIT_PROXY 后重试）：{output[-400:]}")
    _log(task["id"], f"分支已推送：{task['branch']}")

    gh = shutil.which("gh")
    if gh:
        code, output = _run(
            [
                gh,
                "pr",
                "create",
                "--base",
                "develop",
                "--head",
                task["branch"],
                "--title",
                f"fix(ops): {task['title'][:60]}",
                "--body",
                f"运维任务 {task['id']} 自动修复。\n\n报错：\n{task['error_input'][:1500]}",
            ],
            cwd=worktree,
            timeout=120,
        )
        if code == 0:
            for line in output.splitlines():
                if line.startswith("http"):
                    return line.strip()
    return _compare_url(origin_url, task["branch"])


def _compare_url(origin_url: str, branch: str) -> str:
    base = origin_url.strip().rstrip("/")
    if base.endswith(".git"):
        base = base[: -len(".git")]
    if "github.com" not in base:
        return ""
    return f"{base}/compare/develop...{branch}?expand=1"


def _venv_python() -> str:
    for candidate in (
        config.REPO_DIR / ".venv" / "Scripts" / "python.exe",
        config.REPO_DIR / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return "python"


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 600) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


class _GitResult:
    def __init__(self, code: int, output: str) -> None:
        self.code = code
        self.output = output


def _git(args: list[str], *, cwd: Path | None = None, timeout: int = 120) -> _GitResult:
    proc = _run(["git", *args], cwd=cwd, timeout=timeout)
    return _GitResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def _safe_path(worktree: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise OpsError(f"非法路径：{rel}")
    resolved = (worktree / candidate).resolve()
    if not str(resolved).startswith(str(worktree.resolve())):
        raise OpsError(f"路径越出工作树：{rel}")
    return resolved


def _tail_line(output: str) -> str:
    lines = [line for line in (output or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""

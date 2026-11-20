"""管理助手定时任务调度器（轻量：表驱动 + 单线程 tick）。

白名单 kind（不做插件/脚本机制）：
- ``subscription_check_all``：把所有启用的订阅各提交一个检查任务；
- ``gap_suggest_all``：把未处理知识缺口批量提交 AI 找备选任务；
- ``agent_prompt``：把一段预设指令交给管理助手执行一轮，结果落会话并生成通知。

调度线程只做轻操作（提交 jobs 队列/改状态），重活全部在 jobs worker；
到期判定用 ``next_run_at <= now`` 的字符串字典序（db.now 为 naive 本地 ISO），
原子抢占保证错过多个周期也只补跑一次。principal 采用固定系统身份
（super_admin），工具权限与人工操作一致且全部留审计。
"""
from __future__ import annotations

import json
import logging
import threading

from .. import db

logger = logging.getLogger("npu-assistant")

SCHEDULE_KINDS = {
    "subscription_check_all": "检查全部启用订阅（逐个提交后台检查任务）",
    "gap_suggest_all": "为未处理知识缺口批量提交 AI 找备选任务",
    "agent_prompt": "把一段预设指令交给管理助手执行一轮（结果落会话+通知）",
}

INTERVAL_MIN = 15
INTERVAL_MAX = 10080  # 一周

# 定时执行的系统身份：super_admin 权限与人工操作一致，工具层审计照常生效
SCHED_PRINCIPAL = {"role": "super_admin", "auth": "system", "username": "scheduler"}

_worker_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event = threading.Event()
_TICK_SECONDS = 60


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _row_to_schedule(row: dict) -> dict:
    config = db.from_json(row.get("config"), {})
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "kind": row.get("kind") or "",
        "config": config if isinstance(config, dict) else {},
        "interval_minutes": int(row.get("interval_minutes") or 60),
        "enabled": bool(row.get("enabled")),
        "last_run_at": row.get("last_run_at") or "",
        "next_run_at": row.get("next_run_at") or "",
        "last_status": row.get("last_status") or "",
        "last_error": row.get("last_error") or "",
        "created_by": row.get("created_by") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _get_schedule(schedule_id: str) -> dict:
    row = db.query_one("SELECT * FROM agent_schedules WHERE id = ?", (schedule_id,))
    if not row:
        raise ValueError("定时任务不存在或已被删除")
    return _row_to_schedule(row)


def get_schedule(schedule_id: str) -> dict:
    return _get_schedule(schedule_id)


def list_schedules() -> dict:
    rows = db.query("SELECT * FROM agent_schedules ORDER BY created_at, rowid")
    return {"items": [_row_to_schedule(row) for row in rows]}


def _next_run(interval_minutes: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.now() + timedelta(minutes=interval_minutes)).isoformat(timespec="seconds")


def create_schedule(
    name: str,
    kind: str,
    config: dict | None = None,
    interval_minutes: int = 60,
    created_by: str = "",
) -> dict:
    clean_kind = str(kind or "").strip()
    if clean_kind not in SCHEDULE_KINDS:
        raise ValueError(f"不支持的任务类型 {clean_kind}（可选：{'、'.join(SCHEDULE_KINDS)}）")
    try:
        interval = int(interval_minutes)
    except (TypeError, ValueError):
        raise ValueError("interval_minutes 必须是整数") from None
    if not (INTERVAL_MIN <= interval <= INTERVAL_MAX):
        raise ValueError(f"间隔需在 {INTERVAL_MIN}–{INTERVAL_MAX} 分钟之间")
    clean_name = str(name or "").strip()[:60] or SCHEDULE_KINDS[clean_kind][:20]
    clean_config = config if isinstance(config, dict) else {}
    if clean_kind == "agent_prompt" and not str(clean_config.get("prompt") or "").strip():
        raise ValueError("agent_prompt 需要 config.prompt（预设指令）")
    schedule_id = db.new_id()
    now = db.now()
    db.execute(
        "INSERT INTO agent_schedules(id, name, kind, config, interval_minutes, enabled, "
        "last_run_at, next_run_at, last_status, last_error, created_by, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, 1, '', ?, '', '', ?, ?, ?)",
        (
            schedule_id,
            clean_name,
            clean_kind,
            json.dumps(clean_config, ensure_ascii=False),
            interval,
            _next_run(interval),
            created_by,
            now,
            now,
        ),
    )
    return _get_schedule(schedule_id)


def update_schedule(
    schedule_id: str,
    *,
    name: str | None = None,
    config: dict | None = None,
    interval_minutes: int | None = None,
    enabled: bool | None = None,
) -> dict:
    schedule = _get_schedule(schedule_id)
    sets: list[str] = []
    params: list = []
    if name is not None:
        clean = str(name).strip()[:60]
        if clean:
            sets.append("name = ?")
            params.append(clean)
    if config is not None and isinstance(config, dict):
        sets.append("config = ?")
        params.append(json.dumps(config, ensure_ascii=False))
    if interval_minutes is not None:
        try:
            interval = int(interval_minutes)
        except (TypeError, ValueError):
            raise ValueError("interval_minutes 必须是整数") from None
        if not (INTERVAL_MIN <= interval <= INTERVAL_MAX):
            raise ValueError(f"间隔需在 {INTERVAL_MIN}–{INTERVAL_MAX} 分钟之间")
        sets.append("interval_minutes = ?")
        params.append(interval)
        sets.append("next_run_at = ?")
        params.append(_next_run(interval))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
        if enabled and not schedule["next_run_at"]:
            sets.append("next_run_at = ?")
            params.append(_next_run(schedule["interval_minutes"]))
    if sets:
        sets.append("updated_at = ?")
        params.append(db.now())
        params.append(schedule_id)
        db.execute(f"UPDATE agent_schedules SET {', '.join(sets)} WHERE id = ?", params)
    return _get_schedule(schedule_id)


def delete_schedule(schedule_id: str) -> dict:
    _get_schedule(schedule_id)
    db.execute("DELETE FROM agent_schedules WHERE id = ?", (schedule_id,))
    return {"ok": True, "id": schedule_id}


def run_now(schedule_id: str) -> dict:
    """立即触发一次（不重置周期）。"""
    schedule = _get_schedule(schedule_id)
    result = _execute(schedule)
    return {"ok": True, "id": schedule_id, "result": result}


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _execute(schedule: dict) -> str:
    kind = schedule["kind"]
    config = schedule["config"] or {}
    now = db.now()
    if kind == "subscription_check_all":
        from . import jobs

        rows = db.query("SELECT id, name FROM subscriptions WHERE enabled = 1")
        for row in rows:
            jobs.create_job(
                "subscription_check", {"subscription_id": row["id"]}, created_by="scheduler"
            )
        message = f"已提交 {len(rows)} 个订阅检查任务"
    elif kind == "gap_suggest_all":
        from . import jobs

        try:
            limit = max(1, min(int(config.get("limit") or 10), 50))
        except (TypeError, ValueError):
            limit = 10
        rows = db.query(
            "SELECT id FROM knowledge_gaps WHERE status = 'open' "
            "ORDER BY priority DESC, count DESC, created_at LIMIT ?",
            (limit,),
        )
        for row in rows:
            jobs.create_job("gap_suggest", {"gap_id": row["id"]}, created_by="scheduler")
        message = f"已为 {len(rows)} 个缺口提交 AI 找备选任务"
    elif kind == "agent_prompt":
        from . import jobs

        jobs.create_job("agent_prompt", {"schedule_id": schedule["id"]}, created_by="scheduler")
        message = "已提交助手执行任务"
    else:  # pragma: no cover - create_schedule 已做白名单
        message = f"未知类型 {kind}，已跳过"
    db.execute(
        "UPDATE agent_schedules SET last_run_at = ?, last_status = ?, last_error = '', "
        "updated_at = ? WHERE id = ?",
        (now, message, now, schedule["id"]),
    )
    return message


def run_agent_prompt(schedule_id: str) -> dict:
    """jobs worker 内执行体：预设指令跑一轮助手对话，结果落会话并生成通知。"""
    from . import alerts
    from . import agent_chat, agent_sessions

    schedule = _get_schedule(schedule_id)
    prompt = str((schedule["config"] or {}).get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("定时任务缺少预设指令（config.prompt）")

    conversation_id = str((schedule["config"] or {}).get("conversation_id") or "").strip()
    if conversation_id:
        try:
            agent_sessions.get_conversation(conversation_id)
        except ValueError:
            conversation_id = ""
    if not conversation_id:
        conversation = agent_sessions.create_conversation(f"定时任务·{schedule['name']}")
        conversation_id = conversation["id"]
        config = dict(schedule["config"])
        config["conversation_id"] = conversation_id
        db.execute(
            "UPDATE agent_schedules SET config = ?, updated_at = ? WHERE id = ?",
            (json.dumps(config, ensure_ascii=False), db.now(), schedule_id),
        )

    history = agent_chat.prepare_context(conversation_id, prompt, SCHED_PRINCIPAL)
    agent_sessions.append_message(conversation_id, "user", prompt)
    result = agent_chat.run_agent_chat(history, prompt, SCHED_PRINCIPAL)
    agent_sessions.append_message(
        conversation_id, "assistant", str(result.get("reply") or ""), result.get("steps") or []
    )
    alerts.create_alert(
        "定时任务「%s」已执行" % schedule["name"],
        level="info",
        category="agent",
        detail=str(result.get("reply") or "")[:200],
        source="scheduler",
        resource_id=schedule_id,
    )
    return {
        "conversation_id": conversation_id,
        "reply": str(result.get("reply") or "")[:400],
        "steps": len(result.get("steps") or []),
    }


# ---------------------------------------------------------------------------
# 调度线程
# ---------------------------------------------------------------------------
def _tick() -> int:
    """检查到期任务并执行；返回本轮触发数。"""
    now = db.now()
    rows = db.query(
        "SELECT * FROM agent_schedules WHERE enabled = 1 AND next_run_at != '' "
        "AND next_run_at <= ? ORDER BY next_run_at",
        (now,),
    )
    fired = 0
    for row in rows:
        schedule = _row_to_schedule(row)
        interval = schedule["interval_minutes"]
        # 原子抢占：只有仍持有旧 next_run_at 的那次 UPDATE 生效（错过多个周期只补跑一次）。
        # 注意 db.execute 返回 lastrowid（UPDATE 恒 0），抢占判定必须用 rowcount。
        with db.connect() as conn:
            cur = conn.execute(
                "UPDATE agent_schedules SET last_run_at = ?, next_run_at = ? "
                "WHERE id = ? AND next_run_at = ?",
                (now, _next_run(interval), schedule["id"], schedule["next_run_at"]),
            )
            conn.commit()
            claimed = int(cur.rowcount or 0)
        if not claimed:
            continue
        fired += 1
        try:
            _execute(schedule)
        except Exception as exc:  # noqa: BLE001 - 单个任务失败不影响其余调度
            logger.exception("定时任务 %s 执行失败", schedule["name"])
            db.execute(
                "UPDATE agent_schedules SET last_status = '执行失败', last_error = ?, "
                "updated_at = ? WHERE id = ?",
                (str(exc)[:200], db.now(), schedule["id"]),
            )
    return fired


class _SchedulerThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="npu-agent-scheduler", daemon=True)

    def run(self) -> None:
        logger.info("管理助手定时调度线程已启动（tick %ss）", _TICK_SECONDS)
        while not _stop_event.wait(_TICK_SECONDS):
            try:
                _tick()
            except Exception:  # noqa: BLE001 - 任何异常都不能让线程退出
                logger.exception("调度 tick 执行失败（已忽略，下一轮继续）")


def recover_schedules() -> None:
    """启动回收：启用但 next_run_at 为空的记录重算下个周期。"""
    rows = db.query("SELECT id, interval_minutes FROM agent_schedules WHERE enabled = 1")
    for row in rows:
        if not _row_to_schedule(row)["next_run_at"]:
            db.execute(
                "UPDATE agent_schedules SET next_run_at = ? WHERE id = ?",
                (_next_run(int(row["interval_minutes"] or 60)), row["id"]),
            )


def start_scheduler() -> None:
    global _thread
    with _worker_lock:
        if _thread and _thread.is_alive():
            return
        recover_schedules()
        _stop_event.clear()
        _thread = _SchedulerThread()
        _thread.start()


def stop_scheduler() -> None:
    global _thread
    with _worker_lock:
        _stop_event.set()
        thread = _thread
        _thread = None
    if thread and thread.is_alive():
        thread.join(timeout=5.0)

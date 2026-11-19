"""Background job queue (v0.2.0 M4).

Why a DB-backed queue instead of ``BackgroundTasks``?
-----------------------------------------------------
Uploads can take minutes (parse + several LLM calls).  A request-scoped
background task dies with the process and gives the UI no progress/retry story.
Jobs are persisted in SQLite and executed by **one** in-process worker thread,
which also keeps concurrent LLM/SQLite writes predictable.

Lifecycle: ``queued → running → succeeded / failed / cancelled``.  Failed jobs
can be retried (``attempts < max_attempts``); jobs interrupted by a restart are
marked failed and can be retried by an administrator.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .. import config, db
from . import alerts

logger = logging.getLogger("npu-assistant")

JOB_TYPES = {
    "ingest_upload",
    "reindex",
    "rebuild",
    "scan_dir",
    "gap_suggest",
    "subscription_check",
    "agent_prompt",
    "ops_analyze",
    "ops_execute",
}
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}

_handlers: dict[str, Callable[[dict, Any], dict]] = {}
_worker: "JobWorker | None" = None
_worker_lock = threading.Lock()


class JobCancelled(RuntimeError):
    """Raised by a handler when it notices ``cancel_requested``."""


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _payload(row: dict | None) -> dict:
    row = row or {}
    return {
        **_row(row),
        "payload": _json(row.get("payload"), {}),
        "result": _json(row.get("result"), {}),
    }


def _row(row: dict | None) -> dict:
    row = row or {}
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "status": row.get("status"),
        "progress": int(row.get("progress") or 0),
        "message": row.get("message") or "",
        "error": row.get("error") or "",
        "attempts": int(row.get("attempts") or 0),
        "max_attempts": int(row.get("max_attempts") or 2),
        "cancel_requested": bool(row.get("cancel_requested")),
        "created_by": row.get("created_by") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


def stage_upload(job_id: str, filename: str, data: bytes) -> str:
    """Persist request bytes for the worker; returns the staging path."""
    ext = Path(str(filename or "")).suffix.lower()
    if ext not in config.SUPPORTED_EXTS:
        ext = ".bin"
    config.JOB_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = config.JOB_STAGING_DIR / f"{job_id}{ext}"
    path.write_bytes(data)
    return str(path)


def stage_bytes(filename: str, data: bytes) -> str:
    """把上传字节先落盘（不依赖 job id），用于「先写 staging 再入队」避免任务抢跑。"""
    ext = Path(str(filename or "")).suffix.lower()
    if ext not in config.SUPPORTED_EXTS:
        ext = ".bin"
    config.JOB_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = config.JOB_STAGING_DIR / f"{db.new_id()}{ext}"
    path.write_bytes(data)
    return str(path)


def create_job(
    kind: str,
    payload: dict | None = None,
    *,
    created_by: str = "",
    max_attempts: int = 2,
) -> dict:
    if kind not in JOB_TYPES:
        raise ValueError("不支持的任务类型")
    job_id = db.new_id()
    timestamp = db.now()
    db.execute(
        "INSERT INTO jobs(id, type, status, payload, result, progress, message, error, "
        "attempts, max_attempts, cancel_requested, created_by, created_at, updated_at) "
        "VALUES(?, ?, 'queued', ?, '{}', 0, '排队中', '', 0, ?, 0, ?, ?, ?)",
        (
            job_id,
            kind,
            db.to_json(payload or {}),
            max(1, min(int(max_attempts or 2), 5)),
            str(created_by or "")[:80],
            timestamp,
            timestamp,
        ),
    )
    return get_job(job_id) or {}


def get_job(job_id: str) -> dict | None:
    return _payload(db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,)))


def list_jobs(
    *, status: str = "", kind: str = "", limit: int = 50, offset: int = 0
) -> dict:
    where, params = [], []
    if status == "active":
        where.append("status IN ('queued','running')")
    elif status:
        where.append("status = ?")
        params.append(status)
    if kind:
        where.append("type = ?")
        params.append(kind)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = db.query_one(f"SELECT COUNT(*) AS n FROM jobs {clause}", params)
    rows = db.query(
        f"SELECT * FROM jobs {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
    )
    queued = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'")
    running = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'")
    return {
        "items": [_payload(row) for row in rows],
        "total": int((total or {}).get("n") or 0),
        "queued": int((queued or {}).get("n") or 0),
        "running": int((running or {}).get("n") or 0),
    }


def request_cancel(job_id: str) -> dict:
    row = db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise ValueError("任务不存在")
    if row.get("status") in TERMINAL_STATUSES:
        raise ValueError("任务已结束，无法取消")
    if row.get("status") == "queued":
        db.execute(
            "UPDATE jobs SET status = 'cancelled', cancel_requested = 1, message = '已取消', "
            "finished_at = ?, updated_at = ? WHERE id = ?",
            (db.now(), db.now(), job_id),
        )
    else:
        db.execute(
            "UPDATE jobs SET cancel_requested = 1, message = '正在取消…', updated_at = ? WHERE id = ?",
            (db.now(), job_id),
        )
    return get_job(job_id) or {}


def retry_job(job_id: str) -> dict:
    row = db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise ValueError("任务不存在")
    if row.get("status") not in {"failed", "cancelled"}:
        raise ValueError("只有失败或已取消的任务可以重试")
    if int(row.get("attempts") or 0) >= int(row.get("max_attempts") or 2):
        raise ValueError("已达到最大重试次数，请重新发起任务")
    db.execute(
        "UPDATE jobs SET status = 'queued', progress = 0, message = '排队重试中', error = '', "
        "cancel_requested = 0, finished_at = NULL, updated_at = ? "
        "WHERE id = ?",
        (db.now(), job_id),
    )
    return get_job(job_id) or {}


def recover_interrupted() -> int:
    timestamp = db.now()
    row = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'")
    count = int((row or {}).get("n") or 0)
    if count:
        db.execute(
            "UPDATE jobs SET status = 'failed', error = '服务重启导致任务中断，可点击重试', "
            "message = '已中断', finished_at = ?, updated_at = ? WHERE status = 'running'",
            (timestamp, timestamp),
        )
        alerts.create_alert(
            "后台任务因服务重启中断",
            level="warning",
            category="job",
            detail=f"{count} 个任务被中断，请在「任务与告警」中重试。",
            source="jobs",
        )
    # 进程被杀时文档可能永远停在 processing；启动时统一回收为 failed，允许重新编译
    stuck = db.query_one("SELECT COUNT(*) AS n FROM documents WHERE status = 'processing'")
    stuck_count = int((stuck or {}).get("n") or 0)
    if stuck_count:
        db.execute(
            "UPDATE documents SET status = 'failed', error = ?, updated_at = ? "
            "WHERE status = 'processing'",
            ("服务重启导致编译中断，请重新编译该文档", timestamp),
        )
        logger.warning("已把 %s 篇中断文档从 processing 回收为 failed", stuck_count)
    # 运维 Agent 的 executing 任务同理：重启后无 worker 续跑，回收为 failed 保留工作树
    executing = db.query_one("SELECT COUNT(*) AS n FROM ops_tasks WHERE status = 'executing'")
    executing_count = int((executing or {}).get("n") or 0)
    if executing_count:
        from . import ops_agent

        rows = db.query("SELECT id FROM ops_tasks WHERE status = 'executing'")
        for row in rows:
            ops_agent._log(row["id"], "服务重启导致执行中断；任务已回收为 failed，工作树保留供人工接管。")
        db.execute(
            "UPDATE ops_tasks SET status = 'failed', "
            "test_summary = '服务重启导致执行中断', updated_at = ? WHERE status = 'executing'",
            (timestamp,),
        )
        alerts.create_alert(
            "运维任务因服务重启中断",
            level="warning",
            category="ops",
            detail=f"{executing_count} 个执行中的运维修复任务被中断，工作树已保留供人工接管或清理。",
            source="ops",
        )
    return count


def prune_jobs(days: int = 30) -> None:
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")
    rows = db.query(
        "SELECT payload FROM jobs WHERE status IN ('succeeded','failed','cancelled') "
        "AND created_at < ?",
        (cutoff,),
    )
    for row in rows:
        payload = _json(row.get("payload"), {})
        staging = str(payload.get("staging_path") or "").strip()
        if staging:
            try:
                Path(staging).unlink(missing_ok=True)
            except OSError:  # pragma: no cover - 清理失败不影响任务清理
                logger.warning("清理任务临时文件失败：%s", staging)
    db.execute(
        "DELETE FROM jobs WHERE status IN ('succeeded','failed','cancelled') AND created_at < ?",
        (cutoff,),
    )


def register_handler(kind: str, handler: Callable[[dict, Any], dict]) -> None:
    _handlers[kind] = handler


def _update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = db.now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    db.execute(
        f"UPDATE jobs SET {assignments} WHERE id = ?", [*fields.values(), job_id]
    )


def _run_ingest_upload(payload: dict, job: dict) -> dict:
    from . import ingest

    document_id = str(payload.get("document_id") or "").strip()
    if document_id:
        result = ingest.reindex_document(document_id)
        doc = result.get("document") or {}
    else:
        staging = Path(str(payload.get("staging_path") or ""))
        if not staging.is_file():
            raise RuntimeError("上传的临时文件已丢失，请重新上传")
        _update(job["id"], progress=20, message="已接收文件，开始解析与编译")
        data = staging.read_bytes()
        doc = ingest.ingest_upload(
            filename=str(payload.get("filename") or staging.name),
            data=data,
            title=payload.get("title") or None,
            tags=payload.get("tags") or [],
        )
    _update(job["id"], progress=90, message="编译完成，正在收尾")
    if doc.get("id"):
        payload["document_id"] = doc["id"]
        _update(job["id"], payload=db.to_json(payload))
    if str(doc.get("status") or "") == "failed":
        raise RuntimeError(str(doc.get("error") or "文档解析/编译失败"))
    return {"document": doc}


def _run_reindex(payload: dict, job: dict) -> dict:
    from . import ingest

    document_id = str(payload.get("document_id") or "").strip()
    if not document_id:
        raise RuntimeError("缺少 document_id")
    _update(job["id"], progress=30, message="重新解析并编译词条")
    result = ingest.reindex_document(document_id)
    doc = result.get("document") or {}
    if str(doc.get("status") or "") == "failed":
        raise RuntimeError(str(doc.get("error") or "重建失败"))
    return {"document": doc, "build": result.get("build")}


def _run_rebuild(payload: dict, job: dict) -> dict:
    from . import wiki_builder

    document_id = str(payload.get("document_id") or "").strip()
    if not document_id:
        raise RuntimeError("当前仅支持按文档重建")
    _update(job["id"], progress=30, message="重建该文档的词条")
    result = wiki_builder.rebuild_document(document_id)
    return dict(result)


def _run_scan_dir(payload: dict, job: dict) -> dict:
    """扫描服务器本地文件夹并逐个收录/更新（管理员指定目录 → 后台批量解析/编译）。

    ``mode``：``skip``（默认）只导入全新文件，同名/同内容一律跳过；
    ``update`` 额外把「同名但内容已变化」的文件覆盖并重新编译。
    """
    from . import ingest

    path = str(payload.get("path") or "").strip()
    recursive = bool(payload.get("recursive", True))
    mode = "update" if str(payload.get("mode") or "skip").strip().lower() == "update" else "skip"
    _update(job["id"], progress=5, message="正在扫描文件夹")
    limit = max(1, int(config.SCAN_MAX_FILES))
    files = ingest.list_scan_files(path, recursive=recursive, max_files=limit + 1)
    truncated = len(files) > limit
    files = files[:limit]
    empty_message = "该文件夹下没有可收录的文件（支持 md/txt/pdf/docx/doc/xlsx/csv/json/html）"
    if not files:
        return {
            "files": 0,
            "truncated": False,
            "mode": mode,
            "imported": [],
            "updated": [],
            "skipped": [],
            "failed": [],
            "message": empty_message,
        }
    processed = {str(item) for item in (payload.get("processed") or []) if item}
    pending = [item for item in files if str(item) not in processed]
    imported: list[dict] = []
    updated: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    total = len(pending)
    for index, file_path in enumerate(pending, start=1):
        latest = db.query_one("SELECT cancel_requested FROM jobs WHERE id = ?", (job["id"],))
        if latest and int(latest.get("cancel_requested") or 0):
            break
        _update(
            job["id"],
            progress=min(95, 5 + int(index * 90 / max(1, total))),
            message=f"正在处理 {file_path.name}（{index}/{total}，{mode}）",
        )
        try:
            data = file_path.read_bytes()
            digest = ingest.content_hash(data)
            existing = ingest.match_existing_document(digest, file_path.name)
            if existing is not None:
                if str(existing.get("content_hash") or "") == digest:
                    skipped.append(
                        {
                            "file": file_path.name,
                            "document_id": existing.get("id"),
                            "reason": "unchanged",
                        }
                    )
                elif mode == "skip":
                    skipped.append(
                        {
                            "file": file_path.name,
                            "document_id": existing.get("id"),
                            "reason": "name-exists",
                        }
                    )
                else:
                    result = ingest.update_document_content(str(existing["id"]), data)
                    doc = result.get("document") or {}
                    item = {
                        "file": file_path.name,
                        "document_id": doc.get("id"),
                        "title": doc.get("title"),
                        "status": doc.get("status"),
                    }
                    if str(doc.get("status") or "") == "failed":
                        item["error"] = str(doc.get("error") or "")[:200]
                        failed.append(item)
                    else:
                        updated.append(item)
            else:
                doc = ingest.ingest_upload(
                    filename=file_path.name,
                    data=data,
                    title=None,
                    tags=[],
                )
                item = {
                    "file": file_path.name,
                    "document_id": doc.get("id"),
                    "title": doc.get("title"),
                    "status": doc.get("status"),
                }
                if str(doc.get("status") or "") == "failed":
                    item["error"] = str(doc.get("error") or "")[:200]
                    failed.append(item)
                else:
                    imported.append(item)
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不中断整批
            failed.append({"file": file_path.name, "error": str(exc)[:200]})
        processed.add(str(file_path))
        payload["processed"] = sorted(processed)
        _update(job["id"], payload=db.to_json(payload))
    return {
        "files": len(files),
        "truncated": truncated,
        "mode": mode,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "imported_count": len(imported),
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
    }


def _run_gap_suggest(payload: dict, job: dict) -> dict:
    """知识缺口 AI 备选：白名单源检索 + LLM 裁决，结果写回缺口 suggestions。"""
    from . import gap_suggest

    gap_id = str(payload.get("gap_id") or "")
    _update(job["id"], progress=10, message="正在检索白名单源")
    result = gap_suggest.suggest_for_gap(gap_id)
    _update(job["id"], progress=100, message="完成")
    return result


def _run_agent_prompt(payload: dict, job: dict) -> dict:
    """定时指令：把预设 prompt 交给管理助手执行一轮（结果落会话+通知）。"""
    from . import scheduler

    schedule_id = str(payload.get("schedule_id") or "")
    if not schedule_id:
        raise RuntimeError("缺少 schedule_id")
    _update(job["id"], progress=20, message="正在执行预设指令")
    result = scheduler.run_agent_prompt(schedule_id)
    return dict(result)


def _run_subscription_check(payload: dict, job: dict) -> dict:
    """订阅检查：抓列表页发现新内容，结果写回订阅 discoveries。"""
    from . import discovery

    sub_id = str(payload.get("subscription_id") or "")
    _update(job["id"], progress=10, message="正在抓取订阅列表页")
    result = discovery.check_subscription(sub_id)
    _update(job["id"], progress=100, message=result.get("message") or "完成")
    return result


def _run_ops_analyze(payload: dict, job: dict) -> dict:
    """运维 Agent 分析：LLM 定位问题并生成方案，结束进入待许可。"""
    from . import ops_agent

    task_id = str(payload.get("task_id") or "")
    _update(job["id"], progress=15, message="LLM 分析报错中")
    result = ops_agent.analyze(task_id)
    _update(job["id"], progress=100, message="分析完成，等待管理员许可")
    return {"task_id": task_id, "status": result.get("status")}


def _run_ops_execute(payload: dict, job: dict) -> dict:
    """运维 Agent 执行：工作树修复 → 测试 → 推送 → 清理。"""
    from . import ops_agent

    task_id = str(payload.get("task_id") or "")
    run_e2e = bool(payload.get("run_e2e"))
    _update(job["id"], progress=5, message="开始执行修复流水线")
    result = ops_agent.execute(task_id, run_e2e=run_e2e)
    if result.get("status") == "failed":
        log_tail = "\n".join((result.get("log") or "").splitlines()[-3:])
        raise ValueError(
            f"运维任务执行失败（工作树已保留供人工接管）：{result.get('test_summary') or '详见任务日志'}"
            f"｜日志尾部：{log_tail}"
        )
    _update(job["id"], progress=100, message=result.get("status") or "完成")
    return {
        "task_id": task_id,
        "status": result.get("status"),
        "pr_url": result.get("pr_url") or "",
    }


_DEFAULT_HANDLERS = {
    "ingest_upload": _run_ingest_upload,
    "reindex": _run_reindex,
    "rebuild": _run_rebuild,
    "scan_dir": _run_scan_dir,
    "gap_suggest": _run_gap_suggest,
    "subscription_check": _run_subscription_check,
    "agent_prompt": _run_agent_prompt,
    "ops_analyze": _run_ops_analyze,
    "ops_execute": _run_ops_execute,
}


class JobWorker(threading.Thread):
    def __init__(self, poll_seconds: float = 0.8) -> None:
        super().__init__(name="npu-job-worker", daemon=True)
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:  # pragma: no cover - exercised indirectly by API tests
        logger.info("后台任务线程已启动")
        while not self.stop_event.is_set():
            try:
                job = _claim_next()
            except Exception:
                logger.exception("领取后台任务失败")
                job = None
            if not job:
                self.stop_event.wait(self.poll_seconds)
                continue
            try:
                self._execute(job)
            except Exception:  # 兜底：任何未捕获异常都不能让 worker 线程退出
                logger.exception("任务执行异常（已隔离，worker 继续运行）：%s", job.get("id"))
                try:
                    _update(
                        job.get("id") or "",
                        status="failed",
                        error="任务执行异常，请查看服务日志后重试",
                        message="失败，可重试",
                        finished_at=db.now(),
                    )
                except Exception:  # pragma: no cover - 数据库也不可用时只能记日志
                    logger.exception("标记异常任务失败")
        logger.info("后台任务线程已停止")

    def _execute(self, job: dict) -> None:
        job_id = job["id"]
        payload = job["payload"]
        started = db.now()
        db.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, updated_at = ?, "
            "attempts = attempts + 1, progress = 5, message = '开始执行' WHERE id = ?",
            (started, started, job_id),
        )
        handler = _handlers.get(job["type"]) or _DEFAULT_HANDLERS.get(job["type"])
        try:
            if handler is None:
                raise RuntimeError(f"未知任务类型：{job['type']}")
            fresh = get_job(job_id) or job
            if fresh.get("cancel_requested"):
                raise JobCancelled()
            result = handler(payload, fresh) or {}
            latest = db.query_one("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,))
            if latest and int(latest.get("cancel_requested") or 0):
                _update(
                    job_id,
                    status="cancelled",
                    progress=100,
                    message="已取消（执行期间完成的部分会保留）",
                    result=db.to_json(result),
                    finished_at=db.now(),
                )
            else:
                _update(
                    job_id,
                    status="succeeded",
                    progress=100,
                    message="完成",
                    error="",
                    result=db.to_json(result),
                    finished_at=db.now(),
                )
            _cleanup_staging(payload)
        except JobCancelled:
            _update(
                job_id,
                status="cancelled",
                message="已取消",
                finished_at=db.now(),
            )
            _cleanup_staging(payload)
        except Exception as exc:
            logger.exception("后台任务失败：%s", job_id)
            message = str(exc).strip()[:500] or "任务执行失败"
            _update(
                job_id,
                status="failed",
                error=message,
                message="失败，可重试",
                finished_at=db.now(),
            )
            alerts.create_alert(
                f"后台任务失败：{job['type']}",
                level="error",
                category="job",
                detail=message,
                source="jobs",
                resource_id=job_id,
            )
            logger.debug("任务异常堆栈：\n%s", traceback.format_exc())
            _cleanup_staging(payload)


def _cleanup_staging(payload: dict) -> None:
    path = str(payload.get("staging_path") or "").strip()
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        logger.warning("清理任务临时文件失败：%s", path)


def _claim_next() -> dict | None:
    row = db.query_one(
        "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC, rowid ASC LIMIT 1"
    )
    if not row:
        return None
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'",
            (db.now(), row["id"]),
        )
        conn.commit()
        # 多 worker 场景：抢占失败说明被其他 worker 领走，本轮直接跳过
        if cur.rowcount == 0:
            return None
    return get_job(row["id"])


def start_worker() -> JobWorker:
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return _worker
        recover_interrupted()
        prune_jobs()
        _worker = JobWorker()
        _worker.start()
        return _worker


def stop_worker(timeout: float = 5.0) -> None:
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            _worker.stop()
            _worker.join(timeout=timeout)
        _worker = None


def worker_status() -> dict:
    return {
        "worker_running": bool(_worker and _worker.is_alive()),
        "worker_thread": _worker.name if _worker else "",
    }


__all__ = [
    "JOB_TYPES",
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "JobCancelled",
    "create_job",
    "stage_upload",
    "get_job",
    "list_jobs",
    "request_cancel",
    "retry_job",
    "recover_interrupted",
    "register_handler",
    "start_worker",
    "stop_worker",
    "worker_status",
]

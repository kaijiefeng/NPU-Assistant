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

JOB_TYPES = {"ingest_upload", "reindex", "rebuild"}
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
    row = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'")
    count = int((row or {}).get("n") or 0)
    if count:
        timestamp = db.now()
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
    return count


def prune_jobs(days: int = 30) -> None:
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")
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


_DEFAULT_HANDLERS = {
    "ingest_upload": _run_ingest_upload,
    "reindex": _run_reindex,
    "rebuild": _run_rebuild,
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
            self._execute(job)
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
    db.execute(
        "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ? AND status = 'queued'",
        (db.now(), row["id"]),
    )
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

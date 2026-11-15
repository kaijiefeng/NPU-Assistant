#!/usr/bin/env python3
"""额外后台编译 worker：在服务进程之外再开 N 个任务线程，加速大批量导入。

前提：`jobs._claim_next` 已用 `UPDATE ... WHERE status='queued'` 原子抢占，多 worker 不会重复执行。
用法：
    .\\.venv\\Scripts\\python.exe scripts\\job_worker.py 2
按 Ctrl+C 停止；任务状态都在 SQLite 里，进程退出不会丢任务。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import jobs  # noqa: E402


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    workers = [jobs.JobWorker(poll_seconds=0.5) for _ in range(max(1, count))]
    for worker in workers:
        worker.start()
    print(f"已启动 {len(workers)} 个后台编译 worker（Ctrl+C 停止）", flush=True)
    try:
        while any(worker.is_alive() for worker in workers):
            time.sleep(1)
    except KeyboardInterrupt:
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.join(timeout=5)
        print("worker 已停止", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

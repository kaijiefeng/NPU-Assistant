"""沙箱内启动/引导/回收 NPU-Assistant 服务。"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx

from .sandbox import REPO, RunSandbox

PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(os.sys.executable)


class AppServer:
    def __init__(self, sandbox: RunSandbox, profile: str) -> None:
        self.sandbox = sandbox
        self.profile = profile
        self.process: subprocess.Popen | None = None
        self.base_url = sandbox.base_url()
        self.api = httpx.Client(base_url=self.base_url, timeout=60, trust_env=False)

    # ------------------------------------------------------------------
    def start(self) -> "AppServer":
        env = self.sandbox.env(self.profile)
        self.process = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.sandbox.port),
                "--log-level",
                "warning",
            ],
            cwd=str(REPO / "backend"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_health()
        if self.profile == "token":
            self.api.headers.update({"X-Admin-Token": self.sandbox.token})
        return self

    def _wait_health(self) -> None:
        deadline = time.time() + 90
        last_error = ""
        while time.time() < deadline:
            try:
                response = self.api.get("/api/health")
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:  # noqa: BLE001
                last_error = type(exc).__name__
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"服务提前退出（exit={self.process.returncode}），最后错误：{last_error}")
            time.sleep(0.4)
        raise RuntimeError(f"服务 90s 内未就绪：{last_error}")

    # ------------------------------------------------------------------
    def bootstrap(self, mock_url: str) -> None:
        """seed 快照、注册确定性模型；account 档再创建首个管理员。"""
        self.api.post("/api/knowledge/seed", json={})
        provider = self.api.post(
            "/api/models/providers",
            json={
                "name": "E2E Mock",
                "type": "openai",
                "base_url": mock_url,
                "api_key": "sk-e2e",
            },
        )
        provider.raise_for_status()
        provider_id = provider.json()["id"]
        model = self.api.post(
            "/api/models",
            json={
                "provider_id": provider_id,
                "model_name": "e2e-mock",
                "display_name": "E2E Mock",
                "temperature": 0.1,
                "max_tokens": 1024,
                "is_default": True,
            },
        )
        model.raise_for_status()

        if self.profile == "account":
            setup = self.api.post(
                "/api/auth/setup",
                json={
                    "username": "e2e-admin",
                    "password": self.sandbox.admin_password,
                    "display_name": "E2E 管理员",
                },
            )
            setup.raise_for_status()
            # 独立 API 会话：登录后用 Cookie 做跨断言
            login = httpx.post(
                f"{self.base_url}/api/auth/login",
                json={"username": "e2e-admin", "password": self.sandbox.admin_password},
                timeout=30,
                trust_env=False,
            )
            login.raise_for_status()
            self.api.cookies.update(login.cookies)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        try:
            self.api.close()
        except Exception:  # noqa: BLE001
            pass
        if self.process is None or self.process.poll() is not None:
            return
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.process.kill()

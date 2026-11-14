"""E2E 沙箱：临时目录、随机端口、生产数据指纹与清理。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROD_DATA_DIR = (REPO / "backend" / "data").resolve()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint_production() -> dict:
    """生产数据指纹：DB 哈希、uploads 文件清单、令牌文件哈希。"""
    snapshot: dict[str, object] = {}
    db_path = PROD_DATA_DIR / "app.db"
    snapshot["db"] = _digest_file(db_path) if db_path.is_file() else None
    uploads = PROD_DATA_DIR / "uploads"
    snapshot["uploads"] = (
        sorted((item.name, item.stat().st_size) for item in uploads.glob("*") if item.is_file())
        if uploads.is_dir()
        else []
    )
    token = PROD_DATA_DIR / "admin_token.txt"
    snapshot["admin_token"] = _digest_file(token) if token.is_file() else None
    snapshot["data_dir_mtime"] = (
        PROD_DATA_DIR.stat().st_mtime_ns if PROD_DATA_DIR.exists() else None
    )
    return snapshot


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass
class RunSandbox:
    """一次运行的隔离环境；所有可写文件都在 ``root`` 下。"""

    root: Path
    data_dir: Path
    port: int
    mock_port: int
    chrome_profile: Path
    report_dir: Path
    token: str
    admin_password: str
    prod_before: dict = field(default_factory=dict)

    @classmethod
    def create(cls, *, report_root: Path | None = None) -> "RunSandbox":
        root = Path(tempfile.mkdtemp(prefix="npu-e2e-")).resolve()
        if _is_under(root, PROD_DATA_DIR) or _is_under(PROD_DATA_DIR, root):
            raise RuntimeError(f"沙箱目录与生产数据目录重叠，拒绝启动：{root}")
        data_dir = (root / "data").resolve()
        if _is_under(data_dir, PROD_DATA_DIR):
            raise RuntimeError(f"沙箱数据目录位于生产目录内，拒绝启动：{data_dir}")
        if data_dir == PROD_DATA_DIR:
            raise RuntimeError("沙箱数据目录不能等于生产数据目录")

        (root / "seed").mkdir(parents=True, exist_ok=True)
        source_seed = REPO / "backend" / "data" / "wiki_seed.json"
        if source_seed.is_file():
            shutil.copyfile(source_seed, root / "seed" / "wiki_seed.json")

        run_report = (report_root or (REPO / ".artifacts" / "e2e")) / root.name
        sandbox = cls(
            root=root,
            data_dir=data_dir,
            port=free_port(),
            mock_port=free_port(),
            chrome_profile=root / "chrome-profile",
            report_dir=run_report,
            token=os.urandom(16).hex(),
            admin_password="e2e-" + os.urandom(8).hex(),
            prod_before=fingerprint_production(),
        )
        (root / "data" / "uploads").mkdir(parents=True, exist_ok=True)
        sandbox.report_dir.mkdir(parents=True, exist_ok=True)
        return sandbox

    def env(self, profile: str) -> dict[str, str]:
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "NPU_DATA_DIR": str(self.data_dir),
            "NPU_DB_PATH": str(self.data_dir / "app.db"),
            "NPU_WIKI_SEED_PATH": str(self.root / "seed" / "wiki_seed.json"),
            "NPU_HOST": "127.0.0.1",
            "NPU_PORT": str(self.port),
            "NPU_AUTO_SEED": "1",
            "NPU_URL_IMPORT_ALLOW_PRIVATE": "1",
            "NPU_SCAN_MAX_FILES": "5",
        }
        if profile == "token":
            env["NPU_ADMIN_TOKEN"] = self.token
        elif profile == "account":
            env.pop("NPU_ADMIN_TOKEN", None)
        else:
            env.pop("NPU_ADMIN_TOKEN", None)
        return env

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def manifest(self) -> dict:
        return {
            "root": str(self.root),
            "data_dir": str(self.data_dir),
            "port": self.port,
            "mock_port": self.mock_port,
            "chrome_profile": str(self.chrome_profile),
            "report_dir": str(self.report_dir),
        }

    def verify_production_untouched(self) -> None:
        after = fingerprint_production()
        if after != self.prod_before:
            changed = {
                key: (self.prod_before.get(key), after.get(key))
                for key in after
                if after.get(key) != self.prod_before.get(key)
            }
            raise AssertionError(f"生产数据在 E2E 运行期间被修改：{json.dumps(changed, ensure_ascii=False)[:500]}")

    def cleanup(self, keep: bool = False) -> None:
        if keep:
            return
        shutil.rmtree(self.root, ignore_errors=True)

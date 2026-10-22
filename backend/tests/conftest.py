"""pytest 全局 fixture：临时数据目录 + 每用例数据复位。

约定（见 docs/CONTRACT.md §3/§7、scripts/check.ps1）：

* 测试**绝不能**污染共享开发库 ``backend/data/app.db``：在导入 ``app`` 之前把
  ``NPU_DATA_DIR`` / ``NPU_DB_PATH`` 指向 ``tempfile.mkdtemp()``；
* 每个用例开始前清空可变表并调用 ``db.init_db()``，恢复首启默认数据
  （mock Provider、默认模型、控制台通道、默认设置），用例之间互不影响；
* 示例知识库由 ``/api/knowledge/seed`` 显式导入（``NPU_AUTO_SEED=0`` 关闭自动导入），
  需要知识库的用例声明 ``seeded`` fixture；
* 仓库自带种子文档位于 ``backend/data/seed_docs``，测试只读不写。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
REPO_DIR = BACKEND_DIR.parent

# ---------------------------------------------------------------------------
# 1) 环境隔离必须发生在 `import app` 之前（config.py 在导入时读取环境变量）
# ---------------------------------------------------------------------------
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="npu_assistant_tests_"))
os.environ["NPU_DATA_DIR"] = str(_TMP_ROOT)
os.environ["NPU_DB_PATH"] = str(_TMP_ROOT / "app.db")
os.environ["NPU_AUTO_SEED"] = "0"
os.environ["NPU_ADMIN_TOKEN"] = ""
os.environ["NPU_MAX_UPLOAD_MB"] = "20"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 删除顺序必须是「子表在前、父表在后」，否则会触发外键约束
MUTABLE_TABLES = (
    "chunks",
    "messages",
    "documents",
    "conversations",
    "models_registry",
    "providers",
    "bot_channels",
    "settings",
)


@pytest.fixture(scope="session")
def client():
    """会话级 TestClient：整个测试进程共用一个 FastAPI 实例（lifespan 只跑一次）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_state(client):
    """每个用例前把数据库恢复到「刚首启」状态，并清空临时上传目录。"""
    from app import config, db

    with db.connect() as conn:
        for table in MUTABLE_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

    if config.UPLOAD_DIR.is_dir():
        for path in config.UPLOAD_DIR.iterdir():
            if path.is_file() and path.name != ".gitkeep":
                path.unlink(missing_ok=True)

    db.init_db()
    yield


@pytest.fixture()
def seeded(client):
    """导入示例知识库并返回 ``{ingested, skipped, total_documents}``。"""
    response = client.post("/api/knowledge/seed")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_documents"] >= 6, data
    return data


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_data():
    """进程结束时尽力清理临时数据目录（Windows 上句柄可能延迟释放，忽略失败）。"""
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)

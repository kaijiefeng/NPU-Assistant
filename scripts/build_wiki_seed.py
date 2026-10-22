r"""用真实 LLM 预编译 Wiki 快照（backend/data/wiki_seed.json）。

用途：在没有 API Key 的机器上冷启动也能浏览预编译好的 Wiki；需要重新生成快照时运行本脚本。
脚本会使用临时数据目录，不会污染 backend/data/app.db。

用法（PowerShell）：
    $env:NPU_SEED_API_KEY="sk-..."
    $env:NPU_SEED_BASE_URL="https://api.deepseek.com/v1"   # 可选，默认如此
    $env:NPU_SEED_MODEL="deepseek-chat"                    # 可选
    .\.venv\Scripts\python.exe scripts\build_wiki_seed.py

环境变量：
    NPU_SEED_API_KEY   必填，OpenAI 兼容 API Key
    NPU_SEED_BASE_URL  可选，默认 https://api.deepseek.com/v1
    NPU_SEED_MODEL     可选，默认 deepseek-chat
    NPU_SEED_DOCS_DIR  可选，默认 backend/data/seed_docs
    NPU_SEED_OUT       可选，默认 backend/data/wiki_seed.json
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 必须在 import app.* 之前设置临时数据目录
TMP_DIR = tempfile.mkdtemp(prefix="npu-wiki-seed-")
os.environ["NPU_DATA_DIR"] = TMP_DIR
os.environ["NPU_DB_PATH"] = str(Path(TMP_DIR) / "app.db")
os.environ["NPU_AUTO_SEED"] = "0"

from app import config, db  # noqa: E402
from app.services import ingest, wiki_builder  # noqa: E402


def configure_llm() -> None:
    api_key = os.environ.get("NPU_SEED_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("缺少 NPU_SEED_API_KEY 环境变量")
    base_url = os.environ.get("NPU_SEED_BASE_URL", "https://api.deepseek.com/v1").strip()
    model_name = os.environ.get("NPU_SEED_MODEL", "deepseek-chat").strip()

    # 复用 db 层直接写入：新增 openai provider + model 并设为默认
    provider_id = db.new_id()
    db.execute(
        "INSERT INTO providers(id, name, type, base_url, api_key, embedding_model, extra, created_at) "
        "VALUES(?, ?, 'openai', ?, ?, '', '{}', ?)",
        (provider_id, "DeepSeek（种子编译）", base_url, api_key, db.now()),
    )
    db.execute(
        "INSERT INTO models_registry(id, provider_id, model_name, display_name, temperature, max_tokens, "
        "is_default, enabled, created_at) VALUES(?, ?, ?, ?, 0.1, 2000, 1, 1, ?)",
        (db.new_id(), provider_id, model_name, f"{model_name}（种子编译）", db.now()),
    )
    # 去掉默认 mock，避免被选中
    db.execute("UPDATE models_registry SET is_default = 0 WHERE provider_id != ?", (provider_id,))


def main() -> None:
    db.init_db()
    configure_llm()

    docs_dir = Path(os.environ.get("NPU_SEED_DOCS_DIR", config.SEED_DOCS_DIR))
    files = sorted(p for p in docs_dir.iterdir() if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXTS)
    if not files:
        raise SystemExit(f"未找到种子文档：{docs_dir}")

    started = time.time()
    ok = 0
    for index, path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] 编译 {path.name} ...", flush=True)
        try:
            document = ingest.ingest_upload(path.name, path.read_bytes(), title=None)
            print(
                f"    -> status={document.get('status')} pages={document.get('page_count')} "
                f"error={document.get('error') or ''}",
                flush=True,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它文件
            print(f"    !! 失败：{exc}", flush=True)

    out_path = Path(os.environ.get("NPU_SEED_OUT", config.WIKI_SEED_PATH))
    result = wiki_builder.export_seed_wiki(out_path)
    elapsed = time.time() - started
    print("\n=== 完成 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"输出：{out_path}")
    print(f"文档：{ok}/{len(files)} 编译成功；耗时 {elapsed:.1f}s")
    print(f"临时数据目录：{TMP_DIR}")


if __name__ == "__main__":
    main()

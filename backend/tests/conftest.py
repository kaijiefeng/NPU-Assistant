"""pytest 全局 fixture：临时数据目录 + fake LLM + 每用例数据复位（LLM Wiki v0.2）。

约定（docs/WIKI-DESIGN.md §7、docs/CONTRACT.md §3/§7）：

* 测试**绝不能**污染共享开发库 ``backend/data/app.db``：在导入 ``app`` 之前把
  ``NPU_DATA_DIR`` / ``NPU_DB_PATH`` / ``NPU_WIKI_SEED_PATH`` 指向 ``tempfile.mkdtemp()``；
* 每个用例开始前清空可变表并 ``db.init_db()``，恢复首启默认数据
  （mock Provider + 默认模型 + console 通道 + 默认设置）；
* **fake LLM**：monkeypatch ``app.services.llm.chat``，按提示词分流：
  - 编译请求（含「【可用章节】」）→ 固定 JSON 数组（1 个词条 + 逐字 source_quote）；
  - 合并请求（含「【已有词条】」）→ 固定合并结果；
  - 导航请求（含「【Wiki 目录】」）→ ``{"page_ids": [...]}``（默认空，可切换）；
  - 回答请求 → 固定中文回答（默认带 ``[1]``）；
  另提供失败模式（``broken_llm``：所有调用抛异常，编译应降级、不 500），全程不调用真实 API；
* ``seeded_wiki`` 写入最小预编译快照并调用 ``POST /api/knowledge/seed``，供浏览/搜索/对话用例复用。
"""
from __future__ import annotations

import json
import os
import re
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
# 预编译快照指向临时目录，保证测试不受仓库 wiki_seed.json 是否存在影响
os.environ["NPU_WIKI_SEED_PATH"] = str(_TMP_ROOT / "wiki_seed.json")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 删除顺序必须是「子表在前、父表在后」，否则会触发外键约束
MUTABLE_TABLES = (
    "wiki_links",
    "wiki_sources",
    "wiki_builds",
    "wiki_pages",
    "messages",
    "conversations",
    "models_registry",
    "providers",
    "bot_channels",
    "documents",
    "settings",
)

SEED_PAYLOAD = {
    "version": 1,
    "generated_at": "2026-10-22T12:00:00",
    "documents": [
        {
            "id": "d" * 32,
            "title": "国家奖学金与奖助学金评审办法（示例）",
            "filename": "国家奖学金与奖助学金评审办法（示例）.md",
            "file_ext": ".md",
            "size_bytes": 2048,
            "tags": ["奖助"],
            "char_count": 1200,
            "page_count": 3,
            "status": "ready",
            "created_at": "2026-10-22T12:00:00",
            "updated_at": "2026-10-22T12:00:00",
        }
    ],
    "pages": [
        {
            "id": "a" * 32,
            "slug": "国家奖学金申请",
            "title": "国家奖学金申请",
            "summary": "国家奖学金面向特别优秀的本科生，需提交申请表、成绩单与个人事迹材料。",
            "body_md": (
                "## 申请条件\n\n- 学习成绩排名前 10%\n- 通过家庭经济困难认定\n\n"
                "## 申请材料\n\n- 申请表\n- 成绩单\n- 个人事迹材料"
            ),
            "aliases": ["国奖", "国家奖学金"],
            "tags": ["奖助"],
            "status": "ready",
            "version": 2,
            "source_count": 1,
            "created_at": "2026-10-22T12:00:00",
            "updated_at": "2026-10-22T12:10:00",
        },
        {
            "id": "b" * 32,
            "slug": "请假办理流程",
            "title": "请假办理流程",
            "summary": "学生请假需在系统提交申请，病假需提供校医院或二级以上医院的诊断证明。",
            "body_md": (
                "## 办理流程\n\n1. 登录学生系统提交请假申请\n2. 辅导员审批\n\n"
                "## 材料\n\n- 诊断证明\n- 请假事由说明"
            ),
            "aliases": ["请假", "病假"],
            "tags": ["请假考勤"],
            "status": "ready",
            "version": 1,
            "source_count": 1,
            "created_at": "2026-10-22T12:00:00",
            "updated_at": "2026-10-22T12:05:00",
        },
        {
            "id": "c" * 32,
            "slug": "待审核词条",
            "title": "待审核词条",
            "summary": "该词条尚待人工审核，不应参与问答。",
            "body_md": "## 待审核\n\n原始内容待人工确认。",
            "aliases": [],
            "tags": [],
            "status": "needs_review",
            "version": 1,
            "source_count": 1,
            "created_at": "2026-10-22T12:00:00",
            "updated_at": "2026-10-22T12:00:00",
        },
    ],
    "sources": [
        {
            "page_id": "a" * 32,
            "document_id": "d" * 32,
            "document_title": "国家奖学金与奖助学金评审办法（示例）",
            "source_file": "国家奖学金与奖助学金评审办法（示例）.md",
            "section": "五、申请材料清单",
            "quote": "申请材料包括申请表、成绩单与个人事迹材料。",
            "created_at": "2026-10-22T12:00:00",
        },
        {
            "page_id": "b" * 32,
            "document_id": "d" * 32,
            "document_title": "国家奖学金与奖助学金评审办法（示例）",
            "source_file": "国家奖学金与奖助学金评审办法（示例）.md",
            "section": "一、请假类型",
            "quote": "病假须提供校医院或二级以上医院的诊断证明。",
            "created_at": "2026-10-22T12:00:00",
        },
        {
            "page_id": "c" * 32,
            "document_id": "d" * 32,
            "document_title": "国家奖学金与奖助学金评审办法（示例）",
            "source_file": "国家奖学金与奖助学金评审办法（示例）.md",
            "section": "待审核",
            "quote": "待人工审核的原始内容。",
            "created_at": "2026-10-22T12:00:00",
        },
    ],
    "links": [
        {
            "from_page_id": "a" * 32,
            "to_page_id": "b" * 32,
            "relation": "related",
        }
    ],
}

SCHOLARSHIP_PAGE_ID = "a" * 32
LEAVE_PAGE_ID = "b" * 32
NEEDS_REVIEW_PAGE_ID = "c" * 32
SEED_DOCUMENT_ID = "d" * 32


# ---------------------------------------------------------------------------
# 2) Fake LLM
# ---------------------------------------------------------------------------
class FakeLLM:
    """可控的假模型：按提示词分流编译 / 合并 / 导航 / 回答，并记录调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False
        self.error = "模拟模型故障"
        self.nav_ids: list[str] = []
        self.answer_text = (
            "根据校园 Wiki 词条，国家奖学金需要提交申请表、成绩单与个人事迹材料 [1]。"
        )
        self.chitchat_text = (
            "同学你好～我是辅导员数字分身「小南」，可以回答奖助学金、请假考勤、"
            "心理服务等校园政策问题。"
        )
        self.compile_title: str | None = None

    # -- 内部工具 ---------------------------------------------------------
    @staticmethod
    def _result(content: str) -> dict:
        return {
            "content": content,
            "model": "Fake DeepSeek",
            "provider_name": "Fake Provider",
            "provider_type": "openai",
            "fallback": False,
            "usage": {},
            "latency_ms": 3,
            "error": None,
        }

    def _compile_entry(self, prompt: str) -> dict:
        title_match = re.search(r"【文档标题】\s*(.+)", prompt)
        doc_title = (title_match.group(1).strip() if title_match else "") or "测试词条"
        if self.compile_title:
            doc_title = self.compile_title
        if "<<<" in prompt and ">>>" in prompt:
            unit_text = prompt.split("<<<", 1)[1].split(">>>", 1)[0].strip()
        else:
            unit_text = prompt
        quote = re.sub(r"\s+", " ", unit_text).strip()[:60]
        return {
            "title": doc_title,
            "aliases": [],
            "summary": f"{doc_title}的测试摘要，内容来自源文件。",
            "tags": ["测试"],
            "body_md": (
                f"## 概述\n\n{doc_title}的测试正文，内容来自源文件。\n\n"
                "## 注意事项\n\n- 示例条目，请以学校最新通知为准。"
            ),
            "source_section": "",
            "source_quote": quote,
            "related_titles": [],
        }

    # -- 假 chat ----------------------------------------------------------
    def chat(self, messages, *, model_id=None, temperature=None, max_tokens=None, **kwargs):
        prompt = "\n".join(str(item.get("content") or "") for item in messages or [])
        self.calls.append(prompt)
        if self.fail:
            return {
                "content": "",
                "model": "Fake DeepSeek",
                "provider_name": "Fake Provider",
                "provider_type": "openai",
                "fallback": True,
                "usage": {},
                "latency_ms": 1,
                "error": self.error,
            }
        if "【标签分组】" in prompt:
            return self._result(json.dumps({"tags": []}, ensure_ascii=False))
        if "【Wiki 目录】" in prompt:
            return self._result(
                json.dumps({"page_ids": list(self.nav_ids)}, ensure_ascii=False)
            )
        if "【已有词条】" in prompt:
            merged = {
                "summary": "合并后的测试摘要。",
                "body_md": "## 概述\n\n合并后的测试正文，内容来自多个源文件。",
                "aliases": [],
                "tags": ["测试"],
            }
            return self._result(json.dumps(merged, ensure_ascii=False))
        if "【可用章节】" in prompt:
            entries = [self._compile_entry(prompt)]
            return self._result(json.dumps(entries, ensure_ascii=False))
        if "【当前场景】" in prompt and "寒暄" in prompt:
            # 寒暄 / 致谢 / 告别 / 自我介绍：自然回复，不引用 Wiki
            return self._result(
                json.dumps(
                    {
                        "content": self.chitchat_text,
                        "model": "Fake DeepSeek",
                        "provider_type": "openai",
                        "fallback": False,
                    },
                    ensure_ascii=False,
                )
            )
        # 问答
        return self._result(
            json.dumps(
                {
                    "content": self.answer_text,
                    "model": "Fake DeepSeek",
                    "provider_type": "openai",
                    "fallback": False,
                },
                ensure_ascii=False,
            )
        )


class BrokenLLM:
    """失败模式：编译 / 合并 / 导航 / 回答一律抛异常。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, messages, *, model_id=None, temperature=None, max_tokens=None, **kwargs):
        prompt = "\n".join(str(item.get("content") or "") for item in messages or [])
        self.calls.append(prompt)
        raise RuntimeError("模拟 LLM 网络故障")


# ---------------------------------------------------------------------------
# 3) fixtures
# ---------------------------------------------------------------------------
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
def fake_llm(monkeypatch):
    """成功模式 fake LLM（monkeypatch ``app.services.llm.chat``）。"""
    from app.services import llm

    fake = FakeLLM()
    monkeypatch.setattr(llm, "chat", fake.chat)
    return fake


@pytest.fixture()
def broken_llm(monkeypatch):
    """失败模式 LLM：所有调用抛异常（编译应降级为 failed / 不 500）。"""
    from app.services import llm

    broken = BrokenLLM()
    monkeypatch.setattr(llm, "chat", broken.chat)
    return broken


@pytest.fixture()
def real_model(client):
    """插入一个假的 OpenAI 兼容 Provider/模型并设为默认，使 ``can_compile()`` 为 True。"""
    provider = client.post(
        "/api/models/providers",
        json={
            "name": "Fake DeepSeek Provider",
            "type": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-fake-1234567890abcd",
        },
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    model = client.post(
        "/api/models",
        json={
            "provider_id": provider_id,
            "model_name": "deepseek-chat",
            "display_name": "Fake DeepSeek",
            "is_default": True,
        },
    )
    assert model.status_code == 201, model.text
    return model.json()


@pytest.fixture()
def compile_ready(fake_llm, real_model):
    """编译可用环境：真实模型（假地址）+ fake LLM。返回 fake LLM 以便断言/切换。"""
    return fake_llm


@pytest.fixture()
def wiki_seed_file(client):
    """把最小预编译快照写到临时 ``NPU_WIKI_SEED_PATH``，返回文件路径。"""
    from app import config

    config.WIKI_SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.WIKI_SEED_PATH.write_text(
        json.dumps(SEED_PAYLOAD, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config.WIKI_SEED_PATH


@pytest.fixture()
def seeded_wiki(client, wiki_seed_file):
    """导入最小预编译快照（3 个词条：2 ready + 1 needs_review，1 篇源文件）。"""
    response = client.post("/api/knowledge/seed")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("imported") is True, data
    assert data.get("pages") == 3, data
    return data


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_data():
    """进程结束时尽力清理临时数据目录（Windows 上句柄可能延迟释放，忽略失败）。"""
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)

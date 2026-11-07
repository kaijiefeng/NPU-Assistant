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

import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
os.environ["NPU_REMOTE_FALLBACK"] = "0"
os.environ["NPU_LLM_JUDGE"] = "0"
os.environ["NPU_MAX_UPLOAD_MB"] = "20"
# QQ 官方机器人默认会在启动时主动连接腾讯网关；测试不访问真实网络。
os.environ["NPU_QQ_OFFICIAL_AUTOSTART"] = "0"
# 预编译快照指向临时目录，保证测试不受仓库 wiki_seed.json 是否存在影响
os.environ["NPU_WIKI_SEED_PATH"] = str(_TMP_ROOT / "wiki_seed.json")
# URL 收录测试使用本地 127.0.0.1 http.server，需要在 import app 前允许私网地址；
# 默认拒绝私网的用例会在测试内 monkeypatch config.URL_IMPORT_ALLOW_PRIVATE=False。
os.environ["NPU_URL_IMPORT_ALLOW_PRIVATE"] = "1"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 删除顺序必须是「子表在前、父表在后」，否则会触发外键约束
MUTABLE_TABLES = (
    "audit_log",
    "admin_sessions",
    "admin_users",
    "system_alerts",
    "jobs",
    "notification_log",
    "crisis_events",
    "handoff_requests",
    "answer_feedback",
    "knowledge_gaps",
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
    if config.JOB_STAGING_DIR.is_dir():
        for path in config.JOB_STAGING_DIR.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)

    db.init_db()
    client.cookies.clear()
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


# ---------------------------------------------------------------------------
# 4) 移动端 URL 收录：本地 HTTP 服务 + fake importer
# ---------------------------------------------------------------------------
ARTICLE_TEXT = (
    "国家奖学金用于奖励特别优秀的全日制本科在校生，申请材料包括申请表、成绩单、"
    "个人事迹材料与获奖证明。每年 9 月中旬发布通知，9 月下旬学院截止收材料。"
    "本段为公众号样式正文，用于验证 #js_content 提取与正文清洗。"
)
ARTICLE_HTML = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8" />
<meta property="og:title" content="国家奖学金申请指南（公众号）" />
<title>公众号文章 - 国家奖学金</title>
</head>
<body>
<nav>首页 / 通知公告 / 学工动态</nav>
<header>西北工业大学学生工作部</header>
<div class="rich_media_content" id="js_content">
  <p>{ARTICLE_TEXT}</p>
  <p>具体时间、金额、电话均为示例，请以学校官方通知为准。</p>
</div>
<script>console.log("不应进入正文");</script>
<footer>版权所有</footer>
</body></html>
"""


class _ArticleHandler(BaseHTTPRequestHandler):
    """极简 HTTP 服务：HTML / JSON / 超大响应 / gzip / 404，用于 URL 收录测试。

    体积口径用于验证 ``NPU_URL_IMPORT_MAX_BYTES`` 默认上限（v0.3 起 8MB）：
    * ``/article-4mb``：约 4MB 可提取正文 → 默认配置应成功（旧 3MB 上限会拒绝）；
    * ``/over-limit``：约 9MB → 应 400 并给出可操作提示（含环境变量名/文本收录建议）；
    * ``/article-gzip-4mb``：gzip 压缩的 4MB 页面 → 成功（Content-Length 为压缩后大小，不得误拒）。
    """

    def log_message(self, *args) -> None:  # noqa: D401 - 静默日志
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        path = self.path.split("?", 1)[0]
        if path == "/article":
            self._respond(200, "text/html; charset=utf-8", ARTICLE_HTML.encode("utf-8"))
        elif path == "/json":
            self._respond(200, "application/json", b'{"hello": "world"}')
        elif path == "/article-4mb":
            body = _big_article(4 * 1024 * 1024, title="4MB 测试文章").encode("utf-8")
            self._respond(200, "text/html; charset=utf-8", body)
        elif path == "/article-gzip-4mb":
            raw = _big_article(4 * 1024 * 1024, title="gzip 4MB 测试文章").encode("utf-8")
            body = gzip.compress(raw, compresslevel=6)
            self._respond(
                200,
                "text/html; charset=utf-8",
                body,
                extra_headers={"Content-Encoding": "gzip"},
            )
        elif path == "/over-limit":
            body = _big_article(9 * 1024 * 1024, title="9MB 超限测试文章").encode("utf-8")
            self._respond(200, "text/html; charset=utf-8", body)
        else:
            self._respond(404, "text/html; charset=utf-8", b"<html>not found</html>")

    def _respond(
        self,
        status: int,
        content_type: str,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端在超限预检/流式截断时可能提前断开，测试服务器静默忽略
            pass


def _big_article(target_bytes: int, *, title: str) -> str:
    """构造约 ``target_bytes`` 字节、正文可提取的公众号样式 HTML。"""
    page = (
        "校园政策正文内容示例：国家奖学金申请条件、材料清单与时间安排说明。"
        "本条为体积回归测试数据，用于验证 URL 抓取上限与正文提取。"
    )
    # 预留 HTML 包装开销后重复正文，保证总字节数接近目标值
    repeat = max(1, (target_bytes - 600) // len(page.encode("utf-8")))
    text = page * repeat
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/>"
        f"<meta property='og:title' content='{title}'/><title>{title}</title></head>"
        f"<body><div id='js_content'><p>{text}</p></div></body></html>"
    )


@pytest.fixture()
def article_server():
    """本地 127.0.0.1 HTTP 服务（返回公众号样式 HTML），产出 ``base_url``。"""
    from types import SimpleNamespace

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ArticleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield SimpleNamespace(base_url=base_url)
    finally:
        server.shutdown()
        server.server_close()


class FakeImporter:
    """记录机器人入库调用，可配置返回结果 / 抛错 / 阻塞（用于微信 ack 测试）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.event = threading.Event()
        self.error: Exception | None = None
        self.result: dict = {
            "duplicated": False,
            "document": {"id": "doc-bot-1", "title": "机器人收录的文章", "page_count": 2},
            "pages": [
                {"id": "page-a", "title": "词条A", "summary": "摘要A"},
                {"id": "page-b", "title": "词条B", "summary": "摘要B"},
            ],
        }

    def import_url(self, url, title=None, tags=None):
        self.calls.append({"url": url, "title": title, "tags": list(tags or [])})
        self.event.set()
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def fake_importer(monkeypatch):
    """monkeypatch ``app.services.importer.import_url``，避免机器人测试真实抓取网页。"""
    from app.services import importer

    fake = FakeImporter()
    monkeypatch.setattr(importer, "import_url", fake.import_url)
    return fake


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_data():
    """进程结束时尽力清理临时数据目录（Windows 上句柄可能延迟释放，忽略失败）。"""
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)

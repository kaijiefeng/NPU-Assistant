"""移动端快速收录测试：URL / 文本 / 批量导入、SSRF 与内容防护、管理员令牌鉴权（docs/MOBILE-OPS.md §2/§5）。

编译统一使用 fake LLM（``compile_ready``），URL 抓取使用本地 127.0.0.1 http.server，
不调用真实网络与真实 LLM；鉴权用例 monkeypatch ``config.ADMIN_TOKEN``。
"""
from __future__ import annotations

import json

import pytest

LONG_TEXT = (
    "关于调整第十教学周课程安排的通知（转自班级群）\n\n"
    "因教学楼电路检修，第十教学周周三下午的《数据库原理》课程调整至周四 10:00—11:40，"
    "地点改为实验楼 A301 机房。请各班学习委员及时通知同学，如有疑问联系学院教学办公室。"
    "本次调整仅涉及个别课程，其他课程按原课表执行，具体以教务处通知为准。"
)


def _import_text(client, **payload):
    return client.post("/api/knowledge/import/text", json=payload)


def _import_url(client, **payload):
    return client.post("/api/knowledge/import/url", json=payload)


def _documents(client) -> list[dict]:
    return client.get("/api/knowledge/documents").json()["items"]


# ---------------------------------------------------------------------------
# 1) 文本收录
# ---------------------------------------------------------------------------
def test_import_text_success(client, compile_ready):
    response = _import_text(
        client, title="请假政策速览", text=LONG_TEXT, tags=["微信", "请假"]
    )
    assert response.status_code == 201, response.text
    data = response.json()

    assert data["duplicated"] is False
    document = data["document"]
    assert document["status"] == "ready", document
    assert document["source_type"] == "text"
    assert document["source_url"] == ""
    assert document["page_count"] > 0
    assert sorted(document["tags"]) == ["微信", "请假"]
    assert data["pages"], "文本收录应返回生成的词条"
    assert data["pages"][0]["id"] and data["pages"][0]["title"]

    # 源文件列表 / 详情带 source_type，且词条可在 Wiki 目录看到
    listing = _documents(client)
    assert len(listing) == 1
    assert listing[0]["source_type"] == "text"
    detail = client.get(f"/api/knowledge/documents/{document['id']}").json()
    assert detail["wiki_pages"]
    tree = client.get("/api/wiki/tree").json()
    assert tree["total"] >= 1


def test_import_text_auto_title(client, compile_ready):
    # 空标题：取正文首行（≤40 字）
    response = _import_text(client, text=LONG_TEXT)
    assert response.status_code == 201
    document = response.json()["document"]
    assert document["title"].startswith("关于调整第十教学周课程安排的通知")

    # 首行过长/为空时使用「未命名资料-时间」兜底
    body = "短标题行\n\n" + LONG_TEXT
    fallback = _import_text(client, text=body).json()["document"]
    assert fallback["title"]


def test_import_text_validation(client, compile_ready):
    assert _import_text(client, text="").status_code == 400
    assert _import_text(client, text="太短了").status_code == 400
    assert _import_text(client, text="x" * 200001).status_code == 400
    assert _import_text(client).status_code == 400
    # 收录失败不产生文档
    assert _documents(client) == []


# ---------------------------------------------------------------------------
# 2) URL 收录
# ---------------------------------------------------------------------------
def test_extract_article_supports_cms_vsb_content():
    """西工大 CMS 的 #vsb_content/.v_news_content 应作为正文根节点。"""
    from app.services import importer

    html = (
        "<html><body><div id='header'>首页 新闻 通知</div>"
        "<div id='vsb_content' class='v_news_content'>"
        "<p>第一条 为规范学生管理，结合学校实际，制定本办法。</p>"
        "<p>第二条 本办法适用于全体注册在籍学生，具体解释由学生工作部负责。</p>"
        "<p>第三条 学生应当遵守校纪校规，维护正常教学秩序和校园安全。</p>"
        "<p>第四条 对违反本办法的学生，学校按照有关规定给予相应处理。</p>"
        "</div><div id='footer'>版权所有</div></body></html>"
    )
    article = importer.extract_article(html, url="https://xgb.nwpu.edu.cn/info/1227/10612.htm")
    assert "第一条" in article["text"]
    assert "第三条" in article["text"]
    assert "首页 新闻 通知" not in article["text"]
    assert "版权所有" not in article["text"]


def test_import_url_extracts_wechat_style_article(client, compile_ready, article_server):
    url = f"{article_server.base_url}/article"
    response = _import_url(client, url=url, tags=["公众号", "奖助"])
    assert response.status_code == 201, response.text
    data = response.json()

    assert data["duplicated"] is False
    document = data["document"]
    assert document["status"] == "ready", document
    assert document["source_type"] == "url"
    assert document["source_url"] == url
    assert document["title"] == "国家奖学金申请指南（公众号）"  # og:title 优先
    assert document["page_count"] > 0
    assert sorted(document["tags"]) == ["公众号", "奖助"] or set(
        ["公众号", "奖助"]
    ).issubset(set(document["tags"]))
    assert data["pages"]

    # 正文提取：写入的 Markdown 源文件应包含 #js_content 里的正文，且不含 script
    from app import db

    row = db.query_one("SELECT stored_path FROM documents WHERE id = ?", (document["id"],))
    stored = open(row["stored_path"], encoding="utf-8").read()
    assert "国家奖学金用于奖励特别优秀的全日制本科在校生" in stored
    assert "不应进入正文" not in stored
    assert "首页 / 通知公告" not in stored

    listing = _documents(client)
    assert listing[0]["source_type"] == "url"
    assert listing[0]["source_url"] == url


def test_import_url_duplicate_is_not_recompiled(client, compile_ready, article_server):
    url = f"{article_server.base_url}/article"
    first = _import_url(client, url=url)
    assert first.status_code == 201
    calls_after_first = len(compile_ready.calls)
    pages_after_first = client.get("/api/wiki/stats").json()["pages"]

    second = _import_url(client, url=url)
    assert second.status_code == 200, second.text
    data = second.json()
    assert data["duplicated"] is True
    assert data["document"]["id"] == first.json()["document"]["id"]
    # 不重复编译、不重复建文档
    assert len(compile_ready.calls) == calls_after_first
    assert client.get("/api/wiki/stats").json()["pages"] == pages_after_first
    assert len(_documents(client)) == 1


def test_import_url_rejects_ssrf_and_bad_scheme(client, compile_ready, article_server, monkeypatch):
    from app import config

    # file:// 永远拒绝
    assert _import_url(client, url="file:///etc/passwd").status_code == 400
    # 默认禁止私网/回环：临时关闭 allow_private 后 127.0.0.1 应被拒绝
    monkeypatch.setattr(config, "URL_IMPORT_ALLOW_PRIVATE", False)
    private = _import_url(client, url=f"{article_server.base_url}/article")
    assert private.status_code == 400
    assert "私网" in private.json()["detail"] or "内网" in private.json()["detail"]
    # 非 http/https / 带账号密码的 URL
    assert _import_url(client, url="ftp://example.com/a").status_code == 400
    assert _import_url(client, url="http://user:pass@example.com/a").status_code == 400


def test_import_url_rejects_non_html_large_and_missing(client, compile_ready, article_server):
    not_html = _import_url(client, url=f"{article_server.base_url}/json")
    assert not_html.status_code == 400
    assert not_html.json()["detail"]

    over_limit = _import_url(client, url=f"{article_server.base_url}/over-limit")
    assert over_limit.status_code == 400
    assert "过大" in over_limit.json()["detail"]

    missing = _import_url(client, url=f"{article_server.base_url}/missing")
    assert missing.status_code == 400
    assert missing.json()["detail"]

    empty = _import_url(client, url="")
    assert empty.status_code == 400


def test_import_url_accepts_4mb_page_under_new_default_limit(
    client, compile_ready, article_server
):
    """v0.3 起 NPU_URL_IMPORT_MAX_BYTES 默认 8MB：约 4MB 可提取正文页面应成功（旧 3MB 上限会拒绝）。"""
    from app import config

    assert config.URL_IMPORT_MAX_BYTES >= 8 * 1024 * 1024, "默认上限应已提升到 8MB"

    response = _import_url(client, url=f"{article_server.base_url}/article-4mb")
    assert response.status_code == 201, response.text
    data = response.json()
    document = data["document"]
    assert document["status"] == "ready", document
    assert document["source_type"] == "url"
    assert document["source_url"].endswith("/article-4mb")
    assert document["title"] == "4MB 测试文章"
    assert document["page_count"] > 0
    assert data["pages"]
    assert len(_documents(client)) == 1


def test_import_url_over_limit_returns_actionable_message(
    client, compile_ready, article_server
):
    """约 9MB 页面应拒绝，提示包含「过大」+ 环境变量名 + 改用文本收录的建议。"""
    response = _import_url(client, url=f"{article_server.base_url}/over-limit")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "过大" in detail
    assert "NPU_URL_IMPORT_MAX_BYTES" in detail
    assert "文本收录" in detail
    # 失败不产生文档
    assert _documents(client) == []


def test_import_url_accepts_gzip_compressed_4mb_page(
    client, compile_ready, article_server
):
    """gzip 压缩的 4MB 页面：Content-Length 是压缩后大小，不应被预检误拒，解压后仍在上限内。"""
    response = _import_url(client, url=f"{article_server.base_url}/article-gzip-4mb")
    assert response.status_code == 201, response.text
    data = response.json()
    document = data["document"]
    assert document["status"] == "ready", document
    assert document["title"] == "gzip 4MB 测试文章"
    assert document["source_type"] == "url"
    assert document["page_count"] > 0
    assert len(_documents(client)) == 1


# ---------------------------------------------------------------------------
# 3) 批量收录
# ---------------------------------------------------------------------------
def test_import_batch_partial_failure(client, compile_ready, article_server):
    good = f"{article_server.base_url}/article"
    bad = f"{article_server.base_url}/json"
    response = client.post(
        "/api/knowledge/import/batch", json={"urls": [good, bad], "tags": ["待整理"]}
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["url"] for item in items] == [good, bad]

    assert items[0]["ok"] is True
    assert items[0]["duplicated"] is False
    assert items[0]["document_id"]
    assert items[0]["pages"]
    assert items[0]["error"] is None

    assert items[1]["ok"] is False
    assert items[1]["document_id"] is None
    assert items[1]["pages"] == []
    assert items[1]["error"]

    assert len(_documents(client)) == 1


def test_import_batch_validation(client, compile_ready):
    assert client.post("/api/knowledge/import/batch", json={"urls": []}).status_code == 400
    too_many = [f"https://example.com/{index}" for index in range(6)]
    assert (
        client.post("/api/knowledge/import/batch", json={"urls": too_many}).status_code == 400
    )


# ---------------------------------------------------------------------------
# 4) 管理员令牌鉴权（middleware）
# ---------------------------------------------------------------------------
def test_admin_token_protects_write_endpoints(client, compile_ready, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-secret-token")
    headers = {"X-Admin-Token": "mobile-secret-token"}

    # 无令牌：导入 / 上传 / 设置 → 401
    assert _import_text(client, text=LONG_TEXT).status_code == 401
    assert _import_url(client, url="https://example.com/a").status_code == 401
    upload = client.post(
        "/api/knowledge/documents",
        files={"file": ("通知.md", LONG_TEXT.encode("utf-8"), "text/markdown")},
    )
    assert upload.status_code == 401
    assert client.put("/api/settings", json={"counselor_name": "x"}).status_code == 401

    # 错误令牌 → 401
    wrong = client.post(
        "/api/knowledge/import/text",
        json={"text": LONG_TEXT},
        headers={"X-Admin-Token": "wrong-token"},
    )
    assert wrong.status_code == 401

    # 正确令牌 → 成功
    ok = client.post(
        "/api/knowledge/import/text", json={"text": LONG_TEXT}, headers=headers
    )
    assert ok.status_code == 201, ok.text
    settings = client.put(
        "/api/settings", json={"counselor_name": "移动端老师"}, headers=headers
    )
    assert settings.status_code == 200

    # 学生问答与只读 GET 不受令牌影响
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/wiki/tree").status_code == 200
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code == 200


def test_bot_webhook_not_blocked_by_admin_token(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "mobile-secret-token")
    # 机器人通道创建需要令牌；webhook 本身不应被管理员令牌拦截（返回业务错误而非 401）
    created = client.post(
        "/api/bots/channels",
        json={
            "type": "onebot11",
            "name": "鉴权测试",
            "config": {"self_id": "10001", "access_token": "evt-token"},
        },
        headers={"X-Admin-Token": "mobile-secret-token"},
    )
    assert created.status_code == 201
    channel_id = created.json()["id"]
    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10001,
        "user_id": 10002,
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "message_id": 7001,
    }
    # 错误 access_token 应返回 403（业务鉴权），而不是 401（管理员令牌拦截）
    response = client.post(
        f"/api/bots/onebot/{channel_id}/event",
        params={"access_token": "wrong"},
        json=event,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 5) 来源字段在列表/详情中返回
# ---------------------------------------------------------------------------
def test_documents_expose_source_fields(client, compile_ready, article_server):
    url = f"{article_server.base_url}/article"
    _import_text(client, text=LONG_TEXT)
    _import_url(client, url=url)

    listing = client.get("/api/knowledge/documents").json()["items"]
    by_type = {item["source_type"]: item for item in listing}
    assert set(by_type) == {"text", "url"}
    assert by_type["url"]["source_url"] == url
    assert by_type["text"]["source_url"] == ""

    detail = client.get(f"/api/knowledge/documents/{by_type['url']['id']}").json()
    assert detail["source_type"] == "url"
    assert detail["source_url"] == url


# ---------------------------------------------------------------------------
# 6) 并发去重（进程内锁 + documents.source_url 唯一索引兜底）
# ---------------------------------------------------------------------------
def test_import_url_concurrent_requests_do_not_create_duplicates(
    client, compile_ready, article_server
):
    from concurrent.futures import ThreadPoolExecutor

    url = f"{article_server.base_url}/article"

    def call(_index: int):
        return client.post("/api/knowledge/import/url", json={"url": url})

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(call, range(4)))

    statuses = [response.status_code for response in responses]
    assert all(status in (200, 201) for status in statuses), statuses
    assert statuses.count(201) == 1, f"并发收录只应有一次真正创建：{statuses}"

    docs = [doc for doc in _documents(client) if doc["source_url"] == url]
    assert len(docs) == 1, "并发重复 URL 不应产生重复文档"

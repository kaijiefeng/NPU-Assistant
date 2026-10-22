"""Wiki 接口测试（LLM Wiki v0.2）：seed / 目录 / 详情 / 搜索 / 编辑 / 删除 / 上传编译 / 合并 / 级联 / 重建 / 统计。

覆盖 docs/WIKI-DESIGN.md §3.1/§3.2/§4、docs/CONTRACT.md §5.3/§5.4。
编译类用例统一使用 ``compile_ready``（假 OpenAI 模型 + fake LLM），不调用真实 API。
"""
from __future__ import annotations

import re

UPLOAD_MD = """# 课程调整通知

## 调整安排

因教学楼电路检修，第十教学周周三下午的《数据库原理》课程调整至周四 10:00—11:40，
地点改为实验楼 A301 机房。请各班学习委员及时通知同学。

## 注意事项

如有疑问，可在工作时间联系学院教学办公室（示例电话 029-88XXXXXX）。
本次调整仅涉及个别课程，其他课程按原课表执行。
"""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _find_page(client, title: str) -> dict:
    items = client.get("/api/wiki/pages", params={"q": title}).json()["items"]
    for item in items:
        if item["title"] == title:
            return item
    raise AssertionError(f"未找到词条：{title}（items={[i['title'] for i in items]}）")


def _upload(client, filename: str = "课程调整通知.md", content: str = UPLOAD_MD,
            title: str | None = None, tags: str | None = None):
    data = {}
    if title is not None:
        data["title"] = title
    if tags is not None:
        data["tags"] = tags
    return client.post(
        "/api/knowledge/documents",
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        data=data or None,
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


# ---------------------------------------------------------------------------
# 1) 预编译快照导入（幂等）
# ---------------------------------------------------------------------------
def test_seed_import_and_idempotency(client, seeded_wiki):
    assert seeded_wiki["documents"] == 1
    assert seeded_wiki["pages"] == 3
    assert seeded_wiki["sources"] == 3

    stats = client.get("/api/wiki/stats").json()
    assert stats["pages"] == 3
    assert stats["sources"] == 3
    assert stats["documents"] == 1
    # by_tag 只统计带标签的词条（needs_review 的「待审核词条」无标签）
    assert stats["by_tag"] == {"奖助": 1, "请假考勤": 1}

    # 第二次导入：全部跳过，不产生重复
    second = client.post("/api/knowledge/seed")
    assert second.status_code == 200
    data = second.json()
    assert data["imported"] is False
    assert data["pages"] == 0
    assert data["skipped"] >= 8
    assert client.get("/api/wiki/stats").json()["pages"] == 3


# ---------------------------------------------------------------------------
# 2) 目录 / 列表 / 详情
# ---------------------------------------------------------------------------
def test_wiki_tree_groups_and_list_filters(client, seeded_wiki):
    tree = client.get("/api/wiki/tree").json()
    assert tree["total"] == 3
    tags = {group["tag"] for group in tree["groups"]}
    assert {"奖助", "请假考勤", "其他"} <= tags
    for group in tree["groups"]:
        assert group["count"] == len(group["pages"])
        for page in group["pages"]:
            for key in ("id", "title", "summary", "tags", "source_count", "updated_at"):
                assert key in page

    by_query = client.get("/api/wiki/pages", params={"q": "奖学金"}).json()
    assert by_query["total"] == 1
    assert by_query["items"][0]["title"] == "国家奖学金申请"

    by_tag = client.get("/api/wiki/pages", params={"tag": "请假考勤"}).json()
    assert by_tag["total"] == 1
    assert by_tag["items"][0]["title"] == "请假办理流程"

    paged = client.get("/api/wiki/pages", params={"limit": 1, "offset": 0}).json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1


def test_wiki_page_detail_body_html_sources_and_links(client, seeded_wiki):
    page = _find_page(client, "国家奖学金申请")
    detail = client.get(f"/api/wiki/pages/{page['id']}").json()

    assert detail["id"] == page["id"]
    assert detail["status"] == "ready"
    assert "<h2>" in detail["body_html"]
    assert detail["aliases"] == ["国奖", "国家奖学金"]
    assert detail["sources"], "词条详情必须包含来源锚点"
    source = detail["sources"][0]
    assert source["document_id"]
    assert source["document_title"]
    assert source["source_file"].endswith(".md")
    assert source["section"]
    assert source["quote"]
    assert detail["links"]
    assert detail["links"][0]["title"] == "请假办理流程"
    assert detail["links"][0]["relation"] == "related"

    assert client.get("/api/wiki/pages/not-exist").status_code == 404


# ---------------------------------------------------------------------------
# 3) 导航搜索（exact / keyword / none，needs_review 不参与）
# ---------------------------------------------------------------------------
def test_wiki_search_exact_and_keyword(client, seeded_wiki):
    exact = client.post(
        "/api/wiki/search", json={"query": "国家奖学金申请需要哪些材料"}
    ).json()
    assert exact["method"] in {"exact", "keyword"}
    assert exact["selected"], "应命中「国家奖学金申请」"
    assert exact["selected"][0]["title"] == "国家奖学金申请"
    assert exact["selected"][0]["score"] > 0
    assert exact["candidates"]
    assert exact["latency_ms"] >= 0

    keyword = client.post(
        "/api/wiki/search", json={"query": "奖学金 材料", "limit": 3}
    ).json()
    assert keyword["method"] in {"exact", "keyword"}
    assert any(item["title"] == "国家奖学金申请" for item in keyword["selected"])


def test_wiki_search_none_hit_and_needs_review_excluded(client, seeded_wiki):
    no_hit = client.post("/api/wiki/search", json={"query": "xyzzy quux frobnicate"}).json()
    assert no_hit["method"] == "none"
    assert no_hit["selected"] == []
    assert no_hit["candidates"] == []

    # needs_review 词条只展示、不参与问答导航
    pending = client.post("/api/wiki/search", json={"query": "待审核词条"}).json()
    assert pending["method"] == "none"
    assert pending["selected"] == []


def test_wiki_search_validation(client):
    assert client.post("/api/wiki/search", json={"query": "   "}).status_code == 400
    assert client.post("/api/wiki/search", json={"query": "x" * 2001}).status_code == 400
    assert client.post("/api/wiki/search", json={"query": "奖学金", "limit": 0}).status_code == 400
    assert (
        client.post("/api/wiki/search", json={"query": "奖学金", "limit": "abc"}).status_code
        == 400
    )


# ---------------------------------------------------------------------------
# 4) 编辑 / 删除
# ---------------------------------------------------------------------------
def test_wiki_page_edit_increments_version(client, seeded_wiki):
    page = _find_page(client, "国家奖学金申请")
    before = client.get(f"/api/wiki/pages/{page['id']}").json()
    response = client.put(
        f"/api/wiki/pages/{page['id']}",
        json={
            "summary": "人工编辑后的摘要。",
            "body_md": "## 人工编辑\n\n- 更新后的正文",
            "aliases": ["国奖", "国家奖学金", "奖学金"],
            "tags": ["奖助", "奖学金"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == before["version"] + 1
    assert data["status"] == "ready"
    assert data["summary"] == "人工编辑后的摘要。"
    assert "人工编辑" in data["body_html"]
    assert "奖学金" in data["aliases"]
    assert set(data["tags"]) == {"奖助", "奖学金"}

    # 标题冲突 / 未知字段 / 空更新 / 空标题
    leave = _find_page(client, "请假办理流程")
    conflict = client.put(f"/api/wiki/pages/{page['id']}", json={"title": leave["title"]})
    assert conflict.status_code == 400
    assert "已存在" in conflict.json()["detail"]
    assert (
        client.put(f"/api/wiki/pages/{page['id']}", json={"unknown": 1}).status_code == 400
    )
    assert client.put(f"/api/wiki/pages/{page['id']}", json={}).status_code == 400
    assert client.put(f"/api/wiki/pages/{page['id']}", json={"title": " "}).status_code == 400


def test_wiki_page_edit_needs_review_becomes_ready(client, seeded_wiki):
    items = client.get("/api/wiki/pages", params={"q": "待审核"}).json()["items"]
    pending = next(item for item in items if item["status"] == "needs_review")
    before = client.get(f"/api/wiki/pages/{pending['id']}").json()
    updated = client.put(
        f"/api/wiki/pages/{pending['id']}", json={"summary": "审核通过。", "tags": ["其他"]}
    ).json()
    assert updated["status"] == "ready"
    assert updated["version"] == before["version"] + 1


def test_wiki_page_delete(client, seeded_wiki):
    page = _find_page(client, "国家奖学金申请")
    assert client.delete(f"/api/wiki/pages/{page['id']}").json() == {"ok": True}
    assert client.get(f"/api/wiki/pages/{page['id']}").status_code == 404
    assert client.delete(f"/api/wiki/pages/{page['id']}").status_code == 404
    stats = client.get("/api/wiki/stats").json()
    assert stats["pages"] == 2
    assert stats["sources"] == 2


# ---------------------------------------------------------------------------
# 5) 上传 → 编译（fake LLM）
# ---------------------------------------------------------------------------
def test_upload_markdown_compiles_pages_and_sources(client, compile_ready):
    response = _upload(client, tags="教务,测试")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["status"] == "ready", doc
    assert doc["page_count"] > 0
    assert doc["char_count"] > 0
    assert sorted(doc["tags"]) == ["教务", "测试"]
    assert doc["error"] is None

    detail = client.get(f"/api/knowledge/documents/{doc['id']}").json()
    assert len(detail["wiki_pages"]) == doc["page_count"]
    page_id = detail["wiki_pages"][0]["id"]

    page = client.get(f"/api/wiki/pages/{page_id}").json()
    assert page["status"] == "ready"
    assert page["sources"], "编译结果必须带来源锚点"
    source = page["sources"][0]
    assert source["document_id"] == doc["id"]
    assert source["section"]
    assert source["quote"]
    # 来源摘录必须能在原文中逐字找到（去空白比对）
    assert _normalize(source["quote"]) in _normalize(UPLOAD_MD)

    # 导航能命中新词条
    search = client.post("/api/wiki/search", json={"query": "课程调整通知"}).json()
    assert search["selected"]
    assert search["selected"][0]["title"] == page["title"]


def test_duplicate_upload_merges_without_duplicate_pages(client, compile_ready):
    first = _upload(client).json()
    second = _upload(client).json()
    assert first["status"] == "ready" and second["status"] == "ready"

    stats = client.get("/api/wiki/stats").json()
    assert stats["pages"] == 1, "相同标题应合并为一个词条，而不是重复创建"
    assert stats["sources"] == 2, "两个源文件都应保留来源锚点"

    page = _find_page(client, "课程调整通知")
    detail = client.get(f"/api/wiki/pages/{page['id']}").json()
    assert detail["source_count"] == 2
    assert len(detail["sources"]) == 2


def test_delete_document_removes_exclusive_pages_only(client, compile_ready):
    first = _upload(client).json()
    second = _upload(client).json()
    page = _find_page(client, "课程调整通知")

    # 删除第一篇：共享词条保留，只移除该来源
    removed = client.delete(f"/api/knowledge/documents/{first['id']}").json()
    assert removed == {"ok": True, "pages_removed": 0}
    detail = client.get(f"/api/wiki/pages/{page['id']}").json()
    assert detail["source_count"] == 1
    assert len(detail["sources"]) == 1

    # 删除第二篇（最后一个来源）：独占词条被清理
    removed = client.delete(f"/api/knowledge/documents/{second['id']}").json()
    assert removed["ok"] is True
    assert removed["pages_removed"] == 1
    assert client.get(f"/api/wiki/pages/{page['id']}").status_code == 404
    assert client.get("/api/wiki/stats").json()["pages"] == 0


def test_reindex_does_not_duplicate_pages(client, compile_ready):
    first = _upload(client).json()
    _upload(client)
    before = client.get("/api/wiki/stats").json()

    response = client.post(f"/api/knowledge/documents/{first['id']}/reindex")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["document"]["status"] == "ready"
    assert data["document"]["page_count"] > 0
    assert data["build"] is not None
    assert data["build"]["status"] in {"ready", "failed"}

    after = client.get("/api/wiki/stats").json()
    assert after["pages"] == before["pages"]
    assert after["sources"] == before["sources"]


def test_upload_with_mock_model_marks_failed_but_not_500(client):
    response = _upload(client)
    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "failed"
    assert doc["error"]
    assert "mock" in doc["error"] or "模型" in doc["error"]
    assert client.get("/api/wiki/stats").json()["pages"] == 0


def test_upload_with_broken_llm_degrades_to_failed(client, real_model, broken_llm):
    response = _upload(client)
    assert response.status_code == 201, "LLM 故障时上传也必须返回 201 + failed，而不是 500"
    doc = response.json()
    assert doc["status"] == "failed"
    assert doc["error"]
    # 失败单元按设计降级为 needs_review 兜底词条，只展示、不参与问答
    items = client.get("/api/wiki/pages").json()["items"]
    assert items
    assert all(item["status"] == "needs_review" for item in items)


def test_upload_validation_and_removed_rag_endpoints(client, compile_ready):
    assert _upload(client, filename="脚本.py", content="print(1)").status_code == 400
    assert _upload(client, filename="空文件.md", content="").status_code == 400
    assert client.get("/api/knowledge/documents/not-exist").status_code == 404
    assert client.delete("/api/knowledge/documents/not-exist").status_code == 404

    # RAG 遗留接口必须已经移除
    doc = _upload(client).json()
    chunks = client.get(f"/api/knowledge/documents/{doc['id']}/chunks")
    assert chunks.status_code in {404, 405}
    search = client.post("/api/knowledge/search", json={"query": "奖学金"})
    assert search.status_code in {404, 405}


# ---------------------------------------------------------------------------
# 6) 重建 / 构建记录
# ---------------------------------------------------------------------------
def test_rebuild_single_and_all_with_builds(client, compile_ready):
    doc = _upload(client).json()

    single = client.post("/api/wiki/rebuild", json={"document_id": doc["id"]})
    assert single.status_code == 200, single.text
    payload = single.json()
    assert payload["status"] == "ready"
    assert payload["document"]["page_count"] > 0
    assert payload["build"]["status"] == "ready"
    single_build_id = payload["build"]["id"]

    all_response = client.post("/api/wiki/rebuild", json={})
    assert all_response.status_code == 202
    all_payload = all_response.json()
    assert all_payload["status"] == "running"
    build_id = all_payload["build_id"]

    build = client.get(f"/api/wiki/builds/{build_id}").json()
    assert build["status"] in {"ready", "failed"}

    builds = client.get("/api/wiki/builds", params={"limit": 10}).json()
    assert builds["total"] >= 2
    assert any(item["id"] == single_build_id for item in builds["items"])

    assert client.get("/api/wiki/builds/not-exist").status_code == 404
    missing_doc = client.post("/api/wiki/rebuild", json={"document_id": "not-exist"})
    assert missing_doc.status_code == 404


# ---------------------------------------------------------------------------
# 7) 兼容接口
# ---------------------------------------------------------------------------
def test_knowledge_list_detail_and_stats(client, compile_ready):
    doc = _upload(client).json()
    listing = client.get("/api/knowledge/documents").json()
    assert listing["total"] == 1
    assert listing["items"][0]["page_count"] == doc["page_count"]

    stats = client.get("/api/knowledge/stats").json()
    assert stats == {
        "documents": 1,
        "ready_documents": 1,
        "pages": doc["page_count"],
        "sources": doc["page_count"],
    }

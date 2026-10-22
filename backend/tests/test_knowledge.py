"""知识库接口测试：seed 幂等、上传 / 解析 / 切片、检索、详情、删除、校验（契约 §5.3）。"""
from __future__ import annotations

MARKDOWN_DOC = """# 测试课程调整通知

## 一、调整背景

因教学楼电路检修，第十教学周周三下午的课程需要临时调整。本通知用于自动化测试，
请以学校教务处发布的正式通知为准。

## 二、调整安排

1. 原定于周三 14:00—15:40 的《数据库原理》课程调整至周四 10:00—11:40，地点改为
   实验楼 A301 机房。
2. 原定于周三 16:00—17:40 的《计算机网络》课程暂停一次，补课时间由任课教师另行通知。
3. 电路检修期间，教学楼三层自习室暂停开放，学生可前往图书馆自习。

## 三、注意事项

请各班学习委员及时通知到每位同学，并关注班级群内的后续通知。如有疑问，可在工作时间
联系学院教学办公室（示例电话 029-88XXXXXX）。本次调整仅涉及个别课程，其他课程按原
课表执行。
"""

TXT_DOC = """蓝鲸观测实验预约通知

蓝鲸观测实验是天文协会本学期的特色活动。活动时间示例为每周六 19:00 至 21:00，
地点在物理实验楼五层天文台。参与同学需提前在社团系统预约，每次限额 30 人。
如遇阴雨天气活动自动取消，请关注社团群通知。联系人示例为张同学。
"""


def _upload(client, filename: str, content: str, *, title: str | None = None,
            tags: str | None = None):
    data = {}
    if title is not None:
        data["title"] = title
    if tags is not None:
        data["tags"] = tags
    return client.post(
        "/api/knowledge/documents",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        data=data or None,
    )


def test_seed_documents_is_idempotent(client):
    first = client.post("/api/knowledge/seed")
    assert first.status_code == 200
    data = first.json()

    assert len(data["ingested"]) >= 6
    total = data["total_documents"]
    assert total >= 6

    # 幂等：重复导入相同文件全部跳过，总数不变
    second = client.post("/api/knowledge/seed").json()
    assert second["ingested"] == []
    assert len(second["skipped"]) >= 6
    assert second["total_documents"] == total

    stats = client.get("/api/knowledge/stats").json()
    assert stats["documents"] == total
    assert stats["chunks"] > 0
    assert stats["chars"] > 0
    assert stats["by_file_type"].get(".md", 0) >= 6


def test_document_list_and_detail_after_seed(client, seeded):
    listing = client.get("/api/knowledge/documents").json()
    assert listing["total"] >= 6
    assert len(listing["items"]) == listing["total"]

    doc = listing["items"][0]
    assert doc["status"] == "ready"
    assert doc["chunk_count"] > 0
    assert isinstance(doc["tags"], list)

    detail = client.get(f"/api/knowledge/documents/{doc['id']}").json()
    assert detail["id"] == doc["id"]
    assert detail["chunks_preview"]
    assert len(detail["chunks_preview"]) <= 5
    for chunk in detail["chunks_preview"]:
        assert chunk["content"].strip()
        assert len(chunk["content"]) <= 301


def test_upload_markdown_chunks_detail_reindex_delete(client):
    response = _upload(
        client, "测试课程调整通知.md", MARKDOWN_DOC,
        title="测试课程调整通知", tags="教务,测试",
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] > 0
    assert doc["char_count"] > 0
    assert sorted(doc["tags"]) == ["教务", "测试"]
    assert doc["title"] == "测试课程调整通知"
    assert doc["file_ext"] == ".md"

    doc_id = doc["id"]
    detail = client.get(f"/api/knowledge/documents/{doc_id}").json()
    assert detail["chunk_count"] == doc["chunk_count"]
    assert len(detail["chunks_preview"]) == min(5, doc["chunk_count"])

    chunks = client.get(f"/api/knowledge/documents/{doc_id}/chunks").json()
    assert chunks["total"] == doc["chunk_count"]
    assert len(chunks["items"]) == chunks["total"]
    assert [c["seq"] for c in chunks["items"]] == sorted(c["seq"] for c in chunks["items"])
    # 禁止空切片（契约 §5.3）
    assert all(c["content"].strip() for c in chunks["items"])

    reindexed = client.post(f"/api/knowledge/documents/{doc_id}/reindex")
    assert reindexed.status_code == 200
    assert reindexed.json()["status"] == "ready"
    assert reindexed.json()["chunk_count"] > 0

    deleted = client.delete(f"/api/knowledge/documents/{doc_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/knowledge/documents/{doc_id}").status_code == 404
    assert client.post(f"/api/knowledge/documents/{doc_id}/reindex").status_code == 404


def test_upload_txt_then_search_hits_document(client):
    response = _upload(client, "蓝鲸观测实验预约.txt", TXT_DOC)
    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] > 0

    search = client.post(
        "/api/knowledge/search", json={"query": "蓝鲸观测实验怎么预约", "top_k": 5}
    )
    assert search.status_code == 200
    payload = search.json()
    assert payload["query"] == "蓝鲸观测实验怎么预约"
    assert isinstance(payload["latency_ms"], int)
    assert payload["items"], "应命中刚上传的文档"

    hit = payload["items"][0]
    assert hit["document_id"] == doc["id"]
    assert hit["document_title"]
    assert hit["snippet"].strip()
    assert hit["content"].strip()
    assert hit["score"] > 0

    # 检索接口不应改变文档数量
    assert client.get("/api/knowledge/documents").json()["total"] == 1


def test_search_on_empty_library_returns_empty_list(client):
    payload = client.post(
        "/api/knowledge/search", json={"query": "国家奖学金怎么申请", "top_k": 4}
    ).json()
    assert payload["items"] == []


def test_search_validation(client):
    assert client.post("/api/knowledge/search", json={"query": "   "}).status_code == 400
    assert client.post("/api/knowledge/search", json={"query": "x" * 2001}).status_code == 400
    assert (
        client.post("/api/knowledge/search", json={"query": "奖学金", "top_k": 0}).status_code
        == 400
    )
    assert (
        client.post("/api/knowledge/search", json={"query": "奖学金", "top_k": "abc"}).status_code
        == 400
    )


def test_upload_rejects_unsupported_extension_and_empty_file(client):
    unsupported = _upload(client, "脚本.py", "print('hello')")
    assert unsupported.status_code == 400
    assert "不支持" in unsupported.json()["detail"]

    empty = _upload(client, "空文件.md", "")
    assert empty.status_code == 400
    assert "empty" in empty.text.lower() or "空" in empty.json()["detail"]


def test_missing_document_and_unknown_chunk_operations(client):
    assert client.get("/api/knowledge/documents/does-not-exist").status_code == 404
    assert client.delete("/api/knowledge/documents/does-not-exist").status_code == 404
    assert (
        client.get("/api/knowledge/documents/does-not-exist/chunks").status_code == 404
    )

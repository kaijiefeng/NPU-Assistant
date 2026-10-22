"""LLM Wiki 端到端验收脚本（需要服务已启动，且已配置真实 LLM）。

覆盖：健康检查 → 预编译 Wiki 导入 → 目录/详情/导航搜索 → 真实问答双层引用 →
上传源文件并编译出词条 → 编辑词条 → 单篇重建 → 删除源文件级联清理 → 机器人 console 链路。

用法：.\.venv\Scripts\python.exe scripts\e2e_wiki_check.py
可选：-BaseUrl http://127.0.0.1:8000
"""
from __future__ import annotations

import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
client = httpx.Client(base_url=BASE, timeout=180)
ok = 0
fail = 0


def step(name: str, fn) -> None:
    global ok, fail
    try:
        fn()
        print(f"[PASS] {name}")
        ok += 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {name} -> {exc!r}")
        fail += 1


def main() -> None:
    state: dict = {}

    def health() -> None:
        data = client.get("/api/health").json()
        assert data["status"] == "ok", data
        assert data["knowledge_mode"] == "llm-wiki", data
        assert data.get("wiki_pages", 0) > 0, "Wiki 词条为空"
        print("      model =", (data.get("default_model") or {}).get("display_name"))

    def seed() -> None:
        data = client.post("/api/knowledge/seed", json={}).json()
        stats = client.get("/api/wiki/stats").json()
        assert stats.get("pages", 0) > 0, {"seed": data, "stats": stats}
        print(
            f"      seed -> imported={data.get('imported')} skipped={data.get('skipped')} "
            f"当前 documents={stats.get('documents')} pages={stats.get('pages')} sources={stats.get('sources')}"
        )

    def tree() -> None:
        data = client.get("/api/wiki/tree").json()
        assert data["total"] > 0, data
        state["tree"] = data
        tags = [g["tag"] for g in data["groups"]]
        print(f"      tree -> {data['total']} 词条 / 标签 {tags}")

    def search() -> None:
        data = client.post("/api/wiki/search", json={"query": "国家奖学金怎么申请", "limit": 3}).json()
        assert data["method"] in {"exact", "keyword", "llm"}, data
        assert data["selected"], data
        state["selected"] = data["selected"]
        print("      search -> method=%s selected=%s" % (data["method"], [p["title"] for p in data["selected"]][:2]))

    def page_detail() -> None:
        page_id = state["selected"][0]["page_id"]
        data = client.get(f"/api/wiki/pages/{page_id}").json()
        assert data["body_html"].strip().startswith("<"), data
        assert data["sources"], "词条缺少来源锚点"
        assert data["sources"][0]["section"], data["sources"][0]
        state["page"] = data
        print(f"      详情 -> 《{data['title']}》来源 {len(data['sources'])} 条")

    def chat() -> None:
        data = client.post(
            "/api/chat",
            json={"message": "国家奖学金怎么申请？需要哪些材料？", "session_key": "web:e2e-wiki"},
        ).json()
        assert data["reply"], data
        assert data["citations"], "无引用"
        citation = data["citations"][0]
        assert citation.get("page_id") and citation.get("sources"), citation
        assert citation["sources"][0].get("section"), citation
        state["chat"] = data
        print(
            f"      chat -> fallback={data['fallback']} model={data['model']} "
            f"nav={data['navigation']['method']} citations={len(data['citations'])}"
        )
        print(f"      reply：{data['reply'][:120].replace(chr(10), ' ')}...")

    def upload_compile() -> None:
        stamp = uuid.uuid4().hex[:6]
        content = (
            f"# E2E 测试办事指南 {stamp}\n\n"
            "## 办理时间\n\n每周二、周四 14:00-17:00，地点为长安校区学生事务大厅 3 号窗口。\n\n"
            "## 所需材料\n\n- 学生证原件及复印件\n- 申请表（系统导出）\n\n"
            "## 办理流程\n\n1. 线上预约；2. 携带材料到窗口办理；3. 三个工作日内领取结果。\n"
            f"## 咨询方式\n\n示例电话 029-88XXXXXX（E2E 标记 {stamp}）。\n"
        )
        files = {"file": (f"E2E办事指南{stamp}.md", content.encode("utf-8"), "text/markdown")}
        data = client.post("/api/knowledge/documents", files=files, data={"title": f"E2E办事指南{stamp}"}).json()
        assert data.get("status") == "ready", data
        assert data.get("page_count", 0) > 0, data
        state["doc"] = data
        print(f"      编译 -> doc={data['filename']} pages={data['page_count']} status={data['status']}")

    def search_uploaded() -> None:
        stamp = state["doc"]["title"].replace("E2E办事指南", "")
        data = client.post("/api/wiki/search", json={"query": "E2E测试办事指南 办理时间", "limit": 3}).json()
        titles = [p["title"] for p in (data.get("selected") or [])]
        assert data.get("method") != "none", data
        print(f"      上传后搜索 -> {titles}")

    def edit_page() -> None:
        page = state["page"]
        mark = f"\n\n<!-- e2e {uuid.uuid4().hex[:6]} -->"
        updated = client.put(
            f"/api/wiki/pages/{page['id']}",
            json={"body_md": page["body_md"] + mark},
        ).json()
        assert updated["version"] >= page["version"] + 1, updated
        # 还原
        client.put(f"/api/wiki/pages/{page['id']}", json={"body_md": page["body_md"]})
        print(f"      编辑 -> version {page['version']} -> {updated['version']}")

    def rebuild_document() -> None:
        doc = state["doc"]
        data = client.post(f"/api/knowledge/documents/{doc['id']}/reindex", json={}).json()
        document = data.get("document") or data
        assert document.get("status") == "ready", data
        print(f"      重建 -> pages={document.get('page_count')}")

    def delete_document() -> None:
        doc = state["doc"]
        data = client.delete(f"/api/knowledge/documents/{doc['id']}").json()
        assert data.get("ok"), data
        print(f"      删除 -> pages_removed={data.get('pages_removed')}")
        remaining = client.post("/api/wiki/search", json={"query": "E2E办事指南", "limit": 3}).json()
        assert not remaining.get("selected"), "删除后仍能搜到词条"

    def bot_console() -> None:
        channels = client.get("/api/bots/channels").json()["items"]
        console = next((c for c in channels if c["type"] == "console"), None)
        assert console, "缺少 console 通道"
        data = client.post(
            f"/api/bots/channels/{console['id']}/test",
            json={"message": "请假流程是什么？", "user_name": "E2E 同学"},
        ).json()
        assert data.get("reply"), data
        print(f"      console -> citations={len(data.get('citations') or [])} outbound={bool(data.get('outbound'))}")

    step("健康检查 /api/health", health)
    step("导入预编译 Wiki /api/knowledge/seed", seed)
    step("Wiki 目录 /api/wiki/tree", tree)
    step("Wiki 导航 /api/wiki/search", search)
    step("词条详情（正文 + 来源锚点）", page_detail)
    step("真实问答（双层引用）", chat)
    step("上传文件并编译词条", upload_compile)
    step("上传后 Wiki 检索", search_uploaded)
    step("编辑词条（版本递增）", edit_page)
    step("单篇重建", rebuild_document)
    step("删除源文件级联清理", delete_document)
    step("机器人 console 全链路", bot_console)

    print(f"\n结果: {ok} passed, {fail} failed")
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()

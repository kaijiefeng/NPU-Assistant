"""校园栏目同步：列表解析、去重、限条数与 dry-run（无网络）。"""
from __future__ import annotations

from app.services import campus_sync

SAMPLE_HTML = """
<html><body>
<ul>
  <li><em>2026年10月20日</em><a href="../info/1003/1001.htm" title="关于奖学金评审的通知">奖学金通知</a></li>
  <li><em>2026年10月18日</em><a href="../info/1003/1002.htm">请假管理通知</a></li>
  <li><em>2026年10月17日</em><a href="../info/1003/1003.htm" title="学生月饼采购公告">采购公告</a></li>
  <li><a href="https://example.com/info/1/2.htm" title="外站链接">外站</a></li>
  <li><em>2026年10月16日</em><a href="../info/1003/1001.htm" title="重复链接">重复</a></li>
</ul>
</body></html>
"""


def test_parse_list_page_filters_and_dedupes():
    items = campus_sync.parse_list_page(
        SAMPLE_HTML,
        "https://xgb.nwpu.edu.cn/xwzx/tzgg.htm",
        exclude_keywords=["采购"],
    )
    assert [item.title for item in items] == ["关于奖学金评审的通知", "请假管理通知"]
    assert items[0].url == "https://xgb.nwpu.edu.cn/info/1003/1001.htm"
    assert items[0].date == "2026-10-20"


def test_parse_list_page_extracts_dot_date():
    html = '<li><div class="rq">08.26<br>2026</div><a href="../info/1070/13568.htm" title="学生公告">学生公告</a></li>'
    items = campus_sync.parse_list_page(html, "https://xgb.nwpu.edu.cn/xwzx/tzgg.htm")
    assert items[0].date == "2026-08-26"


def test_run_source_dry_run_respects_limit(monkeypatch):
    source = campus_sync.CampusSource(
        id="test",
        name="测试源",
        list_url="https://xgb.nwpu.edu.cn/xwzx/tzgg.htm",
        tags=["真实数据", "测试"],
        max_items=1,
        exclude_keywords=["采购"],
    )
    monkeypatch.setattr(campus_sync, "fetch_list_page", lambda *a, **k: SAMPLE_HTML)
    monkeypatch.setattr(campus_sync, "existing_source_urls", lambda: set())
    monkeypatch.setattr(campus_sync.time, "sleep", lambda *_: None)
    result = campus_sync.run_source(
        source,
        user_agent="test",
        delay_seconds=0,
        dry_run=True,
        robots=type("R", (), {"allowed": lambda self, url: True})(),
    )
    assert result["candidates"] == 2
    assert result["new"] == 1
    assert result["imported"][0]["dry_run"] is True
    assert len(result["skipped"]) == 1


def test_run_source_imports_new_urls(monkeypatch):
    source = campus_sync.CampusSource(
        id="test",
        name="测试源",
        list_url="https://xgb.nwpu.edu.cn/xwzx/tzgg.htm",
        tags=["真实数据", "奖助学金"],
    )
    calls = []
    monkeypatch.setattr(campus_sync, "fetch_list_page", lambda *a, **k: SAMPLE_HTML)
    monkeypatch.setattr(campus_sync, "existing_source_urls", lambda: {"https://xgb.nwpu.edu.cn/info/1003/1002.htm"})
    monkeypatch.setattr(campus_sync.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        campus_sync.importer,
        "import_url",
        lambda url, title=None, tags=None: calls.append((url, title, tags)) or {
            "document": {"id": "doc-1", "title": title, "status": "ready"},
            "duplicated": False,
        },
    )
    result = campus_sync.run_source(
        source,
        user_agent="test",
        delay_seconds=0,
        dry_run=False,
        robots=type("R", (), {"allowed": lambda self, url: True})(),
    )
    assert result["new"] == 2
    assert calls[0][0] == "https://xgb.nwpu.edu.cn/info/1003/1001.htm"
    assert calls[0][2] == ["真实数据", "奖助学金"]
    assert len(result["imported"]) == 2


def test_source_registry_has_public_sources():
    sources = campus_sync.load_sources()
    assert len(sources) >= 6
    assert any(source.id == "xgb-zdgk" for source in sources)
    assert all(source.list_url.startswith("https://") for source in sources)

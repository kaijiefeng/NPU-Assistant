"""真实校园文件解析回归：使用公开的西工大 .doc/.docx/.pdf 样本。

样本仅用于解析器测试，来源与许可说明见同目录 README.md。
"""
from __future__ import annotations

from pathlib import Path

from app.services import parsing

FIXTURES = Path(__file__).parent / "fixtures" / "campus"


def test_parse_real_legacy_doc():
    result = parsing.parse_path(FIXTURES / "西工大-在校学习基本情况表.doc")
    assert result.file_ext == ".doc"
    assert "西北工业大学" in result.text
    assert "在校学习基本情况表" in result.text
    assert len(result.text) > 200


def test_parse_real_docx():
    result = parsing.parse_path(FIXTURES / "西工大-知识产权声明页.docx")
    assert result.file_ext == ".docx"
    assert "知识产权" in result.text
    assert "西北工业大学" in result.text


def test_parse_real_pdf():
    result = parsing.parse_path(FIXTURES / "西工大-学位论文评阅意见处理办法.pdf")
    assert result.file_ext == ".pdf"
    assert "学位论文" in result.text
    assert "西北工业大学" in result.text


def test_legacy_doc_is_in_supported_exts():
    from app import config

    assert ".doc" in config.SUPPORTED_EXTS


def test_upload_real_legacy_doc_compiles(client, compile_ready):
    path = FIXTURES / "西工大-在校学习基本情况表.doc"
    response = client.post(
        "/api/knowledge/documents",
        files={"file": (path.name, path.read_bytes(), "application/msword")},
        data={"title": "在校学习基本情况表（真实样本）", "tags": "真实数据,doc"},
    )
    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "ready", doc
    assert doc["file_ext"] == ".doc"
    assert doc["page_count"] > 0
    assert "在校学习基本情况表" in doc["title"]


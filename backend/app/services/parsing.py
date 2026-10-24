"""文档解析：把知识库支持的文件格式转换成纯文本与章节结构。

设计目标
--------
1. 每种格式一个独立解析函数，便于单元测试直接调用；
2. 统一返回 :class:`ParsedDocument`：``text`` 为纯文本，``blocks`` 为段落级
   结构（含 ``section`` 最近标题），``sections`` 为标题列表；
3. 第三方解析库（pypdf / python-docx / openpyxl / bs4）全部惰性导入，缺依赖时
   抛出带中文说明的 :class:`ParseError`，而不是让整个应用 import 失败；
4. 任何解析失败都抛 :class:`ParseError`（继承 ``ValueError``），由入库逻辑
   记录到 ``documents.error`` 并置 ``status='failed'``。

对外接口（见 docs/CONTRACT.md §5.3）
-----------------------------------
    parse_document(filename, data=None, *, path=None) -> ParsedDocument
    parse_bytes(filename, data) -> ParsedDocument
    parse_path(path) -> ParsedDocument
    parse_markdown / parse_txt / parse_pdf / parse_docx / parse_doc / parse_xlsx /
    parse_csv / parse_json / parse_html
"""
from __future__ import annotations

import codecs
import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("npu-assistant")

# 文本类扩展名（Markdown 的 .markdown 也算）
MARKDOWN_EXTS = {".md", ".markdown"}
TEXT_EXTS = {".txt"}

# 解析结果的文本长度上限保护（防止异常大文件把内存打满）
MAX_PDF_PAGES = 800
MAX_XLSX_ROWS_PER_SHEET = 3000
MAX_XLSX_COLS = 40


class ParseError(ValueError):
    """解析失败（中文可读信息，可直接展示给用户）。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class TextBlock:
    """段落级结构：一段正文或一个标题。"""

    text: str
    section: str | None = None
    is_heading: bool = False
    level: int = 0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "section": self.section,
            "is_heading": self.is_heading,
            "level": self.level,
        }


@dataclass
class ParsedDocument:
    """解析结果：纯文本 + 段落/章节结构。"""

    text: str
    blocks: list[TextBlock] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    title: str | None = None
    file_ext: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def plain_text(self) -> str:
        """``text`` 的别名，方便调用方语义化读取。"""
        return self.text

    @property
    def paragraphs(self) -> list[str]:
        return [block.text for block in self.blocks]

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "blocks": [block.to_dict() for block in self.blocks],
            "sections": list(self.sections),
            "title": self.title,
            "file_ext": self.file_ext,
            "metadata": dict(self.metadata),
        }

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.text

    def __len__(self) -> int:
        return len(self.text)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def decode_bytes(data: bytes) -> str:
    """尽力把字节解码成文本：UTF-8(BOM) → GB18030 → 兜底替换。"""
    if not data:
        return ""
    if data.startswith(codecs.BOM_UTF8):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:  # pragma: no cover - 极端情况
            pass
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean_text(text: str) -> str:
    """统一换行、去行尾空白、压缩多余空行。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _Builder:
    """顺序收集标题与正文块，并维护"最近标题"（section）。"""

    def __init__(self) -> None:
        self.blocks: list[TextBlock] = []
        self.sections: list[dict] = []
        self.current_section: str | None = None

    def heading(self, text: str, level: int = 1) -> None:
        text = _clean_text(text)
        if not text:
            return
        self.current_section = text
        self.blocks.append(TextBlock(text=text, section=text, is_heading=True, level=max(1, level)))
        self.sections.append({"title": text, "level": max(1, level)})

    def body(self, text: str, section: str | None = None) -> None:
        text = _clean_text(text)
        if not text:
            return
        self.blocks.append(
            TextBlock(text=text, section=self.current_section if section is None else section)
        )

    def build(
        self,
        *,
        title: str | None = None,
        file_ext: str = "",
        metadata: dict | None = None,
        raw_text: str | None = None,
    ) -> ParsedDocument:
        blocks = [block for block in self.blocks if block.text.strip()]
        if raw_text is not None:
            text = _clean_text(raw_text)
        else:
            text = _clean_text("\n\n".join(block.text for block in blocks))
        return ParsedDocument(
            text=text,
            blocks=blocks,
            sections=[dict(item) for item in self.sections],
            title=(title or "").strip() or None,
            file_ext=file_ext,
            metadata=dict(metadata or {}),
        )


# Markdown / TXT 标题识别
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TXT_HEADING_PATTERNS = (
    re.compile(r"^第[一二三四五六七八九十百千0-9]+[章节部分篇讲课]"),
    re.compile(r"^[一二三四五六七八九十]+[、．.]"),
    re.compile(r"^（[一二三四五六七八九十]+）"),
    re.compile(r"^\([一二三四五六七八九十]+\)"),
)


def _looks_like_txt_heading(line: str) -> int:
    """对纯文本/PDF 抽取文本做保守的标题判断，返回标题层级（0 表示不是标题）。"""
    text = line.strip()
    if not text or len(text) > 40:
        return 0
    if text.endswith(("。", "；", "，", "：", "！", "？", "!", "?", ";", ",", ":", ".")):
        return 0
    for pattern in _TXT_HEADING_PATTERNS:
        if pattern.match(text):
            return 1
    return 0


# ---------------------------------------------------------------------------
# Markdown / TXT
# ---------------------------------------------------------------------------
def parse_markdown(text: str, filename: str = "") -> ParsedDocument:
    """解析 Markdown：保留原文作为 ``text``，按 ``#`` 标题构建章节结构。"""
    raw = text if isinstance(text, str) else decode_bytes(text)  # type: ignore[arg-type]
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    builder = _Builder()
    title: str | None = None
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        match = _MD_HEADING_RE.match(stripped)
        if match:
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            if level == 1 and not title:
                title = heading_text
            builder.heading(heading_text, level)
        else:
            builder.body(stripped)
    result = builder.build(title=title or Path(filename).stem or None, file_ext=".md", raw_text=raw)
    return result


def parse_txt(text: str, filename: str = "") -> ParsedDocument:
    """解析纯文本：保留原文，按中文结构化编号识别标题。"""
    raw = text if isinstance(text, str) else decode_bytes(text)  # type: ignore[arg-type]
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    builder = _Builder()
    title: str | None = None
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        level = _looks_like_txt_heading(stripped)
        if level:
            builder.heading(stripped, level)
            if not title:
                title = stripped
        else:
            builder.body(stripped)
    return builder.build(title=title or Path(filename).stem or None, file_ext=".txt", raw_text=raw)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def parse_pdf(data: bytes, filename: str = "") -> ParsedDocument:
    """使用 pypdf 逐页抽取文本，按空行/行切分为段落。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ParseError("缺少 pypdf 依赖，无法解析 PDF 文件") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"PDF 文件无法读取（可能已损坏）：{exc}") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ParseError("PDF 文件已加密，无法解析，请上传未加密版本") from exc

    try:
        pages = list(reader.pages)
    except Exception as exc:
        raise ParseError(f"PDF 文件无法读取（可能已加密或损坏）：{exc}") from exc
    if not pages:
        raise ParseError("PDF 文件不包含任何页面")

    builder = _Builder()
    page_count = min(len(pages), MAX_PDF_PAGES)
    for page_index in range(page_count):
        try:
            page_text = pages[page_index].extract_text() or ""
        except Exception as exc:
            raise ParseError(f"PDF 第 {page_index + 1} 页解析失败：{exc}") from exc
        page_text = _clean_text(page_text)
        if not page_text:
            continue
        for paragraph in re.split(r"\n\s*\n", page_text):
            lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
            if not lines:
                continue
            if len(lines) == 1 and _looks_like_txt_heading(lines[0]):
                builder.heading(lines[0], 1)
            else:
                builder.body("\n".join(lines))

    metadata = {"pages": len(pages), "parsed_pages": page_count}
    if len(pages) > MAX_PDF_PAGES:
        metadata["truncated"] = True
        logger.warning("PDF 页数超过 %s，仅解析前 %s 页：%s", MAX_PDF_PAGES, MAX_PDF_PAGES, filename)
    return builder.build(title=Path(filename).stem or None, file_ext=".pdf", metadata=metadata)


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------
def _docx_heading_level(paragraph) -> int:
    """根据段落样式/加粗判断标题层级，返回 0 表示正文。"""
    try:
        style = paragraph.style
    except Exception:  # pragma: no cover - 异常样式
        style = None
    style_name = (getattr(style, "name", "") or "").strip()
    style_id = (getattr(style, "style_id", "") or "").strip()
    for candidate in (style_name, style_id):
        if not candidate:
            continue
        match = re.search(r"(?i)(?:heading|标题)\s*([1-9])", candidate)
        if match:
            return int(match.group(1))
        lowered = candidate.lower()
        if "heading" in lowered or "标题" in candidate:
            return 1
    # 兜底：很短且全部加粗的段落视作小标题（很多中文文档不用内置样式）
    text = (paragraph.text or "").strip()
    if text and len(text) <= 30 and not text.endswith(("。", "；", "，", "：", "！", "？", "!", "?", ";", ",", ":")):
        runs = [run for run in paragraph.runs if run.text.strip()]
        if runs and all(run.bold for run in runs):
            return 2
    return 0


def _iter_docx_body(document) -> Iterator[object]:
    """按文档顺序同时产出段落与表格（python-docx 的 paragraphs 不含表格）。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def parse_docx(data: bytes, filename: str = "") -> ParsedDocument:
    """使用 python-docx 解析段落（含样式标题）与表格文本。"""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ParseError("缺少 python-docx 依赖，无法解析 Word 文件") from exc

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"Word 文件无法读取（可能已损坏或不是 .docx）：{exc}") from exc

    builder = _Builder()
    title: str | None = None
    try:
        for item in _iter_docx_body(document):
            if hasattr(item, "runs"):  # Paragraph
                text = (item.text or "").strip()
                if not text:
                    continue
                level = _docx_heading_level(item)
                if level:
                    builder.heading(text, level)
                    if not title:
                        title = text
                else:
                    builder.body(text)
            else:  # Table
                for row in item.rows:
                    cells = [(cell.text or "").strip().replace("\n", " ") for cell in row.cells]
                    cells = [cell for cell in cells if cell]
                    if cells:
                        builder.body(" | ".join(cells))
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Word 文件解析失败：{exc}") from exc

    return builder.build(title=title or Path(filename).stem or None, file_ext=".docx")


# ---------------------------------------------------------------------------
# Word 97-2003 (.doc)
# ---------------------------------------------------------------------------
def parse_doc(data: bytes, filename: str = "") -> ParsedDocument:
    """使用纯 Python 的 legacy-doc 提取旧版 Word 文本（不执行宏/嵌入对象）。"""
    try:
        from legacy_doc import extract_text
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ParseError("缺少 legacy-doc 依赖，无法解析 .doc 文件；可另存为 .docx 后上传") from exc

    try:
        result = extract_text(data)
    except Exception as exc:
        raise ParseError(f"Word 97-2003 文件无法读取（可能已损坏、加密或不是 .doc）：{exc}") from exc

    text = (result.text or "").strip()
    if not text:
        raise ParseError("未能从 .doc 文件中解析出文本内容（可能是扫描件或空文档）")

    builder = _Builder()
    for paragraph in re.split(r"\n{2,}", text):
        paragraph = paragraph.strip()
        if paragraph:
            builder.body(paragraph)

    metadata = dict(getattr(result, "metadata", {}) or {})
    title = str(metadata.get("title") or "").strip() or Path(filename).stem or None
    return builder.build(title=title, file_ext=".doc", metadata=metadata)


# ---------------------------------------------------------------------------
# Excel (.xlsx)
# ---------------------------------------------------------------------------
def parse_xlsx(data: bytes, filename: str = "") -> ParsedDocument:
    """使用 openpyxl 解析：每个 sheet 名作为章节标题，每行单元格用 `` | `` 连接。"""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ParseError("缺少 openpyxl 依赖，无法解析 Excel 文件") from exc

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(f"Excel 文件无法读取（可能已损坏或不是 .xlsx）：{exc}") from exc

    builder = _Builder()
    sheet_names: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            sheet_name = (worksheet.title or "").strip() or "Sheet"
            sheet_names.append(sheet_name)
            builder.heading(f"工作表：{sheet_name}", 1)
            rows = 0
            for row in worksheet.iter_rows(values_only=True):
                if rows >= MAX_XLSX_ROWS_PER_SHEET:
                    builder.body(f"（工作表「{sheet_name}」超过 {MAX_XLSX_ROWS_PER_SHEET} 行，后续内容已截断）")
                    break
                cells = []
                for value in row[:MAX_XLSX_COLS]:
                    if value is None:
                        continue
                    text = str(value).strip()
                    if text:
                        cells.append(text)
                if cells:
                    builder.body(" | ".join(cells))
                    rows += 1
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Excel 文件解析失败：{exc}") from exc
    finally:
        try:
            workbook.close()
        except Exception:  # pragma: no cover
            pass

    metadata = {"sheets": sheet_names}
    return builder.build(title=Path(filename).stem or None, file_ext=".xlsx", metadata=metadata)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def parse_csv(data: bytes, filename: str = "") -> ParsedDocument:
    """按行解析 CSV，单元格用 `` | `` 连接；自动识别 UTF-8/GBK。"""
    raw = decode_bytes(data)
    builder = _Builder()
    try:
        rows = csv.reader(io.StringIO(raw))
        for row in rows:
            cells = [(cell or "").strip() for cell in row]
            cells = [cell for cell in cells if cell]
            if cells:
                builder.body(" | ".join(cells))
    except csv.Error as exc:
        raise ParseError(f"CSV 文件解析失败：{exc}") from exc
    return builder.build(title=Path(filename).stem or None, file_ext=".csv")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def parse_json(data: bytes, filename: str = "") -> ParsedDocument:
    """解析 JSON 并格式化为缩进文本；非法 JSON 抛 ParseError。"""
    raw = decode_bytes(data).strip()
    if not raw:
        raise ParseError("JSON 文件内容为空")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"JSON 格式不合法：第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}"
        ) from exc
    formatted = json.dumps(payload, ensure_ascii=False, indent=2)
    builder = _Builder()
    for line in formatted.split("\n"):
        builder.body(line)
    title = None
    if isinstance(payload, dict):
        candidate = payload.get("title") or payload.get("name")
        if isinstance(candidate, str) and candidate.strip():
            title = candidate.strip()
    return builder.build(title=title or Path(filename).stem or None, file_ext=".json")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
_HTML_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_HTML_BLOCKS = _HTML_HEADINGS.keys() | {"p", "li", "pre", "blockquote", "dt", "dd", "tr"}


def _has_block_ancestor(element) -> bool:
    for parent in element.parents:
        if getattr(parent, "name", None) in _HTML_BLOCKS:
            return True
    return False


def parse_html(data: bytes, filename: str = "") -> ParsedDocument:
    """使用 BeautifulSoup 去掉脚本/样式后按标题、段落、列表、表格行抽取文本。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ParseError("缺少 beautifulsoup4 依赖，无法解析 HTML 文件") from exc

    raw = decode_bytes(data)
    try:
        soup = BeautifulSoup(raw, "html.parser")
    except Exception as exc:
        raise ParseError(f"HTML 文件解析失败：{exc}") from exc

    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()

    builder = _Builder()
    title: str | None = None
    if soup.title is not None:
        title_text = soup.title.get_text(" ", strip=True)
        if title_text:
            title = title_text

    for element in soup.find_all(list(_HTML_BLOCKS)):
        name = getattr(element, "name", "")
        if not name or _has_block_ancestor(element):
            continue
        if name in _HTML_HEADINGS:
            text = element.get_text(" ", strip=True)
            builder.heading(text, _HTML_HEADINGS[name])
            if not title and _HTML_HEADINGS[name] == 1:
                title = text or None
        elif name == "tr":
            cells = [cell.get_text(" ", strip=True) for cell in element.find_all(["td", "th"])]
            text = " | ".join(cell for cell in cells if cell)
            builder.body(text)
        else:
            builder.body(element.get_text(" ", strip=True))

    if not builder.blocks:
        # 没有常见块级标签的页面（如 <div>正文</div>）退化为整页文本
        body = soup.body or soup
        builder.body(body.get_text("\n", strip=True))

    return builder.build(title=title or Path(filename).stem or None, file_ext=".html")


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
_TEXT_PARSERS = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_txt,
}
_BINARY_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".doc": parse_doc,
    ".xlsx": parse_xlsx,
    ".csv": parse_csv,
    ".json": parse_json,
    ".html": parse_html,
    ".htm": parse_html,
}


def parse_bytes(filename: str, data: bytes) -> ParsedDocument:
    """按扩展名解析内存中的文件字节。"""
    filename = (filename or "").strip() or "未命名文件"
    if data is None:
        raise ParseError("文件内容为空，无法解析")
    ext = Path(filename).suffix.lower()
    if not ext:
        raise ParseError(f"无法识别文件类型：「{filename}」没有扩展名")
    try:
        if ext in _TEXT_PARSERS:
            result = _TEXT_PARSERS[ext](decode_bytes(data), filename)
        elif ext in _BINARY_PARSERS:
            result = _BINARY_PARSERS[ext](data, filename)
        else:
            raise ParseError(f"不支持的文件类型「{ext}」，无法解析")
    except ParseError:
        raise
    except Exception as exc:  # 兜底：任何第三方库异常都转成清晰的中文错误
        raise ParseError(f"「{filename}」解析失败：{exc}") from exc

    if not result.text.strip():
        raise ParseError(f"未能从「{filename}」中解析出文本内容（可能是空文件或扫描件）")
    if not result.file_ext:
        result.file_ext = ext
    if not result.title:
        result.title = Path(filename).stem or None
    return result


def parse_path(path: str | Path) -> ParsedDocument:
    """解析磁盘上的文件。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise ParseError(f"文件不存在：{file_path}")
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise ParseError(f"无法读取文件「{file_path.name}」：{exc}") from exc
    return parse_bytes(file_path.name, data)


def parse_document(
    filename: str,
    data: bytes | None = None,
    *,
    path: str | Path | None = None,
) -> ParsedDocument:
    """统一解析入口：``parse_document(filename, bytes)`` 或 ``parse_document(path=...)``。"""
    if path is not None:
        return parse_path(path)
    if data is None:
        raise ParseError("缺少文件内容，无法解析")
    return parse_bytes(filename, data)


# 兼容常见命名
parse_file = parse_path

__all__ = [
    "ParseError",
    "ParsedDocument",
    "TextBlock",
    "decode_bytes",
    "parse_document",
    "parse_bytes",
    "parse_path",
    "parse_file",
    "parse_markdown",
    "parse_txt",
    "parse_pdf",
    "parse_docx",
    "parse_xlsx",
    "parse_csv",
    "parse_json",
    "parse_html",
    "MARKDOWN_EXTS",
    "TEXT_EXTS",
]

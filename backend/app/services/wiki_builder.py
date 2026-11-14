"""Wiki 编译管线：源文件 → LLM → 结构化词条 + 来源锚点。

设计依据：``docs/WIKI-DESIGN.md`` §3.1 / §4 / §5。核心流程::

    解析(parsing) → 按标题聚合编译单元(≤3500 字)
        → 每单元 1 次 llm.chat(JSON 数组，1–3 个词条)
        → 校验 / 来源摘录逐字校验 / 标题归一化去重
            ├─ 命中已有词条 → LLM merge → 更新正文并追加来源
            └─ 新词条 → INSERT wiki_pages + wiki_sources
        → 建立 related 链接 → documents.status/page_count → wiki_builds 审计

硬约束：

* 只通过 :func:`services.llm.chat` 调模型，不直接访问 httpx；
* mock / 无真实模型时 :func:`can_compile` 返回 ``False``，上传文档标记 failed；
* 任何 LLM 调用失败都不会向外抛异常（接口不会 500），失败原因写成中文 error；
* 来源锚点必须真实：``source_quote`` 必须能在单元原文中逐字找到（去空白比对），
  否则自动替换为单元原文前 120 字；``source_section`` 必须来自解析出的章节。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .. import config, db
from . import llm, parsing, text_utils

logger = logging.getLogger("npu-assistant")

# ---------------------------------------------------------------------------
# 提示词（编译 / 合并）
# ---------------------------------------------------------------------------
COMPILE_SYSTEM_PROMPT = """你是西北工业大学「校园 Wiki」的知识编辑。你的唯一任务是把给定的一小段校园文件原文，编译成 1–3 个结构化 Wiki 词条，供辅导员数字分身回答学生问题。

硬性规则：
1. 只能使用【原文】中明确出现的事实；数字、金额、时间、电话、地点必须保留原文写法，绝不推测或补全。
2. 每个词条聚焦一个可独立检索的主题（例如「国家奖学金申请」「请假流程」），不要把整篇文档塞进一个词条。
3. 只输出一个 JSON 数组（不要 Markdown 代码块、不要解释文字），数组元素为对象，字段如下：
   - "title": 词条标题，2–30 字的名词短语，不含书名号；
   - "aliases": 别名数组（0–5 个，学生可能用的其他叫法，如「国奖」）；
   - "summary": 一句话概述，≤160 字；
   - "tags": 标签数组（1–5 个，从「奖助学金、学籍、请假考勤、心理、就业、宿舍、档案、一卡通、校园生活」等主题中选）；
   - "body_md": 词条正文 Markdown（≤1800 字），按需使用「概述 / 申请条件 / 流程 / 材料 / 时间节点 / 注意事项」等小标题与列表；
   - "source_section": 该词条来源的章节标题，必须从【可用章节】中原样选择；
   - "source_quote": 从【原文】中逐字摘录的最有代表性的一句话或一段（≤160 字，必须连续出现在原文中）；
   - "related_titles": 相关词条标题数组（0–4 个，可为空）。
4. 若原文信息不足以形成词条，输出空数组 []。
5. 原文中的示例数据（标注"示例"）保留"示例"字样，不要当成真实政策。"""

COMPILE_USER_TEMPLATE = """【文档标题】{doc_title}
【文档标签】{doc_tags}
【可用章节】{sections}

【原文】
<<<
{unit_text}
>>>

请严格按系统要求输出 JSON 数组。"""

MERGE_SYSTEM_PROMPT = """你是校园 Wiki 的知识编辑。你收到一个「已有词条」和一段「新增来源」编译出的新内容，请把它们合并为同一个词条，避免信息丢失与自相矛盾。

规则：
1. 只保留两边都有的真实事实；新来源补充的内容并入正文，旧内容不轻易删除。
2. 如果新旧来源对同一事实（金额/时间/条件）说法不一致，在正文中用引用块标注：
   > 注：不同来源说法不一致，请以学校最新官方通知为准。
3. 正文 Markdown ≤1800 字；保留原有小标题结构，可新增「补充说明」小节。
4. 只输出一个 JSON 对象（不要 Markdown 代码块、不要解释），字段：
   {"summary": "≤160 字概述", "body_md": "合并后的正文 Markdown", "aliases": ["..."], "tags": ["..."]}
5. 不要编造任何原文没有的事实。"""

MERGE_USER_TEMPLATE = """【已有词条】
{existing_json}

【新增内容（来自文档《{document_title}》章节：{section}）】
{entry_json}

请输出合并后的 JSON 对象。"""


# ---------------------------------------------------------------------------
# 可用性 / 基础工具
# ---------------------------------------------------------------------------
def can_compile() -> tuple[bool, str]:
    """返回 ``(是否可编译, 不可用原因)``。

    编译必须依赖真实模型；内置 mock 模型只用于演示与降级回答，一律拒绝编译。
    """
    try:
        model = llm.default_model()
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.exception("读取默认模型失败")
        return False, f"读取模型配置失败：{exc}"
    if not model:
        return False, "尚未配置可用的模型，请先在「模型管理」中添加并启用模型"
    provider_type = (model.get("provider_type") or "").strip().lower()
    if provider_type == "mock":
        return (
            False,
            "当前默认模型是内置演示模型（mock），无法编译 Wiki 词条；"
            "请先在「模型管理」中配置并启用真实模型（如 DeepSeek）",
        )
    if provider_type not in {"openai", "ollama"}:
        return False, f"不支持用于编译的 Provider 类型：{provider_type or '空'}"
    if not str(model.get("model_name") or "").strip():
        return False, "默认模型缺少模型名称，请检查「模型管理」配置"
    if provider_type == "openai" and not str(model.get("api_key") or "").strip():
        return False, "OpenAI 兼容 Provider 未配置 API Key，无法调用模型编译 Wiki 词条"
    if not str(model.get("base_url") or "").strip():
        return False, "Provider 未配置 base_url，无法调用模型编译 Wiki 词条"
    return True, ""


def _model_label() -> str:
    try:
        model = llm.default_model()
    except Exception:  # pragma: no cover
        return ""
    if not model:
        return ""
    return str(model.get("display_name") or model.get("model_name") or "")


def _error_text(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if len(message) > 500:
        message = message[:497] + "…"
    return message


def _truncate(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip()


def _first_chars(text: Any, limit: int) -> str:
    """把文本压缩为单行并取前 ``limit`` 个字符（用于来源摘录兜底）。"""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def _strip_ws(text: Any) -> str:
    """去掉所有空白，用于来源摘录的逐字校验。"""
    return re.sub(r"\s+", "", str(text or ""))


def _string_list(value: Any, limit: int, max_chars: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        text = text[:max_chars]
        if text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _document_json(row: dict | None) -> dict:
    """documents 表行 → 契约 doc 对象（与 ingest.document_to_dict 字段一致）。"""
    if not row:
        return {}
    tags = db.from_json(row.get("tags"), [])
    if not isinstance(tags, list):
        tags = []
    return {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "file_ext": row["file_ext"],
        "size_bytes": int(row.get("size_bytes") or 0),
        "status": row["status"],
        "error": row.get("error"),
        "tags": [str(tag) for tag in tags if isinstance(tag, (str, int, float))],
        "char_count": int(row.get("char_count") or 0),
        "page_count": int(row.get("page_count") or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _page_dict(row: dict | None) -> dict | None:
    if not row:
        return None
    aliases = db.from_json(row.get("aliases"), [])
    tags = db.from_json(row.get("tags"), [])
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "summary": row.get("summary") or "",
        "body_md": row.get("body_md") or "",
        "aliases": aliases if isinstance(aliases, list) else [],
        "tags": tags if isinstance(tags, list) else [],
        "status": row.get("status") or "ready",
        "version": int(row.get("version") or 1),
        "source_count": int(row.get("source_count") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# JSON 解析 / 模型调用
# ---------------------------------------------------------------------------
def _extract_json(text: str, expected: type) -> Any:
    """从模型输出中提取 JSON（容忍 ```json 包裹与前后解释文字）。"""
    value = (text or "").strip()
    if not value:
        return None
    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, re.S | re.I)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(value)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, expected):
            return data
        if expected is list and isinstance(data, dict):
            for key in ("pages", "entries", "items", "wiki_pages", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
        if expected is dict and isinstance(data, list):
            first = next((item for item in data if isinstance(item, dict)), None)
            if first is not None:
                return first
        open_char, close_char = ("[", "]") if expected is list else ("{", "}")
        start = candidate.find(open_char)
        end = candidate.rfind(close_char)
        if start != -1 and end > start:
            try:
                data = json.loads(candidate[start : end + 1])
            except (TypeError, ValueError):
                continue
            if isinstance(data, expected):
                return data
    return None


def _call_json(
    messages: list[dict],
    expected: type,
    *,
    max_tokens: int,
    error_prefix: str,
) -> tuple[Any, str | None, bool]:
    """调用模型并解析 JSON。

    返回 ``(data, error, fatal)``：``fatal=True`` 表示模型不可用/调用失败（网络或配置），
    ``fatal=False`` 表示模型可用但输出无法解析（按设计重试 1 次）。
    """
    error = ""
    for attempt in range(2):
        try:
            result = llm.chat(messages, temperature=0.1, max_tokens=max_tokens)
        except Exception as exc:  # pragma: no cover - llm.chat 契约上不抛异常
            logger.exception("%s：模型调用抛出异常", error_prefix)
            return None, f"{error_prefix}失败：{exc}", True
        if not isinstance(result, dict):
            error = f"{error_prefix}失败：模型返回结构异常"
            continue
        if result.get("fallback") or not str(result.get("content") or "").strip():
            reason = str(result.get("error") or "模型未返回内容").strip()
            return None, f"{error_prefix}失败：{reason}", True
        data = _extract_json(str(result.get("content") or ""), expected)
        if data is not None:
            return data, None, False
        error = f"{error_prefix}失败：模型输出不是有效 JSON"
        logger.warning("%s（第 %s 次尝试）", error, attempt + 1)
    return None, error, False


# ---------------------------------------------------------------------------
# 编译单元：按标题聚合 ≤ WIKI_UNIT_MAX_CHARS
# ---------------------------------------------------------------------------
def _hard_split(text: str, max_chars: int) -> list[str]:
    """没有段落边界时按句末标点/换行硬切。"""
    pieces: list[str] = []
    start = 0
    length = len(text)
    boundaries = "。！？!?；;…\n"
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            window = text[start:end]
            best = max(window.rfind(char) for char in boundaries)
            if best > max_chars // 2:
                end = start + best + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        start = end
    return pieces


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """按空行/段落聚合，超长段落再硬切，保证每段 ≤ max_chars。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [line.strip() for line in text.split("\n") if line.strip()]

    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_split(paragraph, max_chars))
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = paragraph
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece.strip()]


def _segments(parsed: parsing.ParsedDocument) -> list[dict]:
    """把正文块按「最近章节」分组，保持文档顺序。标题块本身不进入正文。"""
    default_section = (parsed.title or "").strip() or "全文"
    segments: list[dict] = []
    for block in getattr(parsed, "blocks", None) or []:
        if getattr(block, "is_heading", False):
            continue
        body = (block.text or "").strip()
        if not body:
            continue
        section = (getattr(block, "section", None) or default_section).strip() or default_section
        if segments and segments[-1]["section"] == section:
            segments[-1]["texts"].append(body)
        else:
            segments.append({"section": section, "texts": [body]})
    return segments


def _content_length(unit_text: str) -> int:
    """去掉 "## 章节" 标题行后的正文长度。"""
    body = re.sub(r"(?m)^#{1,6}\s+.*$", "", unit_text or "")
    return len(re.sub(r"\s+", "", body))


def build_units(parsed: parsing.ParsedDocument, doc_title: str) -> list[dict]:
    """按标题聚合编译单元（纯函数，便于单测）。

    返回 ``[{"text": 单元原文, "sections": [章节...]}]``；相邻章节会在
    ≤ ``config.WIKI_UNIT_MAX_CHARS`` 前提下合并；整篇过长时自动放宽上限，
    保证单元数不超过 ``config.WIKI_MAX_UNITS_PER_DOC``。
    """
    segments = _segments(parsed)
    if not segments:
        whole = (parsed.text or "").strip()
        if not whole:
            return []
        title = doc_title or (parsed.title or "") or "全文"
        return [
            {"text": piece, "sections": [title]}
            for piece in _split_long_text(whole, config.WIKI_UNIT_MAX_CHARS)
            if _content_length(piece) >= 1
        ]

    total_chars = sum(len("\n".join(seg["texts"])) for seg in segments)
    max_chars = max(
        config.WIKI_UNIT_MAX_CHARS,
        -(-total_chars // max(1, config.WIKI_MAX_UNITS_PER_DOC)),
    )

    units: list[dict] = []

    def flush(current: dict) -> None:
        if current["parts"]:
            text = "\n\n".join(current["parts"]).strip()
            if text:
                units.append({"text": text, "sections": list(current["sections"])})

    current: dict = {"sections": [], "parts": []}
    current_len = 0
    for segment in segments:
        section = segment["section"]
        body = "\n".join(segment["texts"]).strip()
        if not body:
            continue
        for piece in _split_long_text(body, max_chars):
            block = f"## {section}\n{piece}"
            if current["parts"] and current_len + len(block) + 2 > max_chars:
                flush(current)
                current = {"sections": [], "parts": []}
                current_len = 0
            if not current["sections"] or current["sections"][-1] != section:
                current["sections"].append(section)
            current["parts"].append(block)
            current_len += len(block) + 2
    flush(current)

    # 过短单元尽量并入上一个单元，避免产生无意义的小词条
    merged: list[dict] = []
    for unit in units:
        if merged and _content_length(unit["text"]) < config.WIKI_UNIT_MIN_CHARS:
            previous = merged[-1]
            combined = f"{previous['text']}\n\n{unit['text']}"
            if len(combined) <= max_chars * 1.5:
                previous["text"] = combined
                for section in unit["sections"]:
                    if section not in previous["sections"]:
                        previous["sections"].append(section)
                continue
        merged.append(unit)

    return [unit for unit in merged if _content_length(unit["text"]) >= 1]


# ---------------------------------------------------------------------------
# 词条校验 / 归一化
# ---------------------------------------------------------------------------
def _resolve_section(value: Any, allowed: list[str]) -> str | None:
    """把模型返回的章节名映射到解析出的真实章节；无法匹配返回 ``None``。"""
    candidate = re.sub(r"^#{1,6}\s*", "", str(value or "")).strip()
    if not candidate:
        return None
    normalized = text_utils.normalize_title(candidate)
    if not normalized:
        return None
    for section in allowed:
        section_norm = text_utils.normalize_title(section)
        if section_norm and (
            normalized == section_norm
            or normalized in section_norm
            or section_norm in normalized
        ):
            return section
    return None


def _fallback_entry(unit: dict, doc_title: str) -> dict:
    """模型输出不可用时的兜底词条：保留原文，标记 needs_review。"""
    section = (unit["sections"][0] if unit["sections"] else doc_title) or "文档摘录"
    title = section[:60] or (doc_title[:60] if doc_title else "文档摘录")
    body = unit["text"].strip()
    return {
        "title": title,
        "_status": "needs_review",
        "aliases": [],
        "summary": _first_chars(body, config.WIKI_SUMMARY_MAX_CHARS),
        "tags": [],
        "body_md": _truncate(body, config.WIKI_PAGE_BODY_MAX_CHARS),
        "source_section": section,
        "source_quote": body[:120],
        "related_titles": [],
    }


def normalize_entry(raw: Any, unit: dict, doc_row: dict) -> dict | None:
    """严格校验并归一化一个模型产出的词条；不合法返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    doc_title = str(doc_row.get("title") or "").strip()
    doc_tags = db.from_json(doc_row.get("tags"), [])
    if not isinstance(doc_tags, list):
        doc_tags = []

    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip().strip("《》\"'")
    if len(title) < 2:
        return None
    title = title[:60]

    body_md = str(raw.get("body_md") or raw.get("body") or "").strip()
    if not body_md:
        return None
    body_md = _truncate(body_md, config.WIKI_PAGE_BODY_MAX_CHARS)

    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
    summary = _truncate(summary, config.WIKI_SUMMARY_MAX_CHARS)

    sections = list(unit.get("sections") or [])
    section = _resolve_section(raw.get("source_section"), sections)
    if section is None:
        section = sections[0] if sections else (doc_title or None)

    unit_text = str(unit.get("text") or "")
    quote = re.sub(r"\s+", " ", str(raw.get("source_quote") or "")).strip()
    quote = _truncate(quote, config.WIKI_SOURCE_QUOTE_MAX_CHARS)
    if not quote or _strip_ws(quote) not in _strip_ws(unit_text):
        # 来源摘录必须逐字可核对；不合格时用单元原文前 120 字替换
        quote = unit_text.strip()[:120]

    aliases = _string_list(raw.get("aliases"), 6, 30)
    tags = _string_list(raw.get("tags"), 8, 20) or [
        str(tag) for tag in doc_tags[:3] if str(tag).strip()
    ]
    if not summary:
        summary = _first_chars(body_md, config.WIKI_SUMMARY_MAX_CHARS)
    status = str(raw.get("_status") or "ready").strip().lower()
    if status not in {"ready", "needs_review"}:
        status = "ready"

    return {
        "title": title,
        "aliases": aliases,
        "summary": summary,
        "tags": tags,
        "body_md": body_md,
        "source_section": section,
        "source_quote": quote,
        "related_titles": _string_list(raw.get("related_titles"), 5, 40),
        "status": status,
    }


# ---------------------------------------------------------------------------
# 页面索引（去重）
# ---------------------------------------------------------------------------
class _PageIndex:
    """内存索引：slug / 归一化标题 / 别名 → 词条行，用于编译期去重。"""

    def __init__(self) -> None:
        self.by_slug: dict[str, dict] = {}
        self.by_norm: dict[str, dict] = {}
        for row in db.query("SELECT * FROM wiki_pages"):
            page = _page_dict(row)
            if page:
                self.add(page)

    def add(self, page: dict) -> None:
        self.by_slug[page["slug"]] = page
        for label in [page["title"], *(page.get("aliases") or [])]:
            normalized = text_utils.normalize_title(label)
            if normalized:
                self.by_norm.setdefault(normalized, page)

    def find(self, title: str, aliases: list[str] | None = None) -> dict | None:
        for label in [title, *(aliases or [])]:
            normalized = text_utils.normalize_title(label)
            if normalized and normalized in self.by_norm:
                return self.by_norm[normalized]
        return self.by_slug.get(text_utils.slugify(title))

    def refresh(self, page_id: str) -> dict | None:
        page = _page_dict(db.get_wiki_page(page_id))
        if page:
            self.add(page)
        return page


# ---------------------------------------------------------------------------
# 数据库写入：词条 / 来源 / 链接 / 文档状态 / 构建记录
# ---------------------------------------------------------------------------
def _add_source(
    page_id: str,
    doc_row: dict,
    section: str | None,
    quote: str,
) -> bool:
    """写入来源锚点（同页同文档同章节同摘录去重），并刷新 source_count。"""
    document_id = doc_row["id"]
    section_value = section or ""
    existing = db.query_one(
        "SELECT id FROM wiki_sources WHERE page_id = ? AND document_id = ? "
        "AND COALESCE(section, '') = ? AND quote = ?",
        (page_id, document_id, section_value, quote),
    )
    if existing:
        return False
    db.execute(
        "INSERT INTO wiki_sources(id, page_id, document_id, document_title, source_file, "
        "section, quote, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            db.new_id(),
            page_id,
            document_id,
            str(doc_row.get("title") or ""),
            str(doc_row.get("filename") or ""),
            section,
            quote,
            db.now(),
        ),
    )
    _refresh_source_count(page_id)
    return True


def _refresh_source_count(page_id: str) -> None:
    row = db.query_one("SELECT COUNT(*) AS n FROM wiki_sources WHERE page_id = ?", (page_id,))
    db.execute(
        "UPDATE wiki_pages SET source_count = ? WHERE id = ?",
        (int(row["n"]) if row else 0, page_id),
    )


def _insert_page(entry: dict, doc_row: dict, unit: dict) -> dict | None:
    title = entry["title"]
    slug = text_utils.slugify(title)
    if db.get_wiki_page_by_slug(slug):
        return None
    timestamp = db.now()
    page_id = db.new_id()
    db.execute(
        "INSERT INTO wiki_pages(id, slug, title, summary, body_md, aliases, tags, status, "
        "version, source_count, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
        (
            page_id,
            slug,
            title,
            entry["summary"],
            entry["body_md"],
            db.to_json(entry["aliases"]),
            db.to_json(entry["tags"]),
            entry["status"],
            timestamp,
            timestamp,
        ),
    )
    _add_source(page_id, doc_row, entry["source_section"], entry["source_quote"])
    return _page_dict(db.get_wiki_page(page_id))


def _merge_page(existing: dict, entry: dict, doc_row: dict) -> dict | None:
    """把新编译词条合并进已有词条：优先 LLM merge，失败则本地追加正文。"""
    document_title = str(doc_row.get("title") or doc_row.get("filename") or "")
    existing_payload = {
        "title": existing["title"],
        "summary": existing["summary"],
        "body_md": existing["body_md"],
        "aliases": existing["aliases"],
        "tags": existing["tags"],
    }
    entry_payload = {
        "title": entry["title"],
        "summary": entry["summary"],
        "body_md": entry["body_md"],
        "aliases": entry["aliases"],
        "tags": entry["tags"],
    }
    messages = [
        {"role": "system", "content": MERGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": MERGE_USER_TEMPLATE.format(
                existing_json=json.dumps(existing_payload, ensure_ascii=False, indent=2),
                document_title=document_title,
                section=entry["source_section"] or "",
                entry_json=json.dumps(entry_payload, ensure_ascii=False, indent=2),
            ),
        },
    ]
    merged, error, _fatal = _call_json(
        messages, dict, max_tokens=1800, error_prefix="合并词条"
    )
    if error:
        logger.warning("词条「%s」LLM 合并失败（%s），改用本地追加", existing["title"], error)

    summary = existing["summary"]
    body_md = existing["body_md"]
    aliases = list(existing["aliases"])
    tags = list(existing["tags"])

    if isinstance(merged, dict):
        candidate_summary = re.sub(r"\s+", " ", str(merged.get("summary") or "")).strip()
        if candidate_summary:
            summary = _truncate(candidate_summary, config.WIKI_SUMMARY_MAX_CHARS)
        candidate_body = str(merged.get("body_md") or "").strip()
        if candidate_body:
            body_md = _truncate(candidate_body, config.WIKI_PAGE_BODY_MAX_CHARS)
        aliases = _unique(aliases + _string_list(merged.get("aliases"), 10, 30))[:10]
        tags = _unique(tags + _string_list(merged.get("tags"), 10, 20))[:10]
    else:
        # 本地兜底：把新正文追加为补充小节，保证来源不丢失
        section = entry["source_section"] or "补充说明"
        addition = f"## {section}\n{entry['body_md']}"
        combined = f"{body_md.strip()}\n\n{addition}".strip()
        body_md = _truncate(combined, config.WIKI_PAGE_BODY_MAX_CHARS)
        if not summary:
            summary = entry["summary"]

    aliases = _unique(aliases + entry["aliases"])[:10]
    tags = _unique(tags + entry["tags"])[:10]
    new_status = "ready" if existing["status"] == "ready" or entry["status"] == "ready" else "needs_review"
    db.execute(
        "UPDATE wiki_pages SET summary = ?, body_md = ?, aliases = ?, tags = ?, status = ?, "
        "version = version + 1, updated_at = ? WHERE id = ?",
        (
            summary,
            body_md,
            db.to_json(aliases),
            db.to_json(tags),
            new_status,
            db.now(),
            existing["id"],
        ),
    )
    _add_source(existing["id"], doc_row, entry["source_section"], entry["source_quote"])
    return _page_dict(db.get_wiki_page(existing["id"]))


def _link_related(source_page: dict | None, related_titles: list[str], index: _PageIndex) -> None:
    if not source_page or not related_titles:
        return
    for label in related_titles:
        normalized = text_utils.normalize_title(label)
        target = index.by_norm.get(normalized) if normalized else None
        if not target or target["id"] == source_page["id"]:
            continue
        db.execute(
            "INSERT OR IGNORE INTO wiki_links(id, from_page_id, to_page_id, relation) "
            "VALUES(?, ?, ?, 'related')",
            (db.new_id(), source_page["id"], target["id"]),
        )


def _mark_document(
    document_id: str,
    status: str,
    *,
    error: str | None = None,
    page_count: int | None = None,
) -> None:
    assignments = ["status = ?", "error = ?", "updated_at = ?"]
    params: list[Any] = [status, error, db.now()]
    if page_count is not None:
        assignments.append("page_count = ?")
        params.append(int(page_count))
    params.append(document_id)
    db.execute(f"UPDATE documents SET {', '.join(assignments)} WHERE id = ?", params)


def _document_page_count(document_id: str) -> int:
    row = db.query_one(
        "SELECT COUNT(DISTINCT page_id) AS n FROM wiki_sources WHERE document_id = ?",
        (document_id,),
    )
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# wiki_builds 审计
# ---------------------------------------------------------------------------
def start_build(document_id: str | None = None, model: str | None = None) -> str:
    """创建 running 状态的构建记录并返回 build_id。"""
    build_id = db.new_id()
    db.execute(
        "INSERT INTO wiki_builds(id, document_id, status, model, pages_created, pages_updated, "
        "pages_removed, error, started_at, finished_at) VALUES(?, ?, 'running', ?, 0, 0, 0, NULL, ?, NULL)",
        (build_id, document_id, model, db.now()),
    )
    return build_id


def finish_build(
    build_id: str,
    *,
    status: str,
    model: str | None = None,
    pages_created: int = 0,
    pages_updated: int = 0,
    pages_removed: int = 0,
    error: str | None = None,
) -> None:
    """结束构建记录（供编译流程与 /wiki/rebuild 后台任务共用）。"""
    db.execute(
        "UPDATE wiki_builds SET status = ?, model = COALESCE(?, model), pages_created = ?, "
        "pages_updated = ?, pages_removed = ?, error = ?, finished_at = ? WHERE id = ?",
        (
            status,
            model,
            int(pages_created),
            int(pages_updated),
            int(pages_removed),
            error,
            db.now(),
            build_id,
        ),
    )


# ---------------------------------------------------------------------------
# 编译主流程
# ---------------------------------------------------------------------------
def compile_document(document_id: str, *, force: bool = False) -> dict:
    """编译单个源文件为 Wiki 词条。

    返回 ``{document, pages_created, pages_updated, pages_removed, model, status,
    build_id, error}``。文档不存在抛 ``ValueError``；其余任何失败都写入
    ``documents.error`` 并从返回值返回，不向路由层抛异常。
    """
    row = db.get_document(document_id)
    if not row:
        raise ValueError("文档不存在或已被删除")

    model_label = _model_label()
    build_id = start_build(document_id, model_label)
    result: dict[str, Any] = {
        "document": _document_json(row),
        "pages_created": 0,
        "pages_updated": 0,
        "pages_removed": 0,
        "model": model_label,
        "status": "failed",
        "build_id": build_id,
        "error": None,
    }

    readable, reason = can_compile()
    if not readable:
        error = reason or "未配置可用的真实模型，无法编译 Wiki 词条"
        _mark_document(document_id, "failed", error=error)
        finish_build(build_id, status="failed", model=model_label, error=error)
        result["document"] = _document_json(db.get_document(document_id))
        result["error"] = error
        logger.warning("文档编译被拒绝（%s）：%s", row.get("filename"), error)
        return result

    _mark_document(document_id, "processing", error=None)
    started = time.perf_counter()
    created = 0
    updated = 0
    fatal_units = 0
    page_index = _PageIndex()
    pending_links: list[tuple[dict, list[str]]] = []
    errors: list[str] = []

    try:
        stored_path = Path(row.get("stored_path") or "")
        if not stored_path.is_file():
            raise ValueError("原始文件已丢失，无法编译 Wiki 词条，请重新上传该文档")
        parsed = parsing.parse_document(
            row.get("filename") or stored_path.name, stored_path.read_bytes()
        )
        doc_title = str(row.get("title") or parsed.title or stored_path.stem)
        doc_row = dict(row)
        doc_row["title"] = doc_title

        units = build_units(parsed, doc_title)
        if not units:
            raise ValueError("文档中没有可编译的正文内容，请检查文件是否为空或格式异常")

        for index, unit in enumerate(units, start=1):
            entries, error, fatal = _compile_unit(unit, doc_title, doc_row)
            if error:
                errors.append(error)
            if fatal:
                fatal_units += 1
            if not entries:
                entries = [_fallback_entry(unit, doc_title)]
                logger.warning(
                    "文档《%s》第 %s/%s 个编译单元降级为 needs_review（%s）",
                    doc_title,
                    index,
                    len(units),
                    error or "模型未返回有效词条",
                )

            def store_entry(raw_entry: Any) -> bool:
                nonlocal created, updated
                entry = normalize_entry(raw_entry, unit, doc_row)
                if not entry:
                    return False
                existing = page_index.find(entry["title"], entry["aliases"])
                if existing:
                    merged = _merge_page(existing, entry, doc_row)
                    if merged:
                        page_index.add(merged)
                        updated += 1
                        pending_links.append((merged, entry["related_titles"]))
                    return True
                new_page = _insert_page(entry, doc_row, unit)
                if not new_page:
                    # slug 冲突：退化为合并到同 slug 页面
                    conflict = _page_dict(
                        db.get_wiki_page_by_slug(text_utils.slugify(entry["title"]))
                    )
                    if conflict:
                        merged = _merge_page(conflict, entry, doc_row)
                        if merged:
                            page_index.add(merged)
                            updated += 1
                            pending_links.append((merged, entry["related_titles"]))
                    return True
                page_index.add(new_page)
                created += 1
                pending_links.append((new_page, entry["related_titles"]))
                return True

            accepted = 0
            for raw_entry in entries[:3]:
                if store_entry(raw_entry):
                    accepted += 1
            if accepted == 0:
                # 模型返回了 JSON 但词条全部不合法（缺标题/正文等）：用原文摘录兜底，
                # 保证「文件已上传但整篇失败」不再发生（词条标记 needs_review 待人工确认）。
                if store_entry(_fallback_entry(unit, doc_title)):
                    accepted += 1
                    logger.warning(
                        "文档《%s》第 %s/%s 个编译单元的词条全部不合法，已降级为原文摘录",
                        doc_title,
                        index,
                        len(units),
                    )
            if accepted == 0:
                errors.append("模型返回的词条缺少标题或正文")

        for source_page, related_titles in pending_links:
            _link_related(source_page, related_titles, page_index)

        page_count = _document_page_count(document_id)
        if created + updated == 0:
            raise ValueError(
                "未能从文档中编译出有效词条，请检查文件内容，或在「模型管理」中确认模型可用后重试"
            )

        if fatal_units >= len(units):
            error = errors[0] if errors else "模型调用失败，未能完成词条编译"
            fatal_error = _error_text(RuntimeError(error))
            _mark_document(document_id, "failed", error=fatal_error, page_count=page_count)
            finish_build(
                build_id,
                status="failed",
                model=model_label,
                pages_created=created,
                pages_updated=updated,
                error=fatal_error,
            )
            result.update(
                pages_created=created,
                pages_updated=updated,
                status="failed",
                error=fatal_error,
            )
            result["document"] = _document_json(db.get_document(document_id))
            logger.warning("文档编译失败（%s）：%s", row.get("filename"), error)
            return result

        _mark_document(document_id, "ready", error=None, page_count=page_count)
        finish_build(
            build_id,
            status="ready",
            model=model_label,
            pages_created=created,
            pages_updated=updated,
        )
        result.update(
            pages_created=created,
            pages_updated=updated,
            status="ready",
            error=None,
        )
        result["document"] = _document_json(db.get_document(document_id))
        logger.info(
            "文档编译完成：%s（新增 %s / 更新 %s 个词条，用时 %.1fs）",
            row.get("filename"),
            created,
            updated,
            time.perf_counter() - started,
        )
        return result
    except Exception as exc:  # 解析/写库/模型异常都不得 500
        error = _error_text(exc)
        page_count = _document_page_count(document_id)
        _mark_document(document_id, "failed", error=error, page_count=page_count)
        finish_build(
            build_id,
            status="failed",
            model=model_label,
            pages_created=created,
            pages_updated=updated,
            error=error,
        )
        result.update(
            pages_created=created,
            pages_updated=updated,
            status="failed",
            error=error,
        )
        result["document"] = _document_json(db.get_document(document_id))
        logger.exception("文档编译失败：%s（%s）", row.get("filename"), error)
        return result


def _compile_unit(unit: dict, doc_title: str, doc_row: dict) -> tuple[list[dict], str | None, bool]:
    """对单个编译单元调用一次 LLM，返回 ``(entries, error, fatal)``。"""
    sections = " / ".join(unit.get("sections") or []) or doc_title
    doc_tags = db.from_json(doc_row.get("tags"), [])
    if not isinstance(doc_tags, list):
        doc_tags = []
    messages = [
        {"role": "system", "content": COMPILE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": COMPILE_USER_TEMPLATE.format(
                doc_title=doc_title,
                doc_tags="、".join(str(tag) for tag in doc_tags) or "（无）",
                sections=sections,
                unit_text=unit["text"],
            ),
        },
    ]
    data, error, fatal = _call_json(
        messages, list, max_tokens=4000, error_prefix="编译词条"
    )
    if isinstance(data, list):
        entries = [entry for entry in data if isinstance(entry, dict)][:3]
        return entries, error, fatal
    return [], error or "模型未返回有效词条", fatal


# ---------------------------------------------------------------------------
# 删除 / 重建
# ---------------------------------------------------------------------------
def remove_pages_for_document(document_id: str) -> int:
    """删除「来源全属该文档」的词条，共享词条只移除该文档来源。

    同时清理孤儿链接并刷新受影响词条的 ``source_count``；返回被删除的词条数。
    """
    page_rows = db.query(
        "SELECT DISTINCT page_id FROM wiki_sources WHERE document_id = ?", (document_id,)
    )
    removed = 0
    for row in page_rows:
        page_id = row["page_id"]
        page = db.get_wiki_page(page_id)
        if not page:
            db.execute("DELETE FROM wiki_sources WHERE page_id = ?", (page_id,))
            continue
        other = db.query_one(
            "SELECT COUNT(DISTINCT document_id) AS n FROM wiki_sources "
            "WHERE page_id = ? AND document_id != ?",
            (page_id, document_id),
        )
        if not other or int(other["n"] or 0) == 0:
            db.execute("DELETE FROM wiki_links WHERE from_page_id = ? OR to_page_id = ?",
                       (page_id, page_id))
            db.execute("DELETE FROM wiki_sources WHERE page_id = ?", (page_id,))
            db.execute("DELETE FROM wiki_pages WHERE id = ?", (page_id,))
            removed += 1
        else:
            db.execute(
                "DELETE FROM wiki_sources WHERE page_id = ? AND document_id = ?",
                (page_id, document_id),
            )
            _refresh_source_count(page_id)

    db.execute(
        "DELETE FROM wiki_links WHERE from_page_id NOT IN (SELECT id FROM wiki_pages) "
        "OR to_page_id NOT IN (SELECT id FROM wiki_pages)"
    )
    logger.info("已清理文档 %s 的独占词条：%s 个", document_id, removed)
    return removed


def _insert_row(table: str, row: dict) -> None:
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    db.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [row[column] for column in columns],
    )


def snapshot_document_pages(document_id: str) -> dict:
    """编译前快照该文档独占的页面/来源/链接，编译失败时用于回滚。"""
    page_ids = [
        row["page_id"]
        for row in db.query(
            "SELECT DISTINCT page_id FROM wiki_sources WHERE document_id = ?", (document_id,)
        )
    ]
    pages: list[dict] = []
    sources: list[dict] = []
    for page_id in page_ids:
        page_row = db.query_one("SELECT * FROM wiki_pages WHERE id = ?", (page_id,))
        if page_row:
            pages.append(dict(page_row))
        sources.extend(
            dict(item)
            for item in db.query("SELECT * FROM wiki_sources WHERE page_id = ?", (page_id,))
        )
    links: list[dict] = []
    if page_ids:
        placeholders = ", ".join("?" for _ in page_ids)
        links = [
            dict(item)
            for item in db.query(
                f"SELECT * FROM wiki_links WHERE from_page_id IN ({placeholders}) "
                f"OR to_page_id IN ({placeholders})",
                [*page_ids, *page_ids],
            )
        ]
    return {"pages": pages, "sources": sources, "links": links}


def restore_document_pages(snapshot: dict) -> int:
    """把快照写回（幂等，按主键 INSERT OR REPLACE），返回恢复的词条数。"""
    restored = 0
    for row in snapshot.get("pages", []):
        _insert_row("wiki_pages", row)
        restored += 1
    for row in snapshot.get("sources", []):
        _insert_row("wiki_sources", row)
    for row in snapshot.get("links", []):
        _insert_row("wiki_links", row)
    return restored


def rebuild_document(document_id: str) -> dict:
    """重新解析并编译单个文档（先移除其独占词条，避免重复）。"""
    row = db.get_document(document_id)
    if not row:
        raise ValueError("文档不存在或已被删除")

    readable, reason = can_compile()
    if not readable:
        error = reason or "未配置可用的真实模型，无法重建 Wiki 词条"
        _mark_document(document_id, "failed", error=error)
        build_id = start_build(document_id, _model_label())
        finish_build(build_id, status="failed", error=error)
        return {
            "document": _document_json(db.get_document(document_id)),
            "pages_created": 0,
            "pages_updated": 0,
            "pages_removed": 0,
            "model": _model_label(),
            "status": "failed",
            "build_id": build_id,
            "error": error,
        }

    snapshot = snapshot_document_pages(document_id)
    removed = remove_pages_for_document(document_id)
    result = compile_document(document_id, force=True)
    result["pages_removed"] = removed
    if str(result.get("status") or "") == "failed":
        restored = restore_document_pages(snapshot)
        result["pages_restored"] = restored
        logger.warning(
            "文档 %s 重建失败，已回滚 %s 个原有词条（避免数据丢失）", document_id, restored
        )
    build_id = result.get("build_id")
    if build_id:
        db.execute(
            "UPDATE wiki_builds SET pages_removed = ? WHERE id = ?", (removed, build_id)
        )
    return result


def rebuild_all() -> dict:
    """重建全部源文件的 Wiki 词条（每个文档独立失败，不互相阻塞）。"""
    documents = db.query("SELECT id FROM documents ORDER BY created_at, rowid")
    totals = {
        "documents": len(documents),
        "pages_created": 0,
        "pages_updated": 0,
        "pages_removed": 0,
        "failed_documents": 0,
        "model": _model_label(),
    }

    readable, reason = can_compile()
    if not readable and documents:
        # 模型不可用时不改动现有文档状态：保留已导入/已编译的词条可浏览，
        # 只把整体任务标记为失败，等管理员配置好模型后再重建。
        error = reason or "未配置可用的真实模型，无法重建 Wiki 词条"
        totals.update(status="failed", error=error)
        return totals

    for row in documents:
        try:
            result = rebuild_document(row["id"])
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.exception("重建文档 %s 失败", row["id"])
            totals["failed_documents"] += 1
            continue
        totals["pages_created"] += int(result.get("pages_created") or 0)
        totals["pages_updated"] += int(result.get("pages_updated") or 0)
        totals["pages_removed"] += int(result.get("pages_removed") or 0)
        if result.get("status") != "ready":
            totals["failed_documents"] += 1
    totals["status"] = "ready"
    if totals["failed_documents"]:
        totals["error"] = f"{totals['failed_documents']} 篇文档编译失败，请查看文档状态与错误信息"
    return totals


# ---------------------------------------------------------------------------
# 预编译快照：导出 / 导入（幂等）
# ---------------------------------------------------------------------------
def export_seed_wiki(path: str | Path) -> dict:
    """把当前库的 Wiki 快照导出为 JSON（供 ``scripts/build_wiki_seed.py``）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    documents = []
    for row in db.query("SELECT * FROM documents ORDER BY created_at, rowid"):
        tags = db.from_json(row.get("tags"), [])
        documents.append(
            {
                "id": row["id"],
                "title": row["title"],
                "filename": row["filename"],
                "file_ext": row["file_ext"],
                "size_bytes": int(row.get("size_bytes") or 0),
                "tags": tags if isinstance(tags, list) else [],
                "char_count": int(row.get("char_count") or 0),
                "page_count": int(row.get("page_count") or 0),
                "status": row.get("status") or "ready",
                "source_type": row.get("source_type") or "file",
                "source_url": row.get("source_url") or "",
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )

    pages = []
    for row in db.query("SELECT * FROM wiki_pages ORDER BY created_at, rowid"):
        page = _page_dict(row)
        if page:
            pages.append(page)

    sources = []
    for row in db.query("SELECT * FROM wiki_sources ORDER BY created_at, rowid"):
        sources.append(
            {
                "page_id": row["page_id"],
                "document_id": row["document_id"],
                "document_title": row.get("document_title") or "",
                "source_file": row.get("source_file") or "",
                "section": row.get("section"),
                "quote": row.get("quote") or "",
                "created_at": row.get("created_at"),
            }
        )

    links = [
        {
            "from_page_id": row["from_page_id"],
            "to_page_id": row["to_page_id"],
            "relation": row.get("relation") or "related",
        }
        for row in db.query("SELECT * FROM wiki_links ORDER BY rowid")
    ]

    payload = {
        "version": 1,
        "generated_at": db.now(),
        "documents": documents,
        "pages": pages,
        "sources": sources,
        "links": links,
    }
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(target)
    summary = {
        "path": str(target),
        "documents": len(documents),
        "pages": len(pages),
        "sources": len(sources),
        "links": len(links),
    }
    logger.info("已导出 Wiki 快照：%s", summary)
    return summary


def _path_inside(path: str | Path, root: str | Path) -> bool:
    """判断 ``path`` 是否位于 ``root`` 目录之内（Windows 下不区分大小写）。"""
    try:
        candidate = Path(path).resolve()
        base = Path(root).resolve()
    except OSError:  # pragma: no cover - 极端路径异常
        return False
    candidate_text = os.path.normcase(str(candidate))
    base_text = os.path.normcase(str(base))
    return candidate_text == base_text or candidate_text.startswith(base_text + os.sep)


def _copy_seed_source(document_id: str, filename: str) -> str:
    """把仓库内置种子原文复制到上传目录，返回副本路径（可安全删除 / reindex）。

    安全约束：

    * 只取 ``filename`` 的 basename，防止快照里的路径穿越；
    * 副本已存在（``<document_id><ext>``）时直接复用，保证重复导入不重复复制；
    * 仓库内没有该原文时返回空字符串——词条仍可浏览，只是不能 reindex。
    """
    source = config.SEED_DOCS_DIR / Path(str(filename or "")).name
    try:
        if not source.is_file():
            return ""
        config.ensure_dirs()
        ext = source.suffix.lower() or Path(str(filename or "")).suffix.lower()
        target = config.UPLOAD_DIR / f"{document_id}{ext}"
        if not target.is_file():
            # 先写临时文件再原子替换，避免并发导入时两个请求写同一副本造成半截文件
            temp_target = config.UPLOAD_DIR / f".{target.name}.tmp-{os.getpid()}-{db.new_id()[:8]}"
            try:
                shutil.copyfile(source, temp_target)
                os.replace(temp_target, target)
            finally:
                if temp_target.exists():
                    temp_target.unlink(missing_ok=True)
            logger.info("已复制种子原文到上传目录：%s -> %s", source.name, target.name)
        return str(target)
    except OSError as exc:
        logger.warning("复制种子原文失败：%s（%s）", filename, exc)
        return ""


def _repair_seed_document_path(row: dict, filename: str) -> None:
    """修复旧版快照导入留下的存储路径（指向仓库种子目录或已失效）。

    把内置原文复制到上传目录并更新 ``documents.stored_path``；若已经是上传目录里
    的有效副本则不动，因此可安全重复调用。
    """
    stored = str(row.get("stored_path") or "").strip()
    if stored and not _path_inside(stored, config.SEED_DOCS_DIR) and Path(stored).is_file():
        return
    copy_path = _copy_seed_source(row["id"], filename)
    if copy_path and copy_path != stored:
        db.execute(
            "UPDATE documents SET stored_path = ? WHERE id = ?", (copy_path, row["id"])
        )
        logger.info("已修复种子文档存储路径：%s -> %s", filename, copy_path)


def repair_seed_document_paths() -> int:
    """把全部旧版种子文档的 ``stored_path`` 迁移到 uploads 副本（幂等）。

    适用于修复「升级前已经导入了快照、因此启动时不会重新 seed」的开发/用户库。
    返回本次实际修复的文档数；对普通上传文档（路径在 uploads 且文件存在）无操作。
    """
    repaired = 0
    try:
        rows = db.query("SELECT * FROM documents")
    except Exception:  # pragma: no cover - 数据库异常不应影响列表接口
        logger.exception("读取文档列表失败，跳过种子路径修复")
        return 0
    for row in rows:
        filename = str(row.get("filename") or "").strip()
        if not filename:
            continue
        source = config.SEED_DOCS_DIR / Path(filename).name
        try:
            if not source.is_file():
                continue
        except OSError:  # pragma: no cover
            continue
        stored = str(row.get("stored_path") or "").strip()
        needs_repair = (
            not stored
            or _path_inside(stored, config.SEED_DOCS_DIR)
            or not Path(stored).is_file()
        )
        if not needs_repair:
            continue
        _repair_seed_document_path(row, filename)
        if str(db.get_document(row["id"]).get("stored_path") or "") != stored:
            repaired += 1
    if repaired:
        logger.info("已迁移 %s 篇种子文档到安全的上传目录副本", repaired)
    return repaired



def load_seed_wiki() -> dict:
    """导入 ``backend/data/wiki_seed.json`` 预编译快照（幂等）。

    幂等键：documents 按 id/filename、pages 按 id/slug、sources 按
    (page_id, document_id, section, quote)、links 按唯一约束。
    """
    result: dict[str, Any] = {
        "imported": False,
        "documents": 0,
        "pages": 0,
        "sources": 0,
        "skipped": 0,
    }
    path = config.WIKI_SEED_PATH
    if not path.is_file():
        result["message"] = f"未找到预编译 Wiki 快照：{path}"
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.exception("读取 Wiki 快照失败：%s", path)
        result["message"] = f"Wiki 快照不是有效 JSON：{_error_text(exc)}"
        return result
    if not isinstance(payload, dict):
        result["message"] = "Wiki 快照结构异常：期望 JSON 对象"
        return result

    try:
        result = _import_seed_payload(payload)
    except Exception as exc:  # pragma: no cover - 快照异常不影响主流程
        logger.exception("导入 Wiki 快照失败")
        result["message"] = f"导入 Wiki 快照失败：{_error_text(exc)}"
        result["imported"] = bool(result.get("pages") or result.get("documents"))
    if result.get("imported"):
        logger.info("Wiki 快照导入完成：%s", result)
    return result


def _import_seed_payload(payload: dict) -> dict:
    inserted_documents = 0
    inserted_pages = 0
    inserted_sources = 0
    skipped = 0

    existing_doc_ids = {row["id"] for row in db.query("SELECT id FROM documents")}
    existing_filenames = {
        row["filename"] for row in db.query("SELECT filename FROM documents") if row.get("filename")
    }
    doc_id_map: dict[str, str] = {}

    for doc in payload.get("documents") or []:
        if not isinstance(doc, dict):
            skipped += 1
            continue
        filename = str(doc.get("filename") or "").strip()
        source_id = str(doc.get("id") or "").strip()
        if not filename:
            skipped += 1
            continue
        if source_id in existing_doc_ids or filename in existing_filenames:
            skipped += 1
            row = None
            if source_id in existing_doc_ids:
                row = db.query_one("SELECT * FROM documents WHERE id = ?", (source_id,))
            if row is None:
                row = db.query_one("SELECT * FROM documents WHERE filename = ?", (filename,))
            if row is not None:
                if source_id:
                    doc_id_map[source_id] = row["id"]
                else:
                    doc_id_map[filename] = row["id"]
                # 旧版快照可能把 stored_path 指向仓库种子文件：迁移到上传目录副本，
                # 避免前端删除源文件时误删仓库内容；已迁移过则无操作（幂等）。
                _repair_seed_document_path(row, filename)
            continue
        new_id = source_id if source_id and source_id not in existing_doc_ids else db.new_id()
        title = str(doc.get("title") or Path(filename).stem or filename)
        tags = doc.get("tags") if isinstance(doc.get("tags"), list) else []
        status = str(doc.get("status") or "ready")
        if status not in {"pending", "processing", "ready", "failed"}:
            status = "ready"
        timestamp = str(doc.get("created_at") or db.now())
        stored_path = _copy_seed_source(new_id, filename)
        db.execute(
            "INSERT INTO documents(id, title, filename, stored_path, file_ext, size_bytes, status, "
            "error, tags, char_count, page_count, source_type, source_url, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                title,
                filename,
                stored_path,
                str(doc.get("file_ext") or Path(filename).suffix or ""),
                int(doc.get("size_bytes") or 0),
                status,
                db.to_json([str(tag) for tag in tags if str(tag).strip()]),
                int(doc.get("char_count") or 0),
                int(doc.get("page_count") or 0),
                str(doc.get("source_type") or "file"),
                str(doc.get("source_url") or ""),
                timestamp,
                str(doc.get("updated_at") or timestamp),
            ),
        )
        if source_id:
            doc_id_map[source_id] = new_id
        existing_doc_ids.add(new_id)
        existing_filenames.add(filename)
        inserted_documents += 1

    existing_page_ids = {row["id"] for row in db.query("SELECT id FROM wiki_pages")}
    existing_slugs = {row["slug"] for row in db.query("SELECT slug FROM wiki_pages")}
    page_id_map: dict[str, str] = {}

    for page in payload.get("pages") or []:
        if not isinstance(page, dict):
            skipped += 1
            continue
        title = str(page.get("title") or "").strip()
        source_id = str(page.get("id") or "").strip()
        slug = str(page.get("slug") or "").strip() or text_utils.slugify(title)
        if not title:
            skipped += 1
            continue
        if source_id in existing_page_ids or slug in existing_slugs:
            skipped += 1
            if source_id in existing_page_ids:
                page_id_map[source_id] = source_id
            else:
                row = db.get_wiki_page_by_slug(slug)
                if row and source_id:
                    page_id_map[source_id] = row["id"]
            continue
        new_id = source_id if source_id and source_id not in existing_page_ids else db.new_id()
        aliases = page.get("aliases") if isinstance(page.get("aliases"), list) else []
        tags = page.get("tags") if isinstance(page.get("tags"), list) else []
        status = str(page.get("status") or "ready")
        if status not in {"ready", "needs_review"}:
            status = "ready"
        timestamp = str(page.get("created_at") or db.now())
        db.execute(
            "INSERT INTO wiki_pages(id, slug, title, summary, body_md, aliases, tags, status, "
            "version, source_count, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                slug,
                title,
                str(page.get("summary") or ""),
                str(page.get("body_md") or ""),
                db.to_json([str(alias) for alias in aliases if str(alias).strip()]),
                db.to_json([str(tag) for tag in tags if str(tag).strip()]),
                status,
                int(page.get("version") or 1),
                int(page.get("source_count") or 0),
                timestamp,
                str(page.get("updated_at") or timestamp),
            ),
        )
        if source_id:
            page_id_map[source_id] = new_id
        existing_page_ids.add(new_id)
        existing_slugs.add(slug)
        inserted_pages += 1

    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            skipped += 1
            continue
        page_key = str(source.get("page_id") or "").strip()
        doc_key = str(source.get("document_id") or "").strip()
        page_id = page_id_map.get(page_key, page_key)
        document_id = doc_id_map.get(doc_key, doc_key)
        if not page_id or not document_id:
            skipped += 1
            continue
        if not db.get_wiki_page(page_id) or not db.get_document(document_id):
            skipped += 1
            continue
        section = source.get("section")
        quote = str(source.get("quote") or "")
        duplicate = db.query_one(
            "SELECT id FROM wiki_sources WHERE page_id = ? AND document_id = ? "
            "AND COALESCE(section, '') = ? AND quote = ?",
            (page_id, document_id, section or "", quote),
        )
        if duplicate:
            skipped += 1
            continue
        db.execute(
            "INSERT INTO wiki_sources(id, page_id, document_id, document_title, source_file, "
            "section, quote, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                db.new_id(),
                page_id,
                document_id,
                str(source.get("document_title") or ""),
                str(source.get("source_file") or ""),
                section,
                quote,
                str(source.get("created_at") or db.now()),
            ),
        )
        inserted_sources += 1

    for link in payload.get("links") or []:
        if not isinstance(link, dict):
            skipped += 1
            continue
        from_key = str(link.get("from_page_id") or "").strip()
        to_key = str(link.get("to_page_id") or "").strip()
        from_id = page_id_map.get(from_key, from_key)
        to_id = page_id_map.get(to_key, to_key)
        if (
            not from_id
            or not to_id
            or from_id == to_id
            or not db.get_wiki_page(from_id)
            or not db.get_wiki_page(to_id)
        ):
            skipped += 1
            continue
        existing = db.query_one(
            "SELECT id FROM wiki_links WHERE from_page_id = ? AND to_page_id = ?",
            (from_id, to_id),
        )
        if existing:
            skipped += 1
            continue
        db.execute(
            "INSERT OR IGNORE INTO wiki_links(id, from_page_id, to_page_id, relation) "
            "VALUES(?, ?, ?, ?)",
            (db.new_id(), from_id, to_id, str(link.get("relation") or "related")),
        )

    # 依据实际导入的 sources 重算 page_count / source_count，保持数据自洽
    for row in db.query(
        "SELECT d.id, (SELECT COUNT(DISTINCT s.page_id) FROM wiki_sources s "
        "WHERE s.document_id = d.id) AS n FROM documents d"
    ):
        db.execute(
            "UPDATE documents SET page_count = ? WHERE id = ?",
            (int(row["n"] or 0), row["id"]),
        )
    db.execute(
        "UPDATE wiki_pages SET source_count = "
        "(SELECT COUNT(*) FROM wiki_sources WHERE page_id = wiki_pages.id)"
    )

    return {
        "imported": bool(inserted_documents or inserted_pages),
        "documents": inserted_documents,
        "pages": inserted_pages,
        "sources": inserted_sources,
        "skipped": skipped,
    }


__all__ = [
    "can_compile",
    "build_units",
    "normalize_entry",
    "compile_document",
    "rebuild_all",
    "rebuild_document",
    "remove_pages_for_document",
    "load_seed_wiki",
    "export_seed_wiki",
    "start_build",
    "finish_build",
]

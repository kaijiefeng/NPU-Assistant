"""对话编排服务：检索 → 提示词 → LLM / 降级 → 落库。

对外只暴露 :func:`answer`，供 ``/api/chat`` 与机器人网关（bot-gateway）共用。
实现要点：

1. 完全以设置表为准（``counselor_name`` / ``system_prompt`` / ``retrieval_top_k`` /
   ``retrieval_min_score`` / ``citations_enabled``）。
2. 知识库检索通过 ``services.rag`` 的 ``search`` / ``format_context`` 完成，采用惰性
   导入 + 异常兜底，保证 kb-rag 未就绪时本模块仍可独立运行。
3. LLM 不可用、调用失败或返回内容为空时，降级为「抽取式回答」（``fallback=true``），
   从检索切片中摘取要点并标注 ``[n]``，绝不抛 500。
4. 知识库无命中时给出礼貌兜底 + 咨询渠道，``citations=[]``。
5. ``citations`` 的 ``index`` 与回答中的 ``[n]`` 严格一致，且只包含真正提供了上下文的切片。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from .. import config, db
from . import llm

logger = logging.getLogger("npu-assistant")

MAX_QUESTION_CHARS = 2000
SNIPPET_MAX_CHARS = 200
EXTRACT_POINT_LIMIT = 3
CITATION_RE = re.compile(r"\[(\d{1,3})\]")
CONSULT_CHANNELS = "辅导员本人、学院学工办或学校相关职能部门"
FALLBACK_TIP = "（说明：当前未接入可用的在线大模型，以上内容由知识库原文摘录生成。）"

_rag_warning_logged = False


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，,、;； ") + "…"


def _make_snippet(text: str) -> str:
    return _shorten(text, SNIPPET_MAX_CHARS)


def _load_rag():
    """惰性导入知识库服务；kb-rag 未就绪时返回 ``None``（按无命中处理）。"""
    global _rag_warning_logged
    try:
        from . import rag  # 惰性导入：避免与 kb-rag 并行开发时互相阻塞

        return rag
    except Exception:
        if not _rag_warning_logged:
            logger.warning("知识库检索模块（services.rag）尚不可用，问答将按「无命中」处理")
            _rag_warning_logged = True
        return None


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
def _settings() -> dict[str, Any]:
    def setting(key: str, default: str) -> str:
        try:
            return db.get_setting(key, default) or default
        except Exception:  # pragma: no cover - 数据库异常时退回代码默认值
            return default

    top_k = _as_int(setting("retrieval_top_k", str(config.DEFAULT_TOP_K)), config.DEFAULT_TOP_K)
    top_k = max(1, min(top_k, 10))
    min_score = max(0.0, _as_float(setting("retrieval_min_score", "0"), 0.0))
    try:
        citations_enabled = db.get_bool_setting("citations_enabled", True)
    except Exception:  # pragma: no cover
        citations_enabled = True
    return {
        "counselor_name": setting("counselor_name", config.DEFAULT_COUNSELOR_NAME),
        "system_prompt": setting("system_prompt", config.DEFAULT_SYSTEM_PROMPT),
        "top_k": top_k,
        "min_score": min_score,
        "citations_enabled": citations_enabled,
    }


# ---------------------------------------------------------------------------
# 会话与历史
# ---------------------------------------------------------------------------
def _resolve_conversation(
    conversation_id: str | None,
    channel: str,
    session_key: str,
    user_label: str | None,
) -> dict:
    if conversation_id:
        conversation = db.get_conversation(conversation_id)
        if conversation is not None:
            if user_label and conversation.get("user_label") != user_label:
                db.execute(
                    "UPDATE conversations SET user_label = ?, updated_at = ? WHERE id = ?",
                    (user_label, db.now(), conversation["id"]),
                )
            else:
                db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (db.now(), conversation["id"]),
                )
            return conversation
        # 传入的会话已被删除：退化为按 session_key 新建，避免打断用户提问
        logger.info("会话 %s 不存在，按 session_key 新建会话", conversation_id)
    return db.find_or_create_conversation(channel, session_key, None, user_label)


def _history(conversation_id: str) -> list[dict[str, str]]:
    """取最近 N 条历史消息（升序），用于多轮对话。"""
    try:
        rows = db.query(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, config.MAX_HISTORY_MESSAGES),
        )
    except Exception:  # pragma: no cover
        logger.exception("读取会话历史失败")
        return []
    rows.reverse()
    history: list[dict[str, str]] = []
    for row in rows:
        role = str(row.get("role") or "")
        content = str(row.get("content") or "")
        if role in {"user", "assistant"} and content.strip():
            history.append({"role": role, "content": content})
    return history


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
def _fetch_chunks(question: str, top_k: int, min_score: float) -> list[dict]:
    """调用知识库检索并做防御性归一化；任何异常都按“无命中”处理。"""
    rag = _load_rag()
    if rag is None:
        return []
    try:
        try:
            raw_results = rag.search(question, top_k=top_k)
        except TypeError:  # 兼容更简化的 search(query) 实现
            raw_results = rag.search(question)
    except Exception:
        logger.exception("知识库检索失败，本次按无命中处理")
        return []

    chunks: list[dict] = []
    seen: set[str] = set()
    for item in raw_results or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("snippet") or "").strip()
        if not content:
            continue
        chunk_id = str(item.get("chunk_id") or "")
        if chunk_id and chunk_id in seen:
            continue
        score_value = item.get("score")
        score = _as_float(score_value, 0.0)
        if score_value is not None and min_score > 0 and score < min_score:
            continue
        chunk = {
            "chunk_id": chunk_id,
            "document_id": str(item.get("document_id") or ""),
            "document_title": str(item.get("document_title") or item.get("title") or "未命名文档"),
            "section": str(item.get("section") or "").strip(),
            "content": content,
            "snippet": str(item.get("snippet") or "").strip() or _make_snippet(content),
            "score": round(score, 4),
        }
        if chunk_id:
            seen.add(chunk_id)
        chunks.append(chunk)
        if len(chunks) >= top_k:
            break
    return chunks


def _build_context(chunks: list[dict]) -> str:
    """把检索结果格式化为带 ``【n】`` 编号的知识库资料文本。"""
    if not chunks:
        return ""
    rag = _load_rag()
    formatter = getattr(rag, "format_context", None) if rag is not None else None
    if callable(formatter):
        try:
            try:
                context = formatter(chunks, max_chars=config.MAX_CONTEXT_CHARS)
            except TypeError:
                context = formatter(chunks)
        except Exception:
            logger.exception("格式化知识库上下文失败，改用内置格式")
            context = ""
        if isinstance(context, str) and context.strip():
            return context.strip()
    return _format_context_fallback(chunks)


def _format_context_fallback(chunks: list[dict]) -> str:
    """内置上下文格式（rag.format_context 不可用时兜底）。"""
    remaining = config.MAX_CONTEXT_CHARS
    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        head = f"【{index}】《{chunk.get('document_title') or '未命名文档'}》"
        if chunk.get("section"):
            head += f" · {chunk['section']}"
        body = str(chunk.get("content") or "")[:1200]
        block = f"{head}\n{body}"
        if len(block) > remaining:
            if remaining <= len(head):
                break
            block = block[:remaining]
        blocks.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    if not blocks:
        return ""
    return "【知识库资料】\n\n" + "\n\n".join(blocks)


def _build_system_prompt(settings: dict[str, Any], context: str, has_chunks: bool) -> str:
    base = str(settings.get("system_prompt") or config.DEFAULT_SYSTEM_PROMPT).strip()
    parts = [base]
    if has_chunks and context:
        parts.append(
            "下面是本次检索到的【知识库资料】，请严格以这些资料为依据回答学生问题：\n\n" + context
        )
        parts.append(
            "引用资料时请在句末标注来源编号，例如 [1]、[2]；编号必须与上文资料编号严格一致，"
            "不要编造不存在的编号。资料没有覆盖的内容不要猜测，可建议学生咨询辅导员。"
        )
    else:
        parts.append(
            "本次没有检索到与问题相关的【知识库资料】。请礼貌说明「知识库暂未收录」，"
            f"并建议学生通过以下咨询渠道确认：{CONSULT_CHANNELS}。不要编造政策细节。"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 抽取式降级回答
# ---------------------------------------------------------------------------
def _keywords(text: str) -> list[str]:
    try:
        from .retrieval_core import tokenize  # 复用检索侧分词，保持口径一致

        tokens = tokenize(text)
    except Exception:  # pragma: no cover - 分词不可用时的正则兜底
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{2,}", str(text or ""))
    unique: list[str] = []
    for token in tokens or []:
        token = str(token).strip().lower()
        if len(token) >= 2 and token not in unique:
            unique.append(token)
    return unique[:20]


def _extract_points(content: str, question: str, limit: int = EXTRACT_POINT_LIMIT) -> list[str]:
    """从切片正文中摘取与问题最相关的句子作为要点。"""
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[。！？；!?;])", text) if s.strip()]
    if not sentences:
        sentences = [text]
    keywords = _keywords(question)
    scored: list[tuple[float, int, str]] = []
    for position, sentence in enumerate(sentences):
        overlap = sum(1 for keyword in keywords if keyword in sentence.lower())
        scored.append((overlap * 2.0 - position * 0.1, position, sentence))
    scored.sort(key=lambda item: item[0], reverse=True)
    picked = sorted(scored[:limit], key=lambda item: item[1])
    points: list[str] = []
    for _, _, sentence in picked:
        sentence = sentence.strip(" 　")
        if not sentence:
            continue
        if len(sentence) > 140:
            sentence = sentence[:140].rstrip("，,、 ") + "…"
        points.append(sentence)
    return points


def _first_sentence(content: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return ""
    match = re.split(r"(?<=[。！？!?])", text, maxsplit=1)
    return match[0].strip() if match else text[:120]


def _extractive_reply(
    question: str, chunks: list[dict]
) -> tuple[str, list[dict]]:
    """基于检索切片生成抽取式回答，返回 ``(reply, used_chunks)``。

    ``used_chunks`` 的顺序即回答中 ``[1..m]`` 的编号顺序，保证引用编号一致。
    """
    intro = f"关于「{_shorten(question, 40)}」，我在知识库中查到以下内容："
    blocks: list[str] = []
    used_chunks: list[dict] = []
    for chunk in chunks:
        points = _extract_points(chunk.get("content") or "", question)
        if not points:
            sentence = _first_sentence(chunk.get("content") or "")
            points = [sentence] if sentence else []
        if not points:
            continue
        index = len(used_chunks) + 1
        used_chunks.append(chunk)
        label = f"《{chunk.get('document_title') or '未命名文档'}》"
        if chunk.get("section"):
            label += f"·{chunk['section']}"
        body = "\n".join(f"- {point} [{index}]" for point in points)
        blocks.append(f"{label}：\n{body}")
    if not used_chunks:
        return "", []
    reply = intro + "\n\n" + "\n\n".join(blocks)
    reply += "\n\n以上内容摘自知识库原文，具体办理请以学校最新官方通知为准。"
    reply += f"\n{FALLBACK_TIP}"
    reply += f"\n如需进一步确认，可联系{CONSULT_CHANNELS}。"
    return reply, used_chunks


def _no_hit_reply(settings: dict[str, Any]) -> str:
    counselor_name = str(settings.get("counselor_name") or config.DEFAULT_COUNSELOR_NAME)
    return (
        "抱歉，知识库暂未收录与这个问题直接相关的资料，我暂时无法给出可靠答复，"
        "也不想给你编造政策细节。\n\n"
        "你可以试试：\n"
        "1. 换一种说法再问我一次，补充更具体的关键词（如事项类型、时间、学院）；\n"
        f"2. 通过以下咨询渠道确认：{CONSULT_CHANNELS}；\n"
        "3. 查看学校官网或学生手册上的最新通知。\n\n"
        f"我是辅导员数字分身「{counselor_name}」。如果涉及心理危机、人身安全等紧急情况，"
        "请立即联系辅导员本人，或拨打学校保卫处、心理中心电话（紧急医疗请拨打 120）。"
    )


# ---------------------------------------------------------------------------
# 引用
# ---------------------------------------------------------------------------
def _citation(index: int, chunk: dict) -> dict:
    return {
        "index": index,
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "document_id": str(chunk.get("document_id") or ""),
        "document_title": str(chunk.get("document_title") or "未命名文档"),
        "section": str(chunk.get("section") or ""),
        "snippet": _make_snippet(str(chunk.get("snippet") or chunk.get("content") or "")),
        "score": round(_as_float(chunk.get("score"), 0.0), 4),
    }


def _strip_citation_marks(text: str) -> str:
    return re.sub(r"\s*\[\d{1,3}\]", "", str(text or ""))


def _attach_citations(
    reply: str, chunks: list[dict], citations_enabled: bool
) -> tuple[str, list[dict]]:
    """对齐回答中的 ``[n]`` 与 citations，只返回真正被引用的切片。"""
    reply = str(reply or "")
    if not chunks:
        return reply, []
    if not citations_enabled:
        return _strip_citation_marks(reply), []

    indexes = sorted({int(match) for match in CITATION_RE.findall(reply)})
    valid = [index for index in indexes if 1 <= index <= len(chunks)]
    if valid:
        return reply, [_citation(index, chunks[index - 1]) for index in valid]

    # 模型没有标注编号：追加「参考来源」小节，保证引用可核对、编号与回答一致
    lines = ["", "参考来源："]
    for index, chunk in enumerate(chunks, start=1):
        label = f"[{index}] 《{chunk.get('document_title') or '未命名文档'}》"
        if chunk.get("section"):
            label += f" · {chunk['section']}"
        lines.append(label)
    citations = [_citation(index, chunk) for index, chunk in enumerate(chunks, start=1)]
    return reply.rstrip() + "\n" + "\n".join(lines), citations


# ---------------------------------------------------------------------------
# 主入口（跨模块契约，见 docs/CONTRACT.md §6）
# ---------------------------------------------------------------------------
def answer(
    question: str,
    *,
    channel: str = "web",
    session_key: str = "web:default",
    conversation_id: str | None = None,
    user_label: str | None = None,
) -> dict:
    """完整问答链路（检索 → 提示词 → LLM/降级 → 落库）。

    返回 ``{conversation_id, reply, citations, model, provider_type, latency_ms,
    fallback, sources_used}``；参数非法时抛 ``ValueError``（路由层转换为 400）。
    """
    started = time.perf_counter()
    text = (question or "").strip()
    if not text:
        raise ValueError("请输入问题内容")
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(f"问题内容过长（最多 {MAX_QUESTION_CHARS} 字）")

    settings = _settings()
    channel = (channel or "web").strip() or "web"
    session_key = (session_key or f"{channel}:default").strip() or f"{channel}:default"

    conversation = _resolve_conversation(conversation_id, channel, session_key, user_label)
    history = _history(conversation["id"])
    chunks = _fetch_chunks(text, settings["top_k"], settings["min_score"])

    model_label = ""
    provider_type = ""
    try:
        default_model = llm.default_model()
    except Exception:  # pragma: no cover - LLM 层异常不应影响回答生成
        logger.exception("读取默认模型失败，将按无模型降级")
        default_model = None
    if default_model:
        model_label = default_model.get("display_name") or default_model.get("model_name") or ""
        provider_type = (default_model.get("provider_type") or "").strip().lower()

    citations: list[dict] = []
    fallback_used = False

    if not chunks:
        # 无命中：礼貌兜底 + 咨询渠道，不调用 LLM，避免编造
        reply = _no_hit_reply(settings)
        fallback_used = True
    else:
        context = _build_context(chunks)
        messages: list[dict] = [
            {"role": "system", "content": _build_system_prompt(settings, context, bool(context))}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": text})

        try:
            result = llm.chat(
                messages,
                temperature=None,
                max_tokens=None,
            )
        except Exception:
            # llm.chat 契约上不抛异常；这里再兜一层，确保任何情况下都能降级而不是 500
            logger.exception("LLM 调用抛出异常，降级为抽取式回答")
            result = {}
        if not isinstance(result, dict):
            result = {}
        content = str(result.get("content") or "").strip()
        if result.get("fallback") or not content:
            reply, used_chunks = _extractive_reply(text, chunks)
            if not used_chunks:
                reply = _no_hit_reply(settings)
                fallback_used = True
            else:
                fallback_used = True
                reply, citations = _attach_citations(
                    reply, used_chunks, settings["citations_enabled"]
                )
        else:
            reply = content
            fallback_used = False
            if result.get("model"):
                model_label = str(result["model"])
            result_type = (result.get("provider_type") or "").strip().lower()
            if result_type:
                provider_type = result_type
            reply, citations = _attach_citations(reply, chunks, settings["citations_enabled"])

    if not model_label:
        model_label = "本地知识库摘录（未配置模型）"
    if not provider_type:
        provider_type = "none"

    latency_ms = int((time.perf_counter() - started) * 1000)

    # 持久化：user 在前、assistant 在后，并维护 conversations.updated_at
    try:
        db.add_message(conversation["id"], "user", text)
        db.add_message(
            conversation["id"],
            "assistant",
            reply,
            citations=citations,
            model=model_label,
            latency_ms=latency_ms,
        )
    except Exception:
        logger.exception("保存问答消息失败（不影响本次回复）")

    return {
        "conversation_id": conversation["id"],
        "reply": reply,
        "citations": citations,
        "model": model_label,
        "provider_type": provider_type,
        "latency_ms": latency_ms,
        "fallback": fallback_used,
        "sources_used": len(citations),
    }

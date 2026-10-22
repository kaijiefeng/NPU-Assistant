"""对话编排服务（LLM Wiki 版）：选词条 → 读上下文 → LLM / 降级 → 双层引用 → 落库。

对外只暴露 :func:`answer`，供 ``/api/chat`` 与机器人网关（bot-gateway）共用。
实现要点（见 ``docs/WIKI-DESIGN.md`` §3.3）：

1. 通过 ``services.wiki_nav`` 选择 1–3 个 Wiki 词条（exact / keyword / LLM 选页），
   **不再有切片检索、embedding 或 rerank**；
2. 只把选中的词条正文 / 摘要 / 来源拼成 ``[n]`` 上下文交给模型，要求模型只依据
   词条回答并标注 ``[n]``；
3. 模型失败 / mock 模型时降级为「词条摘要 + 要点摘录」（``fallback=true``），
   仍带 ``[n]`` 与 citations，绝不 500；
4. 无命中时礼貌说明 Wiki 暂未收录并给咨询渠道，``citations=[]``，不调用 LLM；
5. citations 为「词条级 + 来源级」结构，引用编号与回答中的 ``[n]`` 严格对齐。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from .. import config, db
from . import llm, text_utils, wiki_nav

logger = logging.getLogger("npu-assistant")

MAX_QUESTION_CHARS = 2000
SNIPPET_MAX_CHARS = 200
EXTRACT_POINT_LIMIT = 2
CONSULT_CHANNELS = "辅导员本人、学院学工办或学校相关职能部门"
FALLBACK_TIP = "（说明：当前未接入可用的在线大模型，以上内容由 Wiki 词条摘录生成。）"

_MARKDOWN_NOISE_RE = re.compile(
    r"(\*\*|__|`|^#{1,6}[ \t]*|^[ \t]{0,3}>[ \t]?)", re.MULTILINE
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _setting_int(key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(db.get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _settings() -> dict[str, Any]:
    def setting(key: str, default: str) -> str:
        try:
            return db.get_setting(key, default) or default
        except Exception:  # pragma: no cover - 数据库异常时退回代码默认值
            return default

    try:
        citations_enabled = db.get_bool_setting("citations_enabled", True)
    except Exception:  # pragma: no cover
        citations_enabled = True
    return {
        "counselor_name": setting("counselor_name", config.DEFAULT_COUNSELOR_NAME),
        "system_prompt": setting("system_prompt", config.DEFAULT_SYSTEM_PROMPT),
        "max_pages": _setting_int("wiki_max_pages", config.WIKI_MAX_PAGES, 1, 5),
        "answer_max_chars": _setting_int(
            "wiki_answer_max_chars", config.WIKI_CONTEXT_MAX_CHARS, 2000, 20000
        ),
        "citations_enabled": citations_enabled,
    }


def _shorten(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip("，,、;；:： ") + "…"


def _plain_body(text: Any) -> str:
    value = _MARKDOWN_NOISE_RE.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", value).strip()


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
    max_history = int(getattr(config, "MAX_HISTORY_MESSAGES", 8) or 8)
    try:
        rows = db.query(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, max_history),
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
# 提示词
# ---------------------------------------------------------------------------
def _system_prompt(settings: dict[str, Any], context: str) -> str:
    base = str(settings.get("system_prompt") or config.DEFAULT_SYSTEM_PROMPT).strip()
    parts = [base]
    if context:
        parts.append(
            "下面是本次从校园 Wiki 中选中的【Wiki 词条】，编号即引用编号。"
            "请严格以这些词条为依据回答学生问题：\n\n" + context
        )
        parts.append(
            "引用词条时请在句末标注编号，例如 [1]、[2]；编号必须与【Wiki 词条】编号严格一致，"
            "不要编造不存在的编号。词条没有覆盖的内容不要猜测，可建议学生咨询辅导员或相关职能部门。"
        )
    else:
        parts.append(
            "本次没有选中与问题相关的【Wiki 词条】。请礼貌说明「校园 Wiki 暂未收录」，"
            f"并建议学生通过以下咨询渠道确认：{CONSULT_CHANNELS}。不要编造政策细节。"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 抽取式降级回答
# ---------------------------------------------------------------------------
def _extract_points(content: str, question: str, limit: int = EXTRACT_POINT_LIMIT) -> list[str]:
    """从词条正文中摘取与问题最相关的句子作为要点。"""
    text = _plain_body(content)
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[。！？；!?;])", text) if s.strip()]
    if not sentences:
        sentences = [text]
    keywords = [token for token in text_utils.tokenize(question) if len(token) >= 2][:20]
    scored: list[tuple[float, int, str]] = []
    for position, sentence in enumerate(sentences):
        overlap = sum(1 for keyword in keywords if keyword in sentence.lower())
        scored.append((overlap * 2.0 - position * 0.05, position, sentence))
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


def _extractive_reply(question: str, pages: list[dict]) -> str:
    """模型不可用时的词条摘录回答（每个词条带 ``[n]`` 编号）。"""
    intro = f"关于「{_shorten(question, 40)}」，我在校园 Wiki 中查到以下词条："
    blocks: list[str] = []
    for index, page in enumerate(pages, start=1):
        title = str(page.get("title") or "未命名词条")
        body = page.get("body_md") or page.get("summary") or ""
        points = _extract_points(body, question)
        if not points:
            summary = _shorten(page.get("summary") or body, 140)
            points = [summary] if summary else []
        lines = [f"《{title}》"]
        for point in points:
            lines.append(f"- {point} [{index}]")
        sources = page.get("sources") or []
        if sources:
            first = sources[0]
            label = str(first.get("source_file") or first.get("document_title") or "原始文件")
            if first.get("section"):
                label += f" · {first['section']}"
            lines.append(f"  （来源：{label}）")
        blocks.append("\n".join(lines))
    reply = intro + "\n\n" + "\n\n".join(blocks)
    reply += "\n\n以上内容摘自 Wiki 词条，具体办理请以学校最新官方通知为准。"
    reply += f"\n{FALLBACK_TIP}"
    reply += f"\n如需进一步确认，可联系{CONSULT_CHANNELS}。"
    return reply


def _no_hit_reply(settings: dict[str, Any]) -> str:
    counselor_name = str(settings.get("counselor_name") or config.DEFAULT_COUNSELOR_NAME)
    return (
        "抱歉，校园 Wiki 暂未收录与这个问题直接相关的词条，我暂时无法给出可靠答复，"
        "也不想给你编造政策细节。\n\n"
        "你可以试试：\n"
        "1. 换一种说法再问我一次，补充更具体的关键词（如事项类型、时间、学院）；\n"
        f"2. 通过以下咨询渠道确认：{CONSULT_CHANNELS}；\n"
        "3. 查看学校官网或学生手册上的最新通知。\n\n"
        f"我是辅导员数字分身「{counselor_name}」。如果涉及心理危机、人身安全等紧急情况，"
        "请立即联系辅导员本人，或拨打学校保卫处、心理中心电话（紧急医疗请拨打 120）。"
    )


# ---------------------------------------------------------------------------
# 主入口（跨模块契约，见 docs/CONTRACT.md §6；签名保持不变）
# ---------------------------------------------------------------------------
def answer(
    question: str,
    *,
    channel: str = "web",
    session_key: str = "web:default",
    conversation_id: str | None = None,
    user_label: str | None = None,
) -> dict:
    """完整问答链路（Wiki 选页 → 提示词 → LLM/降级 → 落库）。

    返回 ``{conversation_id, reply, citations, model, provider_type, latency_ms,
    fallback, sources_used, navigation}``；参数非法时抛 ``ValueError``（路由层转 400）。
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

    conversation: dict | None = None
    history: list[dict[str, str]] = []
    try:
        conversation = _resolve_conversation(conversation_id, channel, session_key, user_label)
        history = _history(conversation["id"])
    except Exception:  # pragma: no cover - 会话故障不应影响本次回答
        logger.exception("创建/读取会话失败，本次回复将不落库")
        conversation = None

    model_label = ""
    provider_type = ""
    try:
        default_model = llm.default_model()
    except Exception:  # pragma: no cover
        logger.exception("读取默认模型失败，将按无模型降级")
        default_model = None
    if default_model:
        model_label = str(default_model.get("display_name") or default_model.get("model_name") or "")
        provider_type = (default_model.get("provider_type") or "").strip().lower()

    citations: list[dict] = []
    navigation: dict = {"method": "none", "pages": []}
    fallback_used = True
    pages: list[dict] = []

    try:
        pages, navigation = wiki_nav.select_pages_with_method(
            text, max_pages=settings["max_pages"]
        )
        if not pages:
            # 无命中：礼貌兜底 + 咨询渠道，不调用 LLM，避免编造
            reply = _no_hit_reply(settings)
            fallback_used = True
        else:
            context = wiki_nav.build_context(
                pages, max_chars=settings["answer_max_chars"]
            )
            messages: list[dict] = [
                {"role": "system", "content": _system_prompt(settings, context)}
            ]
            messages.extend(history)
            messages.append({"role": "user", "content": text})
            try:
                result = llm.chat(messages, temperature=None, max_tokens=None)
            except Exception:  # pragma: no cover - llm.chat 契约上不抛异常，这里再兜一层
                logger.exception("LLM 调用抛出异常，降级为词条摘录")
                result = {}
            if not isinstance(result, dict):
                result = {}
            content = str(result.get("content") or "").strip()
            if result.get("fallback") or not content:
                reply = _extractive_reply(text, pages)
                fallback_used = True
                reply, citations = wiki_nav.attach_citations(
                    reply, pages, settings["citations_enabled"]
                )
            else:
                reply = content
                fallback_used = False
                if result.get("model"):
                    model_label = str(result["model"])
                result_type = (result.get("provider_type") or "").strip().lower()
                if result_type:
                    provider_type = result_type
                reply, citations = wiki_nav.attach_citations(
                    reply, pages, settings["citations_enabled"]
                )
    except Exception:
        logger.exception("问答编排发生未预期错误，返回兜底回复")
        reply = (
            "抱歉，本次回答生成时出现异常。你可以稍后重试，或通过以下渠道咨询："
            f"{CONSULT_CHANNELS}。"
        )
        citations = []
        navigation = {"method": "none", "pages": []}
        fallback_used = True

    if not model_label:
        model_label = "本地 Wiki 词条摘录（未配置模型）"
    if not provider_type:
        provider_type = "none"

    latency_ms = int((time.perf_counter() - started) * 1000)

    # 持久化：user 在前、assistant 在后，并维护 conversations.updated_at
    if conversation is not None:
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
        "conversation_id": conversation["id"] if conversation else (conversation_id or ""),
        "reply": reply,
        "citations": citations,
        "model": model_label,
        "provider_type": provider_type,
        "latency_ms": latency_ms,
        "fallback": fallback_used,
        "sources_used": len(citations),
        "navigation": navigation,
    }


__all__ = ["answer"]

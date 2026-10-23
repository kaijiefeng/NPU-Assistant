"""机器人统一入站处理管线。

所有渠道（QQ OneBot 11 / 微信公众号 / 控制台调试）的消息最终都进入
:func:`handle_incoming`，由它完成：

1. 管理员「转发即入库」分流（docs/MOBILE-OPS.md §2.2）：管理员发来的链接或
   ``#入库``/``#收录``/``#状态``/``#帮助`` 指令直接处理，不进入问答、不落 Q&A；
2. 普通消息：构造会话标识 ``session_key`` 与展示用 ``user_label``（契约 §5.5），
   惰性调用 ``services.counselor.answer(...)``（由 backend-core 维护）；
3. 统一返回 ``{channel, channel_id, conversation_id, reply, citations,
   latency_ms, outbound}``，其中 ``outbound`` 是「将要发送给平台」的报文描述，
   供前端 / 调试展示；
4. 任何异常都不会向上抛：counselor / importer 未就绪或 LLM/知识库失败时返回
   友好提示，保证消息日志可追溯、WS/HTTP 事件处理不受影响。

设计原则：bot-gateway 不自己拼提示词；收录只通过 ``services.importer`` 的公开
接口，不直接实现抓取/编译逻辑。
"""
from __future__ import annotations

import importlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

from .. import config, db
from ..adapters import onebot, wechat_mp
from ..adapters.base import (
    CHANNEL_CONSOLE,
    CHANNEL_ONEBOT,
    CHANNEL_WECHAT,
    CHAT_PRIVATE,
    IncomingMessage,
    config_bool,
    make_console_message,
    parse_admin_user_ids,
)

logger = logging.getLogger("npu-assistant")

# 与 /api/chat 保持一致的单条消息长度上限（契约 §5.2）
MAX_QUESTION_CHARS = 2000

# 管理员同步入库（QQ/控制台）的超时保护：docs/MOBILE-OPS.md §2.2 通常 2–8 秒
IMPORT_TIMEOUT_SECONDS = 120
# 回复中最多列出的词条标题数量
MAX_REPLY_PAGE_TITLES = 8
# 后台线程名（便于日志排查）
WECHAT_IMPORT_THREAD_NAME = "bot-wechat-import"

FALLBACK_REPLY = (
    "抱歉，助手暂时没能处理这条消息（可能是模型或知识库服务正在初始化）。"
    "请稍后再试，或直接联系你的辅导员老师。"
)

# http(s) 链接（排除空白、尖括号、引号与常见中英文标点）
_URL_RE = re.compile(
    r"https?://[^\s<>\"'`，。；：！？、（）【】《》「」『』]+",
    re.IGNORECASE,
)
_URL_TRAILING_CHARS = ".,;:!?)]}>'\"，。；：！？、）】》」』"

_IMPORT_COMMANDS = ("#入库", "#收录")
_STATUS_COMMAND = "#状态"
_HELP_COMMAND = "#帮助"

COMMAND_RESTRICTED_REPLY = "该命令仅限管理员使用。如需收录资料，请联系管理员。"
IMPORT_DISABLED_REPLY = "机器人收录功能已关闭（通道配置 import_enabled=false）。"
IMPORT_NOT_READY_REPLY = "收录服务尚未就绪（后端缺少 importer 模块），请稍后再试。"


# ---------------------------------------------------------------------------
# 构造入站消息
# ---------------------------------------------------------------------------
def _channel_config(channel: dict | None) -> dict:
    if not isinstance(channel, dict):
        return {}
    return db.from_json(channel.get("config"), {}) or {}


def incoming_for_channel(channel: dict, text: str, *, user_id: str = "",
                         user_name: str = "") -> IncomingMessage:
    """把一条「模拟入站」文本包装成对应渠道的 IncomingMessage。

    用于 ``POST /api/bots/channels/{id}/test``：不真正走平台协议，但后续处理与
    真实消息完全一致（同一套 session_key / 管理员分流 / counselor / outbound）。
    """
    channel_type = str(channel.get("type") or "")
    channel_id = str(channel.get("id") or "")
    config_data = _channel_config(channel)
    clean = str(text or "").strip()

    if channel_type == CHANNEL_ONEBOT:
        self_id = str(config_data.get("self_id") or "")
        return IncomingMessage(
            channel=CHANNEL_ONEBOT,
            channel_id=channel_id,
            text=clean,
            user_id=user_id or "10001",
            user_name=user_name or "QQ测试同学",
            chat_type=CHAT_PRIVATE,
            self_id=self_id,
        )
    if channel_type == CHANNEL_WECHAT:
        return IncomingMessage(
            channel=CHANNEL_WECHAT,
            channel_id=channel_id,
            text=clean,
            user_id=user_id or "test-openid",
            user_name=user_name or "微信测试用户",
            chat_type=CHAT_PRIVATE,
        )
    return make_console_message(channel_id, clean, user_id=user_id, user_name=user_name)


# ---------------------------------------------------------------------------
# 回复装饰 / 兜底落库 / 出站报文（普通问答路径）
# ---------------------------------------------------------------------------
def _append_footer(reply: str) -> str:
    """追加设置里的机器人回复尾注（``bot_reply_footer``）。"""
    try:
        footer = str(db.get_setting("bot_reply_footer") or "")
    except Exception:  # noqa: BLE001 - 设置缺失不应影响回复
        return reply
    footer = footer.strip()
    if not footer:
        return reply
    if reply.rstrip().endswith(footer):
        return reply
    return f"{reply}\n\n{footer}"


def _persist_fallback(message: IncomingMessage, question: str, reply: str) -> str | None:
    """counselor 不可用时，仍把这一轮对话写入 conversations/messages。"""
    try:
        conversation = db.find_or_create_conversation(
            channel=message.channel,
            session_key=message.session_key,
            channel_id=message.channel_id,
            user_label=message.user_label,
        )
        conversation_id = str(conversation["id"])
        if question:
            db.add_message(conversation_id, "user", question)
        db.add_message(conversation_id, "assistant", reply, citations=[])
        return conversation_id
    except Exception:  # noqa: BLE001
        logger.warning("机器人兜底回复落库失败（已忽略）", exc_info=True)
        return None


def _load_channel_config(message: IncomingMessage) -> dict:
    try:
        row = db.query_one("SELECT config FROM bot_channels WHERE id = ?", (message.channel_id,))
    except Exception:  # noqa: BLE001
        return {}
    return db.from_json(row.get("config") if row else None, {}) or {}


def _build_outbound(message: IncomingMessage, reply: str, channel_config: dict | None) -> dict:
    """构造将要发送给平台的报文；不发起任何网络请求。"""
    config_data = channel_config if isinstance(channel_config, dict) else _load_channel_config(message)
    try:
        if message.channel == CHANNEL_ONEBOT:
            return onebot.build_outbound(
                config_data,
                chat_type=message.chat_type,
                user_id=message.user_id,
                group_id=message.group_id,
                text=reply,
            )
        if message.channel == CHANNEL_WECHAT:
            raw = message.raw if isinstance(message.raw, dict) else {}
            return wechat_mp.build_outbound(
                {
                    "FromUserName": raw.get("FromUserName") or message.user_id,
                    "ToUserName": raw.get("ToUserName") or "",
                },
                reply,
                channel_config=config_data,
            )
    except Exception:  # noqa: BLE001 - 报文构造失败不应影响回复
        logger.warning("构造机器人出站报文失败（已忽略）", exc_info=True)
    return {"type": message.channel or CHANNEL_CONSOLE, "text": reply}


# ---------------------------------------------------------------------------
# 管理员「转发即入库」：消息文本 / URL / 命令解析
# ---------------------------------------------------------------------------
def _json_segment_text(value: Any) -> str:
    """把 OneBot ``json`` 卡片消息段转成可搜索文本（提取其中的 URL）。"""
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    text = str(value or "")
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text.replace("\\/", "/")
    if isinstance(parsed, (dict, list)):
        try:
            return json.dumps(parsed, ensure_ascii=False)
        except (TypeError, ValueError):
            return text
    return str(parsed)


def _message_search_texts(message: IncomingMessage) -> list[str]:
    """收集可能包含链接的文本来源：正文、raw_message、消息段（含 JSON 卡片）。"""
    texts: list[str] = [str(message.text or "")]
    raw = message.raw if isinstance(message.raw, dict) else {}
    raw_message = raw.get("raw_message")
    if isinstance(raw_message, str):
        texts.append(raw_message)
    raw_payload = raw.get("message")
    if isinstance(raw_payload, str):
        texts.append(raw_payload)
    elif isinstance(raw_payload, list):
        for segment in raw_payload:
            if not isinstance(segment, dict):
                continue
            segment_type = str(segment.get("type") or "").lower()
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            if segment_type == "text":
                texts.append(str(data.get("text") or ""))
            elif segment_type == "json":
                texts.append(_json_segment_text(data.get("data")))
            elif segment_type in {"share", "xml", "markdown"}:
                texts.append(str(data.get("url") or ""))
                texts.append(_json_segment_text(data.get("data")))
    return [text for text in texts if text]


def extract_urls(text: str) -> list[str]:
    """从文本中提取 http(s) 链接（去重、去掉尾随标点，最多 5 个）。"""
    urls: list[str] = []
    for match in _URL_RE.finditer(str(text or "")):
        url = match.group(0).rstrip(_URL_TRAILING_CHARS)
        if not url or url in urls:
            continue
        urls.append(url)
        if len(urls) >= 5:
            break
    return urls


def _collect_message_urls(message: IncomingMessage) -> list[str]:
    urls: list[str] = []
    for text in _message_search_texts(message):
        for url in extract_urls(text):
            if url not in urls:
                urls.append(url)
    return urls


def _parse_command(text: str) -> tuple[str, str] | None:
    """解析管理员命令，返回 ``(kind, arg)``；非命令返回 None。"""
    cleaned = str(text or "").strip()
    if not cleaned.startswith("#"):
        return None
    for command in _IMPORT_COMMANDS:
        if cleaned.startswith(command):
            return "import", cleaned[len(command):].strip()
    if cleaned == _STATUS_COMMAND or cleaned.startswith(_STATUS_COMMAND + " "):
        return "status", ""
    if cleaned == _HELP_COMMAND or cleaned.startswith(_HELP_COMMAND + " "):
        return "help", ""
    return None


def _is_admin(config_data: dict, user_id: str) -> bool:
    admin_ids = parse_admin_user_ids((config_data or {}).get("admin_user_ids"))
    return bool(admin_ids) and str(user_id or "") in admin_ids


def _channel_tags(message: IncomingMessage) -> list[str]:
    if message.channel == CHANNEL_ONEBOT:
        return ["bot", "QQ"]
    if message.channel == CHANNEL_WECHAT:
        return ["bot", "微信"]
    return ["bot", "控制台"]


# ---------------------------------------------------------------------------
# 管理员「转发即入库」：URL 脱敏与日志
# ---------------------------------------------------------------------------
def mask_url(url: str) -> str:
    """脱敏 URL：只保留 scheme://host[:port]/path，去掉 query/fragment/userinfo。"""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return "[链接]"
    if not parts.scheme or not parts.netloc:
        return "[链接]"
    host = parts.hostname or ""
    try:
        if parts.port:
            host = f"{host}:{parts.port}"
    except ValueError:
        pass
    return f"{parts.scheme}://{host}{parts.path or ''}"


def _mask_url_host(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return "链接"
    host = parts.hostname or ""
    return host or "链接"


def _sanitize_text(text: str) -> str:
    """把文本中的 URL 替换为脱敏形式，避免把敏感 query 回显/写日志。"""
    value = str(text or "")

    def _replace(match: re.Match[str]) -> str:
        return mask_url(match.group(0).rstrip(_URL_TRAILING_CHARS))

    return _URL_RE.sub(_replace, value)


# ---------------------------------------------------------------------------
# 管理员「转发即入库」：状态 / 帮助
# ---------------------------------------------------------------------------
def _safe_value(getter, default):
    try:
        return getter()
    except Exception:  # noqa: BLE001 - 状态展示的单项失败不影响整体
        return default


def _status_text() -> str:
    version = _safe_value(lambda: config.APP_VERSION, "?")
    wiki_pages = _safe_value(db.wiki_page_count, 0)

    def _document_counts() -> tuple[int, int]:
        total = db.query_one("SELECT COUNT(*) AS n FROM documents")
        ready = db.query_one("SELECT COUNT(*) AS n FROM documents WHERE status = 'ready'")
        return int(total["n"] if total else 0), int(ready["n"] if ready else 0)

    total_docs, ready_docs = _safe_value(_document_counts, (0, 0))

    def _model_line() -> str:
        model = db.get_default_model()
        if not model:
            return "未配置"
        name = str(model.get("display_name") or model.get("model_name") or "未命名模型")
        provider_type = str(model.get("provider_type") or "")
        return f"{name}（{provider_type}）" if provider_type else name

    model_line = _safe_value(_model_line, "未配置")

    def _build_line() -> str:
        build = db.query_one(
            "SELECT * FROM wiki_builds ORDER BY started_at DESC, rowid DESC LIMIT 1"
        )
        if not build:
            return "暂无构建记录"
        status_label = {
            "running": "进行中",
            "ready": "成功",
            "failed": "失败",
        }.get(str(build.get("status") or ""), str(build.get("status") or "未知"))
        finished_at = build.get("finished_at") or build.get("started_at") or ""
        counts = f"新增 {int(build.get('pages_created') or 0)} / 更新 {int(build.get('pages_updated') or 0)}"
        return f"{status_label}｜{finished_at}｜{counts}"

    build_line = _safe_value(_build_line, "暂无构建记录")

    return "\n".join(
        [
            "【NPU-Assistant 运行状态】",
            f"版本：v{version}",
            f"Wiki 词条：{wiki_pages} 个",
            f"源文件：{total_docs} 个（已就绪 {ready_docs} 个）",
            f"默认模型：{model_line}",
            f"最近构建：{build_line}",
        ]
    )


def _help_text(user_id: str = "", admin_configured: bool = False) -> str:
    lines = [
        "【机器人收录命令（仅管理员）】",
        "• 直接发送链接，或「#入库 <链接>」「#收录 <链接>」：抓取网页并编译为 Wiki 词条",
        "• #状态：查看版本、Wiki 词条数、源文件数、默认模型与最近构建状态",
        "• #帮助：显示本说明",
        "说明：非管理员发送链接将按普通问题回答；公众号会先回复“已开始收录”，稍后到 Wiki 查看结果。",
    ]
    if user_id:
        if admin_configured:
            lines.append(
                f"你的用户 ID：{user_id}（如需新增/更换管理员，请填入通道配置 admin_user_ids）"
            )
        else:
            # 方便首次部署时把发送者 ID 填进通道配置 admin_user_ids
            lines.append(
                f"提示：当前通道尚未配置管理员。你的用户 ID 为 {user_id}，"
                "填入通道配置 admin_user_ids 后即可启用收录。"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 管理员「转发即入库」：重复检测 / 执行 / 回复
# ---------------------------------------------------------------------------
def _find_existing_document(url: str) -> dict | None:
    try:
        return db.query_one(
            "SELECT id, title, status FROM documents WHERE source_url = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (url,),
        )
    except Exception:  # noqa: BLE001 - 查重失败不阻断导入（importer 也会查重）
        logger.warning("机器人收录查重失败（已忽略）", exc_info=True)
        return None


def _load_importer():
    """惰性导入收录服务：task-11 并行开发，未就绪时给出友好提示。"""
    return importlib.import_module("app.services.importer")


@contextmanager
def _quiet_httpx_logging():
    """收录期间临时压低 httpx 的 INFO 请求日志。

    httpx 默认在 INFO 级别记录完整请求 URL（含 query）；本模块自己的日志已脱敏，
    但第三方库日志仍可能泄露敏感 query。收录结束后恢复原级别。
    """
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    if previous_level < logging.WARNING:
        httpx_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        httpx_logger.setLevel(previous_level)


def _perform_import(message: IncomingMessage, url: str,
                    tags: list[str]) -> tuple[dict | None, str | None]:
    """执行一次入库并记录日志，返回 ``(result, error_message)``，永不抛异常。"""
    masked = mask_url(url)
    try:
        importer = _load_importer()
    except Exception:  # noqa: BLE001 - 模块尚未就绪
        logger.warning(
            "机器人收录失败（importer 未就绪）：channel=%s url=%s", message.channel, masked
        )
        return None, IMPORT_NOT_READY_REPLY

    try:
        # 抑制 httpx 的 URL 请求日志，避免敏感 query 进入服务端日志
        with _quiet_httpx_logging():
            result = importer.import_url(url, tags=list(tags)) or {}
    except Exception as exc:  # noqa: BLE001 - 抓取/编译失败只回复中文原因
        reason = _sanitize_text(str(exc) or exc.__class__.__name__)[:180]
        logger.warning(
            "机器人收录失败：channel=%s user=%s url=%s error=%s",
            message.channel, message.user_id, masked, reason,
        )
        return None, reason

    duplicated = bool(result.get("duplicated"))
    document = result.get("document") if isinstance(result.get("document"), dict) else {}
    title = str(document.get("title") or "未命名资料")
    pages = result.get("pages") if isinstance(result.get("pages"), list) else []
    logger.info(
        "机器人收录%s：channel=%s user=%s url=%s title=%s pages=%d",
        "（重复，跳过）" if duplicated else "成功",
        message.channel, message.user_id, masked, title[:80], len(pages),
    )
    return result, None


def _run_import_sync(message: IncomingMessage, url: str,
                     tags: list[str]) -> tuple[dict | None, str | None]:
    """同步入库（QQ/控制台）并带超时保护；超时返回 ``"__timeout__"``。"""
    box: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            box["result"], box["error"] = _perform_import(message, url, tags)
        except Exception:  # noqa: BLE001 - 防御性兜底
            logger.exception("机器人收录后台线程异常：channel=%s", message.channel)
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True, name="bot-import").start()
    if not done.wait(IMPORT_TIMEOUT_SECONDS):
        logger.warning(
            "机器人收录超时（>%ss）：channel=%s url=%s",
            IMPORT_TIMEOUT_SECONDS, message.channel, mask_url(url),
        )
        return None, "__timeout__"
    return box.get("result"), box.get("error")


def _start_wechat_import(message: IncomingMessage, url: str, tags: list[str]) -> None:
    """微信公众号：5 秒被动回复限制下，后台 daemon 线程完成入库。"""
    def worker() -> None:
        try:
            _perform_import(message, url, tags)
        except Exception:  # noqa: BLE001 - 后台线程绝不能抛出
            logger.exception("微信公众号后台收录线程异常：channel=%s", message.channel)

    threading.Thread(
        target=worker,
        daemon=True,
        name=WECHAT_IMPORT_THREAD_NAME,
    ).start()


def _format_import_result(result: dict | None) -> str:
    result = result or {}
    document = result.get("document") if isinstance(result.get("document"), dict) else {}
    title = str(document.get("title") or "未命名资料")

    if result.get("duplicated"):
        return f"该链接已收录过：《{title}》，无需重复收录。"

    pages = result.get("pages") if isinstance(result.get("pages"), list) else []
    titles = [
        str(page.get("title")).strip()
        for page in pages
        if isinstance(page, dict) and page.get("title")
    ]
    if titles:
        shown = "、".join(titles[:MAX_REPLY_PAGE_TITLES])
        ellipsis = " 等" if len(titles) > MAX_REPLY_PAGE_TITLES else ""
        return f"已收录《{title}》：生成 {len(titles)} 个词条：{shown}{ellipsis}"

    page_count = document.get("page_count")
    try:
        count = int(page_count or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return f"已收录《{title}》：生成 {count} 个词条。"
    return f"已收录《{title}》：本次未生成新词条（可能与已有词条合并或内容过短）。"


def _respond_import(message: IncomingMessage, url: str) -> str:
    tags = _channel_tags(message)
    existing = _find_existing_document(url)
    if existing:
        title = str(existing.get("title") or "未命名资料")
        logger.info(
            "机器人收录跳过（已存在）：channel=%s url=%s title=%s",
            message.channel, mask_url(url), title[:80],
        )
        if message.channel == CHANNEL_WECHAT:
            return f"该链接已收录过：《{title}》，无需重复收录。"
        return f"该链接已收录过：《{title}》。如需重新编译，请在知识库页重建该源文件。"

    if message.channel == CHANNEL_WECHAT:
        _start_wechat_import(message, url, tags)
        return f"收到，已开始收录（{_mask_url_host(url)}），请稍后到 Wiki 查看。"

    result, error = _run_import_sync(message, url, tags)
    if error == "__timeout__":
        return "收录超时（超过 120 秒），已转入后台继续处理，请稍后到 Wiki 查看结果。"
    if error:
        return f"收录失败：{error}"
    return _format_import_result(result)


def _handle_admin_message(message: IncomingMessage, config_data: dict) -> str | None:
    """管理员消息分流；返回回复文本表示已处理，返回 None 表示走普通问答。"""
    config_data = config_data if isinstance(config_data, dict) else {}
    text = str(message.text or "").strip()
    command = _parse_command(text)
    urls = _collect_message_urls(message)
    admin = _is_admin(config_data, message.user_id)
    import_enabled = config_bool(config_data.get("import_enabled"), True)

    if command:
        kind, argument = command
        if kind == "help":
            return _help_text(
                message.user_id,
                admin_configured=bool(parse_admin_user_ids(config_data.get("admin_user_ids"))),
            )
        if not admin:
            return COMMAND_RESTRICTED_REPLY
        if kind == "status":
            return _status_text()
        # 收录命令：#入库 / #收录
        if not import_enabled:
            return IMPORT_DISABLED_REPLY
        command_urls = extract_urls(argument) or urls
        if not command_urls:
            return "请提供要收录的链接，例如：#入库 https://example.com/article"
        return _respond_import(message, command_urls[0])

    if urls and admin:
        if not import_enabled:
            return IMPORT_DISABLED_REPLY
        return _respond_import(message, urls[0])

    # 非管理员发链接 / 普通消息 → 交给问答链路
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def handle_incoming(message: IncomingMessage, *, channel_config: dict | None = None) -> dict:
    """处理一条归一化入站消息并返回统一结果（永不抛异常）。

    返回字段严格对应契约 §5.5 ``POST /bots/channels/{id}/test`` 的响应：
    ``{channel, channel_id, conversation_id, reply, citations, latency_ms, outbound}``
    """
    started = time.perf_counter()
    config_data = channel_config if isinstance(channel_config, dict) else _load_channel_config(message)
    session_key = message.session_key
    user_label = message.user_label
    question = (message.text or "").strip()[:MAX_QUESTION_CHARS]

    # 1) 管理员「转发即入库」/ 命令分流：不进入问答、不落 Q&A 对话
    try:
        admin_reply = _handle_admin_message(message, config_data)
    except Exception:  # noqa: BLE001 - 命令处理异常不影响渠道连接
        logger.exception("机器人管理员指令处理失败：channel=%s", message.channel)
        admin_reply = "命令处理失败，请稍后重试或查看后端日志。"

    if admin_reply is not None:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "机器人管理员指令处理完成：channel=%s user=%s latency=%sms",
            message.channel, message.user_id, latency_ms,
        )
        return {
            "channel": message.channel,
            "channel_id": message.channel_id,
            "conversation_id": None,
            "reply": admin_reply,
            "citations": [],
            "latency_ms": latency_ms,
            "outbound": _build_outbound(message, admin_reply, config_data),
        }

    # 2) 普通问答链路
    reply = ""
    citations: list = []
    conversation_id: str | None = None

    if not question:
        reply = "请告诉我你想咨询的问题，例如“国家奖学金怎么申请？”。"

    try:
        # 惰性导入：counselor 由 backend-core 并行开发，尚未就绪时也要能导入本模块
        from ..services import counselor  # noqa: PLC0415

        result = counselor.answer(
            question,
            channel=message.channel,
            session_key=session_key,
            user_label=user_label,
        ) or {}
        conversation_id = str(result.get("conversation_id") or "") or None
        reply = str(result.get("reply") or "").strip()
        citations = result.get("citations") or []
        if not reply:
            reply = FALLBACK_REPLY
    except Exception:  # noqa: BLE001 - 任何问答链路异常都降级为友好提示
        logger.exception(
            "机器人管线调用 counselor.answer 失败：channel=%s session_key=%s",
            message.channel, session_key,
        )
        reply = FALLBACK_REPLY

    reply = _append_footer(reply)

    # counselor 不可用时也要保证消息日志里有记录
    if conversation_id is None and question:
        conversation_id = _persist_fallback(message, question, reply)

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "机器人消息处理完成：channel=%s session_key=%s conversation=%s latency=%sms",
        message.channel, session_key, conversation_id, latency_ms,
    )

    return {
        "channel": message.channel,
        "channel_id": message.channel_id,
        "conversation_id": conversation_id,
        "reply": reply,
        "citations": citations,
        "latency_ms": latency_ms,
        "outbound": _build_outbound(message, reply, config_data),
    }

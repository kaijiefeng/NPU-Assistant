"""LLM 接入层：OpenAI 兼容 / Ollama / 内置演示（mock）三类 Provider。

统一封装：

* ``openai`` —— 任何 OpenAI 兼容服务，聊天走 ``{base_url}/chat/completions``；
* ``ollama`` —— 本地 Ollama，聊天走 ``{base_url}/api/chat``；
* ``mock``   —— 内置演示模型，不联网，返回可读的占位回答。

设计原则：

1. 所有网络 / 协议错误都转换成可读的 :class:`LLMError`，再由公开函数转成结构化结果，
   绝不把 httpx 异常抛给路由层（避免 500）。
2. 超时统一 60 秒（建连阶段 10 秒）。
3. ``api_key`` 只在服务端使用，任何返回给前端的内容都必须脱敏。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable

import httpx

from .. import db

logger = logging.getLogger("npu-assistant")

REQUEST_TIMEOUT_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10.0
PROVIDER_TYPES = ("openai", "ollama", "mock")
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

_MODEL_JOIN_SQL = (
    "SELECT m.*, p.name AS provider_name, p.type AS provider_type, p.base_url, p.api_key, "
    "p.embedding_model, p.extra AS provider_extra "
    "FROM models_registry m JOIN providers p ON p.id = m.provider_id"
)


class LLMError(RuntimeError):
    """可读的模型调用错误；``str(exc)`` 直接面向最终用户展示。"""


# ---------------------------------------------------------------------------
# Retry + circuit breaker (v0.2.0 M5)
# ---------------------------------------------------------------------------
RETRY_ATTEMPTS = int(os.environ.get("NPU_LLM_RETRIES", "1")) + 1
RETRY_BACKOFF_SECONDS = float(os.environ.get("NPU_LLM_RETRY_BACKOFF", "0.4"))
CIRCUIT_THRESHOLD = int(os.environ.get("NPU_LLM_BREAKER_THRESHOLD", "3"))
CIRCUIT_COOLDOWN_SECONDS = float(os.environ.get("NPU_LLM_BREAKER_COOLDOWN", "60"))
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT: dict[str, dict[str, Any]] = {}


def _circuit_key(target: dict) -> str:
    return str(target.get("provider_id") or target.get("provider_name") or target.get("id") or "llm")


def _circuit_open(key: str) -> bool:
    with _CIRCUIT_LOCK:
        state = _CIRCUIT.get(key)
        if not state:
            return False
        opened_at = float(state.get("opened_at") or 0)
        if opened_at and time.monotonic() - opened_at < CIRCUIT_COOLDOWN_SECONDS:
            return True
        if opened_at:
            _CIRCUIT.pop(key, None)
        return False


def _record_failure(key: str, error: str) -> None:
    with _CIRCUIT_LOCK:
        state = _CIRCUIT.setdefault(key, {"failures": 0, "opened_at": 0.0})
        state["failures"] = int(state.get("failures") or 0) + 1
        state["last_error"] = error
        if state["failures"] >= max(1, CIRCUIT_THRESHOLD) and not state.get("opened_at"):
            state["opened_at"] = time.monotonic()
            opened = True
        else:
            opened = False
    if opened:
        try:
            from . import alerts

            alerts.create_alert(
                f"模型服务连续失败已熔断：{key}",
                level="error",
                category="llm",
                detail=(
                    f"连续失败 {CIRCUIT_THRESHOLD} 次，暂停调用 {int(CIRCUIT_COOLDOWN_SECONDS)} 秒。"
                    f"最近错误：{str(error)[:300]}"
                ),
                source="llm",
                resource_id=key,
                dedupe_hours=1,
            )
        except Exception:  # pragma: no cover - alerting must not break answers
            logger.exception("写入模型熔断告警失败")


def _record_success(key: str) -> None:
    with _CIRCUIT_LOCK:
        _CIRCUIT.pop(key, None)


def circuit_status() -> dict:
    with _CIRCUIT_LOCK:
        return {
            key: {
                **value,
                "open": bool(value.get("opened_at"))
                and time.monotonic() - float(value.get("opened_at") or 0)
                < CIRCUIT_COOLDOWN_SECONDS,
            }
            for key, value in _CIRCUIT.items()
        }


def _retryable(message: str) -> bool:
    text = str(message or "")
    if "api_key" in text or "认证失败" in text or "接口不存在" in text:
        return False
    return any(
        marker in text
        for marker in ("超时", "无法连接", "429", "HTTP 5", "内部错误", "HTTPError")
    )


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def mask_api_key(api_key: str | None) -> str:
    """按契约脱敏 API Key：长度 ≤ 8 显示 ``****``，否则显示 ``前4****后4``。"""
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _client() -> httpx.Client:
    """统一的 HTTP 客户端：60 秒超时、不读取系统代理（本地 Ollama 更稳定）。"""
    return httpx.Client(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        follow_redirects=True,
        trust_env=False,
    )


def _join_url(base_url: str | None, path: str) -> str:
    """拼接接口地址；允许管理员直接把完整接口地址填进 base_url。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("Provider 未配置 base_url，请先在「模型管理」中补全")
    if not (base.startswith("http://") or base.startswith("https://")):
        raise LLMError("Provider 的 base_url 必须以 http:// 或 https:// 开头")
    if base.endswith(path):
        return base
    return base + path


def _auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _clean_messages(messages: Any) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            continue
        if not content.strip():
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    provider_name: str,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
) -> dict:
    """发起请求并解析 JSON；所有异常统一转成可读的 :class:`LLMError`。"""
    try:
        response = client.request(method, url, headers=headers, json=json_body)
    except httpx.TimeoutException as exc:
        raise LLMError(
            f"调用「{provider_name}」超时（{int(REQUEST_TIMEOUT_SECONDS)} 秒）："
            f"请检查服务地址、网络连接或模型响应速度"
        ) from exc
    except httpx.ConnectError as exc:
        raise LLMError(
            f"无法连接到「{provider_name}」（{url}）：请确认服务已启动、地址与端口正确"
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"调用「{provider_name}」失败：{exc.__class__.__name__}: {exc}") from exc

    if response.status_code >= 400:
        snippet = (response.text or "").strip().replace("\n", " ")[:160]
        if response.status_code in (401, 403):
            raise LLMError(
                f"「{provider_name}」认证失败（HTTP {response.status_code}）：请检查 API Key 是否正确"
            )
        if response.status_code == 404:
            raise LLMError(
                f"「{provider_name}」接口不存在（HTTP 404）："
                f"请检查 base_url 是否形如 https://api.openai.com/v1"
            )
        if response.status_code == 429:
            raise LLMError(f"「{provider_name}」请求过于频繁（HTTP 429），请稍后重试")
        if response.status_code >= 500:
            raise LLMError(
                f"「{provider_name}」服务内部错误（HTTP {response.status_code}）：{snippet or '无返回内容'}"
            )
        raise LLMError(
            f"「{provider_name}」返回 HTTP {response.status_code}：{snippet or '无返回内容'}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"「{provider_name}」返回内容不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise LLMError(f"「{provider_name}」返回结构异常：期望 JSON 对象")
    return data


def _resolve_target(model_id: str | None) -> dict | None:
    """取模型 + Provider 配置；``model_id`` 为空时取默认模型。"""
    try:
        if model_id:
            return db.query_one(f"{_MODEL_JOIN_SQL} WHERE m.id = ?", (model_id,))
        return db.get_default_model()
    except Exception:  # pragma: no cover - 数据库异常不应影响服务启动
        logger.exception("读取模型配置失败")
        return None


def _failure(message: str, target: dict | None, started: float) -> dict:
    target = target or {}
    return {
        "content": "",
        "model": target.get("display_name") or target.get("model_name") or "",
        "provider_name": target.get("provider_name") or "",
        "provider_type": (target.get("provider_type") or "").strip().lower(),
        "fallback": True,
        "usage": {},
        "latency_ms": _elapsed_ms(started),
        "error": message,
    }


def _success(content: str, target: dict, usage: Any, started: float) -> dict:
    return {
        "content": content,
        "model": target.get("display_name") or target.get("model_name") or "",
        "provider_name": target.get("provider_name") or "",
        "provider_type": (target.get("provider_type") or "").strip().lower(),
        "fallback": False,
        "usage": usage if isinstance(usage, dict) else {},
        "latency_ms": _elapsed_ms(started),
        "error": None,
    }


# ---------------------------------------------------------------------------
# 各 Provider 的聊天实现
# ---------------------------------------------------------------------------
def _chat_openai(
    target: dict,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int | None,
) -> tuple[str, dict]:
    base_url = target.get("base_url") or ""
    api_key = (target.get("api_key") or "").strip()
    model_name = target.get("model_name") or ""
    if not api_key:
        raise LLMError("OpenAI 兼容 Provider 未配置 api_key，请先在「模型管理」中补全")

    url = _join_url(base_url, "/chat/completions")
    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    with _client() as client:
        data = _request_json(
            client,
            "POST",
            url,
            provider_name=target.get("provider_name") or "OpenAI 兼容服务",
            headers=_auth_headers(api_key),
            json_body=payload,
        )

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("模型服务返回结构异常：choices 为空")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):  # 兼容部分兼容层返回的分段内容
        content = "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict)
        )
    content = str(content or "").strip()
    if not content:
        raise LLMError("模型服务返回了空回复")

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return content, usage


def _chat_ollama(
    target: dict,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int | None,
) -> tuple[str, dict]:
    base_url = target.get("base_url") or DEFAULT_OLLAMA_BASE_URL
    model_name = target.get("model_name") or ""
    url = _join_url(base_url, "/api/chat")
    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    options: dict[str, Any] = {}
    if temperature is not None:
        options["temperature"] = float(temperature)
    if max_tokens is not None:
        options["num_predict"] = int(max_tokens)
    if options:
        payload["options"] = options

    with _client() as client:
        data = _request_json(
            client,
            "POST",
            url,
            provider_name=target.get("provider_name") or "Ollama",
            headers={"Content-Type": "application/json"},
            json_body=payload,
        )

    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    content = str(message.get("content") or data.get("response") or "").strip()
    if not content:
        raise LLMError("Ollama 返回了空回复")

    prompt_tokens = _as_int(data.get("prompt_eval_count"), 0)
    completion_tokens = _as_int(data.get("eval_count"), 0)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    return content, usage


# ---------------------------------------------------------------------------
# 公开接口（跨模块契约，见 docs/CONTRACT.md §6）
# ---------------------------------------------------------------------------
def default_model() -> dict | None:
    """``db.get_default_model()`` 的薄封装（含 Provider 字段）。"""
    return _resolve_target(None)


def chat(
    messages: list[dict],
    *,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict:
    """调用模型生成回复。

    返回 ``{content, model, provider_name, provider_type, fallback, usage, latency_ms, error}``。
    任何异常都会被转换成 ``fallback=True`` 的结构化结果，绝不向上抛出。
    mock Provider 也返回可读的 ``content``（由 counselor 覆盖为抽取式回答）。
    """
    started = time.perf_counter()
    cleaned = _clean_messages(messages)
    if not cleaned:
        return _failure("消息内容为空，无法调用模型", None, started)

    target = _resolve_target(model_id)
    if target is None:
        message = "指定的模型不存在，请检查模型配置" if model_id else "尚未配置可用的模型，请先在「模型管理」中添加并启用模型"
        return _failure(message, None, started)

    model_name = (target.get("model_name") or "").strip()
    provider_type = (target.get("provider_type") or "").strip().lower()
    if not model_name:
        return _failure("模型名称为空，请检查模型配置", target, started)

    temperature = (
        _as_float(target.get("temperature"), 0.3) if temperature is None else float(temperature)
    )
    max_tokens = (
        _as_int(target.get("max_tokens"), 1200) if max_tokens is None else int(max_tokens)
    )

    if provider_type == "mock":
        last_user = next(
            (item["content"] for item in reversed(cleaned) if item["role"] == "user"),
            "",
        )
        snippet = last_user.strip().replace("\n", " ")[:60]
        content = (
            f"（内置演示模型）已收到你的问题：{('「' + snippet + '」') if snippet else '（空消息）'}。"
            "当前未接入在线大模型，我将基于本地知识库摘录作答。"
        )
        return {
            "content": content,
            "model": target.get("display_name") or model_name,
            "provider_name": target.get("provider_name") or "",
            "provider_type": "mock",
            "fallback": True,
            "usage": {},
            "latency_ms": _elapsed_ms(started),
            "error": None,
        }

    key = _circuit_key(target)
    if _circuit_open(key):
        return _failure(
            "模型服务连续失败，已暂时熔断；请稍后重试或检查模型配置/网络。",
            target,
            started,
        )

    try:
        if provider_type == "openai":
            content, usage = _chat_openai(target, cleaned, temperature, max_tokens)
        elif provider_type == "ollama":
            content, usage = _chat_ollama(target, cleaned, temperature, max_tokens)
        else:
            raise LLMError(f"不支持的 Provider 类型：{provider_type or '空'}")
    except LLMError as exc:
        message = str(exc)
        if RETRY_ATTEMPTS > 1 and _retryable(message):
            logger.warning("模型调用失败，准备重试（%s / %s）：%s", target.get("provider_name"), model_name, message)
            time.sleep(max(0.0, RETRY_BACKOFF_SECONDS))
            try:
                if provider_type == "openai":
                    content, usage = _chat_openai(target, cleaned, temperature, max_tokens)
                elif provider_type == "ollama":
                    content, usage = _chat_ollama(target, cleaned, temperature, max_tokens)
                else:  # pragma: no cover - 上面已拦截
                    raise LLMError(message)
            except LLMError as retry_exc:
                message = str(retry_exc)
                logger.warning("模型重试仍失败（%s / %s）：%s", target.get("provider_name"), model_name, message)
                _record_failure(key, message)
                return _failure(message, target, started)
        else:
            logger.warning("模型调用失败（%s / %s）：%s", target.get("provider_name"), model_name, message)
            _record_failure(key, message)
            return _failure(message, target, started)
    except Exception as exc:  # pragma: no cover - 兜底，避免 500
        logger.exception("模型调用发生未预期错误")
        _record_failure(_circuit_key(target), str(exc))
        return _failure(f"调用模型时发生未预期错误：{exc}", target, started)

    _record_success(_circuit_key(target))
    return _success(content, target, usage, started)


def _chat_openai_stream(
    target: dict,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int | None,
    emit: Callable[[str], None],
) -> tuple[str, dict]:
    base_url = target.get("base_url") or ""
    api_key = (target.get("api_key") or "").strip()
    model_name = target.get("model_name") or ""
    if not api_key:
        raise LLMError("OpenAI 兼容 Provider 未配置 api_key，请先在「模型管理」中补全")

    url = _join_url(base_url, "/chat/completions")
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    provider_name = target.get("provider_name") or "OpenAI 兼容服务"
    chunks: list[str] = []
    usage: dict = {}
    try:
        with _client() as client:
            with client.stream(
                "POST", url, headers=_auth_headers(api_key), json=payload
            ) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", "replace")[:160].replace("\n", " ")
                    raise LLMError(
                        f"「{provider_name}」流式调用失败（HTTP {response.status_code}）：{body or '无返回内容'}"
                    )
                for line in response.iter_lines():
                    line = (line or "").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        item = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(item.get("usage"), dict):
                        usage = item["usage"]
                    choices = item.get("choices") or []
                    if not choices or not isinstance(choices[0], dict):
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, list):
                        content = "".join(
                            str(part.get("text") or "")
                            for part in content
                            if isinstance(part, dict)
                        )
                    text = str(content or "")
                    if text:
                        chunks.append(text)
                        emit(text)
    except httpx.TimeoutException as exc:
        raise LLMError(
            f"调用「{provider_name}」超时（{int(REQUEST_TIMEOUT_SECONDS)} 秒）：请检查网络或模型响应速度"
        ) from exc
    except httpx.ConnectError as exc:
        raise LLMError(f"无法连接到「{provider_name}」（{url}）：请确认服务已启动、地址正确") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"调用「{provider_name}」失败：{exc.__class__.__name__}: {exc}") from exc

    content = "".join(chunks).strip()
    if not content:
        raise LLMError("模型服务返回了空回复")
    return content, usage


def _chat_ollama_stream(
    target: dict,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int | None,
    emit: Callable[[str], None],
) -> tuple[str, dict]:
    base_url = target.get("base_url") or DEFAULT_OLLAMA_BASE_URL
    model_name = target.get("model_name") or ""
    url = _join_url(base_url, "/api/chat")
    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": True}
    options: dict[str, Any] = {}
    if temperature is not None:
        options["temperature"] = float(temperature)
    if max_tokens is not None:
        options["num_predict"] = int(max_tokens)
    if options:
        payload["options"] = options

    provider_name = target.get("provider_name") or "Ollama"
    chunks: list[str] = []
    usage: dict = {}
    try:
        with _client() as client:
            with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", "replace")[:160].replace("\n", " ")
                    raise LLMError(
                        f"「{provider_name}」流式调用失败（HTTP {response.status_code}）：{body or '无返回内容'}"
                    )
                for line in response.iter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get("error"):
                        raise LLMError(f"「{provider_name}」返回错误：{item['error']}")
                    message = item.get("message") if isinstance(item.get("message"), dict) else {}
                    text = str(message.get("content") or "")
                    if text:
                        chunks.append(text)
                        emit(text)
                    if item.get("done"):
                        prompt_tokens = _as_int(item.get("prompt_eval_count"), 0)
                        completion_tokens = _as_int(item.get("eval_count"), 0)
                        usage = {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        }
                        break
    except httpx.TimeoutException as exc:
        raise LLMError(f"调用「{provider_name}」超时，请检查本地 Ollama 是否繁忙") from exc
    except httpx.ConnectError as exc:
        raise LLMError(f"无法连接到「{provider_name}」（{url}）：请确认 Ollama 已启动") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"调用「{provider_name}」失败：{exc.__class__.__name__}: {exc}") from exc

    content = "".join(chunks).strip()
    if not content:
        raise LLMError("Ollama 返回了空回复")
    return content, usage


def stream_chat(
    messages: list[dict],
    *,
    model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_delta: Callable[[str], None],
) -> dict:
    """调用模型并逐段回调 ``on_delta(text)``；返回值与 :func:`chat` 相同。"""
    started = time.perf_counter()
    cleaned = _clean_messages(messages)
    if not cleaned:
        return _failure("消息内容为空，无法调用模型", None, started)
    target = _resolve_target(model_id)
    if target is None:
        message = "指定的模型不存在，请检查模型配置" if model_id else "尚未配置可用的模型，请先在「模型管理」中添加并启用模型"
        return _failure(message, None, started)

    model_name = (target.get("model_name") or "").strip()
    provider_type = (target.get("provider_type") or "").strip().lower()
    if not model_name:
        return _failure("模型名称为空，请检查模型配置", target, started)
    temperature = (
        _as_float(target.get("temperature"), 0.3) if temperature is None else float(temperature)
    )
    max_tokens = (
        _as_int(target.get("max_tokens"), 1200) if max_tokens is None else int(max_tokens)
    )

    if provider_type == "mock":
        content = chat(messages, model_id=model_id, temperature=temperature, max_tokens=max_tokens)
        text = str(content.get("content") or "")
        for index in range(0, len(text), 24):
            on_delta(text[index : index + 24])
        return content

    key = _circuit_key(target)
    if _circuit_open(key):
        return _failure(
            "模型服务连续失败，已暂时熔断；请稍后重试或检查模型配置/网络。",
            target,
            started,
        )

    emitted = False
    attempts = max(1, RETRY_ATTEMPTS)
    last_error = ""
    for attempt in range(attempts):
        def emit(text: str) -> None:
            nonlocal emitted
            emitted = True
            on_delta(text)

        try:
            if provider_type == "openai":
                text, usage = _chat_openai_stream(
                    target, cleaned, temperature, max_tokens, emit
                )
            elif provider_type == "ollama":
                text, usage = _chat_ollama_stream(
                    target, cleaned, temperature, max_tokens, emit
                )
            else:
                raise LLMError(f"不支持的 Provider 类型：{provider_type or '空'}")
            _record_success(key)
            return _success(text, target, usage, started)
        except LLMError as exc:
            last_error = str(exc)
            logger.warning(
                "流式模型调用失败（%s / %s，第 %s 次）：%s",
                target.get("provider_name"),
                model_name,
                attempt + 1,
                last_error,
            )
            # Never replay after partial output: the student would see duplicates.
            if emitted or attempt + 1 >= attempts or not _retryable(last_error):
                break
            time.sleep(max(0.0, RETRY_BACKOFF_SECONDS))
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("流式模型调用发生未预期错误")
            last_error = f"调用模型时发生未预期错误：{exc}"
            break

    _record_failure(key, last_error or "流式调用失败")
    return _failure(last_error or "流式调用失败", target, started)


def test_provider(provider_id: str) -> dict:
    """测试 Provider 连通性，返回 ``{ok, message, latency_ms, models?}``；失败也返回对象。"""
    started = time.perf_counter()
    provider = db.query_one("SELECT * FROM providers WHERE id = ?", (provider_id,))
    if provider is None:
        return {"ok": False, "message": "Provider 不存在", "latency_ms": 0}

    provider_name = provider.get("name") or "Provider"
    provider_type = (provider.get("type") or "").strip().lower()
    base_url = provider.get("base_url") or ""

    if provider_type == "mock":
        rows = db.query(
            "SELECT model_name FROM models_registry WHERE provider_id = ? ORDER BY created_at, rowid",
            (provider_id,),
        )
        models = [str(row.get("model_name") or "") for row in rows if row.get("model_name")]
        return {
            "ok": True,
            "message": f"内置演示模型已就绪（无需 API Key），当前配置 {len(models)} 个模型",
            "latency_ms": _elapsed_ms(started),
            "models": models,
        }

    try:
        if provider_type == "openai":
            url = _join_url(base_url, "/models")
            with _client() as client:
                data = _request_json(
                    client,
                    "GET",
                    url,
                    provider_name=provider_name,
                    headers=_auth_headers(provider.get("api_key")),
                )
            items = data.get("data") if isinstance(data.get("data"), list) else []
            models = [
                str(item.get("id"))
                for item in items
                if isinstance(item, dict) and item.get("id")
            ]
        elif provider_type == "ollama":
            url = _join_url(base_url or DEFAULT_OLLAMA_BASE_URL, "/api/tags")
            with _client() as client:
                data = _request_json(
                    client,
                    "GET",
                    url,
                    provider_name=provider_name,
                    headers={"Content-Type": "application/json"},
                )
            items = data.get("models") if isinstance(data.get("models"), list) else []
            models = [
                str(item.get("name") or item.get("model"))
                for item in items
                if isinstance(item, dict) and (item.get("name") or item.get("model"))
            ]
        else:
            raise LLMError(f"不支持的 Provider 类型：{provider_type or '空'}")
    except LLMError as exc:
        return {"ok": False, "message": str(exc), "latency_ms": _elapsed_ms(started)}
    except Exception as exc:  # pragma: no cover - 兜底
        logger.exception("测试 Provider 时发生未预期错误")
        return {
            "ok": False,
            "message": f"测试连接时发生未预期错误：{exc}",
            "latency_ms": _elapsed_ms(started),
        }

    message = (
        f"连接成功，获取到 {len(models)} 个可用模型"
        if models
        else "连接成功，但服务未返回模型列表"
    )
    return {
        "ok": True,
        "message": message,
        "latency_ms": _elapsed_ms(started),
        "models": models[:100],
    }


def test_model(model_id: str) -> dict:
    """真实发送一条「你好」消息测试模型，返回 ``{ok, message, latency_ms, sample}``。"""
    started = time.perf_counter()
    target = _resolve_target(model_id)
    if target is None:
        return {"ok": False, "message": "模型不存在", "latency_ms": 0, "sample": ""}

    provider_type = (target.get("provider_type") or "").strip().lower()
    result = chat(
        [{"role": "user", "content": "你好"}],
        model_id=model_id,
        temperature=0.2,
        max_tokens=64,
    )

    if provider_type == "mock":
        return {
            "ok": True,
            "message": "内置演示模型可用（无需联网，回答由本地知识库摘录生成）",
            "latency_ms": _elapsed_ms(started),
            "sample": str(result.get("content") or ""),
        }

    if result.get("fallback") or not result.get("content"):
        return {
            "ok": False,
            "message": str(result.get("error") or "模型调用失败，请检查配置"),
            "latency_ms": _as_int(result.get("latency_ms"), _elapsed_ms(started)),
            "sample": "",
        }

    return {
        "ok": True,
        "message": f"模型调用成功（耗时 {_as_int(result.get('latency_ms'), 0)} ms）",
        "latency_ms": _as_int(result.get("latency_ms"), _elapsed_ms(started)),
        "sample": str(result.get("content") or "")[:200],
    }

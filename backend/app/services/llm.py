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

import logging
import time
from typing import Any

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

    try:
        if provider_type == "openai":
            content, usage = _chat_openai(target, cleaned, temperature, max_tokens)
        elif provider_type == "ollama":
            content, usage = _chat_ollama(target, cleaned, temperature, max_tokens)
        else:
            raise LLMError(f"不支持的 Provider 类型：{provider_type or '空'}")
    except LLMError as exc:
        logger.warning("模型调用失败（%s / %s）：%s", target.get("provider_name"), model_name, exc)
        return _failure(str(exc), target, started)
    except Exception as exc:  # pragma: no cover - 兜底，避免 500
        logger.exception("模型调用发生未预期错误")
        return _failure(f"调用模型时发生未预期错误：{exc}", target, started)

    return _success(content, target, usage, started)


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

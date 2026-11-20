"""管理助手回复协议的有限容错；不会执行或解释其中的工具。"""
from __future__ import annotations

import json
import re

from .agent_protocol import has_dsml

FORMAT_ERROR = "这次回复格式不完整，请重试；已执行的操作可在任务记录中查看。"
_KEYS = {"reply", "replay"}
_META = {"steps", "conversation_id", "conversation_title"}
_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.S | re.I)
_WRAPPER = re.compile(r'^\{\s*["“”](reply|replay)["“”]\s*[:：]\s*', re.S)
# 模型服务偶尔把内部安全分类文本混进正文；它不是业务回答，绝不能展示给用户。
_SAFETY_NOISE = re.compile(r"</?\s*ds_safety\b|^\s*\[用户未成年\]", re.I)


def has_model_noise(value: object) -> bool:
    return bool(_SAFETY_NOISE.search(str(value or "")))


def _loads(value: str):
    try:
        return json.loads(value, strict=False)
    except (TypeError, ValueError):
        return None


def _decode_wrapper(value: str):
    data = _loads(value)
    if data is not None:
        return data
    match = _WRAPPER.match(value)
    if not match:
        return None
    # 仅修复已知单字段回复外壳，不替换正文中的中文引号。
    tail = value[match.end():].strip()
    if not tail.endswith("}"):
        return None
    scalar = tail[:-1].strip()
    if len(scalar) < 2 or scalar[0] not in '\"“”' or scalar[-1] not in '\"“”':
        return None
    if scalar[0] == scalar[-1] == '"':
        decoded = _loads(scalar)
        if isinstance(decoded, str):
            return {match.group(1): decoded}
    # 中文标点容错只接受单个纯字符串，避免把其他协议字段当回答。
    if re.search(r'[,，]\s*["“”][\w-]+["“”]\s*[:：]', scalar):
        return None
    inner = scalar[1:-1]
    if scalar[0] == '"':
        escaped = re.sub(r'(?<!\\)"', r'\\"', inner)
        decoded = _loads('"' + escaped + '"')
        if isinstance(decoded, str):
            inner = decoded
    return {match.group(1): inner}


def normalize_reply(value: object, *, protocol: bool = False) -> str:
    """只剥离完整已知回复外壳；普通文本与 JSON 示例原样保留。"""
    current = value
    unwrapped = False
    for _ in range(5):
        if isinstance(current, dict):
            keys = set(current)
            reply_key = next((key for key in ("reply", "replay") if key in current), None)
            if reply_key and keys <= _KEYS | _META:
                current = current[reply_key]
                unwrapped = True
                continue
            return FORMAT_ERROR if protocol or unwrapped else json.dumps(current, ensure_ascii=False)
        if not isinstance(current, str):
            return FORMAT_ERROR if protocol or unwrapped else str(current or "")
        text = current.strip()
        if has_model_noise(text):
            return FORMAT_ERROR
        if has_dsml(text):
            return FORMAT_ERROR
        if not text:
            return ""
        fenced = _FENCE.fullmatch(text)
        candidate = fenced.group(1).strip() if fenced else text
        data = _decode_wrapper(candidate)
        if isinstance(data, dict):
            keys = set(data)
            reply_key = next((key for key in ("reply", "replay") if key in data), None)
            if reply_key and keys <= _KEYS | _META:
                current = data[reply_key]
                unwrapped = True
                continue
            if protocol and (keys & {"tool", "args", "reply", "replay"}):
                return FORMAT_ERROR
        if isinstance(data, str) and _WRAPPER.match(data.strip()):
            current = data
            unwrapped = True
            continue
        if protocol and (_WRAPPER.match(candidate) or re.match(r'^\{\s*["“”](?:tool|args)["“”]\s*:', candidate)):
            return FORMAT_ERROR
        return text
    return FORMAT_ERROR

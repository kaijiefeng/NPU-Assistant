"""管理助手模型输出适配：只接收完整单工具 DSML，不执行正文里的示例。"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

MARKER = re.compile(r"<\s*/?\s*[|｜]+\s*DSML\b", re.I)
_TAG = re.compile(r"<\s*(/?)\s*[|｜]+\s*DSML\s*[|｜]+\s*([A-Za-z_]+)", re.I)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FENCE = re.compile(r"\A```(?:xml|dsml)?\s*\n?(.*?)\n?```\s*\Z", re.S | re.I)


class AgentProtocolError(ValueError):
    """工具协议不可明确解析；不能回退成用户可见的成功回答。"""


def has_dsml(value: str) -> bool:
    return bool(MARKER.search(str(value or "")))


def parse_dsml_call(content: str) -> dict | None:
    """明确、完整、唯一的模型工具调用转为内部JSON协议；解析不触发执行。"""
    text = str(content or "").strip()
    if not has_dsml(text):
        return None
    fenced = _FENCE.fullmatch(text)
    if fenced:
        text = fenced.group(1).strip()
    normalized = _TAG.sub(lambda match: '<' + match.group(1) + match.group(2).lower(), text)
    if '<!' in normalized or '<?' in normalized or has_dsml(normalized):
        raise AgentProtocolError("模型工具调用格式不完整，尚未执行该操作。")
    try:
        root = ET.fromstring(normalized)
    except ET.ParseError as exc:
        raise AgentProtocolError("模型工具调用格式不完整，尚未执行该操作。") from exc
    if root.tag not in {"calls", "function_calls", "tool_calls"} or root.attrib or (root.text or '').strip():
        raise AgentProtocolError("模型工具调用存在混杂内容，尚未执行该操作。")
    children = list(root)
    if len(children) != 1 or children[0].tag != 'invoke':
        raise AgentProtocolError("本轮需要单个明确的工具调用，尚未执行这些操作。")
    invoke = children[0]
    name = invoke.attrib.get('name') or ''
    if set(invoke.attrib) != {'name'} or not _NAME.fullmatch(name) or (invoke.tail or '').strip():
        raise AgentProtocolError("模型工具名称格式不正确，尚未执行该操作。")
    args = {}
    if not list(invoke):
        raw = (invoke.text or '').strip()
        if raw:
            try:
                args = json.loads(raw)
            except ValueError as exc:
                raise AgentProtocolError("模型工具参数不是有效JSON，尚未执行该操作。") from exc
            if not isinstance(args, dict):
                raise AgentProtocolError("模型工具参数必须是对象，尚未执行该操作。")
    else:
        if (invoke.text or '').strip():
            raise AgentProtocolError("模型工具参数含混杂内容，尚未执行该操作。")
        for parameter in invoke:
            key = parameter.attrib.get('name') or ''
            string = parameter.attrib.get('string', 'true')
            if parameter.tag != 'parameter' or list(parameter) or set(parameter.attrib) - {'name', 'string'} or not _NAME.fullmatch(key) or key in args or string not in {'true', 'false'} or (parameter.tail or '').strip():
                raise AgentProtocolError("模型工具参数格式不正确，尚未执行该操作。")
            value = parameter.text or ''
            if string == 'false':
                try:
                    value = json.loads(value.strip())
                except ValueError as exc:
                    raise AgentProtocolError("模型工具参数类型不正确，尚未执行该操作。") from exc
            args[key] = value
    return {"tool": name, "args": args}


def parse_json_tool_sequence(content: str) -> dict | None:
    """兼容工具对象后附reply的双JSON，预先声称的结果绝不作为已执行事实。"""
    text = str(content or '').strip()
    if not text.startswith('{'):
        return None
    decoder = json.JSONDecoder()
    objects = []
    cursor = 0
    try:
        while cursor < len(text):
            value, end = decoder.raw_decode(text, cursor)
            objects.append(value)
            cursor = end
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if len(objects) > 2:
                raise AgentProtocolError('模型一次返回了多个操作，请改为逐步执行。')
    except json.JSONDecodeError:
        return None
    if len(objects) == 1:
        value = objects[0]
        if isinstance(value, dict) and ('tool' in value or 'name' in value) and ('reply' in value or 'replay' in value):
            raise AgentProtocolError('模型同时返回工具与结果，尚未执行；请重试。')
        return None
    first, second = objects
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise AgentProtocolError('模型返回多个不明确的协议对象，尚未执行这些操作。')
    name = first.get('tool') or (first.get('name') if any(k in first for k in ('args', 'arguments', 'parameters')) else '')
    args = first.get('args', first.get('arguments', first.get('parameters', {})))
    if (set(first) - {'tool', 'name', 'args', 'arguments', 'parameters'}
            or not _NAME.fullmatch(str(name or '')) or not isinstance(args, dict)
            or set(second) - {'reply', 'replay'}
            or not any(isinstance(second.get(key), str) for key in ('reply', 'replay'))):
        raise AgentProtocolError('模型返回多个不明确的协议对象，尚未执行这些操作。')
    return {'tool': name, 'args': args}

"""Agent 工具结果的结构化裁剪与可公开过程信息。"""
from __future__ import annotations

import json

_PRIVATE = {"password", "old_password", "new_password", "api_key", "app_secret", "secret", "token", "access_token", "csrf_token", "authorization", "admin_token"}
_PRIORITY = ("error", "need_confirm", "message", "path", "job_id", "task_id", "document_id", "build_id", "directory", "accessible", "source", "saved_path", "default_path", "files", "status", "ok", "total", "count", "preview")


def public_value(value, *, depth: int = 0):
    if depth > 5:
        return "（更深层内容已省略）"
    if isinstance(value, dict):
        return {str(key): "***" if str(key).lower() in _PRIVATE or any(part in str(key).lower() for part in ("password", "secret", "api_key")) else public_value(item, depth=depth + 1) for key, item in list(value.items())[:60]}
    if isinstance(value, (list, tuple)):
        return [public_value(item, depth=depth + 1) for item in value[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def result_json(result: dict, budget: int = 2400) -> str:
    """始终返回合法 JSON，优先保留错误、目录与任务号，明确标记省略。"""
    value = public_value(result)
    encode = lambda item: json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":"))
    raw = encode(value)
    if len(raw) <= budget:
        return raw
    keys = list(dict.fromkeys([key for key in _PRIORITY if key in value] + list(value)))
    def trim(item, size):
        if isinstance(item, str):
            return item if len(item) <= size else item[:size] + "…（已截短）"
        if isinstance(item, list):
            return [trim(entry, size) for entry in item[:3]]
        if isinstance(item, dict):
            ordered = list(dict.fromkeys([key for key in _PRIORITY if key in item] + list(item)))
            return {key: trim(item[key], size) for key in ordered[:12]}
        return item
    for size in (400, 180, 80):
        compact = {key: trim(value[key], size) for key in keys[:18]}
        compact["_truncated"] = True
        compact["_note"] = "结果已裁剪；请基于现有字段回答，不要重复同一调用。"
        if len(encode(compact)) <= budget:
            return encode(compact)
    compact = {"_truncated": True, "_note": "结果过长，仅保留关键字段。"}
    for key in keys:
        candidate = {**compact, key: trim(value[key], 100)}
        if len(encode(candidate)) <= budget:
            compact = candidate
    return encode(compact)


def summarize_result(result: dict) -> str:
    if result.get("error"):
        return str(result["error"])[:160]
    if result.get("need_confirm"):
        return "需要用户确认；尚未执行"
    directory = result.get("directory")
    if isinstance(directory, dict) and directory.get("path"):
        suffix = "可访问" if directory.get("accessible") else "当前不可访问"
        return f"目录：{directory['path']}（{suffix}）"[:200]
    for key in ("message", "job_id", "task_id", "document_id", "build_id", "path", "title"):
        if result.get(key):
            return str(result[key])[:200]
    if isinstance(result.get("items"), list):
        return f"已取得 {len(result['items'])} 条记录"
    if "documents" in result and "wiki_pages" in result:
        return f"源文件 {result['documents']} 篇，知识词条 {result['wiki_pages']} 条"
    if "content" in result:
        return f"已读取 {result.get('path') or '文本内容'}，{result.get('total_lines') or len(str(result['content']).splitlines())} 行"
    if "lines" in result:
        return f"已读取 {result.get('count', len(result['lines']))} 行日志"
    if "count" in result or "total" in result:
        return f"已取得 {result.get('total', result.get('count'))} 条记录"
    return "查询完成" if "ok" not in result else "操作完成" if result["ok"] else "操作未完成"


def known_result_reply(results: list[tuple[str, dict]], reason: str = "") -> str:
    """模型无法收尾时只陈述工具已证实的信息，不把未知结果写成成功。"""
    lines = []
    for name, result in results:
        directory = result.get("directory")
        if name in {"get_scan_directory", "set_scan_directory"}:
            directory = directory if isinstance(directory, dict) else result
            if directory.get("path"):
                origin = "已保存的目录设置" if directory.get("source") == "saved" else "系统默认配置"
                lines.extend([f"当前默认扫描文件夹：\n`{directory['path']}`", f"来源：{origin}。"])
                if not directory.get("accessible", True):
                    lines.append("目录当前不可访问，请在“源文件 → 扫描文件夹”中核对路径和允许范围。")
        elif result.get("need_confirm"):
            lines.append("该操作需要确认，本轮尚未执行。请核对参数后明确是否继续。")
        elif result.get("job_id"):
            lines.append(f"已提交后台任务：`{result['job_id']}`。可在“运维管理 → 任务”查看进度。")
    if not lines:
        evidence, errors = [], []
        for name, result in results[-3:]:
            item = summarize_result(result)
            (errors if result.get("error") else evidence).append(item)
        if evidence:
            lines.append("已取得的信息：\n" + "\n".join(f"- {item}" for item in dict.fromkeys(evidence)))
        if errors:
            lines.append("未完成：\n" + "\n".join(f"- {item}" for item in dict.fromkeys(errors)))
    if reason:
        lines.append(reason)
    return "\n\n".join(dict.fromkeys(lines)) or "这次没有取得可用结果，请稍后重试或提供更具体的信息。"

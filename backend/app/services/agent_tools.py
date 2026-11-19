"""管理 Agent 工具层：把页面上的管理接口封装成 LLM 可调用的 tools。

信任边界（docs/ADMIN-OPS.md「Agent 面板」节）：
- 工具权限 = 调用者权限：每个工具声明所需权限键，执行前按角色校验
  （super_admin 的 ``*`` 通配命中），Agent 不越权代理；
- 长耗时操作（扫描/重编译/找备选/订阅检查/运维修复）一律走 jobs 后台
  队列，工具返回 job_id 供模型轮询，不阻塞对话；
- 写操作全部记录审计日志（``agent.tool.<name>``）；高危工具标记
  ``dangerous``，由系统提示词约束模型先向用户复述确认。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable

from .. import db
from . import alerts, auth, discovery, importer, ingest, insights, jobs, ops_agent, wiki_builder

logger = logging.getLogger("npu-assistant")

# 与 main.py 的 INTERNAL_PATH_RE 同款：错误信息回给模型/前端前抹掉本机路径
INTERNAL_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    permission: str
    parameters: dict  # {参数名: 中文说明}，直接进入提示词
    handler: Callable[[dict, dict], dict]
    write: bool = False
    dangerous: bool = False


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, permission: str, parameters: dict, *, write: bool = False, dangerous: bool = False):
    def decorator(fn: Callable[[dict, dict], dict]) -> Callable[[dict, dict], dict]:
        REGISTRY[name] = ToolSpec(name, description, permission, parameters, fn, write, dangerous)
        return fn

    return decorator


def tools_for(role: str) -> list[ToolSpec]:
    """按角色过滤可用工具（权限 = 调用者权限）。"""
    return [spec for spec in REGISTRY.values() if auth.has_permission(str(role or ""), spec.permission)]


def tool_catalog(specs: list[ToolSpec]) -> list[dict]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            **({"dangerous": True} if spec.dangerous else {}),
        }
        for spec in specs
    ]


def call_tool(name: str, args: dict, principal: dict) -> dict:
    """执行一个工具。所有失败都以 ``{"error": ...}`` 返回给模型自行纠正，不抛异常。"""
    spec = REGISTRY.get(str(name or "").strip())
    if not spec:
        return {"error": f"未知工具 {name}，请从工具列表中选择"}
    if not auth.has_permission(str((principal or {}).get("role") or ""), spec.permission):
        return {"error": f"权限不足：当前角色无法执行 {spec.name}（需要 {spec.permission}）"}
    clean_args = args if isinstance(args, dict) else {}
    # 高危工具的服务端确认门：无论提示词怎么说，首次调用一律拦下，必须显式带 confirm
    if spec.dangerous and not _bool(clean_args, "confirm", False):
        return {
            "need_confirm": True,
            "message": (
                f"{spec.name} 是高危操作。请先用 reply 向用户复述以下参数并询问是否执行，"
                '用户明确同意后再带 {"confirm": true} 重新调用本工具；不同意则放弃。'
            ),
            "args": clean_args,
        }
    try:
        result = spec.handler(clean_args, principal)
        if not isinstance(result, dict):
            result = {"ok": bool(result)}
    except ValueError as exc:
        _audit_tool(spec, principal, clean_args, ok=False, detail=str(exc))
        return {"error": _clean_error(str(exc))}
    except Exception as exc:  # noqa: BLE001 - 工具失败反馈给模型而不是打断对话
        logger.exception("Agent 工具 %s 执行失败", spec.name)
        _audit_tool(spec, principal, clean_args, ok=False, detail=str(exc))
        return {"error": _clean_error(f"工具执行失败：{exc}")}
    if spec.write:
        _audit_tool(spec, principal, clean_args, ok=True)
    return result


def _audit_tool(spec: ToolSpec, principal: dict, args: dict, *, ok: bool, detail: str = "") -> None:
    """写工具的成败都留痕（失败尝试同样可追溯），异常永不外抛。"""
    payload = json.dumps(args, ensure_ascii=False)[:300]
    if detail:
        payload = f"{payload} error={_clean_error(detail)[:120]}"
    try:
        auth.record_audit(
            principal,
            f"agent.tool.{spec.name}",
            target_type="agent_tool",
            target_id=spec.name,
            detail=payload,
            status_code=200 if ok else 400,
        )
    except Exception:  # noqa: BLE001
        logger.debug("agent 工具审计写入失败", exc_info=True)


def _clean_error(message: str) -> str:
    return INTERNAL_PATH_RE.sub("<path>", str(message))[:300]


# ---------------------------------------------------------------------------
# 参数辅助
# ---------------------------------------------------------------------------
def _str(args: dict, key: str, default: str = "") -> str:
    value = args.get(key)
    return default if value is None else str(value).strip()


def _bool(args: dict, key: str, default: bool = False) -> bool:
    """布尔参数解析：模型传 "false"/"0"/"true" 等字符串时按语义处理。"""
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "是"}
    return bool(value)


def _limit(args: dict, default: int = 20, hi: int = 50) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), hi))
    except (TypeError, ValueError):
        return default


def _principal_name(principal: dict) -> str:
    return str((principal or {}).get("username") or "")


def _require(row: dict | None, message: str) -> dict:
    if not row:
        raise ValueError(message)
    return row


# ---------------------------------------------------------------------------
# 查询工具（content:read / insights:read，所有角色可用）
# ---------------------------------------------------------------------------
@tool(
    "system_stats",
    "查看系统总览：文档、词条、未处理缺口、活跃任务、未处理告警的数量。",
    "content:read",
    {},
)
def _system_stats(args: dict, principal: dict) -> dict:
    def count(sql: str, params: tuple = ()) -> int:
        return int((db.query_one(sql, params) or {}).get("n") or 0)

    return {
        "documents": count("SELECT COUNT(*) AS n FROM documents"),
        "wiki_pages": count("SELECT COUNT(*) AS n FROM wiki_pages"),
        "open_gaps": count("SELECT COUNT(*) AS n FROM knowledge_gaps WHERE status = 'open'"),
        "active_jobs": count(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"
        ),
        "open_alerts": count("SELECT COUNT(*) AS n FROM system_alerts WHERE status = 'open'"),
        "subscriptions": count("SELECT COUNT(*) AS n FROM subscriptions"),
    }


@tool(
    "list_documents",
    "列出知识库源文件（按收录时间倒序），可按标题关键词过滤。",
    "content:read",
    {"query": "可选，标题关键词", "limit": "可选，默认 20，最多 50"},
)
def _list_documents(args: dict, principal: dict) -> dict:
    keyword = _str(args, "query")
    sql = "SELECT id, title, doc_type, source_type, created_at FROM documents"
    params: tuple = ()
    if keyword:
        sql += " WHERE title LIKE ?"
        params = (f"%{keyword}%",)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params = params + (_limit(args),)
    return {"items": db.query(sql, params)}


@tool(
    "get_document",
    "查看一个源文件的详情及其贡献的 Wiki 词条。",
    "content:read",
    {"document_id": "必填，源文件 ID"},
)
def _get_document(args: dict, principal: dict) -> dict:
    doc_id = _str(args, "document_id")
    row = _require(db.query_one("SELECT * FROM documents WHERE id = ?", (doc_id,)), "源文件不存在")
    pages = db.query(
        "SELECT p.id, p.slug, p.title, p.status FROM wiki_pages p "
        "JOIN wiki_sources s ON s.page_id = p.id WHERE s.document_id = ? "
        "GROUP BY p.id, p.slug, p.title, p.status ORDER BY p.title",
        (doc_id,),
    )
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "doc_type": row.get("doc_type"),
        "source_type": row.get("source_type"),
        "source_url": row.get("source_url"),
        "created_at": row.get("created_at"),
        "wiki_pages": pages,
    }


@tool(
    "list_gaps",
    "列出知识缺口（学生问过但没有词条回答的问题）。",
    "insights:read",
    {"status": "可选，open/linked/resolved/ignored，默认 open", "query": "可选，问题关键词", "limit": "可选，默认 20"},
)
def _list_gaps(args: dict, principal: dict) -> dict:
    return insights.list_gaps(
        status=_str(args, "status") or "open",
        query=_str(args, "query"),
        limit=_limit(args),
    )


@tool(
    "get_gap",
    "查看一个知识缺口的详情（含 AI 备选建议）。",
    "insights:read",
    {"gap_id": "必填，缺口 ID"},
)
def _get_gap(args: dict, principal: dict) -> dict:
    try:
        return insights.get_gap(_str(args, "gap_id"))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


@tool(
    "insights_overview",
    "查看运营总览（问答量、反馈、缺口趋势）。",
    "insights:read",
    {"days": "可选，统计天数，默认 7"},
)
def _insights_overview(args: dict, principal: dict) -> dict:
    try:
        days = max(1, min(int(args.get("days") or 7), 90))
    except (TypeError, ValueError):
        days = 7
    return insights.overview(days=days)


@tool(
    "list_feedback",
    "列出学生对回答的反馈（差评原因常提示知识缺口）。",
    "insights:read",
    {"status": "可选，处理状态", "limit": "可选，默认 20"},
)
def _list_feedback(args: dict, principal: dict) -> dict:
    return insights.list_feedback(status=_str(args, "status"), limit=_limit(args))


@tool(
    "list_subscriptions",
    "列出全部订阅网址及其最近检查状态与待挑选的新内容。",
    "insights:read",
    {},
)
def _list_subscriptions(args: dict, principal: dict) -> dict:
    return discovery.list_subscriptions()


@tool(
    "get_subscription",
    "查看一个订阅的详情（含 discoveries 待挑选列表）。",
    "insights:read",
    {"subscription_id": "必填，订阅 ID"},
)
def _get_subscription(args: dict, principal: dict) -> dict:
    return discovery.get_subscription(_str(args, "subscription_id"))


@tool(
    "list_jobs",
    "列出后台任务（收录/扫描/重建/找备选/修复等）。",
    "content:read",
    {"status": "可选，queued/running/succeeded/failed/cancelled/active", "kind": "可选，任务类型", "limit": "可选，默认 20"},
)
def _list_jobs(args: dict, principal: dict) -> dict:
    return jobs.list_jobs(status=_str(args, "status"), kind=_str(args, "kind"), limit=_limit(args))


@tool(
    "get_job",
    "查看一个后台任务的进度与结果（长任务提交后用它轮询）。",
    "content:read",
    {"job_id": "必填，任务 ID"},
)
def _get_job(args: dict, principal: dict) -> dict:
    job = jobs.get_job(_str(args, "job_id"))
    if not job or not job.get("id"):
        raise ValueError("任务不存在")
    return job


@tool(
    "list_alerts",
    "列出系统告警（LLM 熔断、任务失败等）。",
    "insights:read",
    {"status": "可选，open/acknowledged/resolved", "level": "可选，级别", "limit": "可选，默认 20"},
)
def _list_alerts(args: dict, principal: dict) -> dict:
    return alerts.list_alerts(status=_str(args, "status"), level=_str(args, "level"), limit=_limit(args))


@tool(
    "list_wiki_builds",
    "列出最近的 Wiki 构建记录（全量重建进度用）。",
    "content:read",
    {"limit": "可选，默认 10"},
)
def _list_wiki_builds(args: dict, principal: dict) -> dict:
    rows = db.query(
        "SELECT id, document_id, status, pages_created, pages_updated, pages_removed, error, "
        "started_at, finished_at FROM wiki_builds ORDER BY started_at DESC, rowid DESC LIMIT ?",
        (_limit(args, default=10, hi=100),),
    )
    return {"items": rows}


# ---------------------------------------------------------------------------
# 知识库写操作（content:write）
# ---------------------------------------------------------------------------
def _tags(args: dict) -> list[str] | None:
    raw = args.get("tags")
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()] or None
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()] or None
    return None


@tool(
    "import_url",
    "收录一个网页/公众号文章链接：抓取 → 提取正文 → 查重 → 编译词条（同步完成）。",
    "content:write",
    {"url": "必填，http/https 链接", "title": "可选，标题", "tags": "可选，逗号分隔标签"},
    write=True,
)
def _import_url(args: dict, principal: dict) -> dict:
    result = importer.import_url(_str(args, "url"), title=_str(args, "title") or None, tags=_tags(args))
    document = result.get("document") or {}
    return {
        "duplicated": bool(result.get("duplicated")),
        "document_id": document.get("id"),
        "title": document.get("title"),
        "pages": [
            {"id": page.get("id"), "title": page.get("title")}
            for page in (result.get("pages") or [])
        ],
    }


@tool(
    "import_text",
    "收录一段粘贴文本（30 字以上），生成源文件并编译词条。",
    "content:write",
    {"text": "必填，正文内容", "title": "可选，标题", "tags": "可选，逗号分隔标签"},
    write=True,
)
def _import_text(args: dict, principal: dict) -> dict:
    result = importer.import_text(_str(args, "title") or None, _str(args, "text"), tags=_tags(args))
    document = result.get("document") or {}
    return {
        "document_id": document.get("id"),
        "title": document.get("title"),
        "pages": [
            {"id": page.get("id"), "title": page.get("title")}
            for page in (result.get("pages") or [])
        ],
    }


@tool(
    "scan_dir",
    "扫描服务器本地文件夹批量收录（后台任务，返回 job_id）。",
    "content:write",
    {"path": "可选，服务器目录绝对路径，留空用默认扫描目录", "recursive": "可选，是否递归子目录，默认 true", "mode": "可选，skip=只收新文件 / update=同名变化也重编"},
    write=True,
)
def _scan_dir(args: dict, principal: dict) -> dict:
    from .. import config

    path = _str(args, "path")
    recursive = _bool(args, "recursive", True)
    mode = "update" if _str(args, "mode").lower() == "update" else "skip"
    listed = ingest.list_scan_files(path, recursive=recursive, max_files=int(config.SCAN_MAX_FILES) + 1)
    if not listed:
        raise ValueError("该文件夹下没有可收录的文件（支持 md/txt/pdf/docx/doc/xlsx/csv/json/html）")
    job = jobs.create_job(
        "scan_dir", {"path": path, "recursive": recursive, "mode": mode}, created_by=_principal_name(principal)
    )
    return {
        "job_id": job["id"],
        "files": min(len(listed), int(config.SCAN_MAX_FILES)),
        "message": f"已提交后台扫描任务（模式 {mode}），用 get_job 轮询 {job['id']}。",
    }


def _rebuild_all_bg(build_id: str) -> None:
    """后台线程：与 POST /api/wiki/rebuild 相同的全量重建收尾逻辑。"""
    try:
        result = wiki_builder.rebuild_all()
        wiki_builder.finish_build(
            build_id,
            status=str(result.get("status") or "ready"),
            model=result.get("model"),
            pages_created=int(result.get("pages_created") or 0),
            pages_updated=int(result.get("pages_updated") or 0),
            pages_removed=int(result.get("pages_removed") or 0),
            error=result.get("error"),
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底
        logger.exception("Agent 触发的全量重建失败")
        wiki_builder.finish_build(build_id, status="failed", error=f"重建全部词条失败：{exc}")


@tool(
    "rebuild_wiki",
    "全量重建所有 Wiki 词条（后台执行，耗时长；高危：会重写全部词条）。",
    "content:write",
    {},
    write=True,
    dangerous=True,
)
def _rebuild_wiki(args: dict, principal: dict) -> dict:
    if not db.query_one("SELECT id FROM documents LIMIT 1"):
        raise ValueError("知识库还没有任何源文件，无内容可重建")
    build_id = wiki_builder.start_build(None, None)
    threading.Thread(target=_rebuild_all_bg, args=(build_id,), daemon=True, name="npu-agent-rebuild").start()
    return {
        "build_id": build_id,
        "status": "running",
        "message": f"全量重建已在后台启动（构建 {build_id}），用 list_wiki_builds 查询进度。",
    }


@tool(
    "reindex_document",
    "重新解析并编译单篇源文件（后台任务，返回 job_id）。",
    "content:write",
    {"document_id": "必填，源文件 ID"},
    write=True,
)
def _reindex_document(args: dict, principal: dict) -> dict:
    doc_id = _str(args, "document_id")
    _require(db.query_one("SELECT id FROM documents WHERE id = ?", (doc_id,)), "源文件不存在")
    job = jobs.create_job("reindex", {"document_id": doc_id}, created_by=_principal_name(principal))
    return {"job_id": job["id"], "message": f"已提交重编任务，用 get_job 轮询 {job['id']}。"}


@tool(
    "delete_document",
    "删除一个源文件及其独占词条（高危：不可恢复）。",
    "content:write",
    {"document_id": "必填，源文件 ID"},
    write=True,
    dangerous=True,
)
def _delete_document(args: dict, principal: dict) -> dict:
    doc_id = _str(args, "document_id")
    _require(db.query_one("SELECT id FROM documents WHERE id = ?", (doc_id,)), "源文件不存在")
    result = ingest.delete_document(doc_id)
    if not result.get("ok"):
        raise ValueError("删除失败：文档不存在或已被删除")
    return {"ok": True, "pages_removed": int(result.get("pages_removed") or 0)}


@tool(
    "retry_job",
    "重试一个失败的后台任务。",
    "content:write",
    {"job_id": "必填，任务 ID"},
    write=True,
)
def _retry_job(args: dict, principal: dict) -> dict:
    return jobs.retry_job(_str(args, "job_id"))


@tool(
    "cancel_job",
    "取消一个排队中/运行中的后台任务。",
    "content:write",
    {"job_id": "必填，任务 ID"},
    write=True,
)
def _cancel_job(args: dict, principal: dict) -> dict:
    return jobs.request_cancel(_str(args, "job_id"))


# ---------------------------------------------------------------------------
# 服务台写操作（insights:write）
# ---------------------------------------------------------------------------
@tool(
    "gap_suggest",
    "为一个知识缺口检索白名单源并由 AI 裁决备选资料（后台任务，返回 job_id）。",
    "insights:write",
    {"gap_id": "必填，缺口 ID"},
    write=True,
)
def _gap_suggest(args: dict, principal: dict) -> dict:
    gap_id = _str(args, "gap_id")
    insights.get_gap(gap_id)  # 不存在会抛 ValueError
    job = jobs.create_job("gap_suggest", {"gap_id": gap_id}, created_by=_principal_name(principal))
    return {
        "job_id": job["id"],
        "message": f"已开始检索备选（任务 {job['id']}），完成后用 get_gap 查看 suggestions。",
    }


@tool(
    "gap_collect",
    "收录缺口的一个备选链接并自动关联该缺口。",
    "insights:write",
    {"gap_id": "必填，缺口 ID", "url": "必填，要收录的链接", "title": "可选，标题"},
    write=True,
)
def _gap_collect(args: dict, principal: dict) -> dict:
    from . import gap_suggest as gap_suggest_service

    result = gap_suggest_service.collect_suggestion(
        _str(args, "gap_id"), _str(args, "url"), _str(args, "title")
    )
    document = result.get("document") or {}
    return {"document_id": document.get("id"), "title": document.get("title"), "gap_linked": True}


@tool(
    "gap_link",
    "把一个缺口标记为已关联（已有词条/文档解答了它）。",
    "insights:write",
    {"gap_id": "必填", "document_id": "可选，解答文档 ID", "page_id": "可选，解答词条 ID", "note": "可选，备注"},
    write=True,
)
def _gap_link(args: dict, principal: dict) -> dict:
    return insights.update_gap(
        _str(args, "gap_id"),
        "link",
        page_id=_str(args, "page_id"),
        document_id=_str(args, "document_id"),
        note=_str(args, "note"),
    )


@tool("gap_resolve", "把一个缺口标记为已解决。", "insights:write", {"gap_id": "必填", "note": "可选，备注"}, write=True)
def _gap_resolve(args: dict, principal: dict) -> dict:
    return insights.update_gap(_str(args, "gap_id"), "resolve", note=_str(args, "note"))


@tool("gap_ignore", "忽略一个缺口（不再提醒）。", "insights:write", {"gap_id": "必填", "note": "可选，备注"}, write=True)
def _gap_ignore(args: dict, principal: dict) -> dict:
    return insights.update_gap(_str(args, "gap_id"), "ignore", note=_str(args, "note"))


@tool(
    "subscription_create",
    "添加一个订阅网址（列表页），供定期检查新内容。",
    "insights:write",
    {"url": "必填，列表页链接", "name": "可选，名称"},
    write=True,
)
def _subscription_create(args: dict, principal: dict) -> dict:
    sub = discovery.create_subscription(_str(args, "name"), _str(args, "url"))
    return {"id": sub["id"], "name": sub["name"], "url": sub["url"]}


@tool("subscription_delete", "删除一个订阅。", "insights:write", {"subscription_id": "必填"}, write=True)
def _subscription_delete(args: dict, principal: dict) -> dict:
    return discovery.delete_subscription(_str(args, "subscription_id"))


@tool(
    "subscription_toggle",
    "启用/停用一个订阅。",
    "insights:write",
    {"subscription_id": "必填", "enabled": "必填，true/false"},
    write=True,
)
def _subscription_toggle(args: dict, principal: dict) -> dict:
    if "enabled" not in args:
        raise ValueError("缺少 enabled 参数（true 启用 / false 停用）")
    return discovery.toggle_subscription(_str(args, "subscription_id"), _bool(args, "enabled", False))


@tool(
    "subscription_check",
    "立即检查一个订阅有没有新内容（后台任务，返回 job_id）。",
    "insights:write",
    {"subscription_id": "必填"},
    write=True,
)
def _subscription_check(args: dict, principal: dict) -> dict:
    sub_id = _str(args, "subscription_id")
    discovery.get_subscription(sub_id)  # 不存在会抛 ValueError
    job = jobs.create_job(
        "subscription_check", {"subscription_id": sub_id}, created_by=_principal_name(principal)
    )
    return {"job_id": job["id"], "message": f"已开始检查（任务 {job['id']}），完成后用 get_subscription 查看。"}


@tool(
    "subscription_collect",
    "收录订阅发现中的一条内容。",
    "insights:write",
    {"subscription_id": "必填", "url": "必填，发现的链接", "title": "可选，标题"},
    write=True,
)
def _subscription_collect(args: dict, principal: dict) -> dict:
    result = discovery.collect_discovery(_str(args, "subscription_id"), _str(args, "url"), _str(args, "title"))
    document = result.get("document") or {}
    return {"document_id": document.get("id"), "title": document.get("title")}


@tool(
    "subscription_ignore",
    "忽略订阅发现中的一条内容（不再重复提示）。",
    "insights:write",
    {"subscription_id": "必填", "url": "必填，要忽略的链接"},
    write=True,
)
def _subscription_ignore(args: dict, principal: dict) -> dict:
    return discovery.ignore_discovery(_str(args, "subscription_id"), _str(args, "url"))


@tool(
    "alert_ack",
    "确认一个告警（表示已知悉）。",
    "insights:write",
    {"alert_id": "必填"},
    write=True,
)
def _alert_ack(args: dict, principal: dict) -> dict:
    return alerts.update_alert(_str(args, "alert_id"), "acknowledged", actor=_principal_name(principal))


@tool(
    "alert_resolve",
    "解决一个告警。",
    "insights:write",
    {"alert_id": "必填"},
    write=True,
)
def _alert_resolve(args: dict, principal: dict) -> dict:
    return alerts.update_alert(_str(args, "alert_id"), "resolved", actor=_principal_name(principal))


# ---------------------------------------------------------------------------
# 运维 Agent（ops:manage，仅超级管理员）
# ---------------------------------------------------------------------------
@tool(
    "ops_task_create",
    "创建运维修复任务：粘贴报错，AI 定位并给出方案（后台分析，之后需人工批准执行）。",
    "ops:manage",
    {"error_input": "必填，报错文本（≥20 字）", "title": "可选，任务标题"},
    write=True,
)
def _ops_task_create(args: dict, principal: dict) -> dict:
    task = ops_agent.create_task(_str(args, "error_input"), _str(args, "title"), created_by=_principal_name(principal))
    job = jobs.create_job("ops_analyze", {"task_id": task["id"]}, created_by=_principal_name(principal))
    return {
        "task_id": task["id"],
        "job_id": job["id"],
        "status": task["status"],
        "message": "已开始分析，方案生成后需人工批准才会执行修复。",
    }


@tool(
    "ops_task_approve",
    "批准一个待许可的修复任务，开始在工作树内自动修复+测试+推送（高危）。",
    "ops:manage",
    {"task_id": "必填", "run_e2e": "可选，是否跑端到端验证，默认 false"},
    write=True,
    dangerous=True,
)
def _ops_task_approve(args: dict, principal: dict) -> dict:
    task_id = _str(args, "task_id")
    task = ops_agent.approve(task_id)
    job = jobs.create_job(
        "ops_execute",
        {"task_id": task_id, "run_e2e": _bool(args, "run_e2e", False)},
        created_by=_principal_name(principal),
        max_attempts=1,
    )
    return {
        "task_id": task_id,
        "job_id": job["id"],
        "status": task["status"],
        "message": "已批准，修复流水线执行中（一次性工作树内进行）。",
    }


@tool("ops_task_cancel", "取消一个运维修复任务。", "ops:manage", {"task_id": "必填"}, write=True)
def _ops_task_cancel(args: dict, principal: dict) -> dict:
    return ops_agent.cancel(_str(args, "task_id"))


@tool("ops_task_cleanup", "清理一个运维任务残留的本地工作树。", "ops:manage", {"task_id": "必填"}, write=True)
def _ops_task_cleanup(args: dict, principal: dict) -> dict:
    return ops_agent.cleanup(_str(args, "task_id"))


@tool(
    "list_ops_tasks",
    "列出运维修复任务。",
    "ops:manage",
    {"status": "可选，analyzing/planned/executing/done/failed/cancelled", "limit": "可选，默认 20"},
)
def _list_ops_tasks(args: dict, principal: dict) -> dict:
    return ops_agent.list_tasks(status=_str(args, "status"), limit=_limit(args))


@tool("get_ops_task", "查看一个运维修复任务的详情（分析/方案/日志/PR）。", "ops:manage", {"task_id": "必填"})
def _get_ops_task(args: dict, principal: dict) -> dict:
    try:
        return ops_agent.get_task(_str(args, "task_id"))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

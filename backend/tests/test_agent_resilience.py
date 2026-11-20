"""管理助手韧性：跟随轮次空正文、JSON 模式降级、协议自纠、参数别名与工具定义校验。"""
import json

import pytest

from app.services import agent_chat, agent_protocol, agent_tools, llm

ADMIN = {"role": "admin", "username": "agent-test"}
SUPER = {"role": "super_admin", "username": "agent-root"}


def _model(monkeypatch, responses):
    calls = []

    def fake(messages, **kwargs):
        calls.append((messages, kwargs))
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, dict) else {"content": item, "fallback": False}

    monkeypatch.setattr(llm, "chat", fake)
    return calls


def _run(monkeypatch, responses, *, tool=None, history=None, message="看看系统情况"):
    calls = _model(monkeypatch, responses)
    executed, events = [], []

    def fake_tool(name, args, who):
        executed.append((name, args))
        return tool(name, args, who) if tool else {"ok": True}

    monkeypatch.setattr(agent_chat, "call_tool", fake_tool)
    result = agent_chat.run_agent_chat(
        history or [],
        message,
        ADMIN,
        on_event=lambda kind, data: events.append((kind, data)),
    )
    deltas = [data["text"] for kind, data in events if kind == "delta"]
    assert deltas == [result["reply"]]
    return result, calls, executed


def test_whitespace_reply_is_repaired_without_user_visible_error(monkeypatch):
    result, calls, executed = _run(
        monkeypatch,
        ["   \n\t  ", '{"reply":"已恢复，系统运行正常。"}'],
    )
    assert len(calls) == 2 and not executed
    assert result["stop_reason"] == "completed"
    assert result["reply"] == "已恢复，系统运行正常。"
    assert "空回复" not in result["reply"]


def test_empty_provider_reply_retries_with_plain_mode(monkeypatch):
    result, calls, _ = _run(
        monkeypatch,
        [
            {"fallback": True, "error": "模型服务返回了空回复（finish_reason=stop）"},
            '{"reply":"普通模式已恢复。"}',
        ],
    )
    assert len(calls) == 2
    assert calls[0][1].get("json_mode") is True
    assert calls[1][1].get("json_mode") is False
    assert result["reply"] == "普通模式已恢复。"


def test_json_mode_rejection_falls_back_to_plain(monkeypatch):
    result, calls, _ = _run(
        monkeypatch,
        [
            {"fallback": True, "error": "「deepseek」调用失败（HTTP 400）：response_format is unsupported"},
            '{"reply":"兼容模式可用。"}',
        ],
    )
    assert len(calls) == 2 and calls[1][1].get("json_mode") is False
    assert result["reply"] == "兼容模式可用。"


@pytest.mark.parametrize(
    "content",
    [
        '{"tool":"system_stats","arguments":{}}',
        '{"name":"system_stats","args":{}}',
        '{"function":{"name":"system_stats","arguments":"{}"}}',
    ],
)
def test_tool_argument_aliases_are_normalized(monkeypatch, content):
    result, calls, executed = _run(monkeypatch, [content, '{"reply":"查询完成。"}'])
    assert executed == [("system_stats", {})]
    assert result["reply"] == "查询完成。"


def test_tool_and_reply_in_one_object_never_executes_or_claims_success(monkeypatch):
    result, calls, executed = _run(
        monkeypatch,
        [
            '{"tool":"system_stats","args":{},"reply":"已经处理好了"}',
            '{"tool":"system_stats","args":{}}',
            '{"reply":"系统统计已查询完成。"}',
        ],
    )
    assert len(calls) == 3
    assert executed == [("system_stats", {})]
    assert "已经处理好了" not in result["reply"]
    assert result["reply"] == "系统统计已查询完成。"


def test_dsml_tool_calls_root_is_supported(monkeypatch):
    dsml = (
        '<｜｜DSML｜｜ tool_calls><｜｜DSML｜｜ invoke name="system_stats">'
        "</｜｜DSML｜｜ invoke></｜｜DSML｜｜ tool_calls>"
    )
    result, _, executed = _run(monkeypatch, [dsml, '{"reply":"完成。"}'])
    assert executed == [("system_stats", {})]
    assert result["reply"] == "完成。"


def test_incomplete_protocol_gets_one_correction_before_failing(monkeypatch):
    broken = "<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name=\"system_stats\">"
    result, calls, _ = _run(monkeypatch, [broken, '{"reply":"已按提示重新生成。"}'])
    assert len(calls) == 2
    assert result["stop_reason"] == "completed"
    assert result["reply"] == "已按提示重新生成。"


def test_persistent_protocol_failure_reports_retry_count(monkeypatch):
    broken = "<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name=\"system_stats\">"
    result, calls, _ = _run(monkeypatch, [broken])
    assert len(calls) == 3
    assert result["stop_reason"] == "invalid_protocol"
    assert "已自动重试" in result["reply"]


def test_failed_turn_history_is_not_replayed_to_model(monkeypatch):
    history = [
        {"role": "user", "content": "当前有哪些知识缺口"},
        {"role": "assistant", "content": "模型暂时不可用，我无法完成这次请求。"},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "已取得信息：已取得 1 条记录\n\n模型暂时不可用，我无法完成这次请求。"},
    ]
    result, calls, _ = _run(monkeypatch, ['{"reply":"这次正常。"}'], history=history)
    sent = "\n".join(item["content"] for item in calls[0][0])
    assert "模型暂时不可用" not in sent
    assert "当前有哪些知识缺口" in sent and "继续" in sent
    assert result["reply"] == "这次正常。"


def test_reply_wrapper_in_history_is_unwrapped(monkeypatch):
    history = [
        {"role": "user", "content": "扫描功能怎么用"},
        {"role": "assistant", "content": '{"reply":"扫描入口在源文件页。"}'},
    ]
    result, calls, _ = _run(monkeypatch, ['{"reply":"继续说明。"}'], history=history)
    assistant = [item["content"] for item in calls[0][0] if item["role"] == "assistant" and item["content"] != "（上一条输出不是有效的工具协议，已丢弃。）"]
    assert assistant == ["扫描入口在源文件页。"]


def test_tool_schema_declares_required_and_every_parameter(monkeypatch):
    for spec in agent_tools.REGISTRY.values():
        assert spec.description.strip()
        for key, description in spec.parameters.items():
            assert "必填" in description or "可选" in description, (spec.name, key)
        expected = tuple(key for key, text in spec.parameters.items() if "必填" in text)
        assert spec.required == expected, spec.name
    catalog = {item["name"]: item for item in agent_tools.tool_catalog(list(agent_tools.REGISTRY.values()))}
    assert catalog["get_document"]["required"] == ["document_id"]
    assert catalog["system_stats"]["required"] == []


def test_missing_and_unknown_arguments_return_actionable_errors():
    missing = agent_tools.call_tool("get_document", {}, ADMIN)
    assert "缺少必填参数" in missing["error"] and "document_id" in missing["error"]
    unknown = agent_tools.call_tool("system_stats", {"document_id": "x"}, ADMIN)
    assert "未知参数" in unknown["error"] and "document_id" in unknown["error"]


def test_protocol_alias_in_double_json_sequence():
    content = '{"name":"system_stats","arguments":{}}\n{"reply":"完成"}'
    call = agent_protocol.parse_json_tool_sequence(content)
    assert call == {"tool": "system_stats", "args": {}}


def test_duplicate_call_gets_one_correction_before_continuing(monkeypatch):
    result, calls, executed = _run(
        monkeypatch,
        [
            '{"tool":"system_stats","args":{}}',
            '{"tool":"system_stats","args":{}}',
            '{"tool":"list_documents","args":{}}',
            '{"reply":"已改用文档列表继续。"}',
        ],
    )
    assert len(calls) == 4
    assert executed == [("system_stats", {}), ("list_documents", {})]
    assert result["stop_reason"] == "completed"
    assert result["reply"] == "已改用文档列表继续。"


def test_safety_classification_text_is_never_shown(monkeypatch):
    leaked = (
        "[用户未成年]否\n[分类]其他\n[判定]用户请求读取后端源码，属于正常技术排查。\n"
        "[规则]无\n</ds_safety>Safe"
    )
    result, calls, _ = _run(monkeypatch, [leaked])
    assert len(calls) == 3
    assert result["stop_reason"] == "invalid_reply"
    assert "用户未成年" not in result["reply"] and "ds_safety" not in result["reply"]


def test_safety_text_in_history_is_not_replayed(monkeypatch):
    history = [
        {"role": "user", "content": "读一下源码"},
        {"role": "assistant", "content": "[用户未成年]否\n</ds_safety>Safe"},
    ]
    result, calls, _ = _run(monkeypatch, ['{"reply":"正常回答。"}'], history=history)
    assert "ds_safety" not in "\n".join(item["content"] for item in calls[0][0])
    assert result["reply"] == "正常回答。"


def test_read_file_cap_stops_endless_paging(monkeypatch):
    responses = [
        json.dumps({"tool": "read_file", "args": {"path": "backend/app/main.py", "start": index, "lines": 10}})
        for index in range(1, 11)
    ]
    result, calls, executed = _run(monkeypatch, responses)
    assert len(executed) == 8
    assert result["stop_reason"] == "tool_errors"
    assert "次数过多" in result["reply"]


def test_search_repo_returns_locations_and_rejects_bad_input():
    found = agent_tools.call_tool(
        "search_repo", {"query": "ds_safety", "path": "backend/app/services"}, SUPER
    )
    assert found["count"] >= 1
    assert any(item["path"].endswith("agent_reply.py") for item in found["matches"])
    short = agent_tools.call_tool("search_repo", {"query": "a"}, SUPER)
    assert "至少需要 2 个字符" in short["error"]
    escape = agent_tools.call_tool("search_repo", {"query": "abc", "path": "../"}, SUPER)
    assert "只允许仓库内相对子目录" in escape["error"]


def test_large_read_results_keep_payload_for_the_model():
    payload = {
        "path": "backend/app/services/llm.py",
        "start": 1,
        "end": 100,
        "total_lines": 900,
        "truncated": True,
        "next_start": 101,
        "content": "x" * 12000,
    }
    assert len(agent_chat._clip_result(payload)) <= 2500
    assert len(agent_chat._clip_result(payload, "read_file")) > 10000


def test_read_file_supports_head_and_paging():
    first = agent_tools.call_tool(
        "read_file", {"path": "backend/app/services/llm.py", "lines": 12}, SUPER
    )
    assert first["start"] == 1 and first["end"] == 12
    assert first["total_lines"] > 12 and first["truncated"] is True
    assert first["next_start"] == 13
    second = agent_tools.call_tool(
        "read_file", {"path": "backend/app/services/llm.py", "start": first["next_start"], "lines": 5}, SUPER
    )
    assert second["start"] == 13 and second["end"] == 17
    assert second["content"].splitlines()[0] not in first["content"].splitlines()
    beyond = agent_tools.call_tool(
        "read_file", {"path": "backend/app/services/llm.py", "start": 10**6}, SUPER
    )
    assert "超过文件总行数" in beyond["error"]


def test_gap_link_requires_a_target():
    result = agent_tools.call_tool("gap_link", {"gap_id": "missing-gap", "note": "只写备注"}, ADMIN)
    assert "至少需要 document_id 或 page_id" in result["error"]

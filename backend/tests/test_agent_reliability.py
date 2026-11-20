"""管理助手循环、工具重复、最终反馈与目录查询故障回归。"""
import json

import pytest

from app import config, db
from app.services import agent_chat, agent_execution, agent_tools, ingest, llm

ADMIN = {"role": "admin", "username": "agent-test"}
SUPER = {"role": "super_admin", "username": "agent-root"}


def _model(monkeypatch, responses):
    calls = []
    def fake(messages, **kwargs):
        calls.append(messages)
        item = responses[min(len(calls)-1, len(responses)-1)]
        if isinstance(item, Exception):
            raise item
        return {"content": item, "fallback": False} if isinstance(item, str) else item
    monkeypatch.setattr(llm, "chat", fake)
    return calls


def _run(monkeypatch, responses, *, tool=None, message="查看系统情况", principal=ADMIN):
    calls = _model(monkeypatch, responses)
    executed, events = [], []
    def execute(name, args, who):
        executed.append((name, args))
        return tool(name, args, who) if tool else {"ok": True}
    monkeypatch.setattr(agent_chat, "call_tool", execute)
    result = agent_chat.run_agent_chat([], message, principal, on_event=lambda kind, data: events.append((kind, data)))
    deltas = [data["text"] for kind, data in events if kind == "delta"]
    assert deltas == [result["reply"]] and result["reply"].strip()
    return result, calls, executed, events


def test_default_directory_question_uses_one_tool_and_no_model(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCAN_DEFAULT_DIR", tmp_path)
    monkeypatch.setattr(config, "SCAN_ROOTS", [])
    db.set_setting("knowledge_scan_dir", "")
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: pytest.fail("默认目录问句不应调用模型猜路径"))
    for text in ['默认扫描文件夹是什么', '“默认扫描文件夹”是什么？', '默认扫描目录', '请问默认扫描目录在哪里']:
        events = []
        result = agent_chat.run_agent_chat([], text, ADMIN, on_event=lambda kind, data: events.append((kind, data)))
        assert str(tmp_path) in result['reply']
        assert len(result['steps']) == 1 and result['steps'][0]['name'] == 'get_scan_directory'
        assert [data['text'] for kind, data in events if kind == 'delta'] == [result['reply']]


def test_directory_info_not_failed_when_preview_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SCAN_DEFAULT_DIR", tmp_path)
    db.set_setting("knowledge_scan_dir", "")
    monkeypatch.setattr(ingest, "preview_scan_directory", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("目录文件不可预览")))
    plain = agent_tools.call_tool('get_scan_directory', {}, ADMIN)
    assert 'error' not in plain and 'preview' not in plain
    detail = agent_tools.call_tool('get_scan_directory', {'preview': True}, ADMIN)
    assert 'error' not in detail and detail['directory']['path'] == str(tmp_path)
    assert detail['preview_error'] == '目录文件不可预览'


@pytest.mark.parametrize('result_value', [{"ok": True}, {"error": "不存在"}, {"job_id": "job-123"}])
def test_same_tool_arguments_never_execute_twice(monkeypatch, result_value):
    result, calls, executed, events = _run(monkeypatch, ['{"tool":"system_stats","args":{}}'], tool=lambda *args: result_value)
    assert len(executed) == 1 and len(calls) == 3
    assert result['stop_reason'] == 'repeated_tool'
    assert '重复' in result['reply']
    if result_value.get('job_id'):
        assert 'job-123' in result['reply']


def test_repeated_write_does_not_enqueue_twice(monkeypatch):
    result, _, executed, _ = _run(monkeypatch, ['{"tool":"scan_dir","args":{"path":"scan"}}'], tool=lambda *args: {"job_id":"only-job"})
    assert len(executed) == 1 and 'only-job' in result['reply']


def test_tool_limit_always_streams_final_reply(monkeypatch):
    replies = [json.dumps({"tool":"get_job","args":{"job_id":f"job-{i}"}}) for i in range(201)]
    result, calls, executed, _ = _run(monkeypatch, replies)
    assert len(executed) == 200 and len(calls) == 201
    assert result['stop_reason'] == 'tool_limit' and '上限' in result['reply']


def test_three_different_failures_stop_with_explanation(monkeypatch):
    replies = [json.dumps({"tool":"read_file","args":{"path":f"missing-{i}.py"}}) for i in range(7)]
    result, calls, executed, _ = _run(monkeypatch, replies, tool=lambda *args: {"error":"文件不存在"})
    assert len(executed) == 3 and len(calls) == 3
    assert result['stop_reason'] == 'tool_errors' and '文件不存在' in result['reply']


@pytest.mark.parametrize('response,reason', [
    ({"fallback":True,"error":"private service address"}, 'model_unavailable'),
    (RuntimeError('internal stack/path'), 'model_error'),
    ('', 'invalid_reply'),
    ('{"unexpected":"data"}', 'invalid_reply'),
    ('{"tool":"scan_dir","args":"not-an-object"}', 'invalid_arguments'),
])
def test_failure_paths_always_return_readable_final(monkeypatch, response, reason):
    result, _, executed, _ = _run(monkeypatch, [response])
    assert result['stop_reason'] == reason and not executed
    assert 'private service address' not in result['reply'] and 'internal stack/path' not in result['reply']


def test_model_failure_keeps_confirmed_job_info(monkeypatch):
    result, _, _, _ = _run(monkeypatch, ['{"tool":"scan_dir","args":{}}', {"fallback":True}], tool=lambda *args: {"job_id":"queued-42"})
    assert 'queued-42' in result['reply'] and '模型暂时不可用' in result['reply']


def test_confirmation_is_not_success_or_execution(monkeypatch):
    result, calls, executed, _ = _run(monkeypatch, ['{"tool":"delete_document","args":{"document_id":"doc1"}}'], tool=lambda *args: {"need_confirm":True})
    assert len(calls) == 1 and len(executed) == 1
    assert result['stop_reason'] == 'confirmation_required'
    assert result['steps'][0]['ok'] is False and result['steps'][0]['status'] == 'confirmation'
    assert '尚未执行' in result['reply']


def test_cancel_prevents_future_tools(monkeypatch):
    _model(monkeypatch, ['{"tool":"scan_dir","args":{}}'])
    monkeypatch.setattr(agent_chat, 'call_tool', lambda *args: pytest.fail('不应执行工具'))
    result = agent_chat.run_agent_chat([], '扫描目录', ADMIN, should_stop=lambda: True)
    assert result['stop_reason'] == 'cancelled' and not result['steps']


def test_result_clipping_is_valid_json_and_retains_keys():
    raw = {'content':'很长的结果' * 5000, 'job_id':'job-important', 'error':'需要处理的错误', 'directory':{'path':'E:/资料','accessible':True,'source':'saved'}, 'items':[{'long':'x'*3000} for _ in range(50)]}
    encoded = agent_chat._clip_result(raw)
    data = json.loads(encoded)
    assert len(encoded) <= 2400 and data['_truncated']
    assert data['job_id'] == 'job-important' and data['directory']['path'] == 'E:/资料'
    assert data['error'] == '需要处理的错误'


def test_public_process_redacts_credentials():
    value = agent_execution.public_value({'api_key':'sk-hidden','config':{'password':'hidden','token':'private'},'path':'scan/'})
    assert value['api_key'] == '***' and value['config']['token'] == '***'
    assert value['path'] == 'scan/'


def _sse_events(response):
    events = []
    for block in response.text.split('\n\n'):
        name, data = '', ''
        for line in block.splitlines():
            if line.startswith('event: '): name = line[7:]
            if line.startswith('data: '): data = line[6:]
        if name and data: events.append((name, json.loads(data)))
    return events


@pytest.mark.parametrize('response', [{"fallback":True}, {"content":'{"tool":"system_stats","args":{}}',"fallback":False}])
def test_sse_failure_final_reply_and_database_match(client, monkeypatch, response):
    monkeypatch.setattr(llm, 'chat', lambda *args, **kwargs: response)
    events = _sse_events(client.post('/api/agent/chat', json={"message":"检查系统情况"}))
    done = next(data for name, data in events if name == 'done')
    assert ''.join(data['text'] for name, data in events if name == 'delta') == done['reply']
    assert done['reply'].strip() and any(name == 'status' for name, _ in events)
    messages = client.get('/api/agent/conversations/' + done['conversation_id']).json()['items']
    assert messages[-1]['content'] == done['reply'] and messages[-1]['steps'] == done['steps']


def test_router_exception_still_ends_with_reply_and_done(client, monkeypatch):
    monkeypatch.setattr(agent_chat, 'run_agent_chat', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('internal-only')))
    events = _sse_events(client.post('/api/agent/chat', json={"message":"检查系统情况"}))
    done = next(data for name, data in events if name == 'done')
    assert '失败' in done['reply'] and 'internal-only' not in done['reply']
    assert any(name == 'delta' for name, _ in events)
    messages = client.get('/api/agent/conversations/' + done['conversation_id']).json()['items']
    assert messages[-1]['content'] == done['reply']


def test_default_directory_skips_model_history_compaction(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCAN_DEFAULT_DIR', tmp_path)
    monkeypatch.setattr(ingest, 'preview_scan_directory', lambda *args, **kwargs: pytest.fail('目录问句不应预览文件'))
    from app.services import agent_sessions
    monkeypatch.setattr(agent_sessions, 'build_context', lambda *args, **kwargs: pytest.fail('只查配置不应压缩长历史'))
    history = agent_chat.prepare_context('unused', '默认扫描文件夹是什么', ADMIN)
    assert history == []


def test_finished_tool_metadata_retains_stop_reason(monkeypatch):
    result, _, _, _ = _run(monkeypatch, ['{"tool":"system_stats","args":{}}'])
    assert result['steps'][0]['run_stop_reason'] == 'repeated_tool'
    assert result['steps'][0]['run_elapsed_ms'] >= 0

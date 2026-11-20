"""DeepSeek DSML 工具输出的兼容与边界。"""
import json

import pytest

from app import config, db
from app.services import agent_chat, agent_tools, llm
from app.services.agent_protocol import AgentProtocolError, parse_dsml_call
from app.services.agent_reply import FORMAT_ERROR, normalize_reply

ADMIN = {"role": "admin", "username": "dsml-test"}
VIEWER = {"role": "viewer", "username": "dsml-viewer"}
RAW = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="get_scan_directory">\n\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'


def _call(name, parameters=''):
    return f'<｜DSML｜function_calls><｜DSML｜invoke name="{name}">{parameters}</｜DSML｜invoke></｜DSML｜function_calls>'


@pytest.mark.parametrize('raw', [RAW, '```xml\n' + RAW + '\n```', RAW.replace('｜', '|'), _call('get_scan_directory')])
def test_complete_no_argument_dsml(raw):
    assert parse_dsml_call(raw) == {"tool": "get_scan_directory", "args": {}}


def test_parameters_types_unicode_and_entities():
    raw = _call('get_scan_directory', '<｜DSML｜parameter name="path" string="true">E:\\资料 &amp; 通知</｜DSML｜parameter><｜DSML｜parameter name="preview" string="false">true</｜DSML｜parameter><｜DSML｜parameter name="limit" string="false">12</｜DSML｜parameter>')
    assert parse_dsml_call(raw)['args'] == {'path':'E:\\资料 & 通知', 'preview':True, 'limit':12}
    assert parse_dsml_call(_call('get_scan_directory', '{"preview":true}'))['args'] == {'preview':True}


@pytest.mark.parametrize('raw', [
    RAW[:-15],
    '我来操作：' + RAW,
    RAW + '已完成',
    _call('get_scan_directory') + _call('scan_dir'),
    '<｜DSML｜function_calls><｜DSML｜invoke name="get_scan_directory"/><｜DSML｜invoke name="scan_dir"/></｜DSML｜function_calls>',
    _call('get_scan_directory', '<｜DSML｜parameter name="preview" string="false">not-json</｜DSML｜parameter>'),
    _call('get_scan_directory', '<｜DSML｜parameter name="path">a</｜DSML｜parameter><｜DSML｜parameter name="path">b</｜DSML｜parameter>'),
    _call('get_scan_directory', '<｜DSML｜parameter name="path"><nested/></｜DSML｜parameter>'),
    '<!DOCTYPE calls [<!ENTITY x "unexpected">]>' + RAW,
    _call('get_scan_directory', '[]'),
    _call('../scan_dir'),
])
def test_ambiguous_or_broken_protocol_rejected(raw):
    with pytest.raises(AgentProtocolError):
        parse_dsml_call(raw)


def test_plain_text_and_json_protocol_do_not_change():
    assert parse_dsml_call('请到源文件页面使用扫描文件夹。') is None
    assert parse_dsml_call('{"tool":"get_scan_directory","args":{}}') is None
    assert parse_dsml_call('{"reply":"扫描方法如下"}') is None


def test_real_dsml_scenario_executes_tool_then_returns_scan_help(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCAN_DEFAULT_DIR', tmp_path)
    monkeypatch.setattr(config, 'SCAN_ROOTS', [])
    db.set_setting('knowledge_scan_dir', '')
    calls = []
    replies = [RAW, '{"reply":"在知识库管理 → 内容资料 → 源文件中点击扫描文件夹，预览后开始扫描。"}']
    def model(messages, **kwargs):
        calls.append(messages)
        return {'content': replies.pop(0), 'fallback': False}
    monkeypatch.setattr(llm, 'chat', model)
    result = agent_chat.run_agent_chat([], '如何使用扫描功能', ADMIN)
    assert result['stop_reason'] == 'completed'
    assert len(result['steps']) == 1 and result['steps'][0]['name'] == 'get_scan_directory'
    assert result['steps'][0]['status'] == 'succeeded'
    assert '扫描文件夹' in result['reply'] and 'DSML' not in result['reply']
    assert not db.query('SELECT id FROM jobs')
    assert all('DSML' not in item['content'] or item['role'] == 'system' for item in calls[1])
    assert any(json.loads(item['content']).get('tool') == 'get_scan_directory' for item in calls[1] if item['role'] == 'assistant')


def test_dsml_does_not_bypass_role_permissions(monkeypatch):
    outputs = [RAW, '{"reply":"当前角色不能查询服务器目录，请联系管理员。"}']
    monkeypatch.setattr(llm, 'chat', lambda *args, **kwargs: {'content':outputs.pop(0),'fallback':False})
    result = agent_chat.run_agent_chat([], '如何使用扫描功能', VIEWER)
    assert result['steps'][0]['ok'] is False and '权限不足' in result['steps'][0]['summary']


def test_dsml_dangerous_tool_still_requires_confirmation(monkeypatch):
    monkeypatch.setattr(llm, 'chat', lambda *args, **kwargs: {'content':_call('delete_document', '<｜DSML｜parameter name="document_id">missing-doc</｜DSML｜parameter>'),'fallback':False})
    result = agent_chat.run_agent_chat([], '删除该文档', ADMIN)
    assert result['stop_reason'] == 'confirmation_required'
    assert result['steps'][0]['status'] == 'confirmation'
    assert not db.query("SELECT id FROM audit_log WHERE action='agent.tool.delete_document'")


def test_invalid_dsml_never_marked_completed_or_leaked(monkeypatch):
    monkeypatch.setattr(llm, 'chat', lambda *args, **kwargs: {'content':RAW[:-15],'fallback':False})
    monkeypatch.setattr(agent_chat, 'call_tool', lambda *args: pytest.fail('残缺协议不得执行'))
    result = agent_chat.run_agent_chat([], '如何使用扫描功能', ADMIN)
    assert result['stop_reason'] == 'invalid_protocol'
    assert 'DSML' not in result['reply'] and '尚未执行' in result['reply']
    assert not result['steps']


@pytest.mark.parametrize('raw', [RAW, json.dumps({'reply':RAW}), '```xml\n' + RAW + '\n```'])
def test_final_reply_guard_rejects_protocol_wrappers(raw):
    assert normalize_reply(raw, protocol=True) == FORMAT_ERROR


def test_dsml_sse_reply_and_persistence_match(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCAN_DEFAULT_DIR', tmp_path)
    db.set_setting('knowledge_scan_dir', '')
    replies = [RAW, '{"reply":"点击源文件中的扫描文件夹即可预览并提交。"}']
    monkeypatch.setattr(llm, 'chat', lambda *args, **kwargs: {'content':replies.pop(0),'fallback':False})
    response = client.post('/api/agent/chat', json={'message':'如何使用扫描功能'})
    events = []
    for block in response.text.split('\n\n'):
        name = next((line[7:] for line in block.splitlines() if line.startswith('event: ')), '')
        data = next((line[6:] for line in block.splitlines() if line.startswith('data: ')), '')
        if name and data: events.append((name,json.loads(data)))
    done = next(data for name,data in events if name == 'done')
    assert done['stop_reason'] == 'completed' and len(done['steps']) == 1
    assert 'DSML' not in done['reply']
    assert ''.join(data['text'] for name,data in events if name == 'delta') == done['reply']
    records = client.get('/api/agent/conversations/' + done['conversation_id']).json()['items']
    assert records[-1]['content'] == done['reply'] and records[-1]['steps'] == done['steps']


def test_history_dsml_not_reused_as_valid_assistant_answer(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, 'chat', lambda messages, **kwargs: calls.append(messages) or {'content':'{"reply":"请在源文件页面点击扫描文件夹。"}','fallback':False})
    result = agent_chat.run_agent_chat([{'role':'assistant','content':RAW},{'role':'user','content':'之前没有回答'}], '如何使用扫描功能', ADMIN)
    assert result['stop_reason'] == 'completed'
    assert all('DSML' not in item['content'] for item in calls[0] if item['role'] == 'assistant')
    assert any('未形成有效回答' in item['content'] for item in calls[0])


def test_tool_then_reply_sequence_discards_premature_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'SCAN_DEFAULT_DIR', tmp_path)
    db.set_setting('knowledge_scan_dir', '')
    responses = ['{"tool":"get_scan_directory","args":{}}\n\n{"reply":"未执行却声称扫描完成"}', '{"reply":"先进入源文件页面，点击扫描文件夹并预览，再由你决定是否开始。"}']
    calls = []
    def model(messages, **kwargs):
        calls.append(messages)
        return {'content':responses.pop(0),'fallback':False}
    monkeypatch.setattr(llm, 'chat', model)
    result = agent_chat.run_agent_chat([], '如何使用扫描功能', ADMIN)
    assert result['stop_reason'] == 'completed' and len(result['steps']) == 1
    assert '未执行却声称' not in result['reply']
    assert all('未执行却声称' not in item['content'] for item in calls[1])
    assert not db.query('SELECT id FROM jobs')


@pytest.mark.parametrize('raw', [
    '{"tool":"get_scan_directory","args":{}} {"tool":"scan_dir","args":{}}',
    '{"tool":"get_scan_directory","reply":"已经完成"}',
    '{"tool":"get_scan_directory","args":{}} {"reply":"说明"} {"reply":"重复"}',
])
def test_multiple_ambiguous_json_objects_do_not_execute(monkeypatch, raw):
    monkeypatch.setattr(llm, 'chat', lambda *args, **kwargs: {'content':raw,'fallback':False})
    monkeypatch.setattr(agent_chat, 'call_tool', lambda *args: pytest.fail('混杂协议不能执行'))
    result = agent_chat.run_agent_chat([], '查看扫描功能', ADMIN)
    assert result['stop_reason'] == 'invalid_protocol'
    assert not result['steps']

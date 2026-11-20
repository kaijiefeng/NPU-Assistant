"""Agent JSON对象输出模式仅对显式请求生效，其他调用行为不变。"""
from contextlib import nullcontext

import pytest

from app.services import agent_chat, llm


@pytest.mark.parametrize('provider', ['openai', 'ollama'])
@pytest.mark.parametrize('enabled', [False, True])
def test_provider_json_output_payload(monkeypatch, provider, enabled):
    payloads = []
    monkeypatch.setattr(llm, '_client', lambda: nullcontext(object()))
    def request(*args, **kwargs):
        payloads.append(kwargs['json_body'])
        return {'choices':[{'message':{'content':'{"reply":"完成"}'}}], 'message':{'content':'{"reply":"完成"}'}}
    monkeypatch.setattr(llm, '_request_json', request)
    handler = llm._chat_openai if provider == 'openai' else llm._chat_ollama
    content, _ = handler({'api_key':'test-key','base_url':'https://example.test/v1','model_name':'test-model'}, [{'role':'user','content':'使用JSON回答'}], .2, 1400, json_mode=enabled)
    assert content == '{"reply":"完成"}'
    if provider == 'openai':
        assert (payloads[0].get('response_format') == {'type':'json_object'}) is enabled
    else:
        assert (payloads[0].get('format') == 'json') is enabled


def test_agent_explicitly_requests_json_object_mode(monkeypatch):
    requested = []
    def model(messages, **kwargs):
        requested.append(kwargs)
        return {'content':'{"reply":"请在源文件页面点击扫描文件夹。"}','fallback':False}
    monkeypatch.setattr(llm, 'chat', model)
    result = agent_chat.run_agent_chat([], '如何使用扫描功能', {'role':'admin'})
    assert result['stop_reason'] == 'completed'
    assert requested[0]['json_mode'] is True


def test_chat_passes_json_mode_to_provider_only_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, '_resolve_target', lambda model_id: {'provider_type':'openai','model_name':'json-mode-test','provider_id':'json-mode-test'})
    monkeypatch.setattr(llm, '_circuit_open', lambda key: False)
    def provider(*args, **kwargs):
        calls.append(kwargs)
        return '{"reply":"完成"}', {}
    monkeypatch.setattr(llm, '_chat_openai', provider)
    assert not llm.chat([{'role':'user','content':'普通调用'}])['fallback']
    assert not llm.chat([{'role':'user','content':'JSON调用'}], json_mode=True)['fallback']
    assert calls == [{}, {'json_mode':True}]

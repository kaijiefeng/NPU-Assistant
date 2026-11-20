"""管理助手回复包装容错及 SSE/持久化一致性。"""
import json

import pytest

from app.services import agent_chat
from app.services.agent_reply import FORMAT_ERROR, normalize_reply


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"reply":"系统正常。"}', '系统正常。'),
        ('{"replay":"系统正常。"}', '系统正常。'),
        ('{”replay“:"请打开“任务”查看进度。"}', '请打开“任务”查看进度。'),
        ('{“reply”：“第一行\n第二行”}', '第一行\n第二行'),
        ('```json\n{"replay":"系统正常。"}\n```', '系统正常。'),
        ('{"reply":{"replay":"已完成。"}}', '已完成。'),
        (json.dumps({"reply": json.dumps({"reply": '打开“设置”，选择模型。'}, ensure_ascii=False)}), '打开“设置”，选择模型。'),
        (json.dumps(json.dumps({"reply": '完成'}, ensure_ascii=False)), '完成'),
        ('知识库当前运行正常。', '知识库当前运行正常。'),
        ('示例：{"reply":"保留这个示例"}', '示例：{"reply":"保留这个示例"}'),
        ('```json\n{"timeout":30}\n```', '```json\n{"timeout":30}\n```'),
        ('{"timeout":30}', '{"timeout":30}'),
        ('{"reply":"第一行\\n第二行，路径 C:\\\\tmp"}', '第一行\n第二行，路径 C:\\tmp'),
        ('{"reply":', FORMAT_ERROR),
        ('{"tool":"","args":{}}', FORMAT_ERROR),
        ('{"reply":{"unexpected":"value"}}', FORMAT_ERROR),
    ],
)
def test_normalize_reply(raw, expected):
    assert normalize_reply(raw, protocol=True) == expected


@pytest.mark.parametrize('key', ['reply', 'replay'])
def test_reply_loop_emits_normalized_content(monkeypatch, key):
    raw = '{”' + key + '“:"请打开“任务”查看。"}'
    monkeypatch.setattr(agent_chat.llm, 'chat', lambda *args, **kwargs: {"content": raw, "fallback": False})
    events = []
    result = agent_chat.run_agent_chat([], '系统情况？', {"role": "admin"}, on_event=lambda kind, data: events.append((kind, data)))
    assert result['reply'] == '请打开“任务”查看。'
    assert [(kind, data) for kind, data in events if kind != 'status'] == [('delta', {"text": result['reply']})]


def test_sse_and_saved_reply_share_normalization(client, monkeypatch):
    raw = json.dumps({"reply": {"replay": '已完成检查。'}}, ensure_ascii=False)
    monkeypatch.setattr(agent_chat.llm, 'chat', lambda *args, **kwargs: {"content": raw, "fallback": False})
    response = client.post('/api/agent/chat', json={"message": '检查一下'})
    assert response.status_code == 200
    blocks = response.text.split('\n\n')
    events = {}
    for block in blocks:
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith('event: ')), '')
        data = next((line[6:] for line in lines if line.startswith('data: ')), '')
        if event and data:
            events[event] = json.loads(data)
    assert events['delta']['text'] == '已完成检查。'
    assert events['done']['reply'] == events['delta']['text']
    conversation = client.get('/api/agent/conversations/' + events['done']['conversation_id']).json()
    assert conversation['items'][-1]['content'] == events['done']['reply']

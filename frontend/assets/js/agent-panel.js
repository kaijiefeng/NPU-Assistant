/**
 * agent-panel.js — 右侧常驻「管理助手」面板
 *
 * 挂在壳层（.shell-main 内），不随路由切换销毁；通过 SSE 与后端
 * /api/agent/chat 对话，工具调用步骤内联展示；历史存 localStorage。
 * 未登录且非演示模式时整体隐藏，不打扰公开页访客。
 */
import { sendAgentStream, uploadDocument } from './api.js';
import { esc } from './ui.js';

const HISTORY_KEY = 'npu_agent_history';
const OPEN_KEY = 'npu_agent_open';
const MAX_HISTORY = 50;
const MAX_INPUT = 2000;
const HISTORY_SEND = 12; // 每次请求回传的最近消息条数（后端还会再截断）
const MAX_UPLOAD_FILES = 5; // 单次最多选择的文件数（防误选刷屏）

const state = {
  ready: false,
  available: false,
  open: false,
  sending: false,
  uploading: false,
  canUpload: false,
  messages: [], // {role:'user'|'assistant', content, steps:[{name, ok, summary}]}
  controller: null,
};

const dom = {};

export function initAgentPanel(authState) {
  if (state.ready) {
    refreshAgentPanel(authState);
    return;
  }
  dom.root = document.getElementById('agentPanel');
  dom.toggle = document.getElementById('agentToggle');
  dom.body = document.getElementById('agentBody');
  dom.form = document.getElementById('agentForm');
  dom.input = document.getElementById('agentInput');
  dom.send = document.getElementById('agentSend');
  dom.clear = document.getElementById('agentClear');
  dom.close = document.getElementById('agentClose');
  dom.attach = document.getElementById('agentAttach');
  dom.files = document.getElementById('agentFiles');
  if (!dom.root || !dom.toggle || !dom.form) return;
  state.ready = true;
  try {
    // 默认展开：只有明确关闭过（'0'）才收起
    state.open = localStorage.getItem(OPEN_KEY) !== '0';
  } catch {
    state.open = true;
  }
  state.messages = loadHistory();

  dom.toggle.addEventListener('click', () => setOpen(!state.open));
  dom.close?.addEventListener('click', () => setOpen(false));
  dom.clear?.addEventListener('click', clearHistory);
  dom.form.addEventListener('submit', (event) => {
    event.preventDefault();
    void send();
  });
  dom.input?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void send();
    }
  });
  dom.attach?.addEventListener('click', () => {
    if (!state.canUpload || state.uploading) return;
    dom.files?.click();
  });
  dom.files?.addEventListener('change', () => {
    const picked = Array.from(dom.files?.files || []).slice(0, MAX_UPLOAD_FILES);
    dom.files.value = '';
    if (picked.length) void handleUploads(picked);
  });

  refreshAgentPanel(authState);
}

export function refreshAgentPanel(authState) {
  if (!state.ready) return;
  // 面板对所有登录账号可见（工具按角色过滤由后端负责）；公开访客不可见
  state.available = Boolean(authState?.authenticated || authState?.open_mode);
  // 附件上传走 /knowledge/documents（需要内容写权限）
  const perms = Array.isArray(authState?.permissions) ? authState.permissions : [];
  state.canUpload = Boolean(
    authState?.open_mode || perms.includes('*') || perms.includes('content:write'),
  );
  if (dom.attach) dom.attach.hidden = !(state.available && state.canUpload);
  dom.toggle.hidden = !state.available;
  if (!state.available) {
    dom.root.hidden = true;
    return;
  }
  applyOpen();
}

function setOpen(open) {
  state.open = open;
  try {
    localStorage.setItem(OPEN_KEY, open ? '1' : '0');
  } catch {
    /* ignore */
  }
  applyOpen();
  if (state.open) dom.input?.focus();
}

function applyOpen() {
  const visible = state.available && state.open;
  dom.root.hidden = !visible;
  dom.toggle.setAttribute('aria-expanded', String(state.open));
  dom.toggle.classList.toggle('is-active', state.open);
  if (visible) render();
}

function loadHistory() {
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(parsed) ? parsed.slice(-MAX_HISTORY) : [];
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.messages.slice(-MAX_HISTORY)));
  } catch {
    /* 配额不足等异常忽略 */
  }
}

function clearHistory() {
  if (state.sending) return;
  state.messages = [];
  persist();
  render();
}

/* ------------------------------------------------------------------ */
/* 渲染                                                                */
/* ------------------------------------------------------------------ */
function stepHtml(step) {
  const mark = step.pending ? '…' : step.ok ? '✓' : '✕';
  const cls = `agent-step${step.pending ? ' is-pending' : ''}${step.ok === false ? ' is-error' : ''}`;
  const summary = step.summary
    ? `<span class="agent-step-summary" title="${esc(step.summary)}">${esc(step.summary)}</span>`
    : '';
  return `<div class="${cls}"><span class="agent-step-mark">${mark}</span><span class="agent-step-name">${esc(
    step.name,
  )}</span>${summary}</div>`;
}

function render(draft = null) {
  if (!dom.body) return;
  const items = draft ? [...state.messages, draft] : state.messages;
  if (!items.length) {
    dom.body.innerHTML = `<div class="agent-empty">
      <p>我是管理助手，可以直接代你查询和操作：</p>
      <ul>
        <li>「现在有多少未处理的知识缺口？」</li>
        <li>「给第一条缺口找备选资料」</li>
        <li>「检查所有订阅有没有新内容」</li>
        <li>「触发一次全量词条重建」</li>
      </ul>
      <p class="agent-empty-note">删除、重建、批准修复等高危操作我会先和你确认再执行。</p>
    </div>`;
    return;
  }
  dom.body.innerHTML = items
    .map((message) => {
      if (message.role === 'user') {
        return `<div class="agent-msg is-user"><div class="agent-bubble">${esc(message.content)}</div></div>`;
      }
      const steps = (message.steps || []).map(stepHtml).join('');
      const body = message.content
        ? `<div class="agent-bubble">${renderMarkdown(message.content)}</div>`
        : steps
          ? ''
          : '<div class="agent-bubble agent-typing">正在思考…</div>';
      return `<div class="agent-msg is-assistant">${
        steps ? `<div class="agent-steps">${steps}</div>` : ''
      }${body}</div>`;
    })
    .join('');
  dom.body.scrollTop = dom.body.scrollHeight;
}

/** 轻量 markdown：行内代码 / 加粗 / 换行（管理回复以短句列表为主，不做完整渲染）。 */
function renderMarkdown(text) {
  return esc(String(text || ''))
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

/* ------------------------------------------------------------------ */
/* 附件上传（走 /knowledge/documents?async=1，后台编译为词条）           */
/* ------------------------------------------------------------------ */
async function handleUploads(files) {
  if (state.uploading || !state.canUpload) return;
  state.uploading = true;
  dom.attach && (dom.attach.disabled = true);
  const card = { role: 'assistant', content: '', steps: [] };
  render(card);
  const uploaded = [];
  for (const file of files) {
    const step = { name: `上传 ${file.name}`, pending: true, ok: undefined, summary: '0%' };
    card.steps.push(step);
    render(card);
    try {
      const result = await uploadDocument(file, {
        asyncMode: true,
        onProgress: (percent) => {
          step.summary = `${percent}%`;
          render(card);
        },
      });
      step.pending = false;
      step.ok = true;
      step.summary = result?.job_id ? `已提交编译（任务 ${result.job_id}）` : '已提交';
      if (result?.job_id) uploaded.push({ name: file.name, jobId: result.job_id });
    } catch (err) {
      step.pending = false;
      step.ok = false;
      step.summary = String(err?.message || '上传失败').slice(0, 120);
    }
    render(card);
  }
  state.messages.push({
    role: 'assistant',
    content: '',
    steps: card.steps.map((step) => ({
      name: step.name,
      ok: step.ok !== false,
      summary: step.summary || '',
    })),
  });
  persist();
  state.uploading = false;
  dom.attach && (dom.attach.disabled = false);
  render();

  if (uploaded.length) {
    const summary = uploaded.map((item) => `《${item.name}》→ 任务 ${item.jobId}`).join('；');
    await submitText(
      `我通过面板上传了 ${uploaded.length} 个文件：${summary}。请用 get_job 查询这些编译任务的当前状态并简要汇报。`,
    );
  }
}

/* ------------------------------------------------------------------ */
/* 发送                                                                */
/* ------------------------------------------------------------------ */
async function send() {
  if (state.sending || !state.available) return;
  const text = String(dom.input?.value || '').trim().slice(0, MAX_INPUT);
  if (!text) return;
  dom.input.value = '';
  await submitText(text);
}

async function submitText(text) {
  if (state.sending || !state.available) return;
  state.messages.push({ role: 'user', content: text });
  persist();
  render();

  state.sending = true;
  dom.send.disabled = true;
  dom.input.disabled = true;
  const draft = { role: 'assistant', content: '', steps: [] };
  render(draft);
  state.controller = new AbortController();

  try {
    const history = state.messages
      .slice(0, -1)
      .slice(-HISTORY_SEND)
      .map((m) => ({ role: m.role, content: String(m.content || '').slice(0, 2000) }));
    await sendAgentStream(
      { message: text, history },
      {
        signal: state.controller.signal,
        onTool: (data) => {
          draft.steps.push({ name: String(data?.name || ''), pending: true });
          render(draft);
        },
        onToolResult: (data) => {
          const step = [...draft.steps].reverse().find((s) => s.name === data?.name && s.pending);
          if (step) {
            step.pending = false;
            step.ok = Boolean(data?.ok);
            step.summary = String(data?.summary || '').slice(0, 160);
          }
          render(draft);
        },
        onDelta: (text) => {
          draft.content += text;
          render(draft);
        },
      },
    );
  } catch (err) {
    if (err?.name === 'AbortError') {
      draft.content = draft.content ? `${draft.content}\n（已停止）` : '（已停止）';
    } else {
      const note = `⚠ ${err?.message || '请求失败，请稍后重试'}`;
      draft.content = draft.content ? `${draft.content}\n${note}` : note;
    }
  } finally {
    state.sending = false;
    state.controller = null;
    dom.send.disabled = false;
    dom.input.disabled = false;
    if (draft.content || draft.steps.length) {
      state.messages.push({
        role: 'assistant',
        content: draft.content,
        steps: draft.steps.map((step) => ({
          name: step.name,
          ok: step.pending ? false : step.ok !== false,
          summary: step.summary || '',
        })),
      });
      persist();
    }
    render();
  }
}

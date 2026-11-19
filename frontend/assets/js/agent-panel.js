/**
 * agent-panel.js — 右侧常驻「管理助手」面板
 *
 * 挂在壳层（.shell-main 内），不随路由切换销毁；通过 SSE 与后端
 * /api/agent/chat 对话，工具调用步骤内联展示。会话与历史由服务端
 * 持久化（/api/agent/conversations），本地只记住当前会话 ID 与开合状态。
 */
import {
  deleteAgentConversation,
  getAgentConversation,
  listAgentConversations,
  rewindAgentConversation,
  sendAgentStream,
  uploadDocument,
} from './api.js';
import { confirmDialog, esc, toast } from './ui.js';

const OPEN_KEY = 'npu_agent_open';
const CONV_KEY = 'npu_agent_conv';
const MAX_INPUT = 2000;
const MAX_UPLOAD_FILES = 5; // 单次最多选择的文件数（防误选刷屏）

const state = {
  ready: false,
  available: false,
  open: false,
  sending: false,
  uploading: false,
  canUpload: false,
  uploadCard: null, // 上传进行中的独立展示卡（不与回答 draft 混排）
  conversationId: '',
  conversations: [],
  drawerOpen: false,
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
  dom.historyBtn = document.getElementById('agentHistory');
  dom.newBtn = document.getElementById('agentNew');
  dom.drawer = document.getElementById('agentDrawer');
  dom.drawerClose = document.getElementById('agentDrawerClose');
  dom.convList = document.getElementById('agentConvList');
  if (!dom.root || !dom.toggle || !dom.form) return;
  state.ready = true;
  try {
    // 默认展开：只有明确关闭过（'0'）才收起
    state.open = localStorage.getItem(OPEN_KEY) !== '0';
    state.conversationId = localStorage.getItem(CONV_KEY) || '';
  } catch {
    state.open = true;
    state.conversationId = '';
  }

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
    if (state.sending) {
      toast('请等当前回答完成后再上传文件', 'warning');
      return;
    }
    dom.files?.click();
  });
  dom.files?.addEventListener('change', () => {
    const picked = Array.from(dom.files?.files || []).slice(0, MAX_UPLOAD_FILES);
    dom.files.value = '';
    if (picked.length) void handleUploads(picked);
  });
  dom.historyBtn?.addEventListener('click', () => setDrawer(!state.drawerOpen));
  dom.newBtn?.addEventListener('click', newConversation);
  dom.drawerClose?.addEventListener('click', () => setDrawer(false));
  // 抽屉内交互（打开/删除会话）
  dom.convList?.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-conv-open]');
    if (opener) {
      void openConversation(opener.dataset.convOpen);
      return;
    }
    const remover = event.target.closest('[data-conv-delete]');
    if (remover) void removeConversation(remover.dataset.convDelete);
  });
  // 消息区：从这里重问（回滚）
  dom.body.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action="agent-rewind"]');
    if (trigger) void rewindFrom(trigger.dataset.id);
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
  if (!visible) return;
  render();
  if (state.conversationId && !state.messagesLoaded) void openConversation(state.conversationId, { silent: true });
  if (!state.conversations.length) void loadConversations({ silent: true });
}

/* ------------------------------------------------------------------ */
/* 会话                                                                */
/* ------------------------------------------------------------------ */
function setConversationId(id) {
  state.conversationId = id || '';
  try {
    if (id) localStorage.setItem(CONV_KEY, id);
    else localStorage.removeItem(CONV_KEY);
  } catch {
    /* ignore */
  }
}

async function loadConversations({ silent = false } = {}) {
  try {
    const data = await listAgentConversations(30);
    state.conversations = Array.isArray(data?.items) ? data.items : [];
    renderDrawer();
  } catch (err) {
    if (!silent) toast(err?.message || '会话列表加载失败', 'error');
  }
}

async function openConversation(id, { silent = false } = {}) {
  try {
    const data = await getAgentConversation(id);
    const items = Array.isArray(data?.items) ? data.items : [];
    state.messages = items.map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content || '',
      steps: Array.isArray(item.steps) ? item.steps : [],
    }));
    state.messagesLoaded = true;
    setConversationId(id);
    render();
    renderDrawer();
    void loadConversations({ silent: true });
  } catch (err) {
    setConversationId('');
    state.messages = [];
    state.messagesLoaded = true;
    render();
    if (!silent) toast(err?.message || '会话加载失败，已回到新会话', 'warning');
  }
}

function newConversation() {
  if (state.sending || state.uploading) {
    toast('请等当前回答完成后再开新会话', 'warning');
    return;
  }
  setConversationId('');
  state.messages = [];
  state.messagesLoaded = true;
  render();
  renderDrawer();
  dom.input?.focus();
}

async function removeConversation(id) {
  const target = state.conversations.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除会话',
    message: `确定删除「${target?.title || '该会话'}」吗？`,
    detail: '对话记录与工具步骤将一并删除，不可恢复。',
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteAgentConversation(id);
    if (id === state.conversationId) {
      setConversationId('');
      state.messages = [];
      render();
    }
    await loadConversations({ silent: true });
    renderDrawer();
  } catch (err) {
    toast(err?.message || '删除失败', 'error');
  }
}

function setDrawer(open) {
  state.drawerOpen = open;
  if (dom.drawer) dom.drawer.hidden = !open;
  if (open) {
    void loadConversations({ silent: true });
    renderDrawer();
  }
}

function renderDrawer() {
  if (!dom.convList) return;
  if (!state.conversations.length) {
    dom.convList.innerHTML = `<p class="agent-empty-note">还没有历史会话；新对话会自动保存。</p>`;
    return;
  }
  dom.convList.innerHTML = state.conversations
    .map(
      (item) => `<div class="agent-conv ${item.id === state.conversationId ? 'is-active' : ''}">
        <div class="agent-conv-main" data-conv-open="${esc(item.id)}" role="button" tabindex="0">
          <span class="agent-conv-title">${esc(item.title)}</span>
          <span class="agent-conv-meta">${esc(item.last_message || '')} · ${esc(
            String(item.updated_at || '').slice(5, 16),
          )}</span>
        </div>
        <button class="icon-btn" type="button" data-conv-delete="${esc(item.id)}" aria-label="删除会话" title="删除会话">
          <svg class="i" aria-hidden="true"><use href="#i-trash"></use></svg>
        </button>
      </div>`,
    )
    .join('');
}

async function rewindFrom(messageId) {
  if (!state.conversationId || state.sending || state.uploading) return;
  const target = (state.messages || []).find((item) => item.id === messageId);
  if (!target) return;
  const ok = await confirmDialog({
    title: '从这里重问',
    message: '回滚到这条消息之前，并把原文放回输入框？',
    detail: '仅回退对话内容；已执行的工具操作（收录、删除等）不会被撤销。',
    confirmText: '回滚',
    danger: true,
  });
  if (!ok) return;
  try {
    await rewindAgentConversation(state.conversationId, messageId);
    if (dom.input && !dom.input.value.trim()) dom.input.value = target.content.slice(0, MAX_INPUT);
    await openConversation(state.conversationId, { silent: true });
    toast('已回滚，原文已放回输入框', 'success');
  } catch (err) {
    toast(err?.message || '回滚失败', 'error');
  }
}

function clearHistory() {
  if (state.sending || state.uploading) return;
  newConversation();
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
  const messages = state.messages || [];
  const items = [...messages];
  if (state.uploadCard) items.push(state.uploadCard);
  if (draft) items.push(draft);
  if (!items.length) {
    dom.body.innerHTML = `<div class="agent-empty">
      <p>我是管理助手，可以直接代你查询和操作：</p>
      <ul>
        <li>「现在有多少未处理的知识缺口？」</li>
        <li>「给第一条缺口找备选资料」</li>
        <li>「检查所有订阅有没有新内容」</li>
        <li>「收录这个链接 …」</li>
      </ul>
      <p class="agent-empty-note">📎 可直接上传文件；删除、重建、批准修复等高危操作我会先和你确认。会话自动保存，可随时在右上角「历史」中回看或回滚。</p>
    </div>`;
    return;
  }
  dom.body.innerHTML = items
    .map((message) => {
      if (message.role === 'user') {
        const rewind = message.id
          ? `<button class="agent-rewind" type="button" data-action="agent-rewind" data-id="${esc(
              message.id,
            )}" title="从这里重问">↺ 重问</button>`
          : '';
        return `<div class="agent-msg is-user"><div class="agent-bubble">${esc(
          message.content,
        )}</div>${rewind}</div>`;
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
  state.uploadCard = { role: 'assistant', content: '', steps: [] };
  const card = state.uploadCard;
  render();
  const uploaded = [];
  for (const file of files) {
    const step = { name: `上传 ${file.name}`, pending: true, ok: undefined, summary: '0%' };
    card.steps.push(step);
    render();
    try {
      const result = await uploadDocument(file, {
        asyncMode: true,
        onProgress: (percent) => {
          step.summary = `${percent}%`;
          render();
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
    render();
  }
  (state.messages = state.messages || []).push({
    role: 'assistant',
    content: '',
    steps: card.steps.map((step) => ({
      name: step.name,
      ok: step.ok !== false,
      summary: step.summary || '',
    })),
  });
  state.uploadCard = null;
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
  if (state.sending || !state.available || state.uploading) return;
  const text = String(dom.input?.value || '').trim().slice(0, MAX_INPUT);
  if (!text) return;
  dom.input.value = '';
  await submitText(text);
}

async function submitText(text) {
  if (state.sending || !state.available) {
    // 忙碌时把文本交还输入框，由用户稍后发送，避免静默丢失
    if (dom.input && !dom.input.value.trim()) dom.input.value = text;
    return;
  }
  state.messages = state.messages || [];
  state.messages.push({ role: 'user', content: text });
  render();

  state.sending = true;
  dom.send.disabled = true;
  dom.input.disabled = true;
  const draft = { role: 'assistant', content: '', steps: [] };
  render(draft);
  state.controller = new AbortController();

  try {
    const done = await sendAgentStream(
      { message: text, conversation_id: state.conversationId },
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
    if (done?.conversation_id && done.conversation_id !== state.conversationId) {
      setConversationId(done.conversation_id);
      void loadConversations({ silent: true });
    }
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
    }
    render();
  }
}

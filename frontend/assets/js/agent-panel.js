/**
 * agent-panel.js — 右侧常驻「管理助手」面板
 * 会话与历史由服务端持久化，本地按账号记住当前会话、开合状态与宽度。
 */
import {
  createAgentSchedule,
  deleteAgentConversation,
  deleteAgentSchedule,
  getAgentConversation,
  listAgentConversations,
  listAgentSchedules,
  rewindAgentConversation,
  runAgentSchedule,
  sendAgentStream,
  updateAgentSchedule,
  uploadDocument,
} from './api.js';
import { confirmDialog, esc, toast } from './ui.js';
import { containsAgentProtocol, containsModelNoise, normalizeAgentReply } from './agent-reply.js';
import { updateAgentProcess } from './agent-process.js';

const OPEN_KEY = 'npu_agent_open';
const CONV_KEY = 'npu_agent_conv';
const WIDTH_KEY = 'npu.agent_width';
const MAX_INPUT = 2000;
const MAX_UPLOAD_FILES = 5;
const state = {
  ready: false,
  available: false,
  routeAllowed: false,
  preferredWidth: 360,
  account: null,
  epoch: 0,
  conversationToken: 0,
  listToken: 0,
  scheduleToken: 0,
  conversationBusy: false,
  conversationsLoading: false,
  conversationsLoaded: false,
  open: false,
  sending: false,
  uploading: false,
  canUpload: false,
  uploadCard: null,
  draft: null,
  messages: [],
  messagesLoaded: false,
  conversationId: '',
  conversations: [],
  drawerOpen: false,
  drawerMode: 'conversations',
  schedules: [],
  scheduleDraft: null,
  scheduleCreating: false,
  canManage: false,
  controller: null,
  followLatest: true,
  scrollTop: 0,
  renderFrame: 0,
  resizing: false,
  inputTooLong: false,
};
const dom = {};
let renderedMessages = [];

function storageKey(key) {
  return `${key}:${encodeURIComponent(state.account || 'guest')}`;
}

function isCurrent(epoch) {
  return epoch === state.epoch && state.available;
}

function busy() {
  return state.sending || state.uploading || state.conversationBusy;
}

function blockConversation(silent = false) {
  if (!state.available) return true;
  if (!busy()) return false;
  if (!silent) toast('请等待当前操作完成后再切换、删除或回滚会话', 'warning');
  return true;
}

function syncControls() {
  if (dom.send) dom.send.disabled = busy() || !state.available;
  if (dom.input) dom.input.disabled = state.conversationBusy || !state.available;
  if (dom.attach) dom.attach.disabled = busy() || !state.available;
  if (dom.newBtn) dom.newBtn.disabled = busy();
  if (dom.stop) {
    dom.stop.hidden = !state.sending;
    dom.stop.disabled = !state.sending || Boolean(state.controller?.signal.aborted);
  }
  dom.convList?.querySelectorAll('[data-conv-open], [data-conv-delete]').forEach((button) => {
    button.disabled = busy();
  });
}

function updateLatest() {
  if (dom.latest) dom.latest.hidden = state.followLatest || !dom.body?.querySelector('.agent-msg');
}

function scrollToLatest() {
  state.followLatest = true;
  if (dom.body) {
    dom.body.scrollTop = dom.body.scrollHeight;
    state.scrollTop = dom.body.scrollTop;
  }
  updateLatest();
}

function updateResizeValue() {
  if (!dom.resize) return;
  const width = Math.round(dom.root.getBoundingClientRect().width || 384);
  dom.resize.setAttribute('aria-valuenow', String(Math.max(320, Math.min(720, width))));
}

function layoutPanel() {
  if (!dom.root) return;
  const shellWidth = dom.root.parentElement.clientWidth;
  const overlay = window.innerWidth < 1024 || shellWidth < 940;
  document.body.classList.toggle('agent-overlay', overlay);
  const maxWidth = overlay ? Math.min(720, shellWidth) : Math.min(720, shellWidth - 600);
  dom.root.style.width = `${Math.min(state.preferredWidth, maxWidth)}px`;
  const visible = state.available && state.routeAllowed && state.open;
  if (dom.backdrop) dom.backdrop.hidden = !(visible && overlay);
  dom.resize?.setAttribute('aria-valuemax', String(Math.max(320, maxWidth)));
  updateResizeValue();
}

function setWidth(width, persist = false) {
  state.preferredWidth = Math.max(320, Math.min(720, Math.round(width)));
  layoutPanel();
  if (persist) {
    try {
      localStorage.setItem(storageKey(WIDTH_KEY), String(state.preferredWidth));
    } catch { /* ignore */ }
  }
}

// 路由可见性不改变账号、会话、请求或用户记住的开合偏好。
export function setAgentPanelRoute(route) {
  state.routeAllowed = ['kb-admin', 'ops', 'settings'].includes(route);
  if (state.ready) applyOpen();
}

function clearFullscreen() {
  state.resizing = false;
  document.body.classList.remove('agent-fullscreen', 'agent-resizing');
  if (dom.expand) {
    dom.expand.title = '全屏 / 还原';
    dom.expand.setAttribute('aria-pressed', 'false');
    dom.expand.setAttribute('aria-label', '全屏');
  }
}

function releasePanelFocus() {
  if (!dom.root.contains(document.activeElement)) return;
  const target = !dom.toggle.hidden ? dom.toggle : document.getElementById('menuBtn');
  if (target && target.getClientRects().length) target.focus({ preventScroll: true });
  else document.activeElement?.blur?.();
}

function resetAccount(account) {
  state.epoch += 1;
  state.conversationToken += 1;
  state.listToken += 1;
  state.scheduleToken += 1;
  state.controller?.abort();
  state.controller = null;
  if (state.renderFrame) cancelAnimationFrame(state.renderFrame);
  state.renderFrame = 0;
  state.account = account;
  state.messages = [];
  state.messagesLoaded = false;
  state.conversationId = '';
  state.conversations = [];
  state.conversationsLoaded = false;
  state.conversationsLoading = false;
  state.conversationBusy = false;
  state.sending = false;
  state.uploading = false;
  state.draft = null;
  state.uploadCard = null;
  state.schedules = [];
  state.scheduleDraft = null;
  state.scheduleCreating = false;
  state.drawerOpen = false;
  state.drawerMode = 'conversations';
  state.followLatest = true;
  state.scrollTop = 0;
  state.inputTooLong = false;
  if (dom.input) {
    dom.input.value = '';
    dom.input.removeAttribute('aria-invalid');
  }
  if (dom.files) dom.files.value = '';
  if (dom.drawer) dom.drawer.hidden = true;
  if (dom.convList) dom.convList.replaceChildren();
  dom.body?.replaceChildren();
  dom.draftNode = dom.uploadNode = null;
  renderedMessages = [];
  clearFullscreen();
  releasePanelFocus();
  state.open = true;
  state.preferredWidth = 360;
  dom.root.style.width = '';
  try {
    state.open = localStorage.getItem(storageKey(OPEN_KEY)) !== '0';
    state.conversationId = localStorage.getItem(storageKey(CONV_KEY)) || '';
    const width = parseInt(localStorage.getItem(storageKey(WIDTH_KEY)) || '', 10);
    if (width >= 320) setWidth(width);
  } catch { /* ignore */ }
}

export function initAgentPanel(authState) {
  if (state.ready) {
    refreshAgentPanel(authState);
    return;
  }
  const ids = {
    root: 'agentPanel', toggle: 'agentToggle', body: 'agentBody', form: 'agentForm', backdrop: 'agentBackdrop',
    input: 'agentInput', send: 'agentSend', close: 'agentClose', attach: 'agentAttach',
    files: 'agentFiles', resize: 'agentResize', expand: 'agentExpand', stop: 'agentStop',
    latest: 'agentLatest', historyBtn: 'agentHistory', schedBtn: 'agentSched', newBtn: 'agentNew',
    drawer: 'agentDrawer', drawerClose: 'agentDrawerClose', drawerTitle: 'agentDrawerTitle',
    convList: 'agentConvList',
  };
  Object.entries(ids).forEach(([key, id]) => { dom[key] = document.getElementById(id); });
  if (!dom.root || !dom.toggle || !dom.form || !dom.body) return;
  state.ready = true;
  dom.input?.removeAttribute('maxlength');
  dom.toggle.addEventListener('click', () => setOpen(!state.open));
  dom.close?.addEventListener('click', () => setOpen(false));
  dom.backdrop?.addEventListener('click', () => setOpen(false));
  new ResizeObserver(layoutPanel).observe(dom.root.parentElement);
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
  dom.input?.addEventListener('input', () => {
    const tooLong = dom.input.value.trim().length > MAX_INPUT;
    dom.input.setAttribute('aria-invalid', String(tooLong));
    if (tooLong && !state.inputTooLong) toast(`最多发送 ${MAX_INPUT} 字，已保留全文，请缩短后发送`, 'warning');
    state.inputTooLong = tooLong;
  });
  dom.stop?.addEventListener('click', () => {
    if (!state.sending || !state.controller || state.controller.signal.aborted) return;
    state.controller.abort();
    toast('已停止前端等待；已执行的工具不会撤销，服务端可能继续执行。', 'warning');
    syncControls();
  });
  dom.latest?.addEventListener('click', scrollToLatest);
  dom.body.addEventListener('scroll', () => {
    const top = dom.body.scrollTop;
    const nearBottom = dom.body.scrollHeight - top - dom.body.clientHeight <= 64;
    state.followLatest = top < state.scrollTop ? false : nearBottom;
    state.scrollTop = top;
    updateLatest();
  }, { passive: true });
  dom.attach?.addEventListener('click', () => {
    if (!state.canUpload || busy()) return;
    dom.files?.click();
  });
  dom.files?.addEventListener('change', () => {
    const picked = Array.from(dom.files.files || []);
    dom.files.value = '';
    if (picked.length > MAX_UPLOAD_FILES) {
      toast(`一次最多上传 ${MAX_UPLOAD_FILES} 个附件，请重新选择；本次未上传文件`, 'warning');
      return;
    }
    if (picked.length) void handleUploads(picked);
  });
  if (dom.resize) {
    dom.resize.removeAttribute('aria-hidden');
    dom.resize.tabIndex = 0;
    dom.resize.setAttribute('role', 'separator');
    dom.resize.setAttribute('aria-label', '调整助手面板宽度');
    dom.resize.setAttribute('aria-orientation', 'vertical');
    dom.resize.setAttribute('aria-valuemin', '320');
    dom.resize.setAttribute('aria-valuemax', '720');
  }
  dom.resize?.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || window.innerWidth < 1024 || document.body.classList.contains('agent-fullscreen')) return;
    state.resizing = true;
    document.body.classList.add('agent-resizing');
    dom.resize.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  window.addEventListener('pointermove', (event) => {
    if (state.resizing) setWidth(window.innerWidth - event.clientX);
  });
  const finishResize = () => {
    if (!state.resizing) return;
    state.resizing = false;
    document.body.classList.remove('agent-resizing');
    setWidth(parseInt(dom.root.style.width, 10) || 384, true);
  };
  window.addEventListener('pointerup', finishResize);
  window.addEventListener('pointercancel', finishResize);
  window.addEventListener('resize', layoutPanel);
  dom.resize?.addEventListener('keydown', (event) => {
    if (window.innerWidth < 1024 || document.body.classList.contains('agent-fullscreen')) return;
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const width = parseInt(dom.root.style.width, 10) || dom.root.getBoundingClientRect().width || 384;
    const step = event.shiftKey ? 64 : 24;
    setWidth(event.key === 'Home' ? 320 : event.key === 'End' ? 720 : width + (event.key === 'ArrowLeft' ? step : -step), true);
  });
  dom.resize?.addEventListener('dblclick', () => {
    state.preferredWidth = 360;
    layoutPanel();
    try { localStorage.removeItem(storageKey(WIDTH_KEY)); } catch { /* ignore */ }
  });
  dom.expand?.addEventListener('click', () => {
    const full = document.body.classList.toggle('agent-fullscreen');
    dom.expand.title = full ? '还原' : '全屏 / 还原';
    dom.expand.setAttribute('aria-pressed', String(full));
    dom.expand.setAttribute('aria-label', full ? '还原助手面板' : '全屏');
    updateResizeValue();
    render();
  });
  dom.historyBtn?.addEventListener('click', () => setDrawer(!state.drawerOpen || state.drawerMode !== 'conversations', 'conversations'));
  dom.schedBtn?.addEventListener('click', () => setDrawer(!state.drawerOpen || state.drawerMode !== 'schedules', 'schedules'));
  dom.newBtn?.addEventListener('click', newConversation);
  dom.drawerClose?.addEventListener('click', () => setDrawer(false));
  dom.convList?.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-conv-open]');
    if (opener) { void openConversation(opener.dataset.convOpen); return; }
    const remover = event.target.closest('[data-conv-delete]');
    if (remover) { void removeConversation(remover.dataset.convDelete); return; }
    handleSchedClick(event);
  });
  dom.convList?.addEventListener('submit', (event) => {
    if (event.target.id !== 'agentSchedForm') return;
    event.preventDefault();
    void createScheduleFromForm(event.target);
  });
  dom.convList?.addEventListener('input', rememberScheduleForm);
  dom.convList?.addEventListener('change', (event) => {
    if (event.target.name === 'kind') {
      const prompt = event.target.form?.querySelector('[name="prompt"]');
      if (prompt) prompt.hidden = event.target.value !== 'agent_prompt';
    }
    rememberScheduleForm();
  });
  dom.body.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action="agent-rewind"]');
    if (trigger) void rewindFrom(trigger.dataset.id);
    if (event.target.closest('.agent-refill') && !busy()) {
      dom.input.value = event.target.closest('.agent-msg')._retryText || '';
      dom.input.focus();
    }
  });
  refreshAgentPanel(authState);
}

export function refreshAgentPanel(authState) {
  if (!state.ready) return;
  const available = Boolean(authState?.authenticated || authState?.open_mode);
  const account = authState?.authenticated
    ? `user:${authState.user?.id ?? authState.user?.username ?? 'authenticated'}`
    : authState?.open_mode ? 'open' : 'guest';
  dom.toggle.hidden = !(available && state.routeAllowed);
  if (account !== state.account || (!available && state.available)) resetAccount(account);
  state.available = available;
  const perms = Array.isArray(authState?.permissions) ? authState.permissions : [];
  state.canUpload = available && Boolean(authState?.open_mode || perms.includes('*') || perms.includes('content:write'));
  state.canManage = state.canUpload;
  if (dom.attach) dom.attach.hidden = !state.canUpload;
  if (dom.schedBtn) dom.schedBtn.hidden = !state.canManage;
  if (!state.canManage) {
    state.schedules = [];
    state.scheduleDraft = null;
    state.scheduleToken += 1;
    if (state.drawerMode === 'schedules') setDrawer(false, 'conversations');
    dom.convList?.querySelector('#agentSchedForm')?.remove();
  }
  dom.toggle.hidden = !(available && state.routeAllowed);
  syncControls();
  applyOpen();
}

function setOpen(open) {
  state.open = open;
  try { localStorage.setItem(storageKey(OPEN_KEY), open ? '1' : '0'); } catch { /* ignore */ }
  applyOpen();
  if (state.open && state.available && state.routeAllowed) dom.input?.focus();
}

function applyOpen() {
  const visible = state.available && state.routeAllowed && state.open;
  dom.toggle.hidden = !(state.available && state.routeAllowed);
  layoutPanel();
  dom.root.hidden = !visible;
  dom.toggle.setAttribute('aria-expanded', String(visible));
  dom.toggle.classList.toggle('is-active', visible);
  if (!visible) {
    clearFullscreen();
    setDrawer(false);
    releasePanelFocus();
    return;
  }
  render();
  updateResizeValue();
  if (state.conversationId && !state.messagesLoaded && !state.conversationBusy) void openConversation(state.conversationId, { silent: true });
  if (!state.conversationsLoaded && !state.conversationsLoading) void loadConversations({ silent: true });
}

/* 会话：请求 token 与账号 epoch 防止旧响应覆盖当前状态。 */
function setConversationId(id) {
  state.conversationId = id || '';
  try {
    if (id) localStorage.setItem(storageKey(CONV_KEY), id);
    else localStorage.removeItem(storageKey(CONV_KEY));
  } catch { /* ignore */ }
}

async function loadConversations({ silent = false } = {}) {
  if (!state.available) return;
  const epoch = state.epoch;
  const token = ++state.listToken;
  state.conversationsLoading = true;
  try {
    const data = await listAgentConversations(30);
    if (!isCurrent(epoch) || token !== state.listToken) return;
    state.conversations = Array.isArray(data?.items) ? data.items : [];
    state.conversationsLoaded = true;
    renderDrawer();
  } catch (err) {
    if (isCurrent(epoch) && token === state.listToken && !silent) toast(err?.message || '会话列表加载失败', 'error');
  } finally {
    if (isCurrent(epoch) && token === state.listToken) state.conversationsLoading = false;
  }
}

async function fetchConversation(id, epoch, token) {
  const data = await getAgentConversation(id);
  if (!isCurrent(epoch) || token !== state.conversationToken) return false;
  const items = Array.isArray(data?.items) ? data.items : [];
  state.messages = items.map((item) => ({
    id: item.id, role: item.role, content: item.content || '',
    steps: Array.isArray(item.steps) ? item.steps : [],
    stopReason: item.steps?.[0]?.run_stop_reason,
    elapsedMs: item.steps?.[0]?.run_elapsed_ms,
  }));
  state.messagesLoaded = true;
  state.draft = null;
  state.followLatest = true;
  setConversationId(id);
  render();
  renderDrawer();
  return true;
}

async function openConversation(id, { silent = false } = {}) {
  if (!id || blockConversation(silent)) return;
  const epoch = state.epoch;
  const token = ++state.conversationToken;
  state.conversationBusy = true;
  syncControls();
  try {
    if (await fetchConversation(id, epoch, token)) void loadConversations({ silent: true });
  } catch (err) {
    if (!isCurrent(epoch) || token !== state.conversationToken) return;
    setConversationId('');
    state.messages = [];
    state.messagesLoaded = true;
    render();
    if (!silent) toast(err?.message || '会话加载失败，已回到新会话', 'warning');
  } finally {
    if (isCurrent(epoch) && token === state.conversationToken) {
      state.conversationBusy = false;
      syncControls();
    }
  }
}

function newConversation() {
  if (blockConversation()) return;
  state.conversationToken += 1;
  setConversationId('');
  state.messages = [];
  state.draft = null;
  state.messagesLoaded = true;
  state.followLatest = true;
  render();
  renderDrawer();
  dom.input?.focus();
}

async function removeConversation(id) {
  if (blockConversation()) return;
  const epoch = state.epoch;
  const before = state.conversationToken;
  const target = state.conversations.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除会话', message: `确定删除「${target?.title || '该会话'}」吗？`,
    detail: '对话记录与工具步骤将一并删除，不可恢复。', confirmText: '删除', danger: true,
  });
  if (!ok || !isCurrent(epoch) || before !== state.conversationToken || blockConversation()) return;
  const token = ++state.conversationToken;
  state.conversationBusy = true;
  syncControls();
  try {
    await deleteAgentConversation(id);
    if (!isCurrent(epoch) || token !== state.conversationToken) return;
    if (id === state.conversationId) {
      setConversationId('');
      state.messages = [];
      state.messagesLoaded = true;
      state.followLatest = true;
      render();
    }
    await loadConversations({ silent: true });
  } catch (err) {
    if (isCurrent(epoch) && token === state.conversationToken) toast(err?.message || '删除失败', 'error');
  } finally {
    if (isCurrent(epoch) && token === state.conversationToken) {
      state.conversationBusy = false;
      syncControls();
    }
  }
}

function rememberScheduleForm() {
  const form = dom.convList?.querySelector('#agentSchedForm');
  if (form && state.canManage) state.scheduleDraft = Object.fromEntries(new FormData(form));
}

function setDrawer(open, mode = state.drawerMode) {
  rememberScheduleForm();
  state.drawerMode = mode;
  state.drawerOpen = open;
  if (dom.drawer) dom.drawer.hidden = !open;
  if (dom.drawerTitle) dom.drawerTitle.textContent = mode === 'schedules' ? '定时任务' : '会话历史';
  if (open) {
    if (mode === 'schedules') void loadSchedules({ silent: true });
    else void loadConversations({ silent: true });
    renderDrawer();
  }
}

function renderDrawer() {
  if (!dom.convList) return;
  if (state.drawerMode === 'schedules') { renderSchedules(); return; }
  rememberScheduleForm();
  if (!state.conversations.length) {
    dom.convList.innerHTML = '<p class="agent-empty-note">还没有历史会话；新对话会自动保存。</p>';
    return;
  }
  dom.convList.innerHTML = state.conversations.map((item) => `
    <div class="agent-conv ${item.id === state.conversationId ? 'is-active' : ''}">
      <button class="agent-conv-main" type="button" data-conv-open="${esc(item.id)}" ${item.id === state.conversationId ? 'aria-current="true"' : ''}>
        <span class="agent-conv-title">${esc(item.title)}</span>
        <span class="agent-conv-meta">${esc(item.last_message || '')} · ${esc(String(item.updated_at || '').slice(5, 16))}</span>
      </button>
      <button class="icon-btn" type="button" data-conv-delete="${esc(item.id)}" aria-label="删除会话" title="删除会话">
        <svg class="i" aria-hidden="true"><use href="#i-trash"></use></svg>
      </button>
    </div>`).join('');
  syncControls();
}

/* 定时任务：只刷新列表，保留新建表单节点、焦点与未提交值。 */
const KIND_LABEL = {
  subscription_check_all: '订阅全量检查', gap_suggest_all: '缺口批量找备选', agent_prompt: '定时执行指令',
};
function _intervalLabel(minutes) {
  const value = Number(minutes) || 60;
  if (value % 1440 === 0) return `每 ${value / 1440} 天`;
  if (value % 60 === 0) return `每 ${value / 60} 小时`;
  return `每 ${value} 分钟`;
}
async function loadSchedules({ silent = false } = {}) {
  if (!state.available || !state.canManage) return;
  const epoch = state.epoch;
  const token = ++state.scheduleToken;
  try {
    const data = await listAgentSchedules();
    if (!isCurrent(epoch) || !state.canManage || token !== state.scheduleToken) return;
    state.schedules = Array.isArray(data?.items) ? data.items : [];
    renderDrawer();
  } catch (err) {
    if (isCurrent(epoch) && token === state.scheduleToken && !silent) toast(err?.message || '定时任务加载失败', 'error');
  }
}
function renderSchedules() {
  let list = dom.convList.querySelector('[data-agent-schedules]');
  if (!list) {
    dom.convList.innerHTML = '<div data-agent-schedules></div>';
    list = dom.convList.firstElementChild;
  }
  list.innerHTML = state.schedules.map((item) => {
    const status = item.last_error ? `上次失败：${esc(item.last_error)}` : esc(item.last_status || '尚未运行');
    const prompt = item.kind === 'agent_prompt' ? esc(String((item.config || {}).prompt || '')) : '';
    return `<div class="agent-conv ${item.enabled ? '' : 'is-muted'}">
      <div class="agent-conv-main">
        <span class="agent-conv-title">${esc(item.name)} · ${esc(KIND_LABEL[item.kind] || item.kind)}</span>
        <span class="agent-conv-meta">${esc(_intervalLabel(item.interval_minutes))} · ${status}${prompt ? ` · 指令：${prompt}` : ''}</span>
      </div>
      <button class="icon-btn" type="button" data-sched-toggle="${esc(item.id)}" aria-label="${item.enabled ? '停用' : '启用'}" title="${item.enabled ? '停用' : '启用'}"><svg class="i" aria-hidden="true"><use href="#i-power"></use></svg></button>
      <button class="icon-btn" type="button" data-sched-run="${esc(item.id)}" aria-label="立即运行" title="立即运行"><svg class="i" aria-hidden="true"><use href="#i-play"></use></svg></button>
      <button class="icon-btn" type="button" data-sched-delete="${esc(item.id)}" aria-label="删除" title="删除"><svg class="i" aria-hidden="true"><use href="#i-trash"></use></svg></button>
    </div>`;
  }).join('') || '<p class="agent-empty-note">还没有定时任务。可配置：订阅自动检查、缺口自动找备选、定时让助手执行一段指令。</p>';
  if (!state.canManage || dom.convList.querySelector('#agentSchedForm')) return;
  dom.convList.insertAdjacentHTML('beforeend', `<form id="agentSchedForm" class="agent-sched-form">
    <input class="input" name="name" placeholder="名称（如：每天检查订阅）" required />
    <select class="input" name="kind" aria-label="任务类型">
      <option value="subscription_check_all">订阅全量检查</option>
      <option value="gap_suggest_all">缺口批量找备选</option>
      <option value="agent_prompt">定时执行指令</option>
    </select>
    <input class="input" name="interval" type="number" min="15" max="10080" value="360" required aria-label="间隔分钟" />
    <textarea class="input" name="prompt" rows="2" hidden placeholder="预设指令，如：检查订阅发现的新内容，把与奖学金相关的收录并汇报"></textarea>
    <button class="btn btn-primary btn-sm" type="submit" ${state.scheduleCreating ? 'disabled' : ''}>创建定时任务</button>
  </form>`);
  const form = dom.convList.querySelector('#agentSchedForm');
  for (const [name, value] of Object.entries(state.scheduleDraft || {})) {
    const input = form.elements.namedItem(name);
    if (input) input.value = value;
  }
  form.elements.namedItem('prompt').hidden = form.elements.namedItem('kind').value !== 'agent_prompt';
}
function handleSchedClick(event) {
  if (!state.canManage) return;
  const toggle = event.target.closest('[data-sched-toggle]');
  if (toggle) { void toggleSchedule(toggle.dataset.schedToggle); return; }
  const runner = event.target.closest('[data-sched-run]');
  if (runner) { void runScheduleNow(runner.dataset.schedRun, runner); return; }
  const remover = event.target.closest('[data-sched-delete]');
  if (remover) void removeSchedule(remover.dataset.schedDelete);
}
async function toggleSchedule(id) {
  const epoch = state.epoch;
  const item = state.schedules.find((entry) => entry.id === id);
  if (!item || !state.canManage) return;
  try {
    await updateAgentSchedule(id, { enabled: !item.enabled });
    if (isCurrent(epoch) && state.canManage) await loadSchedules({ silent: true });
  } catch (err) {
    if (isCurrent(epoch)) toast(err?.message || '操作失败', 'error');
  }
}
async function runScheduleNow(id, trigger) {
  const epoch = state.epoch;
  if (!state.canManage) return;
  if (trigger) trigger.disabled = true;
  try {
    const result = await runAgentSchedule(id);
    if (!isCurrent(epoch) || !state.canManage) return;
    toast(result?.result || '已触发一次执行', 'success');
    await loadSchedules({ silent: true });
  } catch (err) {
    if (isCurrent(epoch)) toast(err?.message || '触发失败', 'error');
  } finally {
    if (trigger?.isConnected && isCurrent(epoch)) trigger.disabled = false;
  }
}
async function removeSchedule(id) {
  if (!state.canManage) return;
  const epoch = state.epoch;
  const item = state.schedules.find((entry) => entry.id === id);
  const ok = await confirmDialog({
    title: '删除定时任务', message: `确定删除「${item?.name || '该任务'}」吗？`, confirmText: '删除', danger: true,
  });
  if (!ok || !isCurrent(epoch) || !state.canManage) return;
  try {
    await deleteAgentSchedule(id);
    if (isCurrent(epoch)) await loadSchedules({ silent: true });
  } catch (err) {
    if (isCurrent(epoch)) toast(err?.message || '删除失败', 'error');
  }
}
async function createScheduleFromForm(form) {
  if (!state.canManage || state.scheduleCreating) return;
  const epoch = state.epoch;
  const data = new FormData(form);
  const submitted = Object.fromEntries(data);
  const kind = String(data.get('kind') || '');
  const body = {
    name: String(data.get('name') || '').trim(), kind,
    interval_minutes: Number(data.get('interval')) || 360,
  };
  if (kind === 'agent_prompt') body.config = { prompt: String(data.get('prompt') || '').trim() };
  if (kind === 'gap_suggest_all') body.config = { limit: 10 };
  state.scheduleCreating = true;
  form.querySelector('[type="submit"]').disabled = true;
  try {
    await createAgentSchedule(body);
    if (!isCurrent(epoch) || !state.canManage) return;
    toast('定时任务已创建', 'success');
    rememberScheduleForm();
    if (JSON.stringify(state.scheduleDraft) === JSON.stringify(submitted)) {
      state.scheduleDraft = null;
      const currentForm = dom.convList.querySelector('#agentSchedForm');
      currentForm?.reset();
      const prompt = currentForm?.querySelector('[name="prompt"]');
      if (prompt) prompt.hidden = true;
    }
    await loadSchedules({ silent: true });
  } catch (err) {
    if (isCurrent(epoch)) toast(err?.message || '创建失败', 'error');
  } finally {
    if (isCurrent(epoch)) {
      state.scheduleCreating = false;
      const submit = dom.convList.querySelector('#agentSchedForm [type="submit"]');
      if (submit) submit.disabled = false;
    }
  }
}
async function rewindFrom(messageId) {
  if (!state.conversationId || blockConversation()) return;
  const epoch = state.epoch;
  const id = state.conversationId;
  const before = state.conversationToken;
  const target = state.messages.find((item) => item.id === messageId);
  if (!target) return;
  const ok = await confirmDialog({
    title: '从这里重问', message: '回滚到这条消息之前，并把原文放回输入框？',
    detail: '仅回退对话内容；已执行的工具操作（收录、删除等）不会被撤销。', confirmText: '回滚', danger: true,
  });
  if (!ok || !isCurrent(epoch) || before !== state.conversationToken || blockConversation()) return;
  const token = ++state.conversationToken;
  state.conversationBusy = true;
  syncControls();
  try {
    await rewindAgentConversation(id, messageId);
    if (!isCurrent(epoch) || token !== state.conversationToken) return;
    if (dom.input && !dom.input.value.trim()) dom.input.value = target.content;
    if (await fetchConversation(id, epoch, token)) toast('已回滚，原文已放回输入框', 'success');
  } catch (err) {
    if (isCurrent(epoch) && token === state.conversationToken) toast(err?.message || '回滚失败', 'error');
  } finally {
    if (isCurrent(epoch) && token === state.conversationToken) {
      state.conversationBusy = false;
      syncControls();
    }
  }
}

/* 消息节点按对象增量追加；SSE 更新只修改持久草稿，按帧合并绘制。 */
function createMessageNode(message) {
  const node = document.createElement('div');
  node.className = `agent-msg ${message.role === 'user' ? 'is-user' : 'is-assistant'}`;
  if (message.role === 'user') {
    node.innerHTML = `<div class="agent-bubble">${esc(message.content)}</div>${message.id ? `<button class="agent-rewind" type="button" data-action="agent-rewind" data-id="${esc(message.id)}" title="从这里重问">↺ 重问</button>` : ''}`;
  } else {
    node.innerHTML = '<div class="agent-process-host"></div><div class="agent-bubble"></div><div class="agent-message-actions"></div>';
    updateLiveMessage(node, message);
  }
  return node;
}
function updateLiveMessage(node, message) {
  updateAgentProcess(node.firstElementChild, (containsAgentProtocol(message.content) || containsModelNoise(message.content)) ? { ...message, stopReason: 'invalid_protocol' } : message);
  const content = normalizeAgentReply(message.content || '');
  const bubble = node.querySelector('.agent-bubble');
  bubble.hidden = !content && Boolean(message.active || message.steps?.length);
  bubble.classList.toggle('agent-typing', !content && !message.active && !message.steps?.length);
  if (node._content !== content) {
    bubble.innerHTML = content ? renderMarkdown(content) : '正在处理…';
    node._content = content;
  }
  const actions = node.querySelector('.agent-message-actions');
  if (actions) {
    const retry = !message.active && message.stopReason && message.stopReason !== 'completed' && message.retryText;
    if (Boolean(actions.childNodes.length) !== Boolean(retry)) actions.innerHTML = retry ? '<button class="agent-refill" type="button">编辑后重问</button>' : '';
    node._retryText = message.retryText || '';
    actions.hidden = !retry;
  }
}
function queueRender() {
  if (!state.renderFrame) state.renderFrame = requestAnimationFrame(() => {
    state.renderFrame = 0;
    render();
  });
}
function render() {
  if (!dom.body) return;
  if (state.renderFrame) cancelAnimationFrame(state.renderFrame);
  state.renderFrame = 0;
  const messages = state.messages;
  if (renderedMessages.length > messages.length || renderedMessages.some((message, index) => message !== messages[index])) {
    dom.body.replaceChildren();
    renderedMessages = [];
    dom.draftNode = dom.uploadNode = null;
  }
  if (!messages.length && !state.uploadCard && !state.draft) {
    if (!dom.body.querySelector('.agent-empty')) dom.body.innerHTML = `<div class="agent-empty">
      <p>我是管理助手，可以直接代你查询和操作：</p>
      <ul><li>「现在有多少未处理的知识缺口？」</li><li>「给第一条缺口找备选资料」</li><li>「检查所有订阅有没有新内容」</li><li>「收录这个链接 …」</li></ul>
      <p class="agent-empty-note">可直接上传文件；删除、重建、批准修复等高危操作我会先和你确认。会话自动保存，可随时在右上角「历史」中回看或回滚。</p>
    </div>`;
    dom.draftNode = dom.uploadNode = null;
    updateLatest();
    syncControls();
    return;
  }
  dom.body.querySelector('.agent-empty')?.remove();
  if (!state.uploadCard) { dom.uploadNode?.remove(); dom.uploadNode = null; }
  if (!state.draft) { dom.draftNode?.remove(); dom.draftNode = null; }
  for (let i = renderedMessages.length; i < messages.length; i += 1) {
    dom.body.insertBefore(createMessageNode(messages[i]), dom.uploadNode || dom.draftNode || null);
  }
  renderedMessages = messages.slice();
  for (const [key, message] of [['uploadNode', state.uploadCard], ['draftNode', state.draft]]) {
    if (!message) continue;
    if (!dom[key]) {
      dom[key] = createMessageNode(message);
      dom.body.appendChild(dom[key]);
    } else updateLiveMessage(dom[key], message);
  }
  if (state.followLatest) scrollToLatest();
  else updateLatest();
  syncControls();
}
function renderMarkdown(text) {
  return esc(String(text || '')).replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
}

/* 附件上传：账号变化后忽略旧进度与结果，并停止后续文件上传。 */
async function handleUploads(files) {
  if (busy() || !state.available || !state.canUpload) return;
  const epoch = state.epoch;
  state.uploading = true;
  state.uploadCard = { role: 'assistant', content: '', steps: [], active: true, phaseText: '正在上传附件' };
  const card = state.uploadCard;
  render();
  const uploaded = [];
  for (const file of files) {
    if (!isCurrent(epoch) || !state.canUpload) break;
    const step = { name: `上传 ${file.name}`, pending: true, summary: '0%' };
    card.steps.push(step);
    render();
    try {
      const result = await uploadDocument(file, {
        asyncMode: true,
        onProgress: (percent) => {
          if (!isCurrent(epoch)) return;
          step.summary = `${percent}%`;
          queueRender();
        },
      });
      if (!isCurrent(epoch)) return;
      step.pending = false;
      step.ok = true;
      step.summary = result?.job_id ? `已提交编译（任务 ${result.job_id}）` : '已提交';
      if (result?.job_id) uploaded.push({ name: file.name, jobId: result.job_id });
    } catch (err) {
      if (!isCurrent(epoch)) return;
      step.pending = false;
      step.ok = false;
      step.summary = String(err?.message || '上传失败').slice(0, 120);
    }
    render();
  }
  if (!isCurrent(epoch)) return;
  state.messages.push({ role: 'assistant', content: '', steps: card.steps.map((step) => ({ ...step })) });
  state.uploadCard = null;
  state.uploading = false;
  render();
  if (uploaded.length) {
    const summary = uploaded.map((item) => `《${item.name}》→ 任务 ${item.jobId}`).join('；');
    await submitText(`我通过面板上传了 ${uploaded.length} 个文件：${summary}。请用 get_job 查询这些编译任务的当前状态并简要汇报。`);
  }
}

async function send() {
  if (busy() || !state.available) return;
  const text = String(dom.input?.value || '').trim();
  if (!text) return;
  if (text.length > MAX_INPUT) {
    toast(`最多发送 ${MAX_INPUT} 字，已保留全文，请缩短后发送`, 'warning');
    dom.input.setAttribute('aria-invalid', 'true');
    return;
  }
  dom.input.value = '';
  dom.input.removeAttribute('aria-invalid');
  state.inputTooLong = false;
  await submitText(text);
}
async function submitText(text) {
  if (busy() || !state.available) {
    if (dom.input && !dom.input.value.trim()) dom.input.value = text;
    return;
  }
  const epoch = state.epoch;
  const token = ++state.conversationToken;
  state.messagesLoaded = true;
  state.messages.push({ role: 'user', content: text });
  state.sending = true;
  state.followLatest = true;
  const draft = { role: 'assistant', content: '', steps: [], active: true, phaseText: '正在连接管理助手' };
  state.draft = draft;
  const controller = new AbortController();
  state.controller = controller;
  const current = () => isCurrent(epoch) && token === state.conversationToken && state.draft === draft;
  render();
  try {
    const done = await sendAgentStream({ message: text, conversation_id: state.conversationId }, {
      signal: controller.signal,
      onConversation: (data) => { if (current() && data?.conversation_id) setConversationId(data.conversation_id); },
      onStatus: (data) => {
        if (!current() || controller.signal.aborted) return;
        draft.phaseText = String(data?.text || '正在处理');
        draft.elapsedMs = Number(data?.elapsed_ms || 0);
        queueRender();
      },
      onTool: (data) => {
        if (!current() || controller.signal.aborted) return;
        draft.steps.push({ id: data?.id, name: String(data?.name || ''), args: data?.args || {}, pending: true });
        queueRender();
      },
      onToolResult: (data) => {
        if (!current() || controller.signal.aborted) return;
        const step = [...draft.steps].reverse().find((item) => data?.id ? item.id === data.id : item.name === data?.name && item.pending);
        if (step) {
          step.pending = false;
          step.ok = Boolean(data?.ok);
          step.summary = String(data?.summary || '').slice(0, 240);
          step.status = data?.status;
          step.result = data?.result;
          step.elapsed_ms = data?.elapsed_ms;
        }
        queueRender();
      },
      onDelta: (delta) => {
        if (!current() || controller.signal.aborted) return;
        draft.content += delta;
        queueRender();
      },
    });
    if (!current()) return;
    if (typeof done?.reply === 'string') draft.content = normalizeAgentReply(done.reply);
    if (Array.isArray(done?.steps)) draft.steps = done.steps;
    draft.stopReason = done?.stop_reason || 'completed';
    draft.elapsedMs = done?.elapsed_ms ?? draft.elapsedMs;
    if (done?.save_warning) toast(done.save_warning, 'warning');
    if (done?.conversation_id && done.conversation_id !== state.conversationId) setConversationId(done.conversation_id);
    void loadConversations({ silent: true });
  } catch (err) {
    if (!current()) return;
    const note = controller.signal.aborted || err?.name === 'AbortError'
      ? '已停止前端等待；已执行的工具不会撤销，服务端可能继续执行。'
      : `请求失败：${err?.message || '请稍后重试'}`;
    draft.content = draft.content ? `${draft.content}\n${note}` : note;
    draft.stopReason = controller.signal.aborted ? 'cancelled' : 'connection_error';
  } finally {
    if (current()) {
      state.sending = false;
      state.controller = null;
      const finalMessage = {
        role: 'assistant', content: draft.content || '本轮未收到最终说明，请重试并核对任务记录。',
        active: false, stopReason: draft.stopReason || 'connection_error', elapsedMs: draft.elapsedMs,
        retryText: text,
        steps: draft.steps.map((step) => ({ ...step, pending: false, status: step.pending ? 'unknown' : step.status,
          summary: step.pending ? '等待已结束，执行结果未确认' : step.summary || '',
        })),
      };
      state.messages.push(finalMessage);
      // 完成时保留同一个DOM节点，用户手动展开/收起的选择不会被最终绘制抹掉。
      if (dom.draftNode) {
        updateLiveMessage(dom.draftNode, finalMessage);
        renderedMessages.push(finalMessage);
        dom.draftNode = null;
      }
      state.draft = null;
      render();
    }
  }
}

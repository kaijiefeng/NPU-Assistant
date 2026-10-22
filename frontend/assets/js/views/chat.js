/**
 * views/chat.js — 对话页
 *
 * 欢迎语 + 快捷问题 + 消息流 + 引用卡片 + 历史会话 + 新建会话。
 * 接口：GET /settings、POST /chat、GET/DELETE /chat/conversations…
 */
import {
  deleteConversation,
  getConversationMessages,
  getHealth,
  getSettings,
  listConversations,
  sendChat,
} from '../api.js';
import {
  confirmDialog,
  esc,
  fmtRelative,
  fmtScore,
  icon,
  loadingHtml,
  toast,
} from '../ui.js';

const FALLBACK_SETTINGS = {
  counselor_name: '小南',
  welcome_message:
    '同学你好，我是辅导员数字分身「小南」👋 学校政策、奖助学金、请假流程、心理与就业服务等问题都可以问我。回答会标注引用的知识库文件，方便你核对原文。',
  quick_questions: [
    '国家奖学金怎么申请？需要哪些材料？',
    '请假流程是什么？病假需要什么证明？',
    '心理中心怎么预约？',
    '毕业生档案和报到证怎么办？',
  ],
  citations_enabled: true,
  retrieval_top_k: 4,
};

const state = {
  loaded: false,
  settings: FALLBACK_SETTINGS,
  settingsReady: false,
  conversations: [],
  historyReady: false,
  historyLoading: true,
  messages: [],
  conversationId: null,
  // 新建会话时使用唯一的匿名 session_key，确保「新建会话」真正创建新会话，
  // 而不是复用数据库中已有的 web:anonymous 会话（见 CONTRACT §5.5 会话标识）。
  sessionKey: '',
  meta: null,
  sending: false,
  loadingConversation: false,
  lastFailedText: '',
  healthModel: '',
};

function newSessionKey() {
  return `web:anonymous-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

if (!state.sessionKey) state.sessionKey = newSessionKey();

function initial(name) {
  const text = String(name || '小南').trim();
  return text.slice(0, 1) || '南';
}

function timeText(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

/* ------------------------------------------------------------------ */
/* 消息内容渲染                                                        */
/* ------------------------------------------------------------------ */
function renderContent(content, withCites = true) {
  const escaped = esc(content || '');
  if (!withCites) return escaped;
  return escaped.replace(/\[(\d{1,2})\]/g, (match, num) => {
    return `<button class="cite-ref" type="button" data-action="scroll-cite" data-cite="${num}" title="查看引用 [${num}]">[${num}]</button>`;
  });
}

function citationsHtml(citations, messageIndex) {
  if (!Array.isArray(citations) || !citations.length) return '';
  const cards = citations
    .map((cite, i) => {
      const index = cite?.index ?? i + 1;
      const title = cite?.document_title || '未命名文档';
      const score = cite?.score;
      return `<div class="citation-card" data-citation="${messageIndex}-${index}">
        <button class="citation-summary" type="button" data-action="toggle-citation" data-target="${messageIndex}-${index}" aria-expanded="false">
          <span class="citation-index">${esc(index)}</span>
          <span class="citation-title">${esc(title)}</span>
          ${score !== undefined && score !== null ? `<span class="citation-score">${fmtScore(score)}</span>` : ''}
          <span class="citation-chevron">${icon('chevron-down')}</span>
        </button>
        <div class="citation-body">
          ${cite?.section ? `<div class="citation-section">${icon('book')}<span>${esc(cite.section)}</span></div>` : ''}
          <p class="citation-snippet">${esc(cite?.snippet || '（无摘要）')}</p>
          <div class="citation-meta">
            ${cite?.document_id ? `<span title="${esc(cite.document_id)}">文档 ${esc(String(cite.document_id).slice(0, 8))}</span>` : ''}
            ${cite?.chunk_id ? `<span title="${esc(cite.chunk_id)}">切片 ${esc(String(cite.chunk_id).slice(0, 8))}</span>` : ''}
          </div>
        </div>
      </div>`;
    })
    .join('');
  return `<div class="citations">
    <div class="citations-head">${icon('book')}<span>引用来源</span><span class="citations-count">${citations.length}</span></div>
    ${cards}
  </div>`;
}

function messageHtml(message, index) {
  const role = message.role === 'user' ? 'user' : 'assistant';
  const who = role === 'user' ? '我' : initial(state.settings.counselor_name);
  if (role === 'user') {
    return `<div class="msg msg-user" data-msg-index="${index}">
      <div class="msg-bubble">${renderContent(message.content, false)}</div>
      <div class="msg-avatar msg-avatar-user">${esc(who)}</div>
    </div>`;
  }
  const footParts = [];
  if (message.model) footParts.push(esc(message.model));
  if (message.provider_type === 'mock' || message.fallback) footParts.push('演示降级');
  if (Number.isFinite(Number(message.latency_ms))) footParts.push(`${Math.round(Number(message.latency_ms))} ms`);
  if (Number.isFinite(Number(message.sources_used))) footParts.push(`命中 ${Number(message.sources_used)} 条`);
  return `<div class="msg msg-assistant" data-msg-index="${index}">
    <div class="msg-avatar msg-avatar-bot">${esc(who)}</div>
    <div class="msg-main">
      <div class="msg-meta">
        <strong>${esc(state.settings.counselor_name || '小南')}</strong>
        <span>${esc(timeText(message.created_at))}</span>
      </div>
      <div class="msg-bubble">${renderContent(message.content)}</div>
      ${citationsHtml(message.citations, index)}
      ${footParts.length ? `<div class="msg-foot">${footParts.map((t) => `<span>${t}</span>`).join('')}</div>` : ''}
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* 视图                                                                */
/* ------------------------------------------------------------------ */
export function mount(root) {
  let alive = true;
  const counselor = state.settings.counselor_name || '小南';

  root.innerHTML = `
  <div class="chat-page" id="chatPage">
    <aside class="chat-history" id="chatHistory" aria-label="历史会话">
      <div class="chat-history-head">
        <div>
          <h2>${icon('history')}<span>历史会话</span></h2>
          <p id="historyCount">加载中…</p>
        </div>
        <button class="icon-btn" type="button" data-action="refresh-history" aria-label="刷新历史会话" title="刷新">
          ${icon('refresh')}
        </button>
      </div>
      <div class="chat-history-list" id="historyList">${loadingHtml('加载历史会话…', 'state-compact')}</div>
      <div class="chat-history-foot">
        <button class="btn btn-primary btn-block" type="button" data-action="new-chat">
          ${icon('plus')}<span>新建会话</span>
        </button>
      </div>
    </aside>
    <div class="chat-history-backdrop" data-action="close-history" hidden></div>

    <section class="chat-panel">
      <header class="chat-head">
        <button class="icon-btn chat-history-toggle" type="button" data-action="toggle-history" aria-label="历史会话">
          ${icon('history')}
        </button>
        <div class="chat-head-avatar">${esc(initial(counselor))}</div>
        <div class="chat-head-info">
          <h2><span id="chatCounselorName">${esc(counselor)}</span><span class="live-dot" title="在线"></span></h2>
          <p id="chatMeta">辅导员数字分身${state.healthModel ? ` · ${esc(state.healthModel)}` : ''}</p>
        </div>
        <button class="btn btn-ghost btn-sm" type="button" data-action="new-chat">
          ${icon('plus')}<span>新建会话</span>
        </button>
      </header>

      <div class="chat-scroll" id="chatScroll">
        <div class="chat-messages" id="messageList"></div>
      </div>

      <div class="chat-error" id="chatError" hidden></div>

      <form class="composer" id="chatForm" autocomplete="off">
        <textarea id="chatInput" class="composer-input" rows="1" maxlength="2000"
          placeholder="请输入你的问题，Enter 发送，Shift + Enter 换行" aria-label="输入问题"></textarea>
        <div class="composer-actions">
          <span class="composer-counter" id="composerCounter">0/2000</span>
          <button class="btn btn-primary composer-send" type="submit" id="sendBtn" disabled>
            ${icon('send')}<span>发送</span>
          </button>
        </div>
      </form>
      <p class="composer-hint">${icon('shield')} 回答由 AI 结合知识库生成，仅供参考；重要事项请以学校官方通知为准。</p>
    </section>
  </div>`;

  const page = root.querySelector('#chatPage');
  const historyList = root.querySelector('#historyList');
  const historyCount = root.querySelector('#historyCount');
  const messageList = root.querySelector('#messageList');
  const chatScroll = root.querySelector('#chatScroll');
  const chatForm = root.querySelector('#chatForm');
  const input = root.querySelector('#chatInput');
  const sendBtn = root.querySelector('#sendBtn');
  const counter = root.querySelector('#composerCounter');
  const chatError = root.querySelector('#chatError');
  const backdrop = root.querySelector('.chat-history-backdrop');
  const meta = root.querySelector('#chatMeta');
  const counselorNameEl = root.querySelector('#chatCounselorName');

  /* ----------------------- 历史会话渲染 ----------------------- */
  function renderHistory() {
    if (!alive) return;
    const items = state.conversations;
    historyCount.textContent = state.historyReady ? `${items.length} 个会话` : '暂不可用';
    if (!state.historyReady && state.historyLoading) {
      historyList.innerHTML = loadingHtml('加载历史会话…', 'state-compact');
      return;
    }
    if (!state.historyReady) {
      historyList.innerHTML = `<div class="history-offline">${icon('offline')}<p>历史会话接口暂不可用</p><button class="btn btn-sm btn-ghost" type="button" data-action="refresh-history">重试</button></div>`;
      return;
    }
    if (!items.length) {
      historyList.innerHTML = `<div class="history-empty">${icon('message')}<p>还没有历史会话</p><span>从输入框开始第一次提问吧</span></div>`;
      return;
    }
    historyList.innerHTML = items
      .map((item) => {
        const active = item.id === state.conversationId ? ' is-active' : '';
        return `<div class="history-item${active}" data-id="${esc(item.id)}">
          <button class="history-main" type="button" data-action="open-conversation" data-id="${esc(item.id)}">
            <span class="history-title">${esc(item.last_message || '（空会话）')}</span>
            <span class="history-sub">${esc(fmtRelative(item.updated_at || item.created_at))} · ${Number(item.message_count || 0)} 条消息</span>
          </button>
          <button class="history-del" type="button" data-action="delete-conversation" data-id="${esc(item.id)}" aria-label="删除会话" title="删除会话">${icon('trash')}</button>
        </div>`;
      })
      .join('');
  }

  async function loadHistory({ silent = false } = {}) {
    state.historyLoading = true;
    try {
      const data = await listConversations('web', 30);
      if (!alive) return;
      state.conversations = Array.isArray(data?.items) ? data.items : [];
      state.historyReady = true;
      state.historyLoading = false;
      renderHistory();
    } catch (err) {
      if (!alive) return;
      state.historyLoading = false;
      if (!state.conversations.length) state.historyReady = false;
      renderHistory();
      if (!silent) toast(err.message || '历史会话加载失败', 'error');
    }
  }

  /* ----------------------- 消息渲染 ----------------------- */
  function scrollToBottom(smooth = false) {
    if (!chatScroll) return;
    const top = chatScroll.scrollHeight;
    if (smooth && typeof chatScroll.scrollTo === 'function') {
      chatScroll.scrollTo({ top, behavior: 'smooth' });
    } else {
      chatScroll.scrollTop = top;
    }
  }

  function renderWelcome() {
    const settings = state.settings;
    const questions = Array.isArray(settings.quick_questions)
      ? settings.quick_questions.filter((item) => typeof item === 'string' && item.trim())
      : [];
    return `<div class="chat-welcome">
      <div class="welcome-avatar">${esc(initial(settings.counselor_name))}</div>
      <h2>你好，我是${esc(settings.counselor_name || '小南')}</h2>
      <p class="welcome-text">${esc(settings.welcome_message || '有什么可以帮你的吗？')}</p>
      ${
        questions.length
          ? `<div class="quick-questions">${questions
              .map(
                (question) =>
                  `<button class="quick-question" type="button" data-action="quick-question" data-question="${esc(
                    question,
                  )}">${icon('sparkles')}<span>${esc(question)}</span></button>`,
              )
              .join('')}</div>`
          : ''
      }
      <div class="welcome-tips">
        <span>${icon('book')} 回答会标注知识库引用</span>
        <span>${icon('message')} 支持多轮追问</span>
        <span>${icon('shield')} 重要事项以官方通知为准</span>
      </div>
    </div>`;
  }

  function renderMessages({ smooth = false } = {}) {
    if (!alive) return;
    if (state.loadingConversation) {
      messageList.innerHTML = loadingHtml('正在加载会话…');
      return;
    }
    if (!state.messages.length) {
      messageList.innerHTML = renderWelcome();
      return;
    }
    messageList.innerHTML = state.messages.map((message, index) => messageHtml(message, index)).join('');
    scrollToBottom(smooth);
  }

  function showTyping() {
    if (!alive) return;
    const node = document.createElement('div');
    node.className = 'msg msg-assistant is-typing';
    node.id = 'typingMessage';
    node.innerHTML = `
      <div class="msg-avatar msg-avatar-bot">${esc(initial(state.settings.counselor_name))}</div>
      <div class="msg-main">
        <div class="msg-meta"><strong>${esc(state.settings.counselor_name || '小南')}</strong><span>正在思考…</span></div>
        <div class="typing-dots"><span></span><span></span><span></span></div>
      </div>`;
    messageList.appendChild(node);
    scrollToBottom(true);
  }

  function removeTyping() {
    root.querySelector('#typingMessage')?.remove();
  }

  function showChatError(message) {
    chatError.hidden = false;
    chatError.innerHTML = `<div class="chat-error-inner">${icon('alert')}<div><strong>发送失败</strong><p>${esc(
      message,
    )}</p></div><button class="btn btn-sm btn-ghost" type="button" data-action="retry-send">${icon(
      'refresh',
    )}<span>重试</span></button><button class="icon-btn" type="button" data-action="dismiss-error" aria-label="关闭">${icon(
      'x',
    )}</button></div>`;
  }

  function hideChatError() {
    chatError.hidden = true;
    chatError.innerHTML = '';
  }

  /* ----------------------- 输入框 ----------------------- */
  function autoGrow() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 168)}px`;
  }

  function updateComposer() {
    counter.textContent = `${input.value.length}/2000`;
    input.disabled = state.sending;
    sendBtn.disabled = state.sending || !input.value.trim();
    sendBtn.classList.toggle('is-busy', state.sending);
    sendBtn.innerHTML = state.sending
      ? `${icon('sparkles')}<span>思考中…</span>`
      : `${icon('send')}<span>发送</span>`;
    autoGrow();
  }

  /* ----------------------- 发送 ----------------------- */
  async function sendMessage(rawText) {
    const text = String(rawText ?? '').trim();
    if (!text || state.sending) return;
    if (text.length > 2000) {
      toast('问题长度不能超过 2000 字', 'error');
      return;
    }
    state.sending = true;
    state.lastFailedText = '';
    hideChatError();
    updateComposer();

    const userMessage = {
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    state.messages.push(userMessage);
    renderMessages({ smooth: true });
    input.value = '';
    updateComposer();
    showTyping();

    try {
      const payload = {
        message: text,
        conversation_id: state.conversationId,
        channel: 'web',
        session_key: state.conversationId ? `web:${state.conversationId}` : state.sessionKey,
      };
      const response = await sendChat(payload);
      if (!alive) return;
      removeTyping();
      if (response?.conversation_id) state.conversationId = response.conversation_id;
      if (response?.model) {
        state.healthModel = response.model;
        if (meta) meta.textContent = `辅导员数字分身 · ${response.model}`;
      }
      state.messages.push({
        role: 'assistant',
        content: response?.reply || '（服务未返回内容）',
        citations: Array.isArray(response?.citations) ? response.citations : [],
        model: response?.model,
        provider_type: response?.provider_type,
        latency_ms: response?.latency_ms,
        fallback: response?.fallback,
        sources_used: response?.sources_used,
        created_at: new Date().toISOString(),
      });
      renderMessages({ smooth: true });
      loadHistory({ silent: true });
    } catch (err) {
      if (!alive) return;
      removeTyping();
      state.messages = state.messages.filter((message) => message !== userMessage);
      renderMessages();
      state.lastFailedText = text;
      showChatError(err?.message || '请求失败，请稍后重试');
      toast(err?.message || '请求失败，请稍后重试', 'error');
      input.value = text;
      updateComposer();
      input.focus();
    } finally {
      if (alive) {
        state.sending = false;
        updateComposer();
        input.focus();
      }
    }
  }

  /* ----------------------- 会话操作 ----------------------- */
  function newChat() {
    if (state.sending) {
      toast('请等待当前回答完成后再新建会话', 'warning');
      return;
    }
    state.conversationId = null;
    state.sessionKey = newSessionKey();
    state.messages = [];
    state.meta = null;
    hideChatError();
    closeHistoryDrawer();
    renderMessages();
    renderHistory();
    input.value = '';
    updateComposer();
    input.focus();
  }

  async function openConversation(id) {
    if (state.sending) {
      toast('请等待当前回答完成后再切换会话', 'warning');
      return;
    }
    state.conversationId = id;
    state.loadingConversation = true;
    hideChatError();
    closeHistoryDrawer();
    renderMessages();
    renderHistory();
    try {
      const data = await getConversationMessages(id);
      if (!alive) return;
      state.messages = Array.isArray(data?.items) ? data.items : [];
      state.meta = data?.conversation || null;
      state.loadingConversation = false;
      renderMessages();
      scrollToBottom(false);
    } catch (err) {
      if (!alive) return;
      state.loadingConversation = false;
      state.conversationId = null;
      state.sessionKey = newSessionKey();
      state.messages = [];
      renderMessages();
      renderHistory();
      toast(err?.message || '会话加载失败', 'error');
    }
  }

  async function removeConversation(id) {
    const target = state.conversations.find((item) => item.id === id);
    const ok = await confirmDialog({
      title: '删除会话',
      message: '确定删除这个历史会话及其全部消息吗？',
      detail: target?.last_message ? `最后一条：${target.last_message}` : '',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteConversation(id);
      state.conversations = state.conversations.filter((item) => item.id !== id);
      if (state.conversationId === id) {
        state.conversationId = null;
        state.sessionKey = newSessionKey();
        state.messages = [];
        renderMessages();
      }
      renderHistory();
      toast('会话已删除', 'success');
    } catch (err) {
      toast(err?.message || '删除会话失败', 'error');
    }
  }

  /* ----------------------- 移动端抽屉 ----------------------- */
  function openHistoryDrawer() {
    page.classList.add('is-history-open');
    if (backdrop) backdrop.hidden = false;
  }
  function closeHistoryDrawer() {
    page.classList.remove('is-history-open');
    if (backdrop) backdrop.hidden = true;
  }

  /* ----------------------- 事件 ----------------------- */
  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'new-chat':
        newChat();
        break;
      case 'refresh-history':
        historyList.innerHTML = loadingHtml('加载历史会话…', 'state-compact');
        loadHistory();
        break;
      case 'open-conversation':
        openConversation(trigger.dataset.id);
        break;
      case 'delete-conversation':
        removeConversation(trigger.dataset.id);
        break;
      case 'quick-question': {
        const question = trigger.dataset.question || '';
        input.value = question;
        updateComposer();
        sendMessage(question);
        break;
      }
      case 'toggle-citation': {
        const card = root.querySelector(`[data-citation="${trigger.dataset.target}"]`);
        if (card) {
          const open = card.classList.toggle('is-open');
          trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        break;
      }
      case 'scroll-cite': {
        const messageNode = trigger.closest('[data-msg-index]');
        const card = messageNode?.querySelector(
          `[data-citation="${messageNode.dataset.msgIndex}-${trigger.dataset.cite}"]`,
        );
        if (card) {
          card.classList.add('is-open');
          card.querySelector('[data-action="toggle-citation"]')?.setAttribute('aria-expanded', 'true');
          card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
          toast('该引用未包含在当前回答中', 'info');
        }
        break;
      }
      case 'retry-send':
        if (state.lastFailedText) sendMessage(state.lastFailedText);
        break;
      case 'dismiss-error':
        hideChatError();
        break;
      case 'toggle-history':
        if (page.classList.contains('is-history-open')) closeHistoryDrawer();
        else openHistoryDrawer();
        break;
      case 'close-history':
        closeHistoryDrawer();
        break;
      default:
        break;
    }
  });

  input.addEventListener('input', () => {
    counter.textContent = `${input.value.length}/2000`;
    sendBtn.disabled = state.sending || !input.value.trim();
    autoGrow();
  });

  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    sendMessage(input.value);
  });

  chatForm.addEventListener('submit', (event) => {
    event.preventDefault();
    sendMessage(input.value);
  });

  /* ----------------------- 初始化 ----------------------- */
  updateComposer();
  renderMessages();
  renderHistory();

  // 每次进入对话页都刷新设置与历史，保证设置页保存后立即生效。
  state.loaded = true;
  Promise.all([
    (async () => {
      try {
        const settings = await getSettings();
        if (!alive) return;
        state.settings = { ...FALLBACK_SETTINGS, ...(settings || {}) };
        if (!Array.isArray(state.settings.quick_questions)) {
          state.settings.quick_questions = FALLBACK_SETTINGS.quick_questions;
        }
        state.settingsReady = true;
        if (counselorNameEl) counselorNameEl.textContent = state.settings.counselor_name || '小南';
        const avatar = root.querySelector('.chat-head-avatar');
        if (avatar) avatar.textContent = initial(state.settings.counselor_name);
        if (!state.messages.length && !state.sending) renderMessages();
      } catch (err) {
        if (!alive) return;
        state.settings = FALLBACK_SETTINGS;
        toast(`设置接口暂不可用，已使用默认人设${err?.message ? `：${err.message}` : ''}`, 'warning', {
          title: '降级提示',
        });
        if (!state.messages.length) renderMessages();
      }
    })(),
    loadHistory({ silent: state.historyReady }),
    (async () => {
      try {
        const health = await getHealth();
        if (!alive) return;
        const model = health?.default_model;
        state.healthModel = model ? model.display_name || model.model_name : '';
        if (meta) meta.textContent = `辅导员数字分身${state.healthModel ? ` · ${state.healthModel}` : ''}`;
      } catch {
        /* 健康接口失败不打扰用户，侧边栏已展示状态 */
      }
    })(),
  ]);

  if (state.conversationId && !state.messages.length) {
    openConversation(state.conversationId);
  }

  return () => {
    alive = false;
    removeTyping();
  };
}

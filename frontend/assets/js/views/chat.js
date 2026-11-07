/**
 * views/chat.js — 对话页
 *
 * 欢迎语 + 快捷问题 + 消息流 + 引用卡片 + 历史会话 + 新建会话。
 * 接口：GET /settings、POST /chat、GET/DELETE /chat/conversations…
 */
import {
  createHandoff,
  deleteConversation,
  getConversationMessages,
  getHealth,
  getSettings,
  listConversations,
  sendChatStream,
  submitFeedback,
} from '../api.js';
import {
  confirmDialog,
  esc,
  fmtRelative,
  fmtScore,
  icon,
  loadingHtml,
  openModal,
  setButtonBusy,
  toast,
} from '../ui.js';

const FALLBACK_SETTINGS = {
  counselor_name: '小南',
  welcome_message:
    '同学你好，我是辅导员数字分身「小南」👋 学校政策、奖助学金、请假流程、心理与就业服务等问题都可以问我。回答会标注引用的 Wiki 词条和原始文件来源，方便你核对原文。',
  quick_questions: [
    '国家奖学金怎么申请？需要哪些材料？',
    '请假流程是什么？病假需要什么证明？',
    '心理中心怎么预约？',
    '毕业生档案和报到证怎么办？',
  ],
  citations_enabled: true,
  wiki_max_pages: 3,
  wiki_answer_max_chars: 9000,
};

const state = {
  loaded: false,
  settings: FALLBACK_SETTINGS,
  settingsReady: false,
  conversations: [],
  historyReady: false,
  historyLoading: true,
  historyAuthRequired: false,
  messages: [],
  conversationId: null,
  // 新建会话时使用唯一的匿名 session_key，确保「新建会话」真正创建新会话，
  // 而不是复用数据库中已有的 web:anonymous 会话（见 CONTRACT §5.5 会话标识）。
  sessionKey: '',
  meta: null,
  sending: false,
  inflight: 0,
  abortController: null,
  loadingConversation: false,
  lastFailedText: '',
  healthModel: '',
  // v0.2.0 RSI: message_id -> { rating, reason_code }
  feedback: {},
};

// 视图代次：切页后旧的异步请求返回时，禁止其覆盖新的模块级状态
let generation = 0;

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
/* 反馈（RSI）                                                         */
/* ------------------------------------------------------------------ */
function voterKey() {
  try {
    let key = localStorage.getItem('npu_feedback_voter');
    if (!key) {
      key = `v-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem('npu_feedback_voter', key);
    }
    return key;
  } catch {
    return 'anonymous';
  }
}

function feedbackBlockHtml(messageId) {
  const id = String(messageId || '');
  if (!id) return '';
  const current = state.feedback[id];
  if (current) {
    const label = current.rating === 1 ? '已记录：有帮助' : '已记录：没帮助';
    return `<div class="msg-feedback is-done" data-feedback-message="${esc(id)}">
      <span class="msg-feedback-done">${current.rating === 1 ? '👍' : '👎'} ${esc(label)}</span>
      <button class="link-btn" type="button" data-action="feedback-reset" data-message-id="${esc(id)}">修改</button>
    </div>`;
  }
  return `<div class="msg-feedback" data-feedback-message="${esc(id)}">
    <span class="msg-feedback-label">这条回答有帮助吗？</span>
    <button class="btn btn-xs btn-ghost" type="button" data-action="feedback-up" data-message-id="${esc(id)}">👍 有帮助</button>
    <button class="btn btn-xs btn-ghost" type="button" data-action="feedback-down" data-message-id="${esc(id)}">👎 没帮助</button>
    <div class="feedback-reasons" hidden>
      <span class="feedback-reasons-label">哪里不满意？</span>
      <button class="feedback-chip" type="button" data-action="feedback-reason" data-reason="not_found">没找到</button>
      <button class="feedback-chip" type="button" data-action="feedback-reason" data-reason="incomplete">不完整</button>
      <button class="feedback-chip" type="button" data-action="feedback-reason" data-reason="expired">已过期</button>
      <button class="feedback-chip" type="button" data-action="feedback-reason" data-reason="bad_citation">引用不对</button>
      <button class="feedback-chip" type="button" data-action="feedback-reason" data-reason="other">其他</button>
      <input class="input feedback-comment" data-feedback-comment maxlength="200" placeholder="补充说明（选填）" />
      <button class="btn btn-xs btn-primary" type="button" data-action="feedback-submit" data-message-id="${esc(id)}">提交</button>
      <button class="link-btn" type="button" data-action="feedback-cancel">取消</button>
    </div>
  </div>`;
}

function refreshFeedbackNode(root, messageId) {
  const node = root.querySelector(`[data-feedback-message="${CSS.escape(String(messageId))}"]`);
  if (!node) return;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = feedbackBlockHtml(messageId);
  node.replaceWith(wrapper.firstElementChild);
}

function crisisCardHtml(crisis) {
  if (!crisis) return '';
  const hotlines = Array.isArray(crisis.hotlines) ? crisis.hotlines : [];
  const hotlineHtml = hotlines.length
    ? `<ul class="crisis-hotlines">${hotlines
        .map((item) => {
          if (typeof item === 'string') return `<li>${esc(item)}</li>`;
          return `<li>${esc(item?.name || '')}${item?.phone ? `：${esc(item.phone)}` : ''}</li>`;
        })
        .join('')}</ul>`
    : '';
  return `<div class="crisis-card" role="alert">
    <div class="crisis-card-title">${icon('alert')} 你现在的安全最重要</div>
    <p>请立即联系辅导员、学院学工办、心理中心或保卫处；如果有立即的人身危险，请马上拨打 <b>120</b> / <b>110</b>。</p>
    ${hotlineHtml}
    <p class="crisis-card-ticket">${
      crisis.ticket_no
        ? `已记录求助工单 <b>${esc(crisis.ticket_no)}</b>，值班老师会尽快联系你。`
        : '请同时直接联系辅导员或值班老师，不要只等待线上回复。'
    }</p>
    <div class="crisis-card-actions">
      <button class="btn btn-sm btn-ghost" type="button" data-action="handoff-open">${icon(
        'user',
      )}<span>联系辅导员</span></button>
      <button class="btn btn-sm btn-ghost" type="button" data-action="crisis-ack">我已联系</button>
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* 消息内容渲染                                                        */
/* ------------------------------------------------------------------ */
function renderInline(escaped) {
  return escaped
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[(\d{1,2})\]/g, (match, num) => {
      return `<button class="cite-ref" type="button" data-action="scroll-cite" data-cite="${num}" title="查看引用 [${num}]">[${num}]</button>`;
    });
}

function renderContent(content, withCites = true) {
  const escaped = esc(content || '');
  if (!withCites) return escaped.replace(/\n/g, '<br>');
  // 轻量 Markdown：标题 / 无序列表 / 有序列表 / 粗体 / 行内代码，其余按纯文本处理
  const lines = escaped.split('\n');
  const out = [];
  let list = '';
  const flush = () => {
    if (list) {
      out.push(`</${list}>`);
      list = '';
    }
  };
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flush();
      return;
    }
    const heading = trimmed.match(/^#{1,4}\s+(.*)$/);
    if (heading) {
      flush();
      out.push(`<h4 class="msg-h">${renderInline(heading[1])}</h4>`);
      return;
    }
    const unordered = trimmed.match(/^[-*]\s+(.*)$/);
    if (unordered) {
      if (list !== 'ul') {
        flush();
        out.push('<ul>');
        list = 'ul';
      }
      out.push(`<li>${renderInline(unordered[1])}</li>`);
      return;
    }
    const ordered = trimmed.match(/^\d+[.、]\s+(.*)$/);
    if (ordered) {
      if (list !== 'ol') {
        flush();
        out.push('<ol>');
        list = 'ol';
      }
      out.push(`<li>${renderInline(ordered[1])}</li>`);
      return;
    }
    flush();
    out.push(`<p>${renderInline(trimmed)}</p>`);
  });
  flush();
  return out.join('');
}

function citationSourcesHtml(sources) {
  if (!Array.isArray(sources) || !sources.length) return '';
  return `<div class="citation-sources">${sources
    .map((source) => {
      const title = source.document_title || source.source_file || '来源文件';
      const link = source.source_url
        ? `<a class="citation-source-link" href="${esc(source.source_url)}" target="_blank" rel="noopener noreferrer">${icon('external')}<span>查看原文</span></a>`
        : source.download_url
          ? `<a class="citation-source-link" href="${esc(source.download_url)}" download>${icon('download')}<span>下载原文件</span></a>`
          : '';
      return `<div class="citation-source">
        <div class="citation-source-head">${icon('file')}<span>${esc(title)}</span>${
          source.section ? `<span class="citation-source-section">${esc(source.section)}</span>` : ''
        }${link}</div>
        ${source.quote ? `<blockquote class="citation-source-quote">${esc(source.quote)}</blockquote>` : ''}
      </div>`;
    })
    .join('')}</div>`;
}

function citationsHtml(citations, messageIndex) {
  if (!Array.isArray(citations) || !citations.length) return '';
  const rows = citations
    .map((cite, i) => {
      const index = cite?.index ?? i + 1;
      const title = cite?.page_title || cite?.document_title || '未命名词条';
      const sources = Array.isArray(cite?.sources) ? cite.sources : [];
      return `<details class="citation-row">
        <summary class="citation-row-summary">
          <span class="citation-row-label">来源 ${esc(index)}</span>
          <span class="citation-row-title">${esc(title)}</span>
          <span class="citation-row-chevron">${icon('chevron-down')}</span>
        </summary>
        <div class="citation-row-body">
          ${cite?.summary ? `<p class="citation-summary-text">${esc(cite.summary)}</p>` : ''}
          ${cite?.snippet ? `<p class="citation-snippet">${esc(cite.snippet)}</p>` : ''}
          ${citationSourcesHtml(sources)}
          <div class="citation-meta">
            ${
              cite?.page_id
                ? `<button class="link-btn" type="button" data-action="open-wiki-page" data-page-id="${esc(
                    cite.page_id,
                  )}">${icon('book')}<span>查看词条详情</span></button>`
                : ''
            }
          </div>
        </div>
      </details>`;
    })
    .join('');
  return `<div class="citations">${rows}</div>`;
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
  return `<div class="msg msg-assistant" data-msg-index="${index}"${
    message.id ? ` data-message-id="${esc(message.id)}"` : ''
  }>
    <div class="msg-avatar msg-avatar-bot">${esc(who)}</div>
    <div class="msg-main">
      ${crisisCardHtml(message.crisis)}
      <div class="msg-bubble ${message.streaming ? 'is-streaming' : ''}">${renderContent(
        message.content || (message.streaming ? '正在整理回答…' : ''),
      )}</div>
      ${citationsHtml(message.citations, index)}
      ${feedbackBlockHtml(message.id)}
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* 视图                                                                */
/* ------------------------------------------------------------------ */
export function mount(root) {
  let alive = true;
  const gen = ++generation;
  // 切页后返回：若仍有请求在飞，保持「思考中」而不是直接解禁输入框
  state.sending = state.inflight > 0;
  state.loadingConversation = false;
  state.lastFailedText = '';
  const counselor = state.settings.counselor_name || '小南';

  root.innerHTML = `
  <div class="chat-page" id="chatPage">
    <aside class="chat-history" id="chatHistory" aria-label="历史会话" aria-hidden="true">
      <div class="chat-history-head">
        <div>
          <h2>${icon('history')}<span>历史会话</span></h2>
          <p id="historyCount">加载中…</p>
        </div>
        <button class="icon-btn" type="button" data-action="close-history" aria-label="关闭历史会话" title="关闭">
          ${icon('x')}
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
        <div class="chat-head-avatar">${esc(initial(counselor))}</div>
        <div class="chat-head-info">
          <h2><span id="chatCounselorName">${esc(counselor)}</span></h2>
        </div>
        <div class="chat-head-actions">
          <button class="btn btn-sm btn-ghost chat-handoff-btn" type="button" data-action="handoff-open">
            ${icon('user')}<span>联系辅导员</span>
          </button>
          <button class="icon-btn" type="button" data-action="new-chat" aria-label="新建会话" title="新建会话">
            ${icon('plus')}
          </button>
          <button class="icon-btn" type="button" data-action="toggle-history" aria-label="历史会话" title="历史会话">
            ${icon('history')}
          </button>
        </div>
      </header>

      <div class="chat-scroll" id="chatScroll">
        <div class="chat-messages" id="messageList"></div>
      </div>

      <div class="chat-error" id="chatError" hidden></div>

      <form class="composer" id="chatForm" autocomplete="off">
        <textarea id="chatInput" class="composer-input" rows="1" maxlength="2000"
          placeholder="输入问题…" aria-label="输入问题"></textarea>
        <button class="btn btn-primary composer-send" type="submit" id="sendBtn" disabled aria-label="发送">
          ${icon('send')}<span>发送</span>
        </button>
      </form>
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
  const chatError = root.querySelector('#chatError');
  const backdrop = root.querySelector('.chat-history-backdrop');
  const counselorNameEl = root.querySelector('#chatCounselorName');

  /* ----------------------- 历史会话渲染 ----------------------- */
  function renderHistory() {
    if (!alive) return;
    const items = state.conversations;
    if (state.historyAuthRequired) {
      historyCount.textContent = '需登录';
      historyList.innerHTML = `<div class="history-offline">${icon('shield')}<p>历史会话需要登录</p><span>请使用辅导员/管理员账号登录后查看</span><button class="btn btn-sm btn-ghost" type="button" data-action="go-login">去登录</button></div>`;
      return;
    }
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
    const my = gen;
    state.historyLoading = true;
    try {
      const data = await listConversations('web', 30);
      if (my !== generation) return;
      state.conversations = Array.isArray(data?.items) ? data.items : [];
      state.historyReady = true;
      state.historyAuthRequired = false;
      state.historyLoading = false;
      if (alive) renderHistory();
    } catch (err) {
      if (my !== generation) return;
      state.historyLoading = false;
      if (!state.conversations.length) state.historyReady = false;
      // 401：历史会话需要管理员令牌，展示空态，不再重复 toast
      state.historyAuthRequired = err?.status === 401;
      if (alive) renderHistory();
      if (!silent && !state.historyAuthRequired) toast(err.message || '历史会话加载失败', 'error');
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
      ? settings.quick_questions.filter((item) => typeof item === 'string' && item.trim()).slice(0, 3)
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
                  )}">${esc(question)}</button>`,
              )
              .join('')}</div>`
          : ''
      }
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
    messageList.querySelector('#typingMessage')?.remove();
  }

  function showAuthError(message) {
    chatError.hidden = false;
    chatError.innerHTML = `<div class="chat-error-inner">${icon('shield')}<div><strong>需要登录</strong><p>${esc(
      message || '查看历史会话需要辅导员/管理员账号，请登录后再试。',
    )}</p></div><button class="btn btn-sm btn-primary" type="button" data-action="go-login">去登录</button><button class="icon-btn" type="button" data-action="dismiss-error" aria-label="关闭">${icon(
      'x',
    )}</button></div>`;
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

  function abortStreaming() {
    state.abortController?.abort();
  }

  function updateComposer() {
    input.disabled = state.sending;
    sendBtn.disabled = state.sending ? false : !input.value.trim();
    sendBtn.classList.toggle('is-busy', state.sending);
    sendBtn.innerHTML = state.sending
      ? `${icon('power')}<span>停止</span>`
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
    const my = gen;
    state.inflight += 1;
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

    let streamMessage = null;
    let renderTimer = 0;
    const controller = new AbortController();
    state.abortController = controller;

    const paintStream = () => {
      if (renderTimer) return;
      renderTimer = window.setTimeout(() => {
        renderTimer = 0;
        if (alive && my === generation) renderMessages({ smooth: false });
      }, 80);
    };
    const ensureStreamMessage = () => {
      if (streamMessage) return streamMessage;
      removeTyping();
      streamMessage = {
        role: 'assistant',
        content: '',
        citations: [],
        streaming: true,
        created_at: new Date().toISOString(),
      };
      state.messages.push(streamMessage);
      return streamMessage;
    };

    try {
      const payload = {
        message: text,
        conversation_id: state.conversationId,
        channel: 'web',
        session_key: state.conversationId ? `web:${state.conversationId}` : state.sessionKey,
      };
      const response = await sendChatStream(payload, {
        signal: controller.signal,
        onMeta: (meta) => {
          if (my !== generation) return;
          const message = ensureStreamMessage();
          message.navigation = meta?.navigation;
          message.pages = meta?.pages || [];
          paintStream();
        },
        onDelta: (chunk) => {
          if (my !== generation || !chunk) return;
          const message = ensureStreamMessage();
          message.content += chunk;
          paintStream();
        },
      });
      // 代次已变化（切页/新会话）：不得覆盖当前状态
      if (my !== generation) return;
      removeTyping();
      if (response?.conversation_id) state.conversationId = response.conversation_id;
      if (response?.model) {
        state.healthModel = response.model;
      }
      if (streamMessage) {
        Object.assign(streamMessage, {
          id: response?.message_id,
          content: response?.reply || streamMessage.content || '（服务未返回内容）',
          citations: Array.isArray(response?.citations) ? response.citations : [],
          model: response?.model,
          provider_type: response?.provider_type,
          latency_ms: response?.latency_ms,
          fallback: response?.fallback,
          sources_used: response?.sources_used,
          crisis: response?.crisis,
          handoff: response?.handoff,
          streaming: false,
        });
      } else {
        state.messages.push({
          id: response?.message_id,
          role: 'assistant',
          content: response?.reply || '（服务未返回内容）',
          citations: Array.isArray(response?.citations) ? response.citations : [],
          model: response?.model,
          provider_type: response?.provider_type,
          latency_ms: response?.latency_ms,
          fallback: response?.fallback,
          sources_used: response?.sources_used,
          crisis: response?.crisis,
          handoff: response?.handoff,
          created_at: new Date().toISOString(),
        });
      }
      if (alive) {
        renderMessages({ smooth: true });
        loadHistory({ silent: true });
      }
    } catch (err) {
      if (my !== generation) return;
      removeTyping();
      if (err?.name === 'AbortError') {
        if (streamMessage) {
          streamMessage.streaming = false;
          streamMessage.content = `${streamMessage.content || ''}\n\n（已停止生成）`.trim();
          if (alive) {
            renderMessages();
            loadHistory({ silent: true });
          }
        }
        return;
      }
      if (streamMessage) {
        state.messages = state.messages.filter((message) => message !== streamMessage);
      }
      state.messages = state.messages.filter((message) => message !== userMessage);
      state.lastFailedText = text;
      if (alive) {
        renderMessages();
        showChatError(err?.message || '请求失败，请稍后重试');
        toast(err?.message || '请求失败，请稍后重试', 'error');
        input.value = text;
        updateComposer();
        input.focus();
      }
    } finally {
      if (renderTimer) window.clearTimeout(renderTimer);
      if (state.abortController === controller) state.abortController = null;
      // 先递减在飞计数并复位 sending，再根据 alive/代次决定是否更新 DOM
      state.inflight = Math.max(0, state.inflight - 1);
      state.sending = state.inflight > 0;
      if (state.inflight === 0) {
        // 旧视图的请求收尾时，新视图可能已重建：同步当前页面的输入框/发送按钮/思考气泡
        document.getElementById('typingMessage')?.remove();
        const liveInput = document.getElementById('chatInput');
        const liveSend = document.getElementById('sendBtn');
        if (liveInput) {
          liveInput.disabled = false;
          if (liveSend) {
            liveSend.disabled = !liveInput.value.trim();
            liveSend.classList.remove('is-busy');
            liveSend.innerHTML = `${icon('send')}<span>发送</span>`;
          }
        }
      }
      if (alive && my === generation) {
        updateComposer();
        if (!state.sending) input.focus();
      }
    }
  }

  /* ----------------------- 会话操作 ----------------------- */
  function openHandoffModal() {
    if (!state.conversationId) {
      toast('请先发送一条消息，再联系辅导员', 'info');
      return;
    }
    const lastAssistant = [...state.messages].reverse().find((item) => item.role === 'assistant');
    const modal = openModal({
      title: '联系辅导员',
      subtitle: '提交后会生成工单，辅导员在工作时间回复',
      body: `<form class="form" id="handoffForm">
        <label class="field">
          <span class="field-label">原因</span>
          <select class="input" name="reason">
            <option value="not_solved">问题没解决</option>
            <option value="confirm">需要人工确认</option>
            <option value="personal">涉及个人情况</option>
            <option value="complaint">投诉建议</option>
            <option value="other">其他</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">联系方式 <span class="field-tip">（选填，便于辅导员联系你）</span></span>
          <input class="input" name="contact" maxlength="120" placeholder="手机 / 微信 / QQ" />
        </label>
        <label class="field">
          <span class="field-label">补充说明 <span class="field-tip">（选填）</span></span>
          <textarea class="input" name="note" rows="3" maxlength="500" placeholder="例如：奖助材料截止时间需要确认"></textarea>
        </label>
      </form>`,
      footer: `<button class="btn btn-ghost" type="button" data-modal-cancel>取消</button>
        <button class="btn btn-primary" type="button" data-modal-submit>${icon('send')}<span>提交工单</span></button>`,
    });
    const form = modal.body.querySelector('#handoffForm');
    modal.footer.querySelector('[data-modal-cancel]')?.addEventListener('click', () => modal.close());
    modal.footer.querySelector('[data-modal-submit]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      setButtonBusy(button, true, '提交中…');
      try {
        const data = await createHandoff({
          conversation_id: state.conversationId,
          message_id: lastAssistant?.id || '',
          reason: form.querySelector('[name="reason"]').value,
          contact: form.querySelector('[name="contact"]').value.trim(),
          note: form.querySelector('[name="note"]').value.trim(),
        });
        modal.close();
        toast(`已提交：${data.ticket_no}。${data.sla || ''}`, 'success', { title: '工单已创建' });
      } catch (err) {
        toast(err?.message || '提交工单失败', 'error');
        setButtonBusy(button, false);
      }
    });
  }

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
    const my = gen;
    state.conversationId = id;
    state.loadingConversation = true;
    hideChatError();
    closeHistoryDrawer();
    renderMessages();
    renderHistory();
    try {
      const data = await getConversationMessages(id);
      if (my !== generation) return;
      state.messages = Array.isArray(data?.items) ? data.items : [];
      state.meta = data?.conversation || null;
      if (alive) {
        renderMessages();
        scrollToBottom(false);
      }
    } catch (err) {
      if (my !== generation) return;
      state.conversationId = null;
      state.sessionKey = newSessionKey();
      state.messages = [];
      if (err?.status === 401) {
        state.historyAuthRequired = true;
        if (alive) {
          renderHistory();
          renderMessages();
          showAuthError('查看历史会话需要管理员令牌，请到「设置 → 移动端与管理令牌」登录后再试。');
        }
      } else if (alive) {
        renderHistory();
        toast(err?.message || '会话加载失败', 'error');
      }
    } finally {
      if (my !== generation) return;
      // 先复位加载状态，避免切页后 loadingConversation 永久为 true
      state.loadingConversation = false;
      if (alive) {
        renderMessages();
        renderHistory();
      }
    }
  }

  async function removeConversation(id) {
    const my = gen;
    const target = state.conversations.find((item) => item.id === id);
    const ok = await confirmDialog({
      title: '删除会话',
      message: '确定删除这个历史会话及其全部消息吗？',
      detail: target?.last_message ? `最后一条：${target.last_message}` : '',
      confirmText: '删除',
      danger: true,
    });
    if (!ok || my !== generation) return;
    try {
      await deleteConversation(id);
      if (my !== generation) return;
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
      if (my !== generation) return;
      toast(err?.message || '删除会话失败', 'error');
    }
  }

  /* ----------------------- 移动端抽屉 ----------------------- */
  function openHistoryDrawer() {
    page.classList.add('is-history-open');
    root.querySelector('#chatHistory')?.setAttribute('aria-hidden', 'false');
    if (backdrop) backdrop.hidden = false;
  }
  function closeHistoryDrawer() {
    page.classList.remove('is-history-open');
    root.querySelector('#chatHistory')?.setAttribute('aria-hidden', 'true');
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
      case 'go-settings':
        location.hash = '#/settings';
        break;
      case 'go-login':
        location.hash = `#/login?next=${encodeURIComponent(location.hash)}`;
        break;
      case 'handoff-open':
        openHandoffModal();
        break;
      case 'crisis-ack':
        toast('已记录。请务必直接联系辅导员或拨打 120/110。', 'info');
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
      case 'scroll-cite': {
        const messageNode = trigger.closest('[data-msg-index]');
        const num = trigger.dataset.cite;
        const rows = Array.from(messageNode?.querySelectorAll('.citation-row') || []);
        const row =
          rows.find((item) => item.querySelector('.citation-row-label')?.innerText.includes(`来源 ${num}`)) ||
          rows[Number(num) - 1] ||
          rows[0];
        if (row) {
          row.open = true;
          row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
          toast('该引用未包含在当前回答中', 'info');
        }
        break;
      }
      case 'open-wiki-page': {        const pageId = trigger.dataset.pageId;
        if (pageId) location.hash = `#/knowledge?page=${encodeURIComponent(pageId)}`;
        break;
      }
      case 'retry-send':
        if (state.lastFailedText) sendMessage(state.lastFailedText);
        break;
      case 'feedback-up': {
        const id = trigger.dataset.messageId;
        if (!id) break;
        submitFeedback({ message_id: id, rating: 1, voter_key: voterKey() })
          .then(() => {
            state.feedback[id] = { rating: 1, reason_code: '' };
            refreshFeedbackNode(page, id);
            toast('感谢反馈，已记录', 'success');
          })
          .catch((err) => toast(err?.message || '反馈提交失败', 'error'));
        break;
      }
      case 'feedback-down': {
        const bar = trigger.closest('.msg-feedback');
        const panel = bar?.querySelector('.feedback-reasons');
        if (panel) panel.hidden = !panel.hidden;
        break;
      }
      case 'feedback-reason': {
        const bar = trigger.closest('.msg-feedback');
        bar?.querySelectorAll('.feedback-chip').forEach((chip) => {
          chip.classList.toggle('is-active', chip === trigger);
        });
        break;
      }
      case 'feedback-submit': {
        const id = trigger.dataset.messageId;
        const bar = trigger.closest('.msg-feedback');
        const reason = bar?.querySelector('.feedback-chip.is-active')?.dataset.reason || '';
        const comment = bar?.querySelector('[data-feedback-comment]')?.value.trim() || '';
        if (!id) break;
        if (!reason) {
          toast('请选择不满意的原因', 'warning');
          break;
        }
        submitFeedback({
          message_id: id,
          rating: -1,
          reason_code: reason,
          comment,
          voter_key: voterKey(),
        })
          .then(() => {
            state.feedback[id] = { rating: -1, reason_code: reason };
            refreshFeedbackNode(page, id);
            toast('已记录，辅导员会看到这个问题', 'success');
          })
          .catch((err) => toast(err?.message || '反馈提交失败', 'error'));
        break;
      }
      case 'feedback-cancel': {
        const panel = trigger.closest('.feedback-reasons');
        if (panel) panel.hidden = true;
        break;
      }
      case 'feedback-reset': {
        const id = trigger.dataset.messageId;
        if (!id) break;
        delete state.feedback[id];
        refreshFeedbackNode(page, id);
        break;
      }
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
    sendBtn.disabled = state.sending ? false : !input.value.trim();
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
    if (state.sending) {
      abortStreaming();
      return;
    }
    sendMessage(input.value);
  });

  /* ----------------------- 初始化 ----------------------- */
  updateComposer();
  renderMessages();
  renderHistory();
  // 若切页前有请求在飞，返回后恢复「思考中」气泡，避免用户误以为已结束
  if (state.inflight > 0) {
    showTyping();
    updateComposer();
  }

  // 每次进入对话页都刷新设置与历史，保证设置页保存后立即生效。
  state.loaded = true;
  Promise.all([
    (async () => {
      const my = gen;
      try {
        const settings = await getSettings();
        if (my !== generation) return;
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
        if (my !== generation) return;
        state.settings = FALLBACK_SETTINGS;
        toast(`设置接口暂不可用，已使用默认人设${err?.message ? `：${err.message}` : ''}`, 'warning', {
          title: '降级提示',
        });
        if (!state.messages.length) renderMessages();
      }
    })(),
    loadHistory({ silent: state.historyReady }),
    (async () => {
      const my = gen;
      try {
        const health = await getHealth();
        if (my !== generation) return;
        const model = health?.default_model;
        state.healthModel = model ? model.display_name || model.model_name : '';
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

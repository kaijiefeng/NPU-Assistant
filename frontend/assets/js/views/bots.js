/**
 * views/bots.js — 机器人页
 *
 * 三个通道卡片（QQ OneBot 11 / 微信公众号 / 控制台调试）、配置保存、
 * Webhook 地址一键复制、控制台模拟对话、消息日志。
 * 接口：/api/bots/channels、/api/bots/channels/{id}/test、/api/bots/logs
 */
import {
  createBotChannel,
  deleteBotChannel,
  listBotChannels,
  listBotLogs,
  testBotChannel,
  updateBotChannel,
} from '../api.js';
import {
  confirmDialog,
  copyText,
  emptyHtml,
  errorHtml,
  esc,
  fmtDateTime,
  fmtRelative,
  icon,
  loadingHtml,
  openModal,
  safeJson,
  setButtonBusy,
  toast,
} from '../ui.js';

const CHANNEL_TYPES = {
  onebot11: {
    label: 'QQ · OneBot 11',
    tone: 'blue',
    icon: 'qq',
    desc: 'NapCat / go-cqhttp 等 OneBot 11 实现，支持反向 WebSocket 与反向 HTTP 事件。',
    configFields: [
      { key: 'api_base', label: 'HTTP API 地址', placeholder: 'http://127.0.0.1:3000', hint: 'OneBot HTTP API，用于发送回复' },
      { key: 'access_token', label: 'Access Token', placeholder: '可留空', type: 'password' },
      { key: 'self_id', label: '机器人 QQ 号', placeholder: '如 123456789' },
      {
        key: 'group_mention_only',
        label: '群聊仅 @ 回复',
        type: 'checkbox',
        default: 'true',
        hint: '默认开启：群聊中只有 @ 机器人 才生成回复，避免刷屏',
      },
    ],
    defaults: { api_base: 'http://127.0.0.1:3000', access_token: '', self_id: '', group_mention_only: 'true' },
  },
  wechat_mp: {
    label: '微信公众号',
    tone: 'teal',
    icon: 'wechat',
    desc: '公众号明文模式回调：GET 校验签名，POST 接收 XML 文本消息并回复。',
    configFields: [
      { key: 'token', label: 'Token', placeholder: 'npu-assistant', hint: '须与公众号后台「服务器配置」一致' },
      { key: 'app_id', label: 'AppID', placeholder: '可选' },
      { key: 'welcome', label: '关注欢迎语', placeholder: '可选，关注时自动回复' },
    ],
    defaults: { token: 'npu-assistant', app_id: '', welcome: '' },
  },
  console: {
    label: '控制台调试',
    tone: 'violet',
    icon: 'terminal',
    desc: '本地调试通道，无需任何配置，适合演示完整的机器人问答链路。',
    configFields: [],
    defaults: {},
  },
};

const CHANNEL_ORDER = ['onebot11', 'wechat_mp', 'console'];

const STATUS_META = {
  online: { label: '在线', tone: 'green' },
  offline: { label: '离线', tone: 'slate' },
  error: { label: '异常', tone: 'red' },
  pending: { label: '待连接', tone: 'amber' },
};

const state = {
  loaded: false,
  channels: [],
  channelsError: '',
  logs: [],
  logsLoaded: false,
  logsError: '',
  consoleMessages: [],
  testChannelId: '',
};

function channelTypeMeta(type) {
  return CHANNEL_TYPES[type] || { label: type || '未知通道', tone: 'slate', icon: 'robot', desc: '', configFields: [], defaults: {} };
}

function configOf(channel) {
  return safeJson(channel?.config, {}) || {};
}

function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.offline;
}

function channelLabel(type) {
  return channelTypeMeta(type).label;
}

function webhookEntries(channel) {
  const raw = safeJson(channel?.webhook_urls, {}) || {};
  const labels = {
    ws: 'OneBot 反向 WebSocket',
    ws_url: 'OneBot 反向 WebSocket',
    websocket: 'OneBot 反向 WebSocket',
    event: 'OneBot 反向 HTTP 事件',
    http: 'OneBot 反向 HTTP 事件',
    http_event: 'OneBot 反向 HTTP 事件',
    callback: '微信回调地址',
    wechat: '微信回调地址',
    wechat_url: '微信回调地址',
  };
  const entries = Object.entries(raw)
    .filter(([, value]) => typeof value === 'string' && value)
    .map(([key, value]) => [labels[String(key).toLowerCase()] || key, value]);

  if (entries.length) return entries;

  // 接口未给出 webhook_urls 时，按契约路径生成兜底地址。
  const config = configOf(channel);
  const tokenQuery = config.access_token ? `?access_token=${encodeURIComponent(config.access_token)}` : '';
  if (channel.type === 'onebot11') {
    return [
      ['OneBot 反向 WebSocket', `/api/bots/onebot/${channel.id}/ws${tokenQuery}`],
      ['OneBot 反向 HTTP 事件', `/api/bots/onebot/${channel.id}/event${tokenQuery}`],
    ];
  }
  if (channel.type === 'wechat_mp') {
    return [['微信回调地址', `/api/bots/wechat/${channel.id}`]];
  }
  return [];
}

function absoluteUrl(url) {
  if (/^https?:\/\//i.test(url)) return url;
  if (String(url).startsWith('/')) return `${location.origin}${url}`;
  return url;
}

function fieldHtml(field, value) {
  if (field.type === 'checkbox') {
    const checked = value === true || value === 'true' || value === '1';
    return `<label class="field-inline bot-toggle">
      <input type="checkbox" data-config="${esc(field.key)}" ${checked ? 'checked' : ''} />
      <span class="bot-toggle-text">
        <strong>${esc(field.label)}</strong>
        ${field.hint ? `<span class="field-tip">${esc(field.hint)}</span>` : ''}
      </span>
    </label>`;
  }
  const input =
    field.type === 'textarea'
      ? `<textarea class="input" rows="2" data-config="${esc(field.key)}" placeholder="${esc(field.placeholder || '')}">${esc(value ?? '')}</textarea>`
      : `<input class="input ${field.type === 'password' ? 'mono' : ''}" data-config="${esc(field.key)}" ${
          field.type === 'password' ? 'type="password" autocomplete="new-password"' : ''
        } placeholder="${esc(field.placeholder || '')}" value="${esc(value ?? '')}" />`;
  return `<label class="field">
    <span class="field-label">${esc(field.label)}${field.hint ? `<span class="field-tip">${esc(field.hint)}</span>` : ''}</span>
    ${input}
  </label>`;
}

function channelCardHtml(channel) {
  const meta = channelTypeMeta(channel.type);
  const config = configOf(channel);
  const status = statusMeta(channel.status);
  const enabled = Number(channel.enabled) === 1;
  const webhooks = webhookEntries(channel);
  const webhookHtml = webhooks.length
    ? `<div class="webhook-list">${webhooks
        .map(
          ([label, url]) => `<div class="webhook-row">
            <span class="webhook-label">${esc(label)}</span>
            <code class="webhook-url" title="${esc(absoluteUrl(url))}">${esc(absoluteUrl(url))}</code>
            <button class="icon-btn" type="button" data-copy="${esc(absoluteUrl(url))}" title="复制地址" aria-label="复制地址">${icon('copy')}</button>
          </div>`,
        )
        .join('')}</div>`
    : `<div class="bot-note">${icon('info')}<span>控制台通道无需 Webhook 地址，可直接使用下方模拟对话调试。</span></div>`;

  const fields = meta.configFields
    .map((field) => {
      const value = config[field.key] !== undefined ? config[field.key] : field.default ?? '';
      return fieldHtml(field, value);
    })
    .join('');

  return `<article class="bot-card type-${esc(channel.type)}" data-channel-id="${esc(channel.id)}">
    <header class="bot-head">
      <span class="bot-logo tone-${esc(meta.tone)}">${icon(meta.icon)}</span>
      <div class="bot-title">
        <h3>${esc(channel.name || meta.label)}</h3>
        <p><span class="status-dot status-${esc(status.tone)}"></span>${esc(status.label)}<span class="dot-sep">·</span>${
          channel.last_seen_at ? `最近活跃 ${esc(fmtRelative(channel.last_seen_at))}` : '尚未收到消息'
        }</p>
      </div>
      <label class="switch" title="${enabled ? '点击停用通道' : '点击启用通道'}">
        <input type="checkbox" data-action="toggle-channel" data-id="${esc(channel.id)}" ${enabled ? 'checked' : ''} />
        <span class="switch-track"><span class="switch-thumb"></span></span>
        <span class="switch-label">${enabled ? '已启用' : '已停用'}</span>
      </label>
    </header>
    <p class="bot-desc">${esc(meta.desc)}</p>
    <div class="bot-config">
      <label class="field">
        <span class="field-label">通道名称</span>
        <input class="input" data-channel-name value="${esc(channel.name || meta.label)}" maxlength="80" />
      </label>
      ${fields}
    </div>
    ${webhookHtml}
    <div class="bot-actions">
      <button class="btn btn-primary btn-sm" type="button" data-action="save-channel" data-id="${esc(channel.id)}">${icon('check')}<span>保存配置</span></button>
      <button class="btn btn-ghost btn-sm" type="button" data-action="open-console" data-id="${esc(channel.id)}">${icon('terminal')}<span>模拟对话</span></button>
      ${
        channel.type === 'console'
          ? ''
          : `<button class="btn btn-ghost btn-sm is-danger" type="button" data-action="delete-channel" data-id="${esc(channel.id)}">${icon('trash')}<span>删除通道</span></button>`
      }
    </div>
  </article>`;
}

function emptyChannelCardHtml(type) {
  const meta = channelTypeMeta(type);
  return `<article class="bot-card is-placeholder">
    <div class="placeholder-inner">
      <span class="bot-logo tone-${esc(meta.tone)}">${icon(meta.icon)}</span>
      <h3>尚未配置${esc(meta.label)}</h3>
      <p>${esc(meta.desc)}</p>
      <button class="btn btn-primary btn-sm" type="button" data-action="add-channel" data-type="${esc(type)}">
        ${icon('plus')}<span>添加通道</span>
      </button>
    </div>
  </article>`;
}

function renderChannels() {
  const holder = document.getElementById('botGrid');
  if (!holder) return;
  if (state.channelsError) {
    holder.innerHTML = errorHtml(state.channelsError);
    return;
  }
  if (!state.loaded) {
    holder.innerHTML = `<div class="bot-grid">${Array.from({ length: 3 })
      .map(
        () =>
          '<article class="bot-card is-skeleton"><span class="skeleton-line w-40"></span><span class="skeleton-line w-90"></span><span class="skeleton-line w-70"></span><span class="skeleton-line w-60"></span></article>',
      )
      .join('')}</div>`;
    return;
  }
  const cards = [];
  CHANNEL_ORDER.forEach((type) => {
    const channels = state.channels.filter((channel) => channel.type === type);
    if (channels.length) channels.forEach((channel) => cards.push(channelCardHtml(channel)));
    else cards.push(emptyChannelCardHtml(type));
  });
  // 未知类型（未来扩展）也展示出来
  state.channels
    .filter((channel) => !CHANNEL_ORDER.includes(channel.type))
    .forEach((channel) => cards.push(channelCardHtml(channel)));
  holder.innerHTML = `<div class="bot-grid">${cards.join('')}</div>`;
  syncConsoleChannelOptions();
}

function renderLogs() {
  const holder = document.getElementById('botLogs');
  if (!holder) return;
  if (state.logsError) {
    holder.innerHTML = errorHtml(state.logsError);
    return;
  }
  if (!state.logsLoaded) {
    holder.innerHTML = loadingHtml('正在加载消息日志…');
    return;
  }
  if (!state.logs.length) {
    holder.innerHTML = emptyHtml({
      icon: 'message',
      title: '还没有机器人消息',
      desc: '在上方控制台模拟对话，或接入 QQ / 微信公众号后，这里会记录每条入站消息与回复。',
    });
    return;
  }
  holder.innerHTML = `<div class="log-list">${state.logs
    .map((log) => {
      const type = String(log.channel || '').replace(/:.*$/, '');
      const meta = channelTypeMeta(type);
      return `<article class="log-item">
        <header class="log-head">
          <span class="badge badge-${meta.tone}">${esc(channelLabel(type))}</span>
          <strong>${esc(log.user_label || log.session_key || '未知用户')}</strong>
          <span class="log-time" title="${esc(fmtDateTime(log.created_at))}">${esc(fmtRelative(log.created_at))}</span>
        </header>
        <div class="log-bubbles">
          <p class="log-q"><span class="log-role">用户</span>${esc(log.message || '')}</p>
          <p class="log-a"><span class="log-role">回复</span>${esc(log.reply || '')}</p>
        </div>
      </article>`;
    })
    .join('')}</div>
    <p class="table-note">${icon('info')} 日志来自非 Web 渠道的历史消息，最多展示最近 50 条。</p>`;
}

/* ------------------------------------------------------------------ */
/* 控制台模拟对话                                                      */
/* ------------------------------------------------------------------ */
function consoleChannelOptions() {
  const options = state.channels.map(
    (channel) =>
      `<option value="${esc(channel.id)}">${esc(channel.name || channelLabel(channel.type))}（${esc(
        channelLabel(channel.type),
      )}）</option>`,
  );
  return options.join('');
}

function syncConsoleChannelOptions() {
  const select = document.getElementById('testChannel');
  if (!select) return;
  const current = select.value || state.testChannelId;
  select.innerHTML = consoleChannelOptions() || '<option value="">暂无可用通道</option>';
  const consoleChannel = state.channels.find((channel) => channel.type === 'console');
  const nextValue =
    state.channels.find((channel) => channel.id === current)?.id ||
    consoleChannel?.id ||
    state.channels[0]?.id ||
    '';
  select.value = nextValue;
  state.testChannelId = nextValue;
}

function renderConsoleOutput() {
  const holder = document.getElementById('consoleOutput');
  if (!holder) return;
  if (!state.consoleMessages.length) {
    holder.innerHTML = `<div class="console-empty">${icon('terminal')}<p>发送一条模拟消息，查看机器人的完整回复</p><span>会展示回复内容、引用来源与将要发送的报文（outbound）</span></div>`;
    return;
  }
  holder.innerHTML = state.consoleMessages
    .map((message) => {
      if (message.role === 'user') {
        return `<div class="console-msg console-msg-user"><div class="console-bubble">${esc(message.content)}</div></div>`;
      }
      const citations = Array.isArray(message.citations) ? message.citations : [];
      return `<div class="console-msg console-msg-bot">
        <div class="console-bubble">
          <div class="console-bubble-head">${icon('robot')}<strong>机器人回复</strong>
            ${message.latency_ms !== undefined ? `<span class="muted">${Math.round(Number(message.latency_ms) || 0)} ms</span>` : ''}
          </div>
          <p class="console-reply">${esc(message.reply || '（空回复）')}</p>
          ${
            citations.length
              ? `<div class="console-citations">${citations
                  .map(
                    (cite, index) =>
                      `<span class="citation-mini" title="${esc(cite.snippet || '')}">[${
                        cite.index ?? index + 1
                      }] ${esc(cite.document_title || '引用文档')}</span>`,
                  )
                  .join('')}</div>`
              : ''
          }
          <div class="console-meta">
            <span class="chip-mono">conversation ${esc(String(message.conversation_id || '').slice(0, 8))}</span>
          </div>
          ${
            message.outbound
              ? `<details class="outbound"><summary>查看 outbound 报文</summary><pre>${esc(
                  JSON.stringify(message.outbound, null, 2),
                )}</pre></details>`
              : ''
          }
        </div>
      </div>`;
    })
    .join('');
  holder.scrollTop = holder.scrollHeight;
}

async function runConsoleTest(button) {
  const select = document.getElementById('testChannel');
  const messageInput = document.getElementById('testMessage');
  const userNameInput = document.getElementById('testUserName');
  const userIdInput = document.getElementById('testUserId');
  const channelId = select?.value || '';
  const message = String(messageInput?.value || '').trim();
  if (!channelId) {
    toast('请先添加并选择机器人通道', 'warning');
    return;
  }
  if (!message) {
    toast('请输入要模拟发送的消息', 'warning');
    messageInput?.focus();
    return;
  }
  const channel = state.channels.find((item) => item.id === channelId);
  state.testChannelId = channelId;
  state.consoleMessages.push({ role: 'user', content: message });
  renderConsoleOutput();
  setButtonBusy(button, true, '机器人思考中…');
  try {
    const body = { message };
    if (userNameInput?.value.trim()) body.user_name = userNameInput.value.trim();
    if (userIdInput?.value.trim()) body.user_id = userIdInput.value.trim();
    const result = await testBotChannel(channelId, body);
    state.consoleMessages.push({
      role: 'bot',
      reply: result?.reply || '',
      citations: result?.citations || [],
      latency_ms: result?.latency_ms,
      conversation_id: result?.conversation_id || result?.channel,
      outbound: result?.outbound || null,
    });
    if (messageInput) messageInput.value = '';
    renderConsoleOutput();
    loadLogs({ silent: true });
    toast(`模拟消息已送达「${channel?.name || channelLabel(channel?.type)}」`, 'success');
  } catch (err) {
    state.consoleMessages.push({ role: 'bot', reply: `发送失败：${err?.message || '未知错误'}`, citations: [] });
    renderConsoleOutput();
    toast(err?.message || '模拟对话失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function loadChannels({ silent = false } = {}) {
  try {
    const data = await listBotChannels();
    state.channels = Array.isArray(data?.items) ? data.items : [];
    state.channelsError = '';
    state.loaded = true;
    renderChannels();
  } catch (err) {
    state.loaded = false;
    state.channelsError = err?.message || '机器人通道加载失败';
    renderChannels();
    if (!silent) toast(state.channelsError, 'error');
  }
}

async function loadLogs({ silent = false } = {}) {
  try {
    const data = await listBotLogs(50);
    state.logs = Array.isArray(data?.items) ? data.items : [];
    state.logsError = '';
    state.logsLoaded = true;
    renderLogs();
  } catch (err) {
    state.logsLoaded = false;
    state.logsError = err?.message || '消息日志加载失败';
    renderLogs();
    if (!silent) toast(state.logsError, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* 通道操作                                                            */
/* ------------------------------------------------------------------ */
function collectConfig(card) {
  const config = {};
  card.querySelectorAll('[data-config]').forEach((input) => {
    if (input.type === 'checkbox') {
      config[input.dataset.config] = input.checked ? 'true' : 'false';
    } else {
      config[input.dataset.config] = input.value.trim();
    }
  });
  return config;
}

async function saveChannel(button) {
  const card = button.closest('.bot-card');
  const id = button.dataset.id;
  const channel = state.channels.find((item) => item.id === id);
  const nameInput = card.querySelector('[data-channel-name]');
  const name = nameInput?.value.trim() || '';
  if (!name) {
    toast('通道名称不能为空', 'error');
    nameInput?.focus();
    return;
  }
  setButtonBusy(button, true, '保存中…');
  try {
    const updated = await updateBotChannel(id, { name, config: collectConfig(card) });
    if (updated && typeof updated === 'object' && updated.id) {
      const index = state.channels.findIndex((item) => item.id === id);
      if (index >= 0) state.channels[index] = updated;
    }
    toast('通道配置已保存', 'success');
    renderChannels();
    if (state.logsLoaded) loadLogs({ silent: true });
  } catch (err) {
    toast(err?.message || '保存通道配置失败', 'error');
    setButtonBusy(button, false);
  }
}

async function toggleChannel(input) {
  const id = input.dataset.id;
  const enabled = input.checked;
  input.disabled = true;
  try {
    const updated = await updateBotChannel(id, { enabled });
    const index = state.channels.findIndex((item) => item.id === id);
    if (index >= 0 && updated && typeof updated === 'object' && updated.id) {
      state.channels[index] = updated;
    } else if (index >= 0) {
      state.channels[index] = { ...state.channels[index], enabled: enabled ? 1 : 0 };
    }
    renderChannels();
    toast(enabled ? '通道已启用' : '通道已停用', 'success');
  } catch (err) {
    input.checked = !enabled;
    toast(err?.message || '切换通道状态失败', 'error');
  } finally {
    input.disabled = false;
  }
}

async function removeChannel(id) {
  const channel = state.channels.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除机器人通道',
    message: `确定删除通道「${channel?.name || channelLabel(channel?.type)}」吗？`,
    detail: '删除后相关 Webhook 地址将失效，历史消息日志仍会保留。',
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteBotChannel(id);
    toast('通道已删除', 'success');
    await loadChannels({ silent: true });
  } catch (err) {
    toast(err?.message || '删除通道失败', 'error');
  }
}

function openAddChannelModal(preferredType = 'onebot11') {
  const modal = openModal({
    title: '添加机器人通道',
    subtitle: '同一个类型可添加多个通道（例如多个 QQ 机器人）',
    size: 'md',
    body: `<form class="form" id="channelForm" novalidate>
      <label class="field">
        <span class="field-label">通道类型 <em>*</em></span>
        <select class="input select" name="type">
          ${Object.entries(CHANNEL_TYPES)
            .map(
              ([value, meta]) =>
                `<option value="${value}" ${value === preferredType ? 'selected' : ''}>${esc(meta.label)}</option>`,
            )
            .join('')}
        </select>
      </label>
      <label class="field">
        <span class="field-label">通道名称 <em>*</em></span>
        <input class="input" name="name" maxlength="80" value="${esc(channelTypeMeta(preferredType).label)}" />
      </label>
      <div class="form-hint">${icon('info')}<span>创建后请在通道卡片中填写连接参数并保存。</span></div>
    </form>`,
    footer: `<button class="btn btn-ghost" type="button" data-modal-cancel>取消</button>
      <button class="btn btn-primary" type="button" data-modal-submit>添加通道</button>`,
  });
  const form = modal.body.querySelector('#channelForm');
  const typeSelect = form.querySelector('[name="type"]');
  const nameInput = form.querySelector('[name="name"]');
  typeSelect.addEventListener('change', () => {
    nameInput.value = channelTypeMeta(typeSelect.value).label;
  });
  modal.footer.querySelector('[data-modal-cancel]')?.addEventListener('click', () => modal.close());
  modal.footer.querySelector('[data-modal-submit]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const type = typeSelect.value;
    const name = nameInput.value.trim();
    if (!name) {
      toast('请填写通道名称', 'error');
      nameInput.focus();
      return;
    }
    setButtonBusy(button, true, '添加中…');
    try {
      await createBotChannel({ type, name, config: channelTypeMeta(type).defaults });
      toast('机器人通道已添加', 'success');
      modal.close();
      await loadChannels({ silent: true });
    } catch (err) {
      toast(err?.message || '添加通道失败', 'error');
      setButtonBusy(button, false);
    }
  });
}

/* ------------------------------------------------------------------ */
/* 视图入口                                                            */
/* ------------------------------------------------------------------ */
export function mount(root) {
  let alive = true;

  root.innerHTML = `<div class="page bots-page">
    <section class="card bot-intro">
      <div class="bot-intro-icon">${icon('robot')}</div>
      <div>
        <h2>接入 QQ / 微信，让分身走出网页</h2>
        <p>在下方卡片填写连接参数并保存，把生成的 Webhook 地址填到 OneBot 实现或公众号后台即可。没有外部账号时，可用「控制台调试」通道体验完整链路。</p>
      </div>
      <button class="btn btn-primary btn-sm" type="button" data-action="add-channel">${icon('plus')}<span>添加通道</span></button>
    </section>

    <section class="card">
      <header class="card-head">
        <div>
          <h2>${icon('robot')}机器人通道</h2>
          <p>每个通道独立配置；停用后不再接收 / 回复消息（控制台通道始终可删改配置）</p>
        </div>
        <div class="card-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-action="refresh-channels">${icon('refresh')}<span>刷新</span></button>
        </div>
      </header>
      <div id="botGrid">${loadingHtml('正在加载机器人通道…')}</div>
    </section>

    <section class="card console-card" id="consolePanel">
      <header class="card-head">
        <div>
          <h2>${icon('terminal')}控制台模拟对话</h2>
          <p>直接调用 <code>/api/bots/channels/{id}/test</code>，验证机器人问答、引用与报文格式</p>
        </div>
      </header>
      <div class="console-layout">
        <div class="console-form">
          <label class="field">
            <span class="field-label">模拟通道</span>
            <select class="input select" id="testChannel"></select>
          </label>
          <div class="form-row">
            <label class="field">
              <span class="field-label">用户昵称</span>
              <input class="input" id="testUserName" placeholder="测试同学" value="测试同学" />
            </label>
            <label class="field">
              <span class="field-label">用户 ID</span>
              <input class="input" id="testUserId" placeholder="可选，如 10001" />
            </label>
          </div>
          <label class="field">
            <span class="field-label">消息内容</span>
            <textarea class="input" id="testMessage" rows="3" placeholder="例如：国家奖学金怎么申请？"></textarea>
          </label>
          <div class="console-actions">
            <button class="btn btn-primary" type="button" data-action="run-console">${icon('send')}<span>发送模拟消息</span></button>
            <button class="btn btn-ghost" type="button" data-action="clear-console">${icon('trash')}<span>清空</span></button>
          </div>
        </div>
        <div class="console-output" id="consoleOutput"></div>
      </div>
    </section>

    <section class="card logs-card">
      <header class="card-head">
        <div>
          <h2>${icon('history')}消息日志</h2>
          <p>来自 QQ / 微信 / 控制台通道的最近消息记录</p>
        </div>
        <div class="card-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-action="refresh-logs">${icon('refresh')}<span>刷新</span></button>
        </div>
      </header>
      <div id="botLogs">${loadingHtml('正在加载消息日志…')}</div>
    </section>
  </div>`;

  syncConsoleChannelOptions();
  renderConsoleOutput();
  // 事件委托绑定在视图页面元素上：路由切换后元素被移除，监听器随节点一起回收，
  // 避免挂在 #view 上导致重复绑定（重复发送 / 重复弹窗）。
  const page = root.querySelector('.bots-page');

  page.addEventListener('click', async (event) => {
    const copyTrigger = event.target.closest('[data-copy]');
    if (copyTrigger && page.contains(copyTrigger)) {
      const ok = await copyText(copyTrigger.dataset.copy);
      if (ok) {
        toast('Webhook 地址已复制到剪贴板', 'success', { duration: 2400 });
        const original = copyTrigger.innerHTML;
        copyTrigger.innerHTML = icon('check');
        window.setTimeout(() => {
          if (copyTrigger.isConnected) copyTrigger.innerHTML = original;
        }, 1600);
      } else {
        toast('复制失败，请手动选择地址复制', 'error');
      }
      return;
    }

    const trigger = event.target.closest('[data-action]');
    if (!trigger || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'refresh-channels':
        await loadChannels();
        break;
      case 'refresh-logs':
        await loadLogs();
        break;
      case 'add-channel':
        openAddChannelModal(trigger.dataset.type || 'onebot11');
        break;
      case 'save-channel':
        saveChannel(trigger);
        break;
      case 'delete-channel':
        removeChannel(trigger.dataset.id);
        break;
      case 'open-console': {
        const select = document.getElementById('testChannel');
        if (select) {
          select.value = trigger.dataset.id;
          state.testChannelId = trigger.dataset.id;
        }
        document.getElementById('consolePanel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        document.getElementById('testMessage')?.focus({ preventScroll: true });
        break;
      }
      case 'run-console':
        runConsoleTest(trigger);
        break;
      case 'clear-console':
        state.consoleMessages = [];
        renderConsoleOutput();
        break;
      default:
        break;
    }
  });

  page.addEventListener('change', (event) => {
    const channelToggle = event.target.closest('[data-action="toggle-channel"]');
    if (channelToggle) {
      toggleChannel(channelToggle);
      return;
    }
    const channelSelect = event.target.closest('#testChannel');
    if (channelSelect) state.testChannelId = channelSelect.value;
  });

  loadChannels();
  loadLogs();

  return () => {
    alive = false;
  };
}

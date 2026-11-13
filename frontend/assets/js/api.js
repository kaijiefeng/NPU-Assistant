/**
 * api.js — NPU-Assistant 前端 API 访问层
 *
 * 约定（见 docs/CONTRACT.md §5）：
 *  - 所有请求直接打到当前站点的 `/api`，不依赖任何 CDN 或代理；
 *  - 非 2xx 响应统一抛出 ApiError，message 取后端 `detail`，供 toast 直接展示；
 *  - 后端接口可能尚未就绪（404）或返回空数据，调用方需自行降级，本层只负责如实抛错。
 */

const API_BASE = `${location.origin}/api`;
const ADMIN_TOKEN_KEY = 'npu_admin_token';

export class ApiError extends Error {
  constructor(message, { status = 0, detail = '', payload = null } = {}) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.payload = payload;
  }
}

/* ------------------------------------------------------------------ */
/* 管理令牌（localStorage，所有请求自动附加 X-Admin-Token）              */
/* ------------------------------------------------------------------ */
let unauthorizedHandler = null;
let csrfToken = '';

export function setCsrfToken(token) {
  csrfToken = String(token || '');
}

export function getCsrfToken() {
  return csrfToken;
}

export function getAdminToken() {
  try {
    return (localStorage.getItem(ADMIN_TOKEN_KEY) || '').trim();
  } catch {
    return '';
  }
}

export function setAdminToken(token) {
  try {
    const value = String(token || '').trim();
    if (value) localStorage.setItem(ADMIN_TOKEN_KEY, value);
    else localStorage.removeItem(ADMIN_TOKEN_KEY);
    return value;
  } catch {
    return '';
  }
}

export function clearAdminToken() {
  try {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function hasAdminToken() {
  return Boolean(getAdminToken());
}

/** 注册 401 处理器（app.js 用来 toast 引导用户去设置页填写令牌）。 */
export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === 'function' ? handler : null;
}

function withAdminToken(headers = {}) {
  const token = getAdminToken();
  if (token) headers['X-Admin-Token'] = token;
  return headers;
}

/** 仅写操作与受保护 GET 携带管理员令牌，避免把令牌发给 /health、/chat 等公开接口。 */
const PROTECTED_GET_PREFIXES = [
  '/chat/conversations',
  '/bots/logs',
  '/insights',
  '/handoff',
  '/crisis',
  '/admin',
  '/models',
  '/settings',
  '/system',
  '/auth/me',
  // 后台任务 / 告警：上传后的任务进度轮询也需要令牌，否则会 401 误报「需要登录」
  '/jobs',
  '/alerts',
];

function needsAdminToken(method, path) {
  if (!hasAdminToken()) return false;
  const verb = String(method || 'GET').toUpperCase();
  if (verb !== 'GET' && verb !== 'HEAD') return true;
  return PROTECTED_GET_PREFIXES.some((prefix) => path.startsWith(prefix));
}

function notifyUnauthorized(detail) {
  if (unauthorizedHandler) {
    try {
      unauthorizedHandler(detail);
    } catch {
      /* ignore */
    }
  }
}

function buildUrl(path, query) {
  const url = new URL(API_BASE + path);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return;
      url.searchParams.set(key, String(value));
    });
  }
  return url.toString();
}

function extractDetail(data, status) {
  if (data && typeof data === 'object') {
    const detail = data.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => (item && (item.msg || item.message)) || '')
        .filter(Boolean);
      if (parts.length) return parts.join('；');
    }
    if (typeof data.message === 'string' && data.message.trim()) return data.message.trim();
  }
  return `请求失败（HTTP ${status}）`;
}

async function parseResponse(res, { skipAuthToast = false } = {}) {
  let data = null;
  const text = await res.text().catch(() => '');
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const detail = extractDetail(data, res.status);
    if (res.status === 401 && !skipAuthToast) notifyUnauthorized(detail);
    throw new ApiError(detail, { status: res.status, detail, payload: data });
  }
  return data;
}

async function request(method, path, { body, query, signal, skipAuthToast = false } = {}) {
  const headers = needsAdminToken(method, path) ? withAdminToken({}) : {};
  const verb = String(method || 'GET').toUpperCase();
  if (
    csrfToken &&
    verb !== 'GET' &&
    verb !== 'HEAD' &&
    !path.startsWith('/auth/')
  ) {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const init = {
    method,
    headers,
    signal,
  };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(buildUrl(path, query), init);
    // 兜底：配置了管理令牌时，误判为公开的 GET 若 401，自动带令牌重试一次。
    // 典型场景：上传成功后的任务进度轮询（/jobs/{id}），不应再提示「需要登录」。
    if (
      res.status === 401 &&
      hasAdminToken() &&
      verb === 'GET' &&
      !path.startsWith('/auth/')
    ) {
      res = await fetch(buildUrl(path, query), {
        ...init,
        headers: { ...headers, 'X-Admin-Token': getAdminToken() },
      });
    }
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError('无法连接后端服务，请确认 NPU-Assistant 已启动后重试。', { status: 0 });
  }
  return parseResponse(res, { skipAuthToast });
}

const get = (path, query, opts = {}) => request('GET', path, { query, ...opts });
const post = (path, body, opts = {}) => request('POST', path, { body, ...opts });
const put = (path, body, opts = {}) => request('PUT', path, { body, ...opts });
const del = (path, opts = {}) => request('DELETE', path, opts);

/* ------------------------------------------------------------------ */
/* 系统                                                                */
/* ------------------------------------------------------------------ */
export const getHealth = () => get('/health');
export const getSystemInfo = () => get('/system/info');
export const initAdminToken = (token) => post('/system/admin-token/init', { token });
export const getSystemStats = () => get('/system/stats');
export const getSettings = () => get('/settings');
export const saveSettings = (patch) => put('/settings', patch);

/* ------------------------------------------------------------------ */
/* 后台任务 / 系统告警（v0.2.0 M4）                                     */
/* ------------------------------------------------------------------ */
export const listJobs = (params = {}) => get('/jobs', params);
export const getJob = (id) => get(`/jobs/${encodeURIComponent(id)}`);
export const retryJob = (id) => post(`/jobs/${encodeURIComponent(id)}/retry`);
export const cancelJob = (id) => post(`/jobs/${encodeURIComponent(id)}/cancel`);
export const listAlerts = (params = {}) => get('/alerts', params);
export const ackAlert = (id) => post(`/alerts/${encodeURIComponent(id)}/ack`);
export const resolveAlert = (id) => post(`/alerts/${encodeURIComponent(id)}/resolve`);

/* ------------------------------------------------------------------ */
/* 账号 / 登录 / 审计（v0.2.0 M3）                                      */
/* ------------------------------------------------------------------ */
export const getAuthMe = () => get('/auth/me', undefined, { skipAuthToast: true });
export const loginAdmin = (payload) => post('/auth/login', payload, { skipAuthToast: true });
export const setupAdmin = (payload) => post('/auth/setup', payload, { skipAuthToast: true });
export const logoutAdmin = () => post('/auth/logout', undefined, { skipAuthToast: true });
export const changePassword = (payload) => post('/auth/password', payload);
export const listAdminUsers = () => get('/admin/users');
export const createAdminUser = (payload) => post('/admin/users', payload);
export const updateAdminUser = (id, payload) =>
  request('PATCH', `/admin/users/${encodeURIComponent(id)}`, { body: payload });
export const deleteAdminUser = (id) => del(`/admin/users/${encodeURIComponent(id)}`);
export const resetAdminPassword = (id, payload) =>
  post(`/admin/users/${encodeURIComponent(id)}/reset-password`, payload);
export const getAuditLog = (params = {}) => get('/admin/audit', params);

/* ------------------------------------------------------------------ */
/* 移动端访问（扫码 / 公网隧道）                                        */
/* ------------------------------------------------------------------ */
export const getMobileAccess = () => get('/mobile/access');
export const startMobileTunnel = () => post('/mobile/tunnel/start');
export const stopMobileTunnel = () => post('/mobile/tunnel/stop');
export const mobileQrUrl = (url) =>
  `${API_BASE}/mobile/qr.png?url=${encodeURIComponent(String(url || ''))}`;

/* ------------------------------------------------------------------ */
/* 对话                                                                */
/* ------------------------------------------------------------------ */
export const sendChat = (payload) => post('/chat', payload);

/** SSE 流式问答；返回 done 事件的完整结果。 */
export async function sendChatStream(payload, { onMeta, onDelta, signal } = {}) {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    const detail = extractDetail(data, response.status);
    if (response.status === 401) notifyUnauthorized(detail);
    throw new ApiError(detail, { status: response.status, detail, payload: data });
  }
  if (!response.body) throw new ApiError('浏览器不支持流式响应', { status: 0 });
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let done = null;
  const dispatch = (block) => {
    let event = 'message';
    const dataLines = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    let data = null;
    try {
      data = JSON.parse(dataLines.join('\n'));
    } catch {
      return;
    }
    if (event === 'meta') onMeta?.(data);
    else if (event === 'delta') onDelta?.(String(data?.text || ''));
    else if (event === 'error') {
      throw new ApiError(String(data?.detail || '流式回答失败'), { status: 0 });
    } else if (event === 'done') done = data;
  };
  while (true) {
    const { value, done: finished } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !finished });
    let index = buffer.indexOf('\n\n');
    while (index >= 0) {
      const block = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      if (block.trim()) dispatch(block);
      index = buffer.indexOf('\n\n');
    }
    if (finished) break;
  }
  if (buffer.trim()) dispatch(buffer);
  if (!done) throw new ApiError('流式回答意外中断，请重试', { status: 0 });
  return done;
}
// 历史会话在配置 NPU_ADMIN_TOKEN 后需要管理员令牌；401 由对话页自行展示空态，避免全局 toast 反复打扰
export const listConversations = (channel = 'web', limit = 30) =>
  get('/chat/conversations', { channel, limit }, { skipAuthToast: true });
export const getConversationMessages = (conversationId) =>
  get(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
    undefined,
    { skipAuthToast: true },
  );
export const deleteConversation = (conversationId) =>
  del(`/chat/conversations/${encodeURIComponent(conversationId)}`);
export const submitFeedback = (payload) => post('/chat/feedback', payload);

/* ------------------------------------------------------------------ */
/* 服务台 / RSI（v0.2.0，需管理员令牌）                                  */
/* ------------------------------------------------------------------ */
export const getInsightsOverview = (days = 7) => get('/insights/overview', { days });
export const listGaps = (params = {}) => get('/insights/gaps', params);
export const getGap = (id) => get(`/insights/gaps/${encodeURIComponent(id)}`);
export const linkGap = (id, payload) =>
  post(`/insights/gaps/${encodeURIComponent(id)}/link`, payload);
export const resolveGap = (id, payload = {}) =>
  post(`/insights/gaps/${encodeURIComponent(id)}/resolve`, payload);
export const ignoreGap = (id, payload = {}) =>
  post(`/insights/gaps/${encodeURIComponent(id)}/ignore`, payload);
export const listFeedback = (params = {}) => get('/insights/feedback', params);
export const updateFeedbackStatus = (id, payload) =>
  post(`/insights/feedback/${encodeURIComponent(id)}/status`, payload);
export const getFreshness = (window = 30) => get('/insights/freshness', { window });
export const recomputeGaps = (days = 90) => post('/insights/recompute', { days });

/* ------------------------------------------------------------------ */
/* 转人工 / 危机（v0.2.0）                                              */
/* ------------------------------------------------------------------ */
export const createHandoff = (payload) => post('/handoff', payload);
export const getHandoffTicket = (ticketNo, token) =>
  get(`/handoff/ticket/${encodeURIComponent(ticketNo)}`, { token });
export const listHandoffs = (params = {}) => get('/handoff', params);
export const getHandoff = (id) => get(`/handoff/${encodeURIComponent(id)}`);
export const claimHandoff = (id, payload = {}) =>
  post(`/handoff/${encodeURIComponent(id)}/claim`, payload);
export const replyHandoff = (id, payload) =>
  post(`/handoff/${encodeURIComponent(id)}/reply`, payload);
export const resolveHandoff = (id, payload = {}) =>
  post(`/handoff/${encodeURIComponent(id)}/resolve`, payload);
export const listCrisisEvents = (params = {}) => get('/crisis/events', params);
export const updateCrisisStatus = (id, payload) =>
  post(`/crisis/events/${encodeURIComponent(id)}/status`, payload);

/* ------------------------------------------------------------------ */
/* 知识库（LLM Wiki）：源文件 + 词条                                    */
/* ------------------------------------------------------------------ */
export const listDocuments = () => get('/knowledge/documents');
export const getDocument = (id) => get(`/knowledge/documents/${encodeURIComponent(id)}`);
export const deleteDocument = (id) => del(`/knowledge/documents/${encodeURIComponent(id)}`);
export const reindexDocument = (id) =>
  post(`/knowledge/documents/${encodeURIComponent(id)}/reindex`);
export const documentDownloadUrl = (id) =>
  `${API_BASE}/knowledge/documents/${encodeURIComponent(id)}/download`;
export const getKnowledgeStats = () => get('/knowledge/stats');

/* ------------------------------------------------------------------ */
/* 快速收录（v0.3，需管理令牌）                                          */
/* ------------------------------------------------------------------ */
export const importUrl = (payload) => post('/knowledge/import/url', payload);
export const importText = (payload) => post('/knowledge/import/text', payload);
export const importBatch = (payload) => post('/knowledge/import/batch', payload);
/** 扫描服务器本地文件夹并批量收录（需管理员；返回 job_id）。 */
export const scanKnowledgeFolder = (payload) => post('/knowledge/scan-dir', payload);

/** 导入预编译 Wiki 快照（/knowledge/seed 优先，404 时回退 /wiki/seed）。 */
export async function seedWiki() {
  try {
    return await post('/knowledge/seed');
  } catch (err) {
    if (err && (err.status === 404 || err.status === 405)) {
      return await post('/wiki/seed');
    }
    throw err;
  }
}

/** 兼容旧调用点：seed 语义与 seedWiki 一致。 */
export const seedKnowledge = seedWiki;

/* ------------------------------------------------------------------ */
/* Wiki 词条                                                           */
/* ------------------------------------------------------------------ */
export const getWikiTree = () => get('/wiki/tree');
export const listWikiPages = ({ q = '', tag = '', limit = 50, offset = 0 } = {}) =>
  get('/wiki/pages', { q, tag, limit, offset });
export const getWikiPage = (id) => get(`/wiki/pages/${encodeURIComponent(id)}`);
export const updateWikiPage = (id, body) =>
  put(`/wiki/pages/${encodeURIComponent(id)}`, body);
export const verifyWikiPage = (id, body = {}) =>
  post(`/wiki/pages/${encodeURIComponent(id)}/verify`, body);
export const deleteWikiPage = (id) => del(`/wiki/pages/${encodeURIComponent(id)}`);
export const searchWiki = (query, limit) =>
  post('/wiki/search', limit ? { query, limit } : { query });
export const getWikiStats = () => get('/wiki/stats');
export const rebuildWiki = (body = {}) => post('/wiki/rebuild', body);
export const getWikiBuild = (id) => get(`/wiki/builds/${encodeURIComponent(id)}`);
export const listWikiBuilds = (limit = 10) => get('/wiki/builds', { limit });

/**
 * 上传知识文档（multipart）。使用 XHR 以便拿到真实上传进度。
 * @param {File} file
 * @param {{title?: string, tags?: string[]|string, asyncMode?: boolean, onProgress?: (percent:number)=>void}} options
 * @returns {Promise<object>} 文档对象；``asyncMode`` 时为 ``{job_id,status}``
 */
export function uploadDocument(file, { title = '', tags = [], asyncMode = false, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/knowledge/documents${asyncMode ? '?async=1' : ''}`);
    xhr.responseType = 'text';
    const token = getAdminToken();
    if (token) xhr.setRequestHeader('X-Admin-Token', token);
    if (csrfToken) xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.upload.onprogress = (event) => {
      if (!onProgress) return;
      if (event.lengthComputable && event.total > 0) {
        onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)));
      } else {
        onProgress(40);
      }
    };
    xhr.onerror = () =>
      reject(new ApiError('上传失败：无法连接后端服务，请确认服务已启动。', { status: 0 }));
    xhr.onabort = () => reject(new ApiError('上传已取消', { status: 0 }));
    xhr.onload = () => {
      let data = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100);
        resolve(data);
        return;
      }
      const detail = extractDetail(data, xhr.status);
      if (xhr.status === 401) notifyUnauthorized(detail);
      reject(new ApiError(detail, { status: xhr.status, detail, payload: data }));
    };
    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);
    const tagList = Array.isArray(tags)
      ? tags
      : String(tags || '')
          .split(/[,，]/)
          .map((t) => t.trim())
          .filter(Boolean);
    if (tagList.length) form.append('tags', tagList.join(','));
    xhr.send(form);
  });
}

/* ------------------------------------------------------------------ */
/* 模型                                                                */
/* ------------------------------------------------------------------ */
export const listProviders = () => get('/models/providers');
export const createProvider = (body) => post('/models/providers', body);
export const updateProvider = (id, body) => put(`/models/providers/${encodeURIComponent(id)}`, body);
export const deleteProvider = (id) => del(`/models/providers/${encodeURIComponent(id)}`);
export const testProvider = (id) => post(`/models/providers/${encodeURIComponent(id)}/test`);
export const listModels = () => get('/models');
export const createModel = (body) => post('/models', body);
export const updateModel = (id, body) => put(`/models/${encodeURIComponent(id)}`, body);
export const deleteModel = (id) => del(`/models/${encodeURIComponent(id)}`);
export const setDefaultModel = (id) => post(`/models/${encodeURIComponent(id)}/default`);
export const testModel = (id) => post(`/models/${encodeURIComponent(id)}/test`);

/* ------------------------------------------------------------------ */
/* 机器人                                                              */
/* ------------------------------------------------------------------ */
export const listBotChannels = () => get('/bots/channels');
export const createBotChannel = (body) => post('/bots/channels', body);
export const updateBotChannel = (id, body) => put(`/bots/channels/${encodeURIComponent(id)}`, body);
export const deleteBotChannel = (id) => del(`/bots/channels/${encodeURIComponent(id)}`);
export const testBotChannel = (id, body) =>
  post(`/bots/channels/${encodeURIComponent(id)}/test`, body);
export const listBotLogs = (limit = 50) => get('/bots/logs', { limit });

/**
 * api.js — NPU-Assistant 前端 API 访问层
 *
 * 约定（见 docs/CONTRACT.md §5）：
 *  - 所有请求直接打到当前站点的 `/api`，不依赖任何 CDN 或代理；
 *  - 非 2xx 响应统一抛出 ApiError，message 取后端 `detail`，供 toast 直接展示；
 *  - 后端接口可能尚未就绪（404）或返回空数据，调用方需自行降级，本层只负责如实抛错。
 */

const API_BASE = `${location.origin}/api`;

export class ApiError extends Error {
  constructor(message, { status = 0, detail = '', payload = null } = {}) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.payload = payload;
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

async function parseResponse(res) {
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
    throw new ApiError(detail, { status: res.status, detail, payload: data });
  }
  return data;
}

async function request(method, path, { body, query, signal } = {}) {
  const init = { method, headers: {}, signal };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(buildUrl(path, query), init);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError('无法连接后端服务，请确认 NPU-Assistant 已启动后重试。', { status: 0 });
  }
  return parseResponse(res);
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
export const getSystemStats = () => get('/system/stats');
export const getSettings = () => get('/settings');
export const saveSettings = (patch) => put('/settings', patch);

/* ------------------------------------------------------------------ */
/* 对话                                                                */
/* ------------------------------------------------------------------ */
export const sendChat = (payload) => post('/chat', payload);
export const listConversations = (channel = 'web', limit = 30) =>
  get('/chat/conversations', { channel, limit });
export const getConversationMessages = (conversationId) =>
  get(`/chat/conversations/${encodeURIComponent(conversationId)}/messages`);
export const deleteConversation = (conversationId) =>
  del(`/chat/conversations/${encodeURIComponent(conversationId)}`);

/* ------------------------------------------------------------------ */
/* 知识库                                                              */
/* ------------------------------------------------------------------ */
export const listDocuments = () => get('/knowledge/documents');
export const getDocument = (id) => get(`/knowledge/documents/${encodeURIComponent(id)}`);
export const deleteDocument = (id) => del(`/knowledge/documents/${encodeURIComponent(id)}`);
export const reindexDocument = (id) =>
  post(`/knowledge/documents/${encodeURIComponent(id)}/reindex`);
export const getDocumentChunks = (id, offset = 0, limit = 50) =>
  get(`/knowledge/documents/${encodeURIComponent(id)}/chunks`, { offset, limit });
export const searchKnowledge = (query, topK) => post('/knowledge/search', { query, top_k: topK });
export const seedKnowledge = () => post('/knowledge/seed');
export const getKnowledgeStats = () => get('/knowledge/stats');

/**
 * 上传知识文档（multipart）。使用 XHR 以便拿到真实上传进度。
 * @param {File} file
 * @param {{title?: string, tags?: string[]|string, onProgress?: (percent:number)=>void}} options
 * @returns {Promise<object>} 文档对象
 */
export function uploadDocument(file, { title = '', tags = [], onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/knowledge/documents`);
    xhr.responseType = 'text';
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

/**
 * app.js — SPA 壳层：hash 路由、全局导航、服务健康状态、错误兜底。
 *
 * 5 个视图（#/chat #/knowledge #/models #/bots #/settings）按需动态导入，
 * 每个视图模块导出 `mount(container)`，可返回清理函数。
 */
import { getHealth } from './api.js';
import { esc, icon, toast } from './ui.js';

const ROUTES = {
  chat: {
    title: '对话',
    subtitle: '与辅导员数字分身「小南」对话，回答基于知识库并标注引用。',
    load: () => import('./views/chat.js'),
  },
  knowledge: {
    title: '知识库',
    subtitle: '上传校园资料，自动解析切片，作为数字分身回答的依据。',
    load: () => import('./views/knowledge.js'),
  },
  models: {
    title: '模型',
    subtitle: '管理 OpenAI 兼容 / Ollama / 内置演示模型，设置默认问答模型。',
    load: () => import('./views/models.js'),
  },
  bots: {
    title: '机器人',
    subtitle: '接入 QQ（OneBot 11）与微信公众号，或使用控制台通道联调。',
    load: () => import('./views/bots.js'),
  },
  settings: {
    title: '设置',
    subtitle: '调整分身人设、欢迎语、快捷问题、检索参数与回复样式。',
    load: () => import('./views/settings.js'),
  },
};

const DEFAULT_VIEW = 'chat';

const dom = {
  view: document.getElementById('view'),
  title: document.getElementById('pageTitle'),
  subtitle: document.getElementById('pageSubtitle'),
  nav: document.getElementById('nav'),
  sidebar: document.getElementById('sidebar'),
  backdrop: document.getElementById('sidebarBackdrop'),
  menuBtn: document.getElementById('menuBtn'),
  closeBtn: document.getElementById('sidebarClose'),
  healthCard: document.getElementById('healthCard'),
  healthDot: document.getElementById('healthDot'),
  healthTitle: document.getElementById('healthTitle'),
  healthModel: document.getElementById('healthModel'),
};

let currentView = null;
let currentCleanup = null;
let renderToken = 0;

/* ------------------------------------------------------------------ */
/* 路由                                                                */
/* ------------------------------------------------------------------ */
function parseHash() {
  const raw = String(location.hash || '').replace(/^#\/?/, '');
  const name = raw.split('?')[0].split('/')[0].trim();
  return name || DEFAULT_VIEW;
}

function setActiveNav(viewName) {
  dom.nav.querySelectorAll('[data-view]').forEach((item) => {
    const active = item.dataset.view === viewName;
    item.classList.toggle('is-active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
}

function showFatal(message) {
  dom.view.innerHTML = `<div class="state state-error">
    <div class="state-icon">${icon('alert')}</div>
    <h3>页面加载失败</h3>
    <p>${esc(message)}</p>
    <div class="state-action"><button class="btn btn-primary" type="button" id="fatalReload">${icon(
      'refresh',
    )}<span>重新加载</span></button></div>
  </div>`;
  dom.view.querySelector('#fatalReload')?.addEventListener('click', () => location.reload());
}

async function renderRoute() {
  const requested = parseHash();
  const viewName = ROUTES[requested] ? requested : DEFAULT_VIEW;

  if (requested !== viewName) {
    toast(`页面「${requested}」不存在，已返回对话页`, 'warning', { title: '路由提示' });
    location.replace(`#/${viewName}`);
    return;
  }

  const token = ++renderToken;
  if (typeof currentCleanup === 'function') {
    try {
      currentCleanup();
    } catch (err) {
      console.warn('[NPU-Assistant] 视图清理失败', err);
    }
    currentCleanup = null;
  }
  currentView = viewName;

  const route = ROUTES[viewName];
  setActiveNav(viewName);
  dom.title.textContent = route.title;
  dom.subtitle.textContent = route.subtitle;
  document.title = `${route.title} · NPU-Assistant`;
  closeSidebar();
  dom.view.innerHTML = '';
  dom.view.scrollTop = 0;
  if (document.scrollingElement) document.scrollingElement.scrollTop = 0;

  try {
    const module = await route.load();
    if (token !== renderToken) return;
    const cleanup = module.mount ? module.mount(dom.view) : undefined;
    if (token !== renderToken) {
      if (typeof cleanup === 'function') cleanup();
      return;
    }
    currentCleanup = typeof cleanup === 'function' ? cleanup : null;
  } catch (err) {
    if (token !== renderToken) return;
    console.warn('[NPU-Assistant] 视图加载失败', err);
    showFatal(err?.message || '未知错误，请刷新页面重试');
  }
}

/* ------------------------------------------------------------------ */
/* 移动端侧栏                                                          */
/* ------------------------------------------------------------------ */
function openSidebar() {
  document.body.classList.add('sidebar-open');
  dom.backdrop.hidden = false;
  dom.sidebar.classList.add('is-open');
  dom.menuBtn?.setAttribute('aria-expanded', 'true');
}

function closeSidebar() {
  document.body.classList.remove('sidebar-open');
  dom.backdrop.hidden = true;
  dom.sidebar.classList.remove('is-open');
  dom.menuBtn?.setAttribute('aria-expanded', 'false');
}

function bindShell() {
  dom.menuBtn?.addEventListener('click', () => {
    if (dom.sidebar.classList.contains('is-open')) closeSidebar();
    else openSidebar();
  });
  dom.closeBtn?.addEventListener('click', closeSidebar);
  dom.backdrop?.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && dom.sidebar.classList.contains('is-open')) closeSidebar();
  });
  window.addEventListener('resize', () => {
    if (window.innerWidth > 1080) closeSidebar();
  });
  window.addEventListener('hashchange', () => {
    renderRoute();
  });
}

/* ------------------------------------------------------------------ */
/* 健康状态                                                            */
/* ------------------------------------------------------------------ */
function paintHealth(ok, detail = {}) {
  if (!dom.healthCard) return;
  dom.healthCard.classList.toggle('is-ok', ok);
  dom.healthCard.classList.toggle('is-error', !ok);
  dom.healthDot?.classList.toggle('is-pending', false);
  if (ok) {
    dom.healthTitle.textContent = '服务运行正常';
    const model = detail?.default_model;
    dom.healthModel.textContent = model
      ? `默认模型：${model.display_name || model.model_name}`
      : '未配置默认模型（演示降级）';
    dom.healthCard.title = `NPU-Assistant ${detail?.version || ''} · ${detail?.time || ''}`;
  } else {
    dom.healthTitle.textContent = '后端服务未连接';
    dom.healthModel.textContent = '请用 scripts/run.ps1 启动服务';
    dom.healthCard.title = detail?.message || '无法连接 /api/health';
  }
}

let healthTimer = 0;
async function refreshHealth() {
  try {
    const data = await getHealth();
    paintHealth(true, data);
  } catch (err) {
    paintHealth(false, { message: err?.message });
  }
}

function startHealthPolling() {
  refreshHealth();
  window.clearInterval(healthTimer);
  healthTimer = window.setInterval(() => {
    if (document.visibilityState === 'hidden') return;
    refreshHealth();
  }, 30000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshHealth();
  });
}

/* ------------------------------------------------------------------ */
/* 全局错误兜底：避免控制台出现未捕获异常                              */
/* ------------------------------------------------------------------ */
function bindGlobalErrorHandlers() {
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    if (reason && reason.name === 'AbortError') {
      event.preventDefault();
      return;
    }
    const message = reason?.message || reason?.detail || '发生未知错误，请重试';
    toast(String(message), 'error');
    console.warn('[NPU-Assistant] 未处理的 Promise 异常', reason);
    event.preventDefault();
  });
  window.addEventListener('error', (event) => {
    // 资源加载失败（如接口 404 的图片）不打断页面，仅记录。
    if (event.target && event.target !== window) {
      console.warn('[NPU-Assistant] 资源加载失败', event.target);
      return;
    }
    toast(event.message || '页面发生脚本错误', 'error');
  });
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
function boot() {
  bindShell();
  bindGlobalErrorHandlers();
  if (!location.hash) {
    location.replace(`#/${DEFAULT_VIEW}`);
    return;
  }
  renderRoute();
  startHealthPolling();
}

boot();

export { currentView };

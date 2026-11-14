/**
 * app.js — SPA 壳层：hash 路由、全局导航、服务健康状态、错误兜底。
 *
 * 四个一级入口（#/chat #/knowledge #/ops #/settings）按需动态导入；
 * 运营与设置由 hub 视图聚合原有子页面，旧路由自动重定向。
 */
import { getAuthMe, getHealth, setCsrfToken, setUnauthorizedHandler } from './api.js';
import { esc, icon, toast } from './ui.js';

const ROUTES = {
  login: {
    title: '登录',
    subtitle: '管理员账号登录',
    load: () => import('./views/login.js'),
    public: true,
  },
  chat: {
    title: '对话',
    subtitle: '向辅导员数字分身提问',
    load: () => import('./views/chat.js'),
  },
  knowledge: {
    title: '知识库',
    subtitle: '浏览、收录与维护校园知识',
    load: () => import('./views/knowledge.js'),
  },
  ops: {
    title: '运营',
    subtitle: '服务台、任务告警与审计',
    load: () => import('./views/ops-hub.js'),
    permission: 'insights:read',
  },
  settings: {
    title: '设置',
    subtitle: '助手、模型、机器人与成员',
    load: () => import('./views/settings-hub.js'),
    authOnly: true,
  },
};

const LEGACY_VIEWS = {
  manage: '#/knowledge?manage=1',
  insights: '#/ops?tab=desk',
  jobs: '#/ops?tab=jobs',
  models: '#/settings?tab=models',
  bots: '#/settings?tab=bots',
  team: '#/settings?tab=members',
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
let authState = null;
let authPromise = null;

function can(permission) {
  if (authState?.open_mode) return true;
  const perms = authState?.permissions || [];
  return perms.includes('*') || perms.includes(permission);
}

function canAccess(route) {
  if (!route || route.public) return true;
  const open = Boolean(authState?.open_mode);
  const authed = Boolean(authState?.authenticated);
  if (route.authOnly && !open && !authed) return false;
  if (route.permission && !open && !can(route.permission)) return false;
  return true;
}

function applyNavAuth() {
  dom.nav?.querySelectorAll('[data-view]').forEach((item) => {
    const route = ROUTES[item.dataset.view];
    const visible = route ? canAccess(route) : true;
    item.hidden = !visible;
  });
  dom.nav?.querySelectorAll('.nav-group').forEach((group) => {
    const items = Array.from(group.querySelectorAll('[data-view]'));
    group.hidden = items.length > 0 && items.every((item) => item.hidden);
  });
  const account = document.getElementById('navAccount');
  if (account) {
    const user = authState?.user;
    account.hidden = !authState?.authenticated && !authState?.open_mode;
    const name = account.querySelector('[data-account-name]');
    if (name) name.textContent = user?.display_name || user?.username || '本机演示';
    const role = account.querySelector('[data-account-role]');
    if (role) role.textContent = user?.role_label || (authState?.open_mode ? '未启用账号' : '');
  }
  const login = document.getElementById('navLogin');
  if (login) login.hidden = Boolean(authState?.authenticated || authState?.open_mode);
}

async function ensureAuth(force = false) {
  if (authState && !force) return authState;
  if (!authPromise || force) {
    authPromise = getAuthMe()
      .then((data) => {
        authState = data;
        setCsrfToken(data?.csrf_token || '');
        applyNavAuth();
        return authState;
      })
      .catch(() => {
        authState = null;
        return null;
      })
      .finally(() => {
        authPromise = null;
      });
  }
  return authPromise;
}

/* ------------------------------------------------------------------ */
/* 路由                                                                */
/* ------------------------------------------------------------------ */
function parseHash() {
  const raw = String(location.hash || '').replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const name = (pathPart.split('/')[0] || '').trim() || DEFAULT_VIEW;
  return { name, query: new URLSearchParams(queryPart) };
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
  const { name: requested, query } = parseHash();
  if (LEGACY_VIEWS[requested]) {
    location.replace(LEGACY_VIEWS[requested]);
    return;
  }
  const viewName = ROUTES[requested] ? requested : DEFAULT_VIEW;

  if (requested !== viewName) {
    toast(`页面「${requested}」不存在，已返回对话页`, 'warning', { title: '路由提示' });
    location.replace(`#/${viewName}`);
    return;
  }

  await ensureAuth();
  const route = ROUTES[viewName];
  if (!canAccess(route)) {
    if (!authState?.authenticated && !authState?.open_mode) {
      const next = encodeURIComponent(location.hash || `#/${viewName}`);
      location.replace(`#/login?next=${next}`);
      return;
    }
    dom.title.textContent = route.title;
    dom.subtitle.textContent = '没有访问权限';
    setActiveNav(viewName);
    dom.view.innerHTML = `<div class="state state-empty">
      <div class="state-icon">${icon('shield')}</div>
      <h3>当前账号没有访问权限</h3>
      <p>请联系超级管理员调整角色，或使用其他账号登录。</p>
      <div class="state-action"><a class="btn btn-primary" href="#/settings?tab=account">${icon(
        'shield',
      )}<span>查看我的账号</span></a></div>
    </div>`;
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
    let mountName = route.mountName || 'mount';
    if (viewName === 'knowledge' && can('content:write') && query.get('manage') === '1') {
      mountName = 'mountManage';
    }
    const mountFn = typeof module[mountName] === 'function' ? module[mountName] : module.mount;
    const cleanup = mountFn
      ? mountFn(dom.view, { query, canManage: can('content:write') })
      : undefined;
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
  // skip-link 不能使用默认 hash 跳转（会被 SPA 路由当成页面），改为聚焦主内容区
  document.querySelector('.skip-link')?.addEventListener('click', (event) => {
    event.preventDefault();
    if (!dom.view) return;
    dom.view.focus({ preventScroll: true });
    dom.view.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
}

/* ------------------------------------------------------------------ */
/* 健康状态                                                            */
/* ------------------------------------------------------------------ */
function paintHealth(ok, detail = {}) {
  if (!dom.healthCard) return;
  const degraded = Boolean(ok && detail.status && detail.status !== 'ok');
  const issues = Array.isArray(detail.issues) ? detail.issues.filter(Boolean) : [];
  dom.healthCard.classList.toggle('is-ok', ok && !degraded);
  dom.healthCard.classList.toggle('is-error', !ok || degraded);
  dom.healthDot?.classList.toggle('is-pending', false);
  if (ok) {
    dom.healthTitle.textContent = degraded ? '服务有异常' : '服务运行正常';
    dom.healthCard.title = degraded
      ? issues.join('；') || '部分能力不可用，请检查设置'
      : `NPU-Assistant ${detail?.version || ''} · ${detail?.time || ''}`;
  } else {
    dom.healthTitle.textContent = '服务未连接';
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
/* 管理令牌 401 引导                                                   */
/* ------------------------------------------------------------------ */
let lastUnauthorizedAt = 0;

function bindUnauthorizedGuide() {
  setUnauthorizedHandler((detail) => {
    const now = Date.now();
    if (now - lastUnauthorizedAt < 6000) return;
    lastUnauthorizedAt = now;
    toast(
      detail ||
        '登录已失效或需要更高权限：请重新登录，或到「设置」检查管理令牌。',
      'error',
      {
        title: '需要登录',
        duration: 9000,
        actionLabel: '去登录',
        onAction: () => {
          location.hash = `#/login?next=${encodeURIComponent(location.hash)}`;
        },
      },
    );
  });
}

/* ------------------------------------------------------------------ */
/* PWA：Service Worker 注册 + 安装提示                                 */
/* ------------------------------------------------------------------ */
function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  const isSecure =
    location.protocol === 'https:' ||
    ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
  if (!isSecure) return; // HTTP 局域网访问不支持 SW/PWA 安装
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./service-worker.js').catch((err) => {
      console.warn('[NPU-Assistant] Service Worker 注册失败', err);
    });
  });
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function boot() {
  bindShell();
  bindGlobalErrorHandlers();
  bindUnauthorizedGuide();
  registerServiceWorker();
  await ensureAuth();
  window.addEventListener('npu-auth-changed', async () => {
    await ensureAuth(true);
    renderRoute();
  });
  // 无论是否有 hash 都要启动健康轮询（首访无 hash 会重定向到对话页）
  startHealthPolling();
  if (!location.hash) {
    location.replace(`#/${DEFAULT_VIEW}`);
    return;
  }
  renderRoute();
}

boot();

export { currentView };

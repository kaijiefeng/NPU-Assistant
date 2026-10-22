/**
 * ui.js — 通用 UI 组件与工具
 *
 * 只依赖原生 DOM：toast、模态弹窗、右侧抽屉、确认框、空态/加载态/错误态、
 * 时间/字节格式化、剪贴板、内联 SVG 图标等。
 */

/* ------------------------------------------------------------------ */
/* 基础工具                                                            */
/* ------------------------------------------------------------------ */
export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function icon(name, cls = '') {
  return `<svg class="i ${cls}" aria-hidden="true" focusable="false"><use href="#i-${esc(name)}"></use></svg>`;
}

export function htmlToNode(html) {
  const template = document.createElement('template');
  template.innerHTML = String(html).trim();
  return template.content.firstElementChild;
}

export function truncate(text, max = 80) {
  const value = String(text ?? '');
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(0, max - 1))}…`;
}

export function safeJson(value, fallback = null) {
  if (value === null || value === undefined) return fallback;
  if (typeof value === 'object') return value;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

export function debounce(fn, wait = 250) {
  let timer = 0;
  return function debounced(...args) {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn.apply(this, args), wait);
  };
}

/* ------------------------------------------------------------------ */
/* 格式化                                                              */
/* ------------------------------------------------------------------ */
function pad2(n) {
  return String(n).padStart(2, '0');
}

export function fmtDateTime(value) {
  if (!value) return '—';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(
    date.getHours(),
  )}:${pad2(date.getMinutes())}`;
}

export function fmtRelative(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const diff = Date.now() - date.getTime();
  if (diff < 0) return fmtDateTime(date);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`;
  return fmtDateTime(date);
}

export function fmtBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB'];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`;
}

export function fmtNumber(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return num.toLocaleString('zh-CN');
}

export function fmtScore(score) {
  const num = Number(score);
  if (!Number.isFinite(num)) return '—';
  return num >= 10 ? num.toFixed(1) : num.toFixed(2);
}

/* ------------------------------------------------------------------ */
/* 图标 / 徽标 / 状态块                                                */
/* ------------------------------------------------------------------ */
export function badge(text, tone = 'blue') {
  return `<span class="badge badge-${esc(tone)}">${esc(text)}</span>`;
}

export function spinner(cls = '') {
  return `<span class="spinner ${cls}" aria-hidden="true"></span>`;
}

export function loadingHtml(text = '加载中…', extraClass = '') {
  return `<div class="state state-loading ${extraClass}">${spinner('spinner-lg')}<p>${esc(text)}</p></div>`;
}

export function emptyHtml({ icon: iconName = 'folder', title = '暂无数据', desc = '', action = '' } = {}) {
  return `<div class="state state-empty">
    <div class="state-icon">${icon(iconName)}</div>
    <h3>${esc(title)}</h3>
    ${desc ? `<p>${esc(desc)}</p>` : ''}
    ${action ? `<div class="state-action">${action}</div>` : ''}
  </div>`;
}

export function errorHtml(message = '加载失败', { retryAction = 'retry', retryLabel = '重新加载' } = {}) {
  return `<div class="state state-error">
    <div class="state-icon">${icon('alert')}</div>
    <h3>加载失败</h3>
    <p>${esc(message)}</p>
    ${
      retryAction
        ? `<div class="state-action"><button class="btn btn-sm btn-ghost" type="button" data-action="${esc(
            retryAction,
          )}">${icon('refresh')}<span>${esc(retryLabel)}</span></button></div>`
        : ''
    }
  </div>`;
}

export function setButtonBusy(button, busy, busyText = '处理中…') {
  if (!button) return;
  if (busy) {
    if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.classList.add('is-busy');
    button.innerHTML = `${spinner()}<span>${esc(busyText)}</span>`;
  } else {
    button.disabled = false;
    button.classList.remove('is-busy');
    if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
      delete button.dataset.originalHtml;
    }
  }
}

/* ------------------------------------------------------------------ */
/* Toast                                                               */
/* ------------------------------------------------------------------ */
const TOAST_TONE = {
  success: { icon: 'check', title: '操作成功' },
  error: { icon: 'alert', title: '操作失败' },
  warning: { icon: 'alert', title: '请注意' },
  info: { icon: 'info', title: '提示' },
};

export function toast(message, type = 'info', options = {}) {
  const root = document.getElementById('toastRoot');
  if (!root) return;
  const tone = TOAST_TONE[type] ? type : 'info';
  const config = TOAST_TONE[tone];
  const title = options.title === undefined ? config.title : options.title;
  const node = document.createElement('div');
  node.className = `toast toast-${tone}`;
  node.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  node.innerHTML = `
    <span class="toast-icon">${icon(config.icon)}</span>
    <div class="toast-body">
      ${title ? `<strong>${esc(title)}</strong>` : ''}
      <p>${esc(message)}</p>
    </div>
    <button class="toast-close" type="button" aria-label="关闭通知">${icon('x')}</button>`;
  root.appendChild(node);
  window.requestAnimationFrame(() => node.classList.add('is-visible'));

  let timer = 0;
  const remove = () => {
    window.clearTimeout(timer);
    node.classList.remove('is-visible');
    node.classList.add('is-leaving');
    window.setTimeout(() => node.remove(), 220);
  };
  node.querySelector('.toast-close')?.addEventListener('click', remove);
  node.addEventListener('mouseenter', () => window.clearTimeout(timer));
  node.addEventListener('mouseleave', () => {
    timer = window.setTimeout(remove, 1600);
  });

  const duration = options.duration ?? (tone === 'error' ? 7000 : 4000);
  if (duration > 0) timer = window.setTimeout(remove, duration);

  const maxToasts = window.innerWidth <= 720 ? 3 : 5;
  const toasts = Array.from(root.children);
  if (toasts.length > maxToasts) {
    toasts.slice(0, toasts.length - maxToasts).forEach((item) => item.remove());
  }
  return remove;
}

/* ------------------------------------------------------------------ */
/* 弹窗 / 抽屉（带 Esc 关闭与焦点管理）                                 */
/* ------------------------------------------------------------------ */
const overlayStack = [];

function onGlobalKeydown(event) {
  const top = overlayStack[overlayStack.length - 1];
  if (!top) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    top.close();
    return;
  }
  if (event.key === 'Tab') {
    const focusables = top.panel.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

function pushOverlay(item) {
  if (!overlayStack.length) document.addEventListener('keydown', onGlobalKeydown, true);
  overlayStack.push(item);
}

function popOverlay(item) {
  const index = overlayStack.indexOf(item);
  if (index >= 0) overlayStack.splice(index, 1);
  if (!overlayStack.length) document.removeEventListener('keydown', onGlobalKeydown, true);
}

function lockBodyScroll(lock) {
  document.body.classList.toggle('is-locked', lock);
}

const MODAL_SIZES = { sm: 'modal-sm', md: '', lg: 'modal-lg', xl: 'modal-xl' };

/**
 * 打开模态弹窗。
 * @returns {{overlay: HTMLElement, panel: HTMLElement, body: HTMLElement, footer: HTMLElement, close: Function, setTitle: Function}}
 */
export function openModal(options = {}) {
  const {
    title = '',
    subtitle = '',
    body = '',
    footer = null,
    size = 'md',
    closable = true,
    onClose = null,
  } = options;

  const previousActive = document.activeElement;
  const overlay = document.createElement('div');
  overlay.className = 'modal-backdrop';
  overlay.innerHTML = `
    <div class="modal ${MODAL_SIZES[size] || ''}" role="dialog" aria-modal="true" aria-label="${esc(
      title || '对话框',
    )}">
      <div class="modal-head">
        <div class="modal-titles">
          <h2 class="modal-title">${esc(title)}</h2>
          ${subtitle ? `<p class="modal-subtitle">${esc(subtitle)}</p>` : ''}
        </div>
        ${
          closable
            ? `<button class="icon-btn modal-close" type="button" aria-label="关闭">${icon('x')}</button>`
            : ''
        }
      </div>
      <div class="modal-body"></div>
      <div class="modal-foot"></div>
    </div>`;
  const panel = overlay.querySelector('.modal');
  const bodyEl = overlay.querySelector('.modal-body');
  const footerEl = overlay.querySelector('.modal-foot');
  if (body instanceof Node) bodyEl.appendChild(body);
  else bodyEl.innerHTML = body;
  if (footer instanceof Node) footerEl.appendChild(footer);
  else if (typeof footer === 'string') footerEl.innerHTML = footer;
  if (footerEl.childNodes.length) footerEl.hidden = false;
  else footerEl.hidden = true;

  const close = () => {
    popOverlay(item);
    overlay.classList.add('is-closing');
    window.setTimeout(() => {
      overlay.remove();
      if (!overlayStack.length) lockBodyScroll(false);
      if (previousActive && typeof previousActive.focus === 'function') {
        try {
          previousActive.focus({ preventScroll: true });
        } catch {
          /* ignore */
        }
      }
      onClose?.();
    }, 160);
  };

  const item = { panel, close };
  const modalRoot = document.getElementById('modalRoot') || document.body;
  modalRoot.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-visible'));
  lockBodyScroll(true);
  pushOverlay(item);

  overlay.querySelector('.modal-close')?.addEventListener('click', close);
  overlay.addEventListener('mousedown', (event) => {
    if (event.target === overlay && closable) close();
  });

  const setTitle = (nextTitle) => {
    const node = overlay.querySelector('.modal-title');
    if (node) node.textContent = nextTitle;
  };

  window.setTimeout(() => {
    const target = bodyEl.querySelector(
      'input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled])',
    );
    if (target) target.focus({ preventScroll: true });
  }, 60);

  return { overlay, panel, body: bodyEl, footer: footerEl, close, setTitle };
}

/**
 * 确认对话框，返回 Promise<boolean>。
 */
export function confirmDialog(options = {}) {
  const {
    title = '请确认',
    message = '',
    confirmText = '确认',
    cancelText = '取消',
    danger = false,
    detail = '',
  } = options;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const modal = openModal({
      title,
      size: 'sm',
      body: `<p class="confirm-message">${esc(message)}</p>${
        detail ? `<div class="confirm-detail">${esc(detail)}</div>` : ''
      }`,
      footer: `<button class="btn btn-ghost" type="button" data-confirm-cancel>${esc(
        cancelText,
      )}</button><button class="btn ${
        danger ? 'btn-danger' : 'btn-primary'
      }" type="button" data-confirm-ok>${esc(confirmText)}</button>`,
      onClose: () => finish(false),
    });
    modal.overlay.querySelector('[data-confirm-cancel]')?.addEventListener('click', () => {
      finish(false);
      modal.close();
    });
    modal.overlay.querySelector('[data-confirm-ok]')?.addEventListener('click', () => {
      finish(true);
      modal.close();
    });
  });
}

/**
 * 打开右侧抽屉。
 */
export function openDrawer(options = {}) {
  const { title = '', subtitle = '', body = '', footer = null, size = 'md', onClose = null } = options;
  const previousActive = document.activeElement;
  const overlay = document.createElement('div');
  overlay.className = 'drawer-backdrop';
  overlay.innerHTML = `
    <aside class="drawer drawer-${esc(size)}" role="dialog" aria-modal="true" aria-label="${esc(
      title || '详情',
    )}">
      <header class="drawer-head">
        <div class="drawer-titles">
          <h2 class="drawer-title">${esc(title)}</h2>
          ${subtitle ? `<p class="drawer-subtitle">${esc(subtitle)}</p>` : ''}
        </div>
        <button class="icon-btn drawer-close" type="button" aria-label="关闭">${icon('x')}</button>
      </header>
      <div class="drawer-body"></div>
      <div class="drawer-foot"></div>
    </aside>`;
  const panel = overlay.querySelector('.drawer');
  const bodyEl = overlay.querySelector('.drawer-body');
  const footerEl = overlay.querySelector('.drawer-foot');
  if (body instanceof Node) bodyEl.appendChild(body);
  else bodyEl.innerHTML = body;
  if (footer instanceof Node) footerEl.appendChild(footer);
  else if (typeof footer === 'string') footerEl.innerHTML = footer;
  if (footerEl.childNodes.length) footerEl.hidden = false;
  else footerEl.hidden = true;

  const close = () => {
    popOverlay(item);
    overlay.classList.add('is-closing');
    window.setTimeout(() => {
      overlay.remove();
      if (!overlayStack.length) lockBodyScroll(false);
      if (previousActive && typeof previousActive.focus === 'function') {
        try {
          previousActive.focus({ preventScroll: true });
        } catch {
          /* ignore */
        }
      }
      onClose?.();
    }, 180);
  };
  const item = { panel, close };
  const drawerRoot = document.getElementById('drawerRoot') || document.body;
  drawerRoot.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-visible'));
  lockBodyScroll(true);
  pushOverlay(item);
  overlay.querySelector('.drawer-close')?.addEventListener('click', close);
  overlay.addEventListener('mousedown', (event) => {
    if (event.target === overlay) close();
  });
  window.setTimeout(() => {
    const target = bodyEl.querySelector(
      'input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled])',
    );
    if (target) target.focus({ preventScroll: true });
  }, 180);
  return { overlay, panel, body: bodyEl, footer: footerEl, close };
}

/* ------------------------------------------------------------------ */
/* 剪贴板                                                              */
/* ------------------------------------------------------------------ */
export async function copyText(text) {
  const value = String(text ?? '');
  if (!value) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* 忽略，走降级方案 */
  }
  try {
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', 'readonly');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none;';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

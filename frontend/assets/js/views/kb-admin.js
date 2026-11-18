/**
 * views/kb-admin.js — 知识库管理聚合页
 *
 * 内容管理（收录资料/源文件/词条维护，复用 knowledge.js mountManage）+
 * 服务台（缺口/AI 找备选/订阅发现/反馈，复用 insights.js）。
 * 子视图按角色显示入口，切换不销毁彼此状态以外的全局内容。
 */
import { getAuthMe } from '../api.js';
import { esc, loadingHtml, toast } from '../ui.js';

const TABS = [
  { key: 'content', label: '内容管理', permission: 'content:write' },
  { key: 'desk', label: '服务台', permission: 'insights:read' },
];

const LOADERS = {
  content: () => import('./knowledge.js'),
  desk: () => import('./insights.js'),
};

// 内容管理入口复用 knowledge.js 的 mountManage（收录/源文件/词条维护三段）
const MOUNT_NAMES = { content: 'mountManage', desk: 'mount' };

function allowedTabs(me) {
  const perms = me?.permissions || [];
  const open = Boolean(me?.open_mode);
  return TABS.filter((tab) => open || perms.includes('*') || perms.includes(tab.permission));
}

function tabsHtml(tabs, current) {
  return `<div class="seg-tabs hub-tabs" role="tablist" aria-label="知识库管理">
    ${tabs
      .map(
        (tab) =>
          `<button class="seg-tab ${tab.key === current ? 'is-active' : ''}" type="button" role="tab" aria-selected="${
            tab.key === current
          }" data-kb-tab="${tab.key}">${esc(tab.label)}</button>`,
      )
      .join('')}
  </div>`;
}

function writeHash(tab) {
  const hash = tab === 'content' ? '#/kb-admin' : `#/kb-admin?tab=${tab}`;
  history.replaceState(null, '', hash);
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;

  root.innerHTML = `<div class="page hub-page" id="kbAdminHub">
    ${loadingHtml('正在加载知识库管理…')}
  </div>`;
  const shell = root.querySelector('#kbAdminHub');

  let me = null;
  try {
    me = await getAuthMe();
  } catch (err) {
    if (!alive) return undefined;
    shell.innerHTML = `<div class="state state-error"><h3>无法加载知识库管理入口</h3><p>${esc(
      err?.message || '请稍后重试',
    )}</p></div>`;
    return undefined;
  }
  if (!alive) return undefined;

  const tabs = allowedTabs(me);
  if (!tabs.length) {
    shell.innerHTML = `<div class="state state-empty"><h3>当前账号没有知识库管理权限</h3><p>请联系超级管理员调整角色。</p></div>`;
    return undefined;
  }

  const requested = options.query?.get('tab');
  current = tabs.some((tab) => tab.key === requested) ? requested : tabs[0].key;

  async function switchTab(tab, { updateHash = true } = {}) {
    if (!tabs.some((item) => item.key === tab)) return;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 知识库管理子视图清理失败', err);
      }
    }
    cleanup = null;
    current = tab;
    if (updateHash) writeHash(tab);

    shell.innerHTML = `<div id="kbTabs">${tabsHtml(tabs, current)}</div>
      <div class="hub-body" id="kbBody">${loadingHtml('正在加载…')}</div>`;
    const body = shell.querySelector('#kbBody');
    shell.querySelectorAll('[data-kb-tab]').forEach((button) => {
      button.addEventListener('click', () => switchTab(button.dataset.kbTab));
    });

    try {
      const module = await LOADERS[tab]();
      if (!alive || current !== tab) return;
      const mountName = MOUNT_NAMES[tab] || 'mount';
      const mountFn = typeof module[mountName] === 'function' ? module[mountName] : module.mount;
      const result = mountFn?.(body, { query: new URLSearchParams(), canManage: tab === 'content' });
      cleanup = typeof result === 'function' ? result : null;
    } catch (err) {
      if (!alive || current !== tab) return;
      body.innerHTML = `<div class="state state-error"><h3>页面加载失败</h3><p>${esc(
        err?.message || '请稍后重试',
      )}</p></div>`;
      toast(err?.message || '知识库管理子页面加载失败', 'error');
    }
  }

  await switchTab(current, { updateHash: false });

  return () => {
    alive = false;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 知识库管理视图清理失败', err);
      }
    }
  };
}

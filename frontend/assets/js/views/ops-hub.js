/**
 * views/ops-hub.js — 运营聚合页
 *
 * 服务台 / 任务与告警 / 审计日志。子视图复用现有模块，仅按角色显示入口。
 */
import { getAuthMe } from '../api.js';
import { esc, loadingHtml, toast } from '../ui.js';

const TABS = [
  { key: 'desk', label: '服务台', permission: 'insights:read' },
  { key: 'jobs', label: '任务与告警', permission: 'insights:read' },
  { key: 'audit', label: '审计日志', permission: 'audit:read' },
];

const LOADERS = {
  desk: () => import('./insights.js'),
  jobs: () => import('./jobs.js'),
  audit: () => import('./team.js'),
};

function allowedTabs(me) {
  const perms = me?.permissions || [];
  const open = Boolean(me?.open_mode);
  return TABS.filter((tab) => open || perms.includes('*') || perms.includes(tab.permission));
}

function tabsHtml(tabs, current) {
  return `<div class="seg-tabs hub-tabs" role="tablist" aria-label="运营">
    ${tabs
      .map(
        (tab) =>
          `<button class="seg-tab ${tab.key === current ? 'is-active' : ''}" type="button" role="tab" aria-selected="${
            tab.key === current
          }" data-ops-tab="${tab.key}">${esc(tab.label)}</button>`,
      )
      .join('')}
  </div>`;
}

function writeHash(tab) {
  const hash = tab === 'desk' ? '#/ops' : `#/ops?tab=${tab}`;
  history.replaceState(null, '', hash);
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;

  root.innerHTML = `<div class="page hub-page" id="opsHub">
    ${loadingHtml('正在加载运营数据…')}
  </div>`;
  const shell = root.querySelector('#opsHub');

  let me = null;
  try {
    me = await getAuthMe();
  } catch (err) {
    if (!alive) return undefined;
    shell.innerHTML = `<div class="state state-error"><h3>无法加载运营入口</h3><p>${esc(
      err?.message || '请稍后重试',
    )}</p></div>`;
    return undefined;
  }
  if (!alive) return undefined;

  const tabs = allowedTabs(me);
  if (!tabs.length) {
    shell.innerHTML = `<div class="state state-empty"><h3>当前账号没有运营权限</h3><p>请联系超级管理员调整角色。</p></div>`;
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
        console.warn('[NPU-Assistant] 运营子视图清理失败', err);
      }
    }
    cleanup = null;
    current = tab;
    if (updateHash) writeHash(tab);

    shell.innerHTML = `<div id="opsTabs">${tabsHtml(tabs, current)}</div>
      <div class="hub-body" id="opsBody">${loadingHtml('正在加载…')}</div>`;
    const body = shell.querySelector('#opsBody');
    shell.querySelectorAll('[data-ops-tab]').forEach((button) => {
      button.addEventListener('click', () => switchTab(button.dataset.opsTab));
    });

    try {
      const module = await LOADERS[tab]();
      if (!alive || current !== tab) return;
      const childQuery =
        tab === 'audit' ? new URLSearchParams({ tab: 'audit', only: '1' }) : new URLSearchParams();
      const result = module.mount?.(body, { query: childQuery });
      cleanup = typeof result === 'function' ? result : null;
    } catch (err) {
      if (!alive || current !== tab) return;
      body.innerHTML = `<div class="state state-error"><h3>页面加载失败</h3><p>${esc(
        err?.message || '请稍后重试',
      )}</p></div>`;
      toast(err?.message || '运营子页面加载失败', 'error');
    }
  }

  shell.innerHTML = `<div id="opsTabs">${tabsHtml(tabs, current)}</div>
    <div class="hub-body" id="opsBody">${loadingHtml('正在加载…')}</div>`;
  shell.querySelectorAll('[data-ops-tab]').forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.opsTab));
  });

  const module = await LOADERS[current]();
  if (!alive) return undefined;
  const body = shell.querySelector('#opsBody');
  const childQuery =
    current === 'audit' ? new URLSearchParams({ tab: 'audit', only: '1' }) : new URLSearchParams();
  const result = module.mount?.(body, { query: childQuery });
  cleanup = typeof result === 'function' ? result : null;

  return () => {
    alive = false;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 运营视图清理失败', err);
      }
    }
  };
}

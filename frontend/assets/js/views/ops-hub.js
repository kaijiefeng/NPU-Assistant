/**
 * views/ops-hub.js — 运维管理（macOS 设置页式布局）
 *
 * 左侧分组子导航（任务与告警 / 运维），右侧内容区。任务与告警拆成
 * 两个独立子页（投射模式下隐藏 jobs.js 的内部 tab），层级始终两级。
 * 报错驱动的修复已并入右侧「管理助手」（ops_task_* 工具，仅超级管理员）。
 */
import { getAuthMe } from '../api.js';
import { esc, loadingHtml, toast } from '../ui.js';

const GROUPS = [
  {
    label: '任务与告警',
    items: [
      {
        key: 'jobs',
        label: '任务日志',
        permission: 'insights:read',
        module: () => import('./jobs.js'),
        query: { tab: 'jobs', embed: '1' },
      },
      {
        key: 'alerts',
        label: '告警',
        permission: 'insights:read',
        module: () => import('./jobs.js'),
        query: { tab: 'alerts', embed: '1' },
      },
    ],
  },
  {
    label: '运维',
    items: [
      {
        key: 'audit',
        label: '审计日志',
        permission: 'audit:read',
        module: () => import('./team.js'),
        query: { tab: 'audit', only: '1' },
      },
    ],
  },
];

function allowedGroups(me) {
  const perms = me?.permissions || [];
  const open = Boolean(me?.open_mode);
  return GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(
      (item) => open || perms.includes('*') || perms.includes(item.permission),
    ),
  })).filter((group) => group.items.length);
}

function navHtml(groups, current) {
  return groups
    .map(
      (group) => `<div class="hub-nav-group">
        <span class="hub-nav-label">${esc(group.label)}</span>
        ${group.items
          .map(
            (item) =>
              `<button class="hub-nav-item ${
                item.key === current ? 'is-active' : ''
              }" type="button" data-hub-item="${esc(item.key)}" aria-current="${
                item.key === current ? 'page' : 'false'
              }">${esc(item.label)}</button>`,
          )
          .join('')}
      </div>`,
    )
    .join('');
}

function writeHash(key) {
  const hash = key === 'jobs' ? '#/ops' : `#/ops?tab=${key}`;
  history.replaceState(null, '', hash);
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;

  root.innerHTML = `<div class="page hub-page settings-shell" id="opsHub">
    <aside class="hub-subnav" id="opsSubnav">${loadingHtml('加载中…', 'state-compact')}</aside>
    <div class="hub-content" id="opsContent">${loadingHtml('正在加载运维管理…')}</div>
  </div>`;
  const nav = root.querySelector('#opsSubnav');
  const content = root.querySelector('#opsContent');

  let me = null;
  try {
    me = await getAuthMe();
  } catch (err) {
    if (!alive) return undefined;
    nav.innerHTML = '';
    content.innerHTML = `<div class="state state-error"><h3>无法加载运维管理</h3><p>${esc(
      err?.message || '请稍后重试',
    )}</p></div>`;
    return undefined;
  }
  if (!alive) return undefined;

  const groups = allowedGroups(me);
  if (!groups.length) {
    nav.innerHTML = '';
    content.innerHTML = `<div class="state state-empty"><h3>当前账号没有运维管理权限</h3><p>请联系超级管理员调整角色。</p></div>`;
    return undefined;
  }

  const allItems = groups.flatMap((group) => group.items);
  const requested = options.query?.get('tab');
  current = allItems.some((item) => item.key === requested) ? requested : allItems[0].key;
  nav.innerHTML = navHtml(groups, current);

  async function switchItem(key, { updateHash = true } = {}) {
    const item = allItems.find((entry) => entry.key === key);
    if (!item) return;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 运维子视图清理失败', err);
      }
    }
    cleanup = null;
    current = key;
    if (updateHash) writeHash(key);
    nav.querySelectorAll('[data-hub-item]').forEach((button) => {
      const active = button.dataset.hubItem === key;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-current', active ? 'page' : 'false');
    });
    content.innerHTML = loadingHtml('正在加载…');

    try {
      const module = await item.module();
      if (!alive || current !== key) return;
      const result = module.mount?.(content, {
        query: new URLSearchParams(item.query || {}),
      });
      cleanup = typeof result === 'function' ? result : null;
    } catch (err) {
      if (!alive || current !== key) return;
      content.innerHTML = `<div class="state state-error"><h3>页面加载失败</h3><p>${esc(
        err?.message || '请稍后重试',
      )}</p></div>`;
      toast(err?.message || '运维子页面加载失败', 'error');
    }
  }

  nav.addEventListener('click', (event) => {
    const button = event.target.closest('[data-hub-item]');
    if (button) void switchItem(button.dataset.hubItem);
  });

  await switchItem(current, { updateHash: false });

  return () => {
    alive = false;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 运维视图清理失败', err);
      }
    }
  };
}

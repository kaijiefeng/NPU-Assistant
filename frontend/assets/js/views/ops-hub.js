/**
 * views/ops-hub.js — 运维管理（任务 / 告警 / 审计）
 *
 * 三个直接入口复用现有子视图；修复任务继续交给管理助手。
 */
import { getAuthMe } from '../api.js';
import { mountHubNavigation } from '../hub-navigation.js';
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

function writeHash(key) {
  const hash = key === 'jobs' ? '#/ops' : `#/ops?tab=${key}`;
  history.replaceState(null, '', hash);
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;
  let switchToken = 0;
  let navigation = null;

  root.innerHTML = `<div class="page hub-page settings-shell" id="opsHub">
    <nav class="hub-navigation" id="opsSubnav">${loadingHtml('加载中…', 'state-compact')}</nav>
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
  navigation = mountHubNavigation(nav, {
    groups: allItems.map((item) => ({ key: item.key, label: item.key === 'jobs' ? '任务' : item.key === 'audit' ? '审计' : item.label, items: [item] })),
    current, label: '运维导航', onSelect: (key) => { void switchItem(key); },
  });

  async function switchItem(key, { updateHash = true } = {}) {
    const item = allItems.find((entry) => entry.key === key);
    if (!item) return;
    const token = ++switchToken;
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
    navigation.update(key);
    content.innerHTML = loadingHtml('正在加载…');

    try {
      const module = await item.module();
      if (!alive || token !== switchToken) return;
      const result = await module.mount?.(content, {
        query: new URLSearchParams(item.query || {}),
      });
      if (!alive || token !== switchToken) {
        if (typeof result === 'function') result();
        return;
      }
      cleanup = typeof result === 'function' ? result : null;
    } catch (err) {
      if (!alive || token !== switchToken) return;
      content.innerHTML = `<div class="state state-error"><h3>页面加载失败</h3><p>${esc(
        err?.message || '请稍后重试',
      )}</p></div>`;
      toast(err?.message || '运维子页面加载失败', 'error');
    }
  }

  await switchItem(current, { updateHash: false });

  return () => {
    alive = false;
    navigation?.destroy();
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 运维视图清理失败', err);
      }
    }
  };
}

/**
 * views/kb-admin.js — 知识库管理（macOS 设置页式布局）
 *
 * 左侧分组子导航（内容管理 / 服务台），右侧内容区。子页面以「外部投射」
 * 模式挂载（隐藏各自内部 tab，由本页子导航控制当前页）——整个应用层级
 * 始终只有两级：全局侧栏 + 页内子导航，不再是 tab 套 tab。
 */
import { getAuthMe } from '../api.js';
import { esc, loadingHtml, toast } from '../ui.js';

function deskItem(key, label) {
  return {
    key,
    label,
    permission: 'insights:read',
    module: () => import('./insights.js'),
    query: { section: key, embed: '1' },
  };
}

const GROUPS = [
  {
    label: '内容管理',
    items: [
      {
        key: 'pages',
        label: '词条维护',
        permission: 'content:write',
        module: () => import('./knowledge.js'),
        mountName: 'mountManage',
        query: { tab: 'pages', embed: '1' },
      },
      {
        key: 'sources',
        label: '源文件',
        permission: 'content:write',
        module: () => import('./knowledge.js'),
        mountName: 'mountManage',
        query: { tab: 'sources', embed: '1' },
      },
    ],
  },
  {
    label: '服务台',
    items: [
      deskItem('overview', '概览'),
      deskItem('handoffs', '待人工'),
      deskItem('crisis', '危机'),
      deskItem('gaps', '知识缺口'),
      deskItem('feedback', '反馈'),
      deskItem('freshness', '时效'),
      deskItem('subscriptions', '订阅发现'),
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
  const hash = key === 'pages' ? '#/kb-admin' : `#/kb-admin?tab=${key}`;
  history.replaceState(null, '', hash);
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;

  root.innerHTML = `<div class="page hub-page settings-shell" id="kbAdminHub">
    <aside class="hub-subnav" id="kbSubnav">${loadingHtml('加载中…', 'state-compact')}</aside>
    <div class="hub-content" id="kbContent">${loadingHtml('正在加载知识库管理…')}</div>
  </div>`;
  const nav = root.querySelector('#kbSubnav');
  const content = root.querySelector('#kbContent');

  let me = null;
  try {
    me = await getAuthMe();
  } catch (err) {
    if (!alive) return undefined;
    nav.innerHTML = '';
    content.innerHTML = `<div class="state state-error"><h3>无法加载知识库管理</h3><p>${esc(
      err?.message || '请稍后重试',
    )}</p></div>`;
    return undefined;
  }
  if (!alive) return undefined;

  const groups = allowedGroups(me);
  if (!groups.length) {
    nav.innerHTML = '';
    content.innerHTML = `<div class="state state-empty"><h3>当前账号没有知识库管理权限</h3><p>请联系超级管理员调整角色。</p></div>`;
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
        console.warn('[NPU-Assistant] 知识库管理子视图清理失败', err);
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
      const mountName = item.mountName || 'mount';
      const mountFn = typeof module[mountName] === 'function' ? module[mountName] : module.mount;
      const result = mountFn?.(content, {
        query: new URLSearchParams(item.query || {}),
        canManage: true,
      });
      cleanup = typeof result === 'function' ? result : null;
    } catch (err) {
      if (!alive || current !== key) return;
      content.innerHTML = `<div class="state state-error"><h3>页面加载失败</h3><p>${esc(
        err?.message || '请稍后重试',
      )}</p></div>`;
      toast(err?.message || '知识库管理子页面加载失败', 'error');
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
        console.warn('[NPU-Assistant] 知识库管理视图清理失败', err);
      }
    }
  };
}

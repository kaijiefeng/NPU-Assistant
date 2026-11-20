/**
 * views/kb-admin.js — 知识库管理（任务分组导航）
 *
 * 顶部按内容资料、服务台分组，当前组用选择器切换子页。
 */
import { getAuthMe } from '../api.js';
import { mountHubNavigation } from '../hub-navigation.js';
import { esc, loadingHtml, toast } from '../ui.js';

function deskItem(key, label, section = '') {
  return {
    key,
    label,
    section,
    permission: 'insights:read',
    module: () => import('./insights.js'),
    query: { section: key, embed: '1' },
  };
}

const GROUPS = [
  {
    key: 'content',
    label: '内容资料',
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
    key: 'desk',
    label: '服务台',
    items: [
      deskItem('overview', '概览'),
      deskItem('handoffs', '待人工', '人工处置'),
      deskItem('crisis', '危机', '人工处置'),
      deskItem('gaps', '知识缺口', '知识维护'),
      deskItem('subscriptions', '订阅发现', '知识维护'),
      deskItem('freshness', '时效', '知识维护'),
      deskItem('feedback', '反馈', '知识维护'),
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
  const hash = key === 'pages' ? '#/kb-admin' : `#/kb-admin?tab=${key}`;
  history.replaceState(null, '', hash);
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;
  let switchToken = 0;
  let navigation = null;

  root.innerHTML = `<div class="page hub-page settings-shell" id="kbAdminHub">
    <nav class="hub-navigation" id="kbSubnav">${loadingHtml('加载中…', 'state-compact')}</nav>
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
  const legacy = options.query?.get('tab');
  const requested = ({ content: 'pages', desk: 'overview' })[legacy] || legacy;
  current = allItems.some((item) => item.key === requested) ? requested : allItems[0].key;
  navigation = mountHubNavigation(nav, { groups, current, label: '知识管理导航', onSelect: (key) => { void switchItem(key); } });

  async function switchItem(key, { updateHash = true } = {}) {
    const item = allItems.find((entry) => entry.key === key);
    if (!item) return;
    const token = ++switchToken;
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
    navigation.update(key);
    content.innerHTML = loadingHtml('正在加载…');

    try {
      const module = await item.module();
      if (!alive || token !== switchToken) return;
      const mountName = item.mountName || 'mount';
      const mountFn = typeof module[mountName] === 'function' ? module[mountName] : module.mount;
      const result = await mountFn?.(content, {
        query: new URLSearchParams(item.query || {}),
        canManage: true,
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
      toast(err?.message || '知识库管理子页面加载失败', 'error');
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
        console.warn('[NPU-Assistant] 知识库管理视图清理失败', err);
      }
    }
  };
}

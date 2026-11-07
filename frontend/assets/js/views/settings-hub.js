/**
 * views/settings-hub.js — 设置聚合页
 *
 * 助手设置 / 模型 / 机器人 / 成员与权限 / 我的账号。
 * 只显示当前角色可访问的 tab；子视图复用现有模块。
 */
import { changePassword, getAuthMe, logoutAdmin } from '../api.js';
import { esc, icon, loadingHtml, openModal, toast } from '../ui.js';

const TABS = [
  { key: 'assistant', label: '助手设置', permission: 'settings:write', load: () => import('./settings.js') },
  { key: 'models', label: '模型', permission: 'model:write', load: () => import('./models.js') },
  { key: 'bots', label: '机器人', permission: 'bot:write', load: () => import('./bots.js') },
  {
    key: 'members',
    label: '成员与权限',
    permission: 'admin:manage',
    load: () => import('./team.js'),
    query: { tab: 'users', only: '1' },
  },
  { key: 'account', label: '我的账号' },
];

const ROLE_LABEL = {
  super_admin: '超级管理员',
  admin: '管理员',
  counselor: '辅导员',
  viewer: '只读',
};

function allowed(me, tab) {
  if (!tab.permission) return true;
  const perms = me?.permissions || [];
  return Boolean(me?.open_mode) || perms.includes('*') || perms.includes(tab.permission);
}

function tabsHtml(tabs, current) {
  return `<div class="seg-tabs hub-tabs" role="tablist" aria-label="设置">
    ${tabs
      .map(
        (tab) =>
          `<button class="seg-tab ${tab.key === current ? 'is-active' : ''}" type="button" role="tab" aria-selected="${
            tab.key === current
          }" data-settings-tab="${tab.key}">${esc(tab.label)}</button>`,
      )
      .join('')}
  </div>`;
}

function authModeLabel(me) {
  if (me?.auth_mode === 'session') return '账号会话';
  if (me?.auth_mode === 'token') return '管理令牌';
  if (me?.open_mode) return '本机演示（未启用账号）';
  return '未登录';
}

function accountHtml(me) {
  const user = me?.user || {};
  return `<section class="card">
    <div class="card-head"><h3>${icon('shield')} 我的账号</h3></div>
    <div class="account-grid">
      <div><span class="muted">显示名称</span><strong>${esc(user.display_name || '—')}</strong></div>
      <div><span class="muted">用户名</span><strong>${esc(user.username || '—')}</strong></div>
      <div><span class="muted">角色</span><strong>${esc(user.role_label || ROLE_LABEL[user.role] || '—')}</strong></div>
      <div><span class="muted">登录方式</span><strong>${authModeLabel(me)}</strong></div>
    </div>
    ${
      me?.auth_mode === 'session'
        ? `<div class="card-actions" style="margin-top:14px">
            <button class="btn btn-primary btn-sm" type="button" data-action="password-self">${icon(
              'key',
            )}<span>修改密码</span></button>
            <button class="btn btn-ghost btn-sm" type="button" data-action="logout">${icon(
              'power',
            )}<span>退出登录</span></button>
          </div>`
        : `<p class="muted">当前使用管理令牌。创建账号并登录后即可在此修改密码。</p>`
    }
  </section>`;
}

function changePasswordModal() {
  const form = document.createElement('form');
  form.innerHTML = `<label class="field"><span class="field-label">当前密码</span>
      <input class="input" name="old_password" type="password" required maxlength="128" autocomplete="current-password" />
    </label>
    <label class="field"><span class="field-label">新密码</span>
      <input class="input" name="new_password" type="password" required minlength="8" maxlength="128" autocomplete="new-password" />
    </label>
    <label class="field"><span class="field-label">确认新密码</span>
      <input class="input" name="confirm" type="password" required minlength="8" maxlength="128" autocomplete="new-password" />
    </label>`;
  const footer = document.createElement('div');
  footer.className = 'modal-actions';
  footer.innerHTML = `<button class="btn btn-ghost" type="button" data-action="cancel">取消</button>
    <button class="btn btn-primary" type="submit">保存</button>`;
  form.appendChild(footer);
  const modal = openModal({ title: '修改我的密码', body: form, size: 'md' });
  footer.querySelector('[data-action="cancel"]').addEventListener('click', modal.close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    if (data.get('new_password') !== data.get('confirm')) {
      toast('两次输入的新密码不一致', 'warning');
      return;
    }
    try {
      await changePassword({
        old_password: String(data.get('old_password') || ''),
        new_password: String(data.get('new_password') || ''),
      });
      modal.close();
      toast('密码已修改', 'success');
    } catch (err) {
      toast(err?.message || '修改失败', 'error');
    }
  });
}

export async function mount(root, options = {}) {
  let alive = true;
  let current = null;
  let cleanup = null;

  root.innerHTML = `<div class="page hub-page" id="settingsHub">${loadingHtml('正在加载设置…')}</div>`;
  const shell = root.querySelector('#settingsHub');

  let me = null;
  try {
    me = await getAuthMe();
  } catch (err) {
    if (!alive) return undefined;
    shell.innerHTML = `<div class="state state-error"><h3>无法加载设置</h3><p>${esc(
      err?.message || '请稍后重试',
    )}</p></div>`;
    return undefined;
  }
  if (!alive) return undefined;

  const tabs = TABS.filter((tab) => allowed(me, tab));
  if (!tabs.length) {
    shell.innerHTML = `<div class="state state-empty"><h3>当前账号没有设置权限</h3><p>请联系超级管理员。</p></div>`;
    return undefined;
  }

  const requested = options.query?.get('tab');
  current = tabs.some((tab) => tab.key === requested) ? requested : tabs[0].key;

  async function switchTab(tab, { updateHash = true } = {}) {
    const target = tabs.find((item) => item.key === tab);
    if (!target) return;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 设置子视图清理失败', err);
      }
    }
    cleanup = null;
    current = tab;
    if (updateHash) history.replaceState(null, '', `#/settings?tab=${tab}`);

    shell.innerHTML = `<div id="settingsTabs">${tabsHtml(tabs, current)}</div>
      <div class="hub-body" id="settingsBody"></div>`;
    const body = shell.querySelector('#settingsBody');
    shell.querySelectorAll('[data-settings-tab]').forEach((button) => {
      button.addEventListener('click', () => switchTab(button.dataset.settingsTab));
    });

    if (tab === 'account') {
      body.innerHTML = accountHtml(me);
      body.querySelector('[data-action="password-self"]')?.addEventListener('click', changePasswordModal);
      body.querySelector('[data-action="logout"]')?.addEventListener('click', () => {
        logoutAdmin()
          .then(() => {
            location.replace('#/login');
            window.location.reload();
          })
          .catch((err) => toast(err?.message || '退出失败', 'error'));
      });
      return;
    }

    body.innerHTML = loadingHtml('正在加载…');
    try {
      const module = await target.load();
      if (!alive || current !== tab) return;
      const childQuery = new URLSearchParams(target.query || {});
      const result = module.mount?.(body, { query: childQuery });
      cleanup = typeof result === 'function' ? result : null;
    } catch (err) {
      if (!alive || current !== tab) return;
      body.innerHTML = `<div class="state state-error"><h3>页面加载失败</h3><p>${esc(
        err?.message || '请稍后重试',
      )}</p></div>`;
      toast(err?.message || '设置子页面加载失败', 'error');
    }
  }

  // 首次渲染：直接复用切换逻辑，但不改写地址栏。
  await switchTab(current, { updateHash: false });

  return () => {
    alive = false;
    if (typeof cleanup === 'function') {
      try {
        cleanup();
      } catch (err) {
        console.warn('[NPU-Assistant] 设置视图清理失败', err);
      }
    }
  };
}

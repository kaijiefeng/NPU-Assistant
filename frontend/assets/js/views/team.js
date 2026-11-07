/**
 * views/team.js — 团队成员与审计日志（v0.2.0 M3）
 */
import {
  changePassword,
  createAdminUser,
  deleteAdminUser,
  getAuditLog,
  getAuthMe,
  listAdminUsers,
  logoutAdmin,
  resetAdminPassword,
  updateAdminUser,
} from '../api.js';
import {
  confirmDialog,
  esc,
  fmtRelative,
  icon,
  loadingHtml,
  openModal,
  toast,
} from '../ui.js';

const ROLE_LABEL = {
  super_admin: '超级管理员',
  admin: '管理员',
  counselor: '辅导员',
  viewer: '只读',
};

const state = {
  tab: 'users',
  locked: false,
  me: null,
  users: [],
  audit: { items: [], total: 0, actions: [] },
  auditDays: 7,
  auditUsername: '',
  auditAction: '',
  loading: false,
};

function can(permission) {
  if (state.me?.open_mode) return true;
  const perms = state.me?.permissions || [];
  return perms.includes('*') || perms.includes(permission);
}

function tabsHtml() {
  if (state.locked) return '';
  const tabs = [];
  if (can('admin:manage')) tabs.push(['users', `成员 (${state.users.length})`]);
  if (can('audit:read')) tabs.push(['audit', '审计日志']);
  if (state.me?.authenticated || state.me?.open_mode) tabs.push(['account', '我的账号']);
  return `<div class="insights-tabs">${tabs
    .map(
      ([key, label]) =>
        `<button class="insights-tab ${state.tab === key ? 'is-active' : ''}" type="button" data-action="team-tab" data-tab="${key}">${esc(label)}</button>`,
    )
    .join('')}</div>`;
}

function accountHtml() {
  const user = state.me?.user || {};
  const authMode =
    state.me?.auth_mode === 'session'
      ? '账号会话'
      : state.me?.auth_mode === 'token'
        ? '管理令牌'
        : state.me?.open_mode
          ? '本机演示（未启用账号）'
          : '未登录';
  return `<section class="card">
    <div class="card-head"><h3>${icon('shield')} 我的账号</h3></div>
    <div class="account-grid">
      <div><span class="muted">显示名称</span><strong>${esc(user.display_name || '—')}</strong></div>
      <div><span class="muted">用户名</span><strong>${esc(user.username || '—')}</strong></div>
      <div><span class="muted">角色</span><strong>${esc(user.role_label || ROLE_LABEL[user.role] || '—')}</strong></div>
      <div><span class="muted">登录方式</span><strong>${authMode}</strong></div>
    </div>
    ${
      state.me?.auth_mode === 'session'
        ? `<div class="card-actions" style="margin-top:14px">
            <button class="btn btn-primary btn-sm" type="button" data-action="password-self">${icon(
              'key',
            )}<span>修改密码</span></button>
            <button class="btn btn-ghost btn-sm" type="button" data-action="logout">${icon(
              'power',
            )}<span>退出登录</span></button>
          </div>`
        : `<p class="muted">当前使用管理令牌，无法修改个人密码。建议创建账号后改用账号登录。</p>`
    }
  </section>`;
}

function usersHtml() {
  if (!can('admin:manage')) {
    return `<div class="state state-empty"><h3>没有成员管理权限</h3><p>请联系超级管理员。</p></div>`;
  }
  const selfId = state.me?.user?.id;
  return `<section class="card">
    <div class="card-head">
      <div>
        <h3>${icon('shield')} 团队成员</h3>
        <p class="muted" style="margin:4px 0 0">角色决定可执行的操作。至少保留一名启用的超级管理员。</p>
      </div>
      <div class="chip-row">
        <button class="btn btn-sm btn-ghost" type="button" data-action="password-self">${icon(
          'key',
        )}<span>修改我的密码</span></button>
        <button class="btn btn-sm btn-primary" type="button" data-action="user-add">${icon(
          'plus',
        )}<span>新增成员</span></button>
      </div>
    </div>
    <div class="table-wrap">
      <table class="table">
        <thead><tr><th>成员</th><th>角色</th><th>状态</th><th>最近登录</th><th class="th-actions">操作</th></tr></thead>
        <tbody>${state.users
          .map((user) => {
            const self = user.id === selfId;
            return `<tr>
              <td>
                <strong>${esc(user.display_name || user.username)}</strong>
                <div class="muted" style="font-size:12px">${esc(user.username)}${self ? ' · 当前账号' : ''}</div>
              </td>
              <td>
                <select class="input input-sm" data-action="user-role" data-id="${esc(
                  user.id,
                )}" ${self && user.role === 'super_admin' ? 'disabled title="不能修改自己的角色"' : ''}>
                  ${Object.entries(ROLE_LABEL)
                    .map(
                      ([key, label]) =>
                        `<option value="${key}" ${user.role === key ? 'selected' : ''}>${label}</option>`,
                    )
                    .join('')}
                </select>
              </td>
              <td><span class="badge ${user.active ? '' : 'is-off'}">${user.active ? '启用' : '停用'}</span></td>
              <td class="muted">${esc(fmtRelative(user.last_login_at) || '—')}</td>
              <td class="td-actions">
                <button class="btn btn-xs btn-ghost" type="button" data-action="user-reset" data-id="${esc(
                  user.id,
                )}" data-name="${esc(user.username)}">${icon('key')}<span>重置密码</span></button>
                ${
                  self
                    ? ''
                    : `<button class="btn btn-xs btn-ghost" type="button" data-action="user-toggle" data-id="${esc(
                        user.id,
                      )}" data-active="${user.active ? '1' : '0'}">${icon('power')}<span>${
                        user.active ? '停用' : '启用'
                      }</span></button>
                    <button class="btn btn-xs btn-ghost" type="button" data-action="user-delete" data-id="${esc(
                      user.id,
                    )}" data-name="${esc(user.username)}">${icon('x')}<span>删除</span></button>`
                }
              </td>
            </tr>`;
          })
          .join('')}</tbody>
      </table>
    </div>
  </section>`;
}

function auditHtml() {
  const { items, total, actions } = state.audit;
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('history')} 审计日志</h3>
      <div class="chip-row">
        <select class="input input-sm" id="auditDays">
          ${[1, 7, 30, 90]
            .map(
              (days) =>
                `<option value="${days}" ${state.auditDays === days ? 'selected' : ''}>近 ${days} 天</option>`,
            )
            .join('')}
        </select>
        <button class="btn btn-xs btn-ghost" type="button" data-action="audit-reload">${icon(
          'refresh',
        )}<span>刷新</span></button>
      </div>
    </div>
    <div class="audit-filters">
      <input class="input input-sm" id="auditUsername" placeholder="按账号筛选" value="${esc(
        state.auditUsername,
      )}" />
      <select class="input input-sm" id="auditAction">
        <option value="">全部操作</option>
        ${(actions || [])
          .map(
            (action) =>
              `<option value="${esc(action)}" ${
                state.auditAction === action ? 'selected' : ''
              }>${esc(action)}</option>`,
          )
          .join('')}
      </select>
      <span class="muted">共 ${total} 条</span>
    </div>
    ${
      items.length
        ? `<div class="table-wrap">
            <table class="table">
              <thead><tr><th>时间</th><th>账号</th><th>操作</th><th>对象</th><th>结果</th><th>IP</th></tr></thead>
              <tbody>${items
                .map(
                  (item) => `<tr>
                    <td class="muted" title="${esc(item.created_at || '')}">${esc(
                      fmtRelative(item.created_at) || item.created_at || '',
                    )}</td>
                    <td>${esc(item.username || '—')}<div class="muted" style="font-size:12px">${esc(
                      ROLE_LABEL[item.role] || item.role || '',
                    )}</div></td>
                    <td><code class="code-inline">${esc(item.action)}</code></td>
                    <td class="muted">${esc(item.target_id || item.target_type || '—')}</td>
                    <td>${esc(item.detail || '—')}</td>
                    <td class="muted">${esc(item.ip || '—')}</td>
                  </tr>`,
                )
                .join('')}</tbody>
            </table>
          </div>`
        : `<p class="muted">所选条件下暂无审计记录。</p>`
    }
  </section>`;
}

function bodyHtml() {
  if (state.loading && !state.users.length) return loadingHtml('加载团队数据…');
  if (state.tab === 'audit') return auditHtml();
  if (state.tab === 'account') return accountHtml();
  return usersHtml();
}

function addUserModal(reload) {
  const form = document.createElement('form');
  form.innerHTML = `<label class="field"><span class="field-label">用户名</span>
      <input class="input" name="username" required minlength="3" maxlength="32" placeholder="例如：zhangsan" />
    </label>
    <label class="field"><span class="field-label">显示名称</span>
      <input class="input" name="display_name" maxlength="60" placeholder="例如：学工办 张老师" />
    </label>
    <label class="field"><span class="field-label">角色</span>
      <select class="input" name="role">
        ${Object.entries(ROLE_LABEL)
          .map(([key, label]) => `<option value="${key}" ${key === 'counselor' ? 'selected' : ''}>${label}</option>`)
          .join('')}
      </select>
    </label>
    <label class="field"><span class="field-label">初始密码</span>
      <input class="input" name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password" />
    </label>
    <p class="muted">成员首次登录后应自行修改密码。辅导员可处理工单与服务台，只读账号只能查看。</p>`;
  const footer = document.createElement('div');
  footer.className = 'modal-actions';
  footer.innerHTML = `<button class="btn btn-ghost" type="button" data-action="cancel">取消</button>
    <button class="btn btn-primary" type="submit">创建成员</button>`;
  form.appendChild(footer);
  const modal = openModal({ title: '新增团队成员', body: form, size: 'md' });
  footer.querySelector('[data-action="cancel"]').addEventListener('click', modal.close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await createAdminUser({
        username: String(data.get('username') || '').trim(),
        display_name: String(data.get('display_name') || '').trim(),
        role: String(data.get('role') || 'counselor'),
        password: String(data.get('password') || ''),
      });
      modal.close();
      toast('成员已创建', 'success');
      await reload();
    } catch (err) {
      toast(err?.message || '创建失败', 'error');
    }
  });
}

function resetPasswordModal(user, reload) {
  const form = document.createElement('form');
  form.innerHTML = `<label class="field"><span class="field-label">新密码</span>
      <input class="input" name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password" />
    </label>
    <label class="field-inline">
      <input type="checkbox" name="must_change" checked />
      <span>要求下次登录后修改</span>
    </label>
    <p class="muted">重置后该成员的所有登录会话会被注销。</p>`;
  const footer = document.createElement('div');
  footer.className = 'modal-actions';
  footer.innerHTML = `<button class="btn btn-ghost" type="button" data-action="cancel">取消</button>
    <button class="btn btn-primary" type="submit">重置密码</button>`;
  form.appendChild(footer);
  const modal = openModal({ title: `重置 ${user.username} 的密码`, body: form, size: 'md' });
  footer.querySelector('[data-action="cancel"]').addEventListener('click', modal.close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await resetAdminPassword(user.id, {
        password: String(data.get('password') || ''),
        must_change: data.get('must_change') === 'on',
      });
      modal.close();
      toast('密码已重置', 'success');
      await reload();
    } catch (err) {
      toast(err?.message || '重置失败', 'error');
    }
  });
}

function changeOwnPasswordModal(reload) {
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
      await reload();
    } catch (err) {
      toast(err?.message || '修改失败', 'error');
    }
  });
}

export function mount(root, options = {}) {
  const requestedTab = options.query?.get('tab');
  if (['users', 'audit', 'account'].includes(requestedTab)) state.tab = requestedTab;
  state.locked = options.query?.get('only') === '1';
  const auditOnly = state.locked && state.tab === 'audit';
  let alive = true;
  root.innerHTML = `<div class="insights-page" id="teamPage">
    ${
      auditOnly
        ? ''
        : `<div class="card" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div>
        <h3 style="margin:0">${icon('shield')} 团队与审计</h3>
        <p class="muted" style="margin:4px 0 0">管理登录账号、角色权限，并追溯所有管理操作。</p>
      </div>
      <button class="btn btn-ghost btn-sm" type="button" data-action="team-reload">${icon(
        'refresh',
      )}<span>刷新</span></button>
    </div>`
    }
    <div id="teamTabs"></div>
    <div id="teamBody"></div>
  </div>`;
  const page = root.querySelector('#teamPage');
  const tabs = root.querySelector('#teamTabs');
  const body = root.querySelector('#teamBody');

  function render() {
    tabs.innerHTML = tabsHtml();
    body.innerHTML = bodyHtml();
  }

  async function load() {
    state.loading = true;
    render();
    try {
      if (!state.me) state.me = await getAuthMe();
      const canUsers = can('admin:manage');
      const canAudit = can('audit:read');
      if (!canUsers && !canAudit) {
        state.tab = 'account';
        render();
        return;
      }
      if (state.tab === 'users' && !canUsers) state.tab = canAudit ? 'audit' : 'account';
      if (state.tab === 'users' && canUsers) {
        state.users = (await listAdminUsers()).items || [];
      }
      if (state.tab === 'audit' && canAudit) {
        state.audit = await getAuditLog({
          days: state.auditDays,
          username: state.auditUsername,
          action: state.auditAction,
          limit: 100,
        });
      }
    } catch (err) {
      if (alive) {
        toast(err?.message || '加载团队数据失败', 'error');
        if (err?.status === 401) {
          location.replace(`#/login?next=${encodeURIComponent(location.hash)}`);
          return;
        }
      }
    } finally {
      if (alive) {
        state.loading = false;
        render();
      }
    }
  }

  function bindFilters() {
    body.querySelector('#auditDays')?.addEventListener('change', (event) => {
      state.auditDays = Number(event.target.value) || 7;
      load();
    });
    body.querySelector('#auditAction')?.addEventListener('change', (event) => {
      state.auditAction = event.target.value || '';
      load();
    });
    const usernameInput = body.querySelector('#auditUsername');
    usernameInput?.addEventListener('change', () => {
      state.auditUsername = usernameInput.value.trim();
      load();
    });
    body.querySelectorAll('[data-action="user-role"]').forEach((select) => {
      select.addEventListener('change', async () => {
        const user = state.users.find((item) => item.id === select.dataset.id);
        try {
          await updateAdminUser(select.dataset.id, { role: select.value });
          toast(`${user?.username || '成员'} 的角色已更新`, 'success');
          await load();
        } catch (err) {
          toast(err?.message || '角色更新失败', 'error');
          await load();
        }
      });
    });
  }

  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'team-tab':
        state.tab = trigger.dataset.tab || 'users';
        state.loading = false;
        load();
        break;
      case 'team-reload':
        load();
        break;
      case 'user-add':
        addUserModal(load);
        break;
      case 'user-reset': {
        const user = state.users.find((item) => item.id === trigger.dataset.id);
        if (user) resetPasswordModal(user, load);
        break;
      }
      case 'password-self':
        changeOwnPasswordModal(load);
        break;
      case 'logout':
        logoutAdmin()
          .then(() => {
            location.replace('#/login');
            window.location.reload();
          })
          .catch((err) => toast(err?.message || '退出失败', 'error'));
        break;
      case 'user-toggle': {
        const active = trigger.dataset.active !== '1';
        updateAdminUser(trigger.dataset.id, { active })
          .then(() => {
            toast(active ? '成员已启用' : '成员已停用', 'success');
            return load();
          })
          .catch((err) => toast(err?.message || '操作失败', 'error'));
        break;
      }
      case 'user-delete':
        confirmDialog({
          title: '删除成员',
          message: `确定删除 ${trigger.dataset.name} 吗？该操作不可撤销。`,
          confirmText: '删除',
          danger: true,
        }).then(async (ok) => {
          if (!ok) return;
          try {
            await deleteAdminUser(trigger.dataset.id);
            toast('成员已删除', 'success');
            await load();
          } catch (err) {
            toast(err?.message || '删除失败', 'error');
          }
        });
        break;
      default:
        break;
    }
  });

  load();
  // Filters are re-created on every render; bind after each render.
  const observer = new MutationObserver(bindFilters);
  observer.observe(body, { childList: true });

  return () => {
    alive = false;
    observer.disconnect();
  };
}

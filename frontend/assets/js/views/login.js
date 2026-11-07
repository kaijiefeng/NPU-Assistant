/**
 * views/login.js — 管理员登录 / 首次初始化（v0.2.0 M3）
 */
import {
  getAuthMe,
  loginAdmin,
  setAdminToken,
  setCsrfToken,
  setupAdmin,
} from '../api.js';
import { icon, loadingHtml, toast } from '../ui.js';

const state = { me: null, mode: 'login', error: '', busy: false };

function authChanged() {
  window.dispatchEvent(new CustomEvent('npu-auth-changed'));
}

function targetHash() {
  const params = new URLSearchParams(String(location.hash.split('?')[1] || ''));
  const next = params.get('next') || '';
  return next.startsWith('#/') ? next : '#/chat';
}

function shellHtml(inner) {
  return `<div class="auth-page">
    <section class="auth-card">
      <div class="auth-brand">${icon('shield')}<span>NPU-Assistant 管理后台</span></div>
      ${inner}
    </section>
    <p class="auth-foot muted">学生问答页无需登录；管理功能需要账号。忘记密码请联系超级管理员重置。</p>
  </div>`;
}

function loginFormHtml(me) {
  const tokenRequired = me.setup_required && me.auth_required;
  return `<h1>登录</h1>
    <p class="muted">使用管理员账号登录，或继续使用原有的管理令牌。</p>
    <form id="loginForm" class="auth-form" autocomplete="on">
      <label class="field"><span class="field-label">用户名</span>
        <input class="input" name="username" autocomplete="username" required maxlength="32" />
      </label>
      <label class="field"><span class="field-label">密码</span>
        <input class="input" name="password" type="password" autocomplete="current-password" required maxlength="128" />
      </label>
      ${
        tokenRequired
          ? `<label class="field"><span class="field-label">管理令牌（本实例已启用）</span>
              <input class="input" name="admin_token" type="password" autocomplete="off" placeholder="初始化首个账号需要" />
            </label>`
          : ''
      }
      <p class="auth-error" id="authError" role="alert"></p>
      <button class="btn btn-primary auth-submit" type="submit">${icon('shield')}<span>登录</span></button>
    </form>
    <details class="auth-token">
      <summary>使用管理令牌进入</summary>
      <div class="auth-token-body">
        <input class="input" id="tokenInput" type="password" autocomplete="off" placeholder="X-Admin-Token" />
        <button class="btn btn-ghost btn-sm" id="tokenSave" type="button">保存并刷新</button>
      </div>
      <p class="muted">令牌仅保存在本机浏览器，适合临时运维；正式使用建议创建个人账号。</p>
    </details>`;
}

function setupFormHtml(me) {
  return `<h1>初始化管理员</h1>
    <p class="muted">首次使用请创建超级管理员账号。密码至少 8 位，请妥善保管。</p>
    <form id="setupForm" class="auth-form" autocomplete="on">
      <label class="field"><span class="field-label">用户名</span>
        <input class="input" name="username" autocomplete="username" required minlength="3" maxlength="32" value="admin" />
      </label>
      <label class="field"><span class="field-label">显示名称</span>
        <input class="input" name="display_name" maxlength="60" placeholder="例如：学工办 张老师" />
      </label>
      <label class="field"><span class="field-label">密码</span>
        <input class="input" name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="128" />
      </label>
      <label class="field"><span class="field-label">确认密码</span>
        <input class="input" name="confirm" type="password" autocomplete="new-password" required minlength="8" maxlength="128" />
      </label>
      ${
        me.auth_required
          ? `<label class="field"><span class="field-label">管理令牌（本实例已启用）</span>
              <input class="input" name="admin_token" type="password" autocomplete="off" required />
            </label>`
          : ''
      }
      <p class="auth-error" id="authError" role="alert"></p>
      <button class="btn btn-primary auth-submit" type="submit">${icon('shield')}<span>创建并登录</span></button>
    </form>
    <p class="auth-alt muted">已有账号？<a href="#/login" id="toLogin">返回登录</a></p>`;
}

function openModeHtml() {
  return `<h1>本机演示模式</h1>
    <p class="muted">当前实例未配置管理员账号或令牌，所有管理功能对本机开放。建议创建管理员账号后再对外提供服务。</p>
    <div class="auth-actions">
      <a class="btn btn-primary" href="#/chat">${icon('chat')}<span>进入助手</span></a>
      <a class="btn btn-ghost" href="#/login?setup=1">${icon('shield')}<span>创建管理员</span></a>
    </div>`;
}

function bodyHtml() {
  if (!state.me) return loadingHtml('检查登录状态…');
  if (state.mode === 'setup') return setupFormHtml(state.me);
  if (state.me.open_mode) return openModeHtml();
  return loginFormHtml(state.me);
}

export function mount(root) {
  let alive = true;
  setCsrfToken('');
  root.innerHTML = shellHtml('<div id="authBody"></div>');
  const body = root.querySelector('#authBody');

  function paint() {
    body.innerHTML = bodyHtml();
    const errorNode = body.querySelector('#authError');
    if (errorNode) errorNode.textContent = state.error;
    bind();
  }

  function setError(message) {
    state.error = message || '';
    const node = body.querySelector('#authError');
    if (node) node.textContent = state.error;
  }

  async function submitLogin(form) {
    const data = new FormData(form);
    const username = String(data.get('username') || '').trim();
    const password = String(data.get('password') || '');
    const token = String(data.get('admin_token') || '').trim();
    if (!username || !password) return setError('请填写用户名和密码');
    if (token) setAdminToken(token);
    state.busy = true;
    try {
      const result = await loginAdmin({ username, password });
      if (!alive) return;
      setCsrfToken(result.csrf_token || '');
      authChanged();
      toast(`欢迎回来，${result.user?.display_name || username}`, 'success');
      location.replace(targetHash());
    } catch (err) {
      setError(err?.message || '登录失败');
    } finally {
      state.busy = false;
    }
  }

  async function submitSetup(form) {
    const data = new FormData(form);
    const username = String(data.get('username') || '').trim();
    const password = String(data.get('password') || '');
    const confirm = String(data.get('confirm') || '');
    const displayName = String(data.get('display_name') || '').trim();
    const token = String(data.get('admin_token') || '').trim();
    if (!/^[a-zA-Z0-9_.-]{3,32}$/.test(username)) {
      return setError('用户名需为 3-32 位字母、数字、点、下划线或短横线');
    }
    if (password.length < 8) return setError('密码至少 8 位');
    if (password !== confirm) return setError('两次输入的密码不一致');
    if (state.me?.auth_required && !token) return setError('请填写管理令牌');
    if (token) setAdminToken(token);
    state.busy = true;
    try {
      const result = await setupAdmin({
        username,
        password,
        display_name: displayName,
      });
      if (!alive) return;
      setCsrfToken(result.csrf_token || '');
      authChanged();
      toast('管理员已创建', 'success');
      location.replace(targetHash());
    } catch (err) {
      setError(err?.message || '初始化失败');
    } finally {
      state.busy = false;
    }
  }

  function bind() {
    body.querySelector('#loginForm')?.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!state.busy) submitLogin(event.currentTarget);
    });
    body.querySelector('#setupForm')?.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!state.busy) submitSetup(event.currentTarget);
    });
    body.querySelector('#toLogin')?.addEventListener('click', (event) => {
      event.preventDefault();
      state.mode = 'login';
      state.error = '';
      history.replaceState(null, '', '#/login');
      paint();
    });
    body.querySelector('#tokenSave')?.addEventListener('click', async () => {
      const value = body.querySelector('#tokenInput')?.value.trim() || '';
      if (!value) return setError('请填写管理令牌');
      setAdminToken(value);
      try {
        const me = await getAuthMe();
        if (!alive) return;
        state.me = me;
        if (me.authenticated) {
          authChanged();
          location.replace(targetHash());
        } else {
          setError('令牌已保存，但服务端未认可；请确认令牌是否正确或账号是否存在');
        }
      } catch (err) {
        setError(err?.message || '令牌校验失败');
      }
    });
  }

  getAuthMe()
    .then((me) => {
      if (!alive) return;
      state.me = me;
      if (me.authenticated) {
        location.replace(targetHash());
        return;
      }
      const wantSetup = new URLSearchParams(String(location.hash.split('?')[1] || '')).has(
        'setup',
      );
      state.mode = wantSetup
        ? 'setup'
        : me.open_mode
          ? 'open'
          : me.setup_required
            ? 'setup'
            : 'login';
      paint();
    })
    .catch((err) => {
      if (!alive) return;
      state.me = { open_mode: false, setup_required: false, auth_required: true };
      state.mode = 'login';
      paint();
      setError(err?.message || '无法连接后端服务');
    });

  return () => {
    alive = false;
  };
}

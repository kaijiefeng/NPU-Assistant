/**
 * views/settings.js — 设置页
 *
 * 分身人设 / 快捷问题 / Wiki 阅读与引用 / 移动端与管理令牌 / 回复尾注。
 * 接口：GET/PUT /api/settings、GET /api/system/info
 */
import {
  clearAdminToken,
  deleteDocument,
  getAdminToken,
  getSettings,
  getSystemInfo,
  initAdminToken,
  saveSettings,
  setAdminToken,
} from '../api.js';
import {
  esc,
  errorHtml,
  icon,
  loadingHtml,
  setButtonBusy,
  toast,
} from '../ui.js';
import { openMobileAccessModal } from '../mobile-access.js';

const state = {
  loaded: false,
  loading: false,
  error: '',
  settings: null,
  snapshot: '',
  serverTokenRequired: null,
};

function collect() {
  const quickRows = Array.from(document.querySelectorAll('[data-quick-row]'));
  const quickQuestions = quickRows
    .map((row) => row.querySelector('[data-quick]')?.value.trim() || '')
    .filter(Boolean);
  return {
    counselor_name: document.getElementById('settingName')?.value.trim() || '',
    welcome_message: document.getElementById('settingWelcome')?.value || '',
    system_prompt: document.getElementById('settingPrompt')?.value || '',
    quick_questions: quickQuestions,
    wiki_max_pages: Number(document.getElementById('settingWikiMaxPages')?.value || 3),
    wiki_answer_max_chars: Number(document.getElementById('settingWikiChars')?.value || 9000),
    citations_enabled: Boolean(document.getElementById('settingCitations')?.checked),
    bot_reply_footer: document.getElementById('settingFooter')?.value || '',
    academic_year: document.getElementById('settingAcademicYear')?.value.trim() || '',
    freshness_stale_days: Number(document.getElementById('settingFreshnessStale')?.value || 180),
    freshness_expiry_warn_days: Number(
      document.getElementById('settingFreshnessWarn')?.value || 30,
    ),
  };
}

function quickRowHtml(value) {
  return `<div class="quick-row" data-quick-row>
    <span class="quick-row-index">${icon('sparkles')}</span>
    <input class="input" data-quick maxlength="120" value="${esc(value)}" placeholder="例如：图书馆开放时间？" />
    <button class="icon-btn is-danger" type="button" data-action="remove-quick" title="删除" aria-label="删除快捷问题">${icon('trash')}</button>
  </div>`;
}

function updateDirty() {
  const current = JSON.stringify(collect());
  const dirty = state.snapshot !== '' && current !== state.snapshot;
  const status = document.getElementById('saveState');
  if (status) {
    status.innerHTML = dirty
      ? `<span class="dot dot-amber"></span>有未保存的修改`
      : `<span class="dot dot-green"></span>已是最新设置`;
    status.classList.toggle('is-dirty', dirty);
  }
  const resetBtn = document.getElementById('resetBtn');
  if (resetBtn) resetBtn.disabled = !dirty;
  const saveBtn = document.getElementById('saveBtn');
  if (saveBtn && !saveBtn.classList.contains('is-busy')) saveBtn.disabled = !dirty;
  return dirty;
}

function bindForm() {
  const form = document.getElementById('settingsForm');
  if (!form) return;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    document.getElementById('saveBtn')?.click();
  });
  form.addEventListener('input', () => {
    updateDirty();
  });
  form.addEventListener('change', () => {
    updateDirty();
  });
}

function fillForm(settings) {
  state.settings = settings;
  const nameInput = document.getElementById('settingName');
  if (nameInput) nameInput.value = settings.counselor_name ?? '';
  const welcome = document.getElementById('settingWelcome');
  if (welcome) welcome.value = settings.welcome_message ?? '';
  const prompt = document.getElementById('settingPrompt');
  if (prompt) prompt.value = settings.system_prompt ?? '';
  const wikiMaxPages = document.getElementById('settingWikiMaxPages');
  if (wikiMaxPages) wikiMaxPages.value = settings.wiki_max_pages ?? 3;
  const wikiChars = document.getElementById('settingWikiChars');
  if (wikiChars) wikiChars.value = settings.wiki_answer_max_chars ?? 9000;
  const citations = document.getElementById('settingCitations');
  if (citations) citations.checked = settings.citations_enabled !== false;
  const footer = document.getElementById('settingFooter');
  if (footer) footer.value = settings.bot_reply_footer ?? '';
  const academicYear = document.getElementById('settingAcademicYear');
  if (academicYear) academicYear.value = settings.academic_year ?? '';
  const freshnessStale = document.getElementById('settingFreshnessStale');
  if (freshnessStale) freshnessStale.value = settings.freshness_stale_days ?? 180;
  const freshnessWarn = document.getElementById('settingFreshnessWarn');
  if (freshnessWarn) freshnessWarn.value = settings.freshness_expiry_warn_days ?? 30;
  const quickList = document.getElementById('quickList');
  if (quickList) {
    const questions = Array.isArray(settings.quick_questions) ? settings.quick_questions : [];
    quickList.innerHTML = questions.length
      ? questions.map((question) => quickRowHtml(String(question))).join('')
      : `<div class="quick-empty" id="quickEmpty">还没有快捷问题，可点击下方按钮添加</div>`;
  }
  state.loaded = true;
  state.snapshot = JSON.stringify(collect());
  updateDirty();
}

function renderShell() {
  return `<div class="page settings-page">
    <div class="seg-tabs" role="tablist" aria-label="设置">
      <button class="seg-tab is-active" type="button" role="tab" aria-selected="true" data-action="settings-tab" data-tab="basic">基础</button>
      <button class="seg-tab" type="button" role="tab" aria-selected="false" data-action="settings-tab" data-tab="security">移动访问与安全</button>
      <button class="seg-tab" type="button" role="tab" aria-selected="false" data-action="settings-tab" data-tab="reply">回复与引用</button>
    </div>

    <form class="settings-layout" id="settingsForm" novalidate>
      <section class="manage-panel" id="settingsBasicPanel">
        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('user')}分身人设</h2>
              <p>名称与提示词会注入到每次对话和机器人回复中</p>
            </div>
          </header>
          <div class="form">
            <label class="field">
              <span class="field-label">分身名称 <em>*</em></span>
              <input class="input" id="settingName" maxlength="40" placeholder="例如：小南" />
            </label>
            <label class="field">
              <span class="field-label">欢迎语</span>
              <textarea class="input" id="settingWelcome" rows="3" placeholder="对话页欢迎语"></textarea>
            </label>
            <label class="field">
              <span class="field-label">系统提示词 <em>*</em></span>
              <textarea class="input textarea-code" id="settingPrompt" rows="12" spellcheck="false"></textarea>
            </label>
          </div>
        </section>

        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('sparkles')}快捷问题</h2>
              <p>显示在对话页欢迎区，最多展示 3 条</p>
            </div>
            <button class="btn btn-ghost btn-sm" type="button" data-action="add-quick">${icon('plus')}<span>添加</span></button>
          </header>
          <div class="quick-list" id="quickList"></div>
        </section>
      </section>

      <section class="manage-panel" id="settingsSecurityPanel" hidden>
        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('qr')}手机扫码访问</h2>
              <p>手机与电脑同一 Wi-Fi 时扫码即用；外网访问可开启 Cloudflare 临时隧道</p>
            </div>
            <button class="btn btn-primary btn-sm" type="button" data-action="open-mobile-access">${icon('qr')}<span>扫码 / 公网访问</span></button>
          </header>
          <div class="form">
            <div class="form-hint">${icon('info')}<span>扫码后在手机浏览器打开；需要管理操作时，在手机设置页填入下方同一管理令牌即可。</span></div>
          </div>
        </section>

        <section class="card admin-token-card" id="adminTokenCard">
          <header class="card-head">
            <div>
              <h2>${icon('shield')}管理令牌</h2>
              <p>公网 / 局域网多人使用时，写操作需要 NPU_ADMIN_TOKEN；令牌仅保存在本机浏览器</p>
            </div>
          </header>
          <div class="form">
            <label class="field">
              <span class="field-label">管理令牌 <span class="field-tip">（与服务端环境变量 NPU_ADMIN_TOKEN 一致）</span></span>
              <div class="token-input-row">
                <input class="input mono" id="adminTokenInput" type="password" autocomplete="new-password" placeholder="未启用令牌时可留空" />
                <button class="icon-btn" type="button" data-action="toggle-token-visibility" title="显示 / 隐藏令牌" aria-label="显示或隐藏令牌">${icon(
                  'eye',
                )}</button>
              </div>
            </label>
            <div class="token-status" id="adminTokenStatus">${icon('info')}<span>读取中…</span></div>
            <div class="token-actions">
              <button class="btn btn-primary btn-sm" type="button" data-action="save-token">${icon('check')}<span>保存令牌</span></button>
              <button class="btn btn-ghost btn-sm" type="button" data-action="enable-server-token" id="enableServerTokenBtn" hidden>${icon('shield')}<span>启用服务端令牌</span></button>
              <button class="btn btn-ghost btn-sm" type="button" data-action="test-token">${icon('link')}<span>测试连接</span></button>
              <button class="btn btn-ghost btn-sm is-danger" type="button" data-action="clear-token">${icon('trash')}<span>清除令牌</span></button>
            </div>
            <div class="form-hint" id="tokenServerHint">${icon('info')}<span>保存后所有 API 请求自动携带 <code>X-Admin-Token</code> 请求头；令牌缺失或不正确时，写操作会返回 401 并提示。</span></div>
          </div>
        </section>
      </section>

      <section class="manage-panel" id="settingsReplyPanel" hidden>
        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('book')}Wiki 阅读与引用</h2>
              <p>控制对话时送入模型的词条数量、读取上限与引用展示</p>
            </div>
          </header>
          <div class="form">
            <div class="form-row">
              <label class="field">
                <span class="field-label">单次读取词条数上限</span>
                <input class="input" id="settingWikiMaxPages" type="number" min="1" max="5" step="1" />
                <span class="field-help">范围 1 ~ 5，建议 3</span>
              </label>
              <label class="field">
                <span class="field-label">回答上下文字符上限</span>
                <input class="input" id="settingWikiChars" type="number" min="2000" max="20000" step="500" />
                <span class="field-help">范围 2000 ~ 20000</span>
              </label>
            </div>
            <label class="field field-inline">
              <input type="checkbox" id="settingCitations" />
              <span>在回答中展示引用词条与来源锚点</span>
            </label>
          </div>
        </section>

        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('clock')}时效与学年</h2>
              <p>过期内容默认不参与问答；回答会按当前日期理解“今年/明年”</p>
            </div>
          </header>
          <div class="form">
            <div class="form-row">
              <label class="field">
                <span class="field-label">当前学年</span>
                <input class="input" id="settingAcademicYear" placeholder="2026-2027" />
                <span class="field-help">用于判断词条是否属于旧学年</span>
              </label>
              <label class="field">
                <span class="field-label">超过多少天未核对视为“可能过时”</span>
                <input class="input" id="settingFreshnessStale" type="number" min="30" max="730" step="10" />
                <span class="field-help">默认 180 天</span>
              </label>
            </div>
            <label class="field">
              <span class="field-label">提前多少天提示“即将过期”</span>
              <input class="input" id="settingFreshnessWarn" type="number" min="1" max="180" step="1" />
              <span class="field-help">默认 30 天</span>
            </label>
          </div>
        </section>

        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('message')}回复尾注</h2>
              <p>附加在 QQ / 微信机器人回复末尾</p>
            </div>
          </header>
          <div class="form">
            <label class="field">
              <span class="field-label">回复尾注</span>
              <textarea class="input" id="settingFooter" rows="3" placeholder="例如：以上信息仅供参考，请以学校官方通知为准。"></textarea>
            </label>
          </div>
        </section>
      </section>
    </form>

    <div class="settings-savebar" id="settingsSavebar">
      <span class="save-state" id="saveState">正在加载设置…</span>
      <div class="savebar-actions">
        <button class="btn btn-ghost" type="button" id="resetBtn" disabled>${icon('refresh')}<span>撤销修改</span></button>
        <button class="btn btn-primary" type="button" id="saveBtn" disabled>${icon('check')}<span>保存设置</span></button>
      </div>
    </div>
  </div>`;
}

async function loadSettings({ silent = false } = {}) {
  const shell = document.getElementById('settingsShell');
  if (shell && !document.getElementById('settingsForm')) shell.innerHTML = loadingHtml('正在加载设置…');
  try {
    const settings = await getSettings();
    if (shell && !document.getElementById('settingsForm')) {
      shell.innerHTML = renderShell();
      bindForm();
      bindActions();
    }
    state.loaded = true;
    state.error = '';
    fillForm(settings);
    initTokenCard();
    const saveBtn = document.getElementById('saveBtn');
    if (saveBtn) saveBtn.disabled = true;
  } catch (err) {
    state.loaded = false;
    state.error = err?.message || '设置加载失败';
    if (shell) {
      shell.innerHTML = `<div class="card">${errorHtml(state.error)}</div>`;
      shell.querySelector('[data-action="retry"]')?.addEventListener('click', () => loadSettings());
    }
    if (!silent) toast(state.error, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* 移动端与管理令牌                                                    */
/* ------------------------------------------------------------------ */
function renderTokenStatus(message = '') {
  const holder = document.getElementById('adminTokenStatus');
  if (!holder) return;
  const token = getAdminToken();
  const parts = [];
  if (message) parts.push(message);
  else parts.push(token ? `已保存令牌（${token.length} 字符）` : '未配置令牌');
  if (state.serverTokenRequired === true) parts.push('服务端已启用令牌校验');
  else if (state.serverTokenRequired === false) parts.push('服务端未启用令牌（本机演示）');
  const ok = Boolean(token) || state.serverTokenRequired === false;
  holder.className = `token-status ${ok ? 'is-ok' : 'is-empty'}`;
  holder.innerHTML = `${icon(ok ? 'shield' : 'info')}<span>${esc(parts.join(' · '))}</span>`;
  updateTokenServerUi();
}

function updateTokenServerUi() {
  const enableBtn = document.getElementById('enableServerTokenBtn');
  const hint = document.getElementById('tokenServerHint');
  const serverOff = state.serverTokenRequired === false;
  if (enableBtn) enableBtn.hidden = !serverOff;
  if (hint) {
    hint.innerHTML = serverOff
      ? `${icon('info')}<span>服务端当前未启用管理令牌。若要开启<b>公网访问</b>或防止他人修改知识库，请在上方输入 ≥16 位随机令牌并点击「启用服务端令牌」；启用后所有管理操作都需要此令牌（会保存到本机，重启后仍有效）。</span>`
      : `${icon('info')}<span>「替换令牌」会同时更新<b>服务端与本机</b>（服务端校验当前令牌，旧令牌立即失效）；新设备只需在上方保存同一令牌。所有 API 请求自动携带 <code>X-Admin-Token</code>。</span>`;
  }
}

async function refreshTokenServerState() {
  try {
    const info = await getSystemInfo();
    state.serverTokenRequired = Boolean(info?.admin_token_required);
  } catch {
    state.serverTokenRequired = null;
  }
  updateTokenInputState();
  renderTokenStatus();
}

function updateTokenInputState() {
  const input = document.getElementById('adminTokenInput');
  const saveBtn = document.querySelector('[data-action="save-token"]');
  const token = getAdminToken();
  if (input) {
    input.value = '';
    input.type = 'password';
    input.placeholder =
      state.serverTokenRequired === false
        ? '输入 ≥16 位随机令牌，保存后同时启用服务端校验'
        : token
          ? `当前已保存 ${token.length} 字符，输入新令牌可替换`
          : '未启用令牌时可留空';
  }
  if (saveBtn) {
    saveBtn.innerHTML = token
      ? `${icon('refresh')}<span>替换令牌</span>`
      : `${icon('check')}<span>保存令牌</span>`;
  }
}

function initTokenCard() {
  updateTokenInputState();
  renderTokenStatus();
  refreshTokenServerState();
}

async function testAdminToken(button) {
  setButtonBusy(button, true, '测试中…');
  try {
    const info = await getSystemInfo();
    state.serverTokenRequired = Boolean(info?.admin_token_required);
    if (!state.serverTokenRequired) {
      renderTokenStatus('服务端未启用管理令牌，当前无需令牌即可操作');
      toast('连接正常：服务端未启用管理令牌；如需公网访问，请在上方输入令牌并保存启用', 'success', { title: '测试连接' });
      return;
    }
    if (!getAdminToken()) {
      renderTokenStatus('服务端已启用令牌，但本机尚未保存');
      toast('服务端已启用管理令牌，请先填写并保存令牌', 'warning', { title: '测试连接' });
      return;
    }
    // 用一个不存在的文档 ID 探测受保护的写接口：401=令牌错误，404=令牌有效
    try {
      await deleteDocument('__npu_admin_token_probe__');
      renderTokenStatus('令牌有效，可执行管理操作');
      toast('令牌有效，管理操作可用', 'success', { title: '测试连接' });
    } catch (err) {
      if (err?.status === 401) {
        renderTokenStatus('令牌不正确，请核对服务端 NPU_ADMIN_TOKEN');
        toast('令牌不正确：服务端拒绝了该令牌', 'error', { title: '测试连接' });
      } else if (err?.status === 404) {
        renderTokenStatus('令牌有效，可执行管理操作');
        toast('令牌有效，管理操作可用', 'success', { title: '测试连接' });
      } else {
        throw err;
      }
    }
  } catch (err) {
    toast(err?.message || '测试连接失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

function bindActions() {
  const saveBtn = document.getElementById('saveBtn');
  const resetBtn = document.getElementById('resetBtn');
  const quickList = document.getElementById('quickList');

  saveBtn?.addEventListener('click', async () => {
    const values = collect();
    if (!values.counselor_name) {
      toast('分身名称不能为空', 'error');
      document.getElementById('settingName')?.focus();
      return;
    }
    if (!values.welcome_message.trim()) {
      toast('欢迎语不能为空', 'error');
      document.getElementById('settingWelcome')?.focus();
      return;
    }
    if (!values.system_prompt.trim()) {
      toast('系统提示词不能为空', 'error');
      document.getElementById('settingPrompt')?.focus();
      return;
    }
    if (!Number.isInteger(values.wiki_max_pages) || values.wiki_max_pages < 1 || values.wiki_max_pages > 5) {
      toast('单次读取词条数需为 1 ~ 5 的整数', 'error');
      document.getElementById('settingWikiMaxPages')?.focus();
      return;
    }
    if (
      !Number.isInteger(values.wiki_answer_max_chars) ||
      values.wiki_answer_max_chars < 2000 ||
      values.wiki_answer_max_chars > 20000
    ) {
      toast('回答上下文字符上限需在 2000 ~ 20000 之间', 'error');
      document.getElementById('settingWikiChars')?.focus();
      return;
    }
    if (values.quick_questions.length > 12) {
      toast('快捷问题建议不超过 12 条', 'warning');
    }
    if (values.academic_year && !/^\d{4}-\d{4}$/.test(values.academic_year)) {
      toast('当前学年格式应为 2026-2027', 'error');
      document.getElementById('settingAcademicYear')?.focus();
      return;
    }
    if (
      !Number.isInteger(values.freshness_stale_days) ||
      values.freshness_stale_days < 30 ||
      values.freshness_stale_days > 730
    ) {
      toast('未核对天数需在 30 ~ 730 之间', 'error');
      return;
    }
    if (
      !Number.isInteger(values.freshness_expiry_warn_days) ||
      values.freshness_expiry_warn_days < 1 ||
      values.freshness_expiry_warn_days > 180
    ) {
      toast('到期提醒天数需在 1 ~ 180 之间', 'error');
      return;
    }
    setButtonBusy(saveBtn, true, '保存中…');
    try {
      const updated = await saveSettings(values);
      fillForm(updated && typeof updated === 'object' ? updated : values);
      saveBtn.disabled = true;
      toast('设置已保存，新的对话将使用最新配置', 'success', { title: '保存成功' });
    } catch (err) {
      toast(err?.message || '保存设置失败', 'error');
    } finally {
      setButtonBusy(saveBtn, false);
      updateDirty();
    }
  });

  resetBtn?.addEventListener('click', () => {
    loadSettings();
    toast('已重新载入服务端设置', 'info', { duration: 2200 });
  });

  quickList?.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action="remove-quick"]');
    if (!trigger) return;
    trigger.closest('[data-quick-row]')?.remove();
    const rows = document.querySelectorAll('[data-quick-row]');
    if (!rows.length && !document.getElementById('quickEmpty')) {
      quickList.insertAdjacentHTML(
        'beforeend',
        `<div class="quick-empty" id="quickEmpty">还没有快捷问题，可点击右上角「添加」</div>`,
      );
    }
    updateDirty();
  });

  document.querySelector('[data-action="add-quick"]')?.addEventListener('click', () => {
    document.getElementById('quickEmpty')?.remove();
    quickList?.insertAdjacentHTML('beforeend', quickRowHtml(''));
    const rows = document.querySelectorAll('[data-quick-row]');
    rows[rows.length - 1]?.querySelector('[data-quick]')?.focus();
    updateDirty();
  });

  // 设置分区 tabs + 手机扫码入口
  const settingsPanels = {
    basic: document.getElementById('settingsBasicPanel'),
    security: document.getElementById('settingsSecurityPanel'),
    reply: document.getElementById('settingsReplyPanel'),
  };
  const switchSettingsTab = (tab) => {
    const next = ['basic', 'security', 'reply'].includes(tab) ? tab : 'basic';
    Object.entries(settingsPanels).forEach(([key, panel]) => {
      if (panel) panel.hidden = key !== next;
    });
    document.querySelectorAll('.settings-page .seg-tab').forEach((btn) => {
      const active = btn.dataset.tab === next;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  };
  document.querySelectorAll('.settings-page .seg-tab').forEach((btn) => {
    btn.addEventListener('click', () => switchSettingsTab(btn.dataset.tab));
  });
  document.querySelector('[data-action="open-mobile-access"]')?.addEventListener('click', () => {
    openMobileAccessModal();
  });

  /* 管理令牌：保存 / 替换 / 清除 / 测试 / 显示隐藏 */
  document.querySelector('[data-action="save-token"]')?.addEventListener('click', async (event) => {
    const input = document.getElementById('adminTokenInput');
    const value = String(input?.value || '').trim();
    if (!value) {
      toast('请填写管理令牌；若服务端未启用令牌，可输入新令牌并保存来启用', 'warning');
      input?.focus();
      return;
    }
    if (state.serverTokenRequired === null) await refreshTokenServerState();
    const button = event.currentTarget;

    // 服务端未启用：保存即同时启用服务端校验
    if (state.serverTokenRequired === false) {
      if (value.length < 16) {
        toast('启用服务端令牌需要至少 16 位字符，建议 24 位以上随机串', 'warning');
        input?.focus();
        return;
      }
      setButtonBusy(button, true, '启用中…');
      try {
        await initAdminToken(value);
        setAdminToken(value);
        await refreshTokenServerState();
        updateTokenInputState();
        renderTokenStatus('服务端令牌已启用，公网访问现在可用');
        toast('服务端令牌已启用并保存；手机端请在设置页填入同一令牌', 'success', {
          title: '公网访问已解锁',
        });
      } catch (err) {
        toast(err?.message || '启用服务端令牌失败', 'error');
      } finally {
        setButtonBusy(button, false);
      }
      return;
    }

    const hadLocalToken = Boolean(getAdminToken());
    if (!hadLocalToken) {
      // 新设备接入已有服务端令牌：只保存到本机，不改动服务端
      if (value.length < 8) {
        toast('令牌长度偏短，建议使用服务端配置的完整随机串', 'warning');
      }
      setAdminToken(value);
      updateTokenInputState();
      renderTokenStatus();
      toast('管理令牌已保存到本机，后续请求将自动携带 X-Admin-Token', 'success', {
        title: '已保存',
      });
      return;
    }

    // 本机已有令牌 → 替换：同时更新服务端与本机（服务端用当前令牌鉴权）
    setButtonBusy(button, true, '替换中…');
    try {
      const result = await initAdminToken(value);
      setAdminToken(value);
      await refreshTokenServerState();
      updateTokenInputState();
      renderTokenStatus(
        result?.rotated ? '服务端与本机令牌已替换，旧令牌已失效' : '',
      );
      toast(
        result?.rotated
          ? '服务端令牌已替换；其他设备需重新填写新令牌'
          : '管理令牌已保存到本机',
        'success',
        { title: result?.rotated ? '已替换' : '已保存' },
      );
    } catch (err) {
      toast(err?.message || '替换服务端令牌失败，本机令牌保持不变', 'error');
    } finally {
      setButtonBusy(button, false);
    }
  });

  document.querySelector('[data-action="enable-server-token"]')?.addEventListener('click', async (event) => {
    const input = document.getElementById('adminTokenInput');
    const value = String(input?.value || '').trim();
    if (value.length < 16) {
      toast('请先输入 ≥16 位随机令牌', 'warning');
      input?.focus();
      return;
    }
    const button = event.currentTarget;
    setButtonBusy(button, true, '启用中…');
    try {
      await initAdminToken(value);
      setAdminToken(value);
      await refreshTokenServerState();
      updateTokenInputState();
      renderTokenStatus('服务端令牌已启用，公网访问现在可用');
      toast('服务端令牌已启用并保存', 'success', { title: '已启用' });
    } catch (err) {
      toast(err?.message || '启用失败', 'error');
    } finally {
      setButtonBusy(button, false);
    }
  });

  document.querySelector('[data-action="clear-token"]')?.addEventListener('click', () => {
    clearAdminToken();
    updateTokenInputState();
    renderTokenStatus();
    toast('已清除本机管理令牌', 'success');
  });

  document.querySelector('[data-action="test-token"]')?.addEventListener('click', (event) => {
    testAdminToken(event.currentTarget);
  });

  document.querySelector('[data-action="toggle-token-visibility"]')?.addEventListener('click', (event) => {
    const input = document.getElementById('adminTokenInput');
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
    event.currentTarget.classList.toggle('is-on', input.type === 'text');
  });
}

/* ------------------------------------------------------------------ */
/* 视图入口                                                            */
/* ------------------------------------------------------------------ */
export function mount(root) {
  root.innerHTML = `<div class="page settings-page-wrap" id="settingsShell">${loadingHtml('正在加载设置…')}</div>`;
  state.loaded = false;
  loadSettings();
  return () => {};
}

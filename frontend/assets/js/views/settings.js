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

function renderPreview() {
  const holder = document.getElementById('settingsPreview');
  if (!holder || !state.loaded) return;
  const values = collect();
  const questions = values.quick_questions.filter(Boolean);
  holder.innerHTML = `<div class="preview-chat">
    <div class="preview-head">
      <div class="preview-avatar">${esc((values.counselor_name || '小南').slice(0, 1))}</div>
      <div>
        <strong>${esc(values.counselor_name || '小南')}</strong>
        <span>辅导员数字分身 · 预览</span>
      </div>
    </div>
    <div class="preview-bubble">${esc(values.welcome_message || '（欢迎语为空）')}</div>
    ${
      questions.length
        ? `<div class="preview-quick">${questions
            .slice(0, 4)
            .map((question) => `<span class="preview-chip">${esc(question)}</span>`)
            .join('')}</div>`
        : '<p class="muted preview-muted">未配置快捷问题</p>'
    }
    <div class="preview-divider"></div>
    <div class="preview-foot-label">机器人回复尾注示例</div>
    <div class="preview-bubble preview-reply">根据《国家奖学金评审办法》，申请需提交申请表、成绩单等材料 [1]。${
      values.bot_reply_footer ? esc(values.bot_reply_footer) : ''
    }</div>
    <div class="preview-tags">
      <span class="chip-mono">词条 ${esc(values.wiki_max_pages)} 个</span>
      <span class="chip-mono">上下文 ${esc(values.wiki_answer_max_chars)} 字</span>
      <span class="chip-mono">引用${values.citations_enabled ? '开启' : '关闭'}</span>
    </div>
  </div>`;
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
    renderPreview();
  });
  form.addEventListener('change', () => {
    updateDirty();
    renderPreview();
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
  renderPreview();
}

function renderShell() {
  return `<div class="page settings-page">
    <form class="settings-layout" id="settingsForm" novalidate>
      <div class="settings-main">
        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('user')}分身人设</h2>
              <p>名称与提示词会注入到每次对话和机器人回复中</p>
            </div>
            <span class="card-tag">身份</span>
          </header>
          <div class="form">
            <label class="field">
              <span class="field-label">分身名称 <em>*</em></span>
              <input class="input" id="settingName" maxlength="40" placeholder="例如：小南" />
            </label>
            <label class="field">
              <span class="field-label">欢迎语</span>
              <textarea class="input" id="settingWelcome" rows="3" placeholder="对话页欢迎语，会显示在首次进入时"></textarea>
            </label>
            <label class="field">
              <span class="field-label">系统提示词 <em>*</em><span class="field-tip">（建议保留「只依据知识库回答」与「标注引用编号」约束）</span></span>
              <textarea class="input textarea-code" id="settingPrompt" rows="12" spellcheck="false"></textarea>
            </label>
          </div>
        </section>

        <section class="card admin-token-card" id="adminTokenCard">
          <header class="card-head">
            <div>
              <h2>${icon('shield')}移动端与管理令牌</h2>
              <p>公网 / 局域网多人使用时，写操作需要 NPU_ADMIN_TOKEN；令牌仅保存在本机浏览器</p>
            </div>
            <span class="card-tag">安全</span>
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
              <button class="btn btn-ghost btn-sm" type="button" data-action="test-token">${icon('link')}<span>测试连接</span></button>
              <button class="btn btn-ghost btn-sm is-danger" type="button" data-action="clear-token">${icon('trash')}<span>清除令牌</span></button>
            </div>
            <div class="form-hint">${icon('info')}<span>保存后所有 API 请求自动携带 <code>X-Admin-Token</code> 请求头；令牌缺失或不正确时，写操作会返回 401 并提示。手机上可先在此登录一次，之后直接使用快速收录与编辑功能。</span></div>
          </div>
        </section>

        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('sparkles')}快捷问题</h2>
              <p>显示在对话页欢迎区，最多建议 6 条；点击即可直接发送</p>
            </div>
            <button class="btn btn-ghost btn-sm" type="button" data-action="add-quick">${icon('plus')}<span>添加</span></button>
          </header>
          <div class="quick-list" id="quickList"></div>
        </section>

        <section class="card">
          <header class="card-head">
            <div>
              <h2>${icon('book')}Wiki 阅读与引用</h2>
              <p>控制对话时送入模型的词条数量、读取上限与引用展示</p>
            </div>
            <span class="card-tag">Wiki</span>
          </header>
          <div class="form">
            <div class="form-row">
              <label class="field">
                <span class="field-label">单次读取词条数上限</span>
                <input class="input" id="settingWikiMaxPages" type="number" min="1" max="5" step="1" />
                <span class="field-help">导航命中的词条最多读取几个，范围 1 ~ 5，建议 3</span>
              </label>
              <label class="field">
                <span class="field-label">回答上下文字符上限</span>
                <input class="input" id="settingWikiChars" type="number" min="2000" max="20000" step="500" />
                <span class="field-help">词条正文送入模型的字符总量，范围 2000 ~ 20000</span>
              </label>
            </div>
            <label class="field field-inline">
              <input type="checkbox" id="settingCitations" />
              <span>在回答中展示引用词条与来源锚点</span>
            </label>
            <label class="field">
              <span class="field-label">机器人回复尾注</span>
              <textarea class="input" id="settingFooter" rows="3" placeholder="附加在 QQ / 微信回复末尾的提示语"></textarea>
            </label>
          </div>
        </section>
      </div>

      <aside class="settings-side">
        <section class="card preview-card">
          <header class="card-head">
            <div>
              <h2>${icon('chat')}实时预览</h2>
              <p>修改后即时反映，保存后对对话页生效</p>
            </div>
          </header>
          <div id="settingsPreview">${loadingHtml('加载设置中…', 'state-compact')}</div>
        </section>
        <section class="card tips-card">
          <header class="card-head">
            <div><h2>${icon('info')}配置建议</h2></div>
          </header>
          <ul class="tips-list">
            <li>提示词中明确「词条没有覆盖时说明 Wiki 未收录，并给出咨询渠道」，可减少编造。</li>
            <li>引用编号由后端自动标注，回答中的 [1][2] 与词条引用卡片一一对应。</li>
            <li>提高「单次读取词条数」会带来更完整的上下文，但也会增加模型调用成本与延迟。</li>
            <li>修改后如对话页仍显示旧欢迎语，重新进入对话页即可刷新。</li>
            <li>Wiki 词条来自源文件编译，建议先在<a href="#/knowledge">知识库</a>上传或导入学生手册相关资料。</li>
          </ul>
        </section>
      </aside>
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
}

async function refreshTokenServerState() {
  try {
    const info = await getSystemInfo();
    state.serverTokenRequired = Boolean(info?.admin_token_required);
  } catch {
    state.serverTokenRequired = null;
  }
  renderTokenStatus();
}

function initTokenCard() {
  const input = document.getElementById('adminTokenInput');
  if (input) input.value = getAdminToken();
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
      toast('连接正常：服务端未启用管理令牌，无需填写', 'success', { title: '测试连接' });
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
    renderPreview();
  });

  document.querySelector('[data-action="add-quick"]')?.addEventListener('click', () => {
    document.getElementById('quickEmpty')?.remove();
    quickList?.insertAdjacentHTML('beforeend', quickRowHtml(''));
    const rows = document.querySelectorAll('[data-quick-row]');
    rows[rows.length - 1]?.querySelector('[data-quick]')?.focus();
    updateDirty();
    renderPreview();
  });

  /* 管理令牌：保存 / 清除 / 测试 / 显示隐藏 */
  document.querySelector('[data-action="save-token"]')?.addEventListener('click', () => {
    const input = document.getElementById('adminTokenInput');
    const value = String(input?.value || '').trim();
    if (!value) {
      toast('请填写管理令牌；若服务端未启用令牌，可直接点击「清除令牌」', 'warning');
      input?.focus();
      return;
    }
    if (value.length < 8) {
      toast('令牌长度偏短，建议使用服务端配置的完整随机串', 'warning');
    }
    setAdminToken(value);
    if (input) input.value = getAdminToken();
    renderTokenStatus();
    toast('管理令牌已保存，后续请求将自动携带 X-Admin-Token', 'success', { title: '已保存' });
  });

  document.querySelector('[data-action="clear-token"]')?.addEventListener('click', () => {
    clearAdminToken();
    const input = document.getElementById('adminTokenInput');
    if (input) {
      input.value = '';
      input.type = 'password';
    }
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

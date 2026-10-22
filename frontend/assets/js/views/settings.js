/**
 * views/settings.js — 设置页
 *
 * 分身名称 / 系统提示词 / 欢迎语 / 快捷问题 / 检索参数 / 引用开关 / 回复尾注。
 * 接口：GET /api/settings、PUT /api/settings
 */
import { getSettings, saveSettings } from '../api.js';
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
    retrieval_top_k: Number(document.getElementById('settingTopK')?.value || 4),
    retrieval_min_score: Number(document.getElementById('settingMinScore')?.value || 0.05),
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
      <span class="chip-mono">top_k ${esc(values.retrieval_top_k)}</span>
      <span class="chip-mono">阈值 ${esc(values.retrieval_min_score)}</span>
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
  const topK = document.getElementById('settingTopK');
  if (topK) topK.value = settings.retrieval_top_k ?? 4;
  const minScore = document.getElementById('settingMinScore');
  if (minScore) minScore.value = settings.retrieval_min_score ?? 0.05;
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
              <h2>${icon('search')}检索与引用</h2>
              <p>控制知识库召回数量、相关度阈值与回答中的引用展示</p>
            </div>
            <span class="card-tag">RAG</span>
          </header>
          <div class="form">
            <div class="form-row">
              <label class="field">
                <span class="field-label">检索条数 top_k</span>
                <input class="input" id="settingTopK" type="number" min="1" max="10" step="1" />
                <span class="field-help">每次问答最多带入的切片数量，建议 3 ~ 6</span>
              </label>
              <label class="field">
                <span class="field-label">相关性阈值</span>
                <input class="input" id="settingMinScore" type="number" min="0" max="1" step="0.01" />
                <span class="field-help">低于该分数的切片视为无命中，建议 0.03 ~ 0.1</span>
              </label>
            </div>
            <label class="field field-inline">
              <input type="checkbox" id="settingCitations" />
              <span>在回答中展示引用来源卡片</span>
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
            <li>提示词中明确「资料没有覆盖时说明知识库未收录，并给出咨询渠道」，可减少编造。</li>
            <li>引用编号由后端自动标注，回答中的 [1][2] 与来源卡片一一对应。</li>
            <li>提高 top_k 会带来更多上下文，但也会增加模型调用成本与延迟。</li>
            <li>修改后如对话页仍显示旧欢迎语，重新进入对话页即可刷新。</li>
            <li>知识库文档决定回答质量，建议先在<a href="#/knowledge">知识库</a>导入与学生手册相关的资料。</li>
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
    if (!Number.isInteger(values.retrieval_top_k) || values.retrieval_top_k < 1 || values.retrieval_top_k > 10) {
      toast('检索条数 top_k 需为 1 ~ 10 的整数', 'error');
      document.getElementById('settingTopK')?.focus();
      return;
    }
    if (
      !Number.isFinite(values.retrieval_min_score) ||
      values.retrieval_min_score < 0 ||
      values.retrieval_min_score > 1
    ) {
      toast('相关性阈值需在 0 ~ 1 之间', 'error');
      document.getElementById('settingMinScore')?.focus();
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

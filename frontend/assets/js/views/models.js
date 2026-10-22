/**
 * views/models.js — 模型管理页
 *
 * Provider 卡片（类型徽标 / 脱敏 Key / 测试连接 / 增删改）+
 * 模型列表（设默认 / 启停 / 测试 / 编辑 / 删除），默认模型高亮。
 * 接口：/api/models、/api/models/providers、/api/health
 */
import {
  createModel,
  createProvider,
  deleteModel,
  deleteProvider,
  getHealth,
  listModels,
  listProviders,
  setDefaultModel,
  testModel,
  testProvider,
  updateModel,
  updateProvider,
} from '../api.js';
import {
  badge,
  confirmDialog,
  emptyHtml,
  errorHtml,
  esc,
  fmtDateTime,
  icon,
  loadingHtml,
  openModal,
  setButtonBusy,
  toast,
} from '../ui.js';

const PROVIDER_TYPES = {
  openai: { label: 'OpenAI 兼容', tone: 'blue', icon: 'cpu', hint: 'base_url 形如 https://api.openai.com/v1，将请求 {base_url}/chat/completions' },
  ollama: { label: 'Ollama 本地', tone: 'teal', icon: 'terminal', hint: 'base_url 形如 http://127.0.0.1:11434，将请求 {base_url}/api/chat' },
  mock: { label: '内置演示', tone: 'violet', icon: 'graduation', hint: '内置演示模型，不需要网络与 API Key，用于离线演示 RAG 链路' },
};

const state = {
  loaded: false,
  loading: false,
  providers: [],
  providersError: '',
  models: [],
  modelsError: '',
  defaultModel: null,
};

function providerMeta(type) {
  return PROVIDER_TYPES[type] || { label: type || '未知类型', tone: 'slate', icon: 'cpu', hint: '' };
}

function providerBadge(type) {
  const meta = providerMeta(type);
  return `<span class="badge badge-${meta.tone}"><span class="badge-dot"></span>${esc(meta.label)}</span>`;
}

function findProvider(providerId) {
  return state.providers.find((item) => item.id === providerId) || null;
}

/* ------------------------------------------------------------------ */
/* 默认模型摘要                                                        */
/* ------------------------------------------------------------------ */
function renderSummary() {
  const holder = document.getElementById('modelsSummary');
  if (!holder) return;
  if (!state.loaded && state.loading) {
    holder.innerHTML = loadingHtml('正在加载模型配置…', 'state-compact');
    return;
  }
  const model = state.defaultModel;
  if (!model) {
    holder.innerHTML = `<div class="summary-empty">
      ${icon('alert')}
      <div><strong>当前没有可用的默认模型</strong>
      <p>对话接口会降级为知识库抽取式回答（fallback=true）。可在下方添加 Provider 与模型后设为默认。</p></div>
      <button class="btn btn-primary btn-sm" type="button" data-action="add-provider">${icon('plus')}<span>添加 Provider</span></button>
    </div>`;
    return;
  }
  const type = model.provider_type;
  holder.innerHTML = `<div class="default-model">
    <div class="default-model-icon">${icon('star')}</div>
    <div class="default-model-main">
      <span class="eyebrow">当前默认模型</span>
      <h2>${esc(model.display_name || model.model_name)}</h2>
      <div class="default-model-meta">
        ${providerBadge(type)}
        <span class="chip-mono">${esc(model.model_name || '')}</span>
        <span>${esc(model.provider_name || (findProvider(model.provider_id)?.name ?? ''))}</span>
        ${model.is_default === 0 || model.enabled === 0 ? badge('已停用将回退', 'amber') : ''}
      </div>
    </div>
    <div class="default-model-stats">
      <div><span>温度</span><strong>${esc(model.temperature ?? '—')}</strong></div>
      <div><span>最大 tokens</span><strong>${esc(model.max_tokens ?? '—')}</strong></div>
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* Provider 列表                                                       */
/* ------------------------------------------------------------------ */
function providerCardHtml(provider) {
  const meta = providerMeta(provider.type);
  const keyText = provider.api_key_masked || (provider.type === 'mock' ? '无需 API Key' : '未配置');
  return `<article class="provider-card" data-provider-id="${esc(provider.id)}">
    <div class="provider-head">
      <span class="provider-logo tone-${esc(meta.tone)}">${icon(meta.icon)}</span>
      <div class="provider-title">
        <strong>${esc(provider.name || '未命名 Provider')}</strong>
        <div class="provider-badges">${providerBadge(provider.type)}
          <span class="muted">${Number(provider.model_count || 0)} 个模型</span>
        </div>
      </div>
      <div class="provider-actions">
        <button class="icon-btn" type="button" data-action="test-provider" data-id="${esc(provider.id)}" title="测试连接" aria-label="测试连接">${icon('play')}</button>
        <button class="icon-btn" type="button" data-action="edit-provider" data-id="${esc(provider.id)}" title="编辑" aria-label="编辑">${icon('edit')}</button>
        <button class="icon-btn is-danger" type="button" data-action="delete-provider" data-id="${esc(provider.id)}" title="删除" aria-label="删除">${icon('trash')}</button>
      </div>
    </div>
    <dl class="provider-meta">
      <div><dt>Base URL</dt><dd class="mono">${esc(provider.base_url || (provider.type === 'mock' ? '本地内置（不联网）' : '—'))}</dd></div>
      <div><dt>API Key</dt><dd class="mono">${esc(keyText)}</dd></div>
      <div><dt>Embedding</dt><dd class="mono">${esc(provider.embedding_model || '—')}</dd></div>
      <div><dt>创建时间</dt><dd>${esc(fmtDateTime(provider.created_at))}</dd></div>
    </dl>
    <div class="provider-test-result" hidden></div>
  </article>`;
}

function renderProviders() {
  const holder = document.getElementById('providerGrid');
  if (!holder) return;
  if (state.providersError) {
    holder.innerHTML = errorHtml(state.providersError);
    return;
  }
  if (!state.loaded) {
    holder.innerHTML = `<div class="provider-grid">${Array.from({ length: 2 })
      .map(() => '<article class="provider-card is-skeleton"><span class="skeleton-line w-50"></span><span class="skeleton-line w-80"></span><span class="skeleton-line w-60"></span></article>')
      .join('')}</div>`;
    return;
  }
  if (!state.providers.length) {
    holder.innerHTML = emptyHtml({
      icon: 'cpu',
      title: '还没有配置 Provider',
      desc: '添加 OpenAI 兼容服务、本地 Ollama，或直接使用内置演示模型。',
      action: `<button class="btn btn-primary" type="button" data-action="add-provider">${icon('plus')}<span>添加 Provider</span></button>`,
    });
    return;
  }
  holder.innerHTML = `<div class="provider-grid">${state.providers.map(providerCardHtml).join('')}</div>`;
}

/* ------------------------------------------------------------------ */
/* 模型表格                                                            */
/* ------------------------------------------------------------------ */
function modelRowHtml(model) {
  const isDefault = Number(model.is_default) === 1;
  const enabled = Number(model.enabled) === 1;
  const provider = findProvider(model.provider_id);
  return `<tr class="${isDefault ? 'is-default' : ''} ${enabled ? '' : 'is-disabled'}" data-model-id="${esc(model.id)}">
    <td data-label="模型">
      <div class="model-cell">
        <span class="model-star ${isDefault ? 'is-on' : ''}">${icon('star')}</span>
        <div class="model-cell-main">
          <strong>${esc(model.display_name || model.model_name)}</strong>
          <span class="model-name mono">${esc(model.model_name || '')}</span>
        </div>
        ${isDefault ? badge('默认', 'green') : ''}
      </div>
    </td>
    <td data-label="Provider">
      <div class="model-provider">
        <strong>${esc(model.provider_name || provider?.name || '未知 Provider')}</strong>
        ${providerBadge(model.provider_type || provider?.type)}
      </div>
    </td>
    <td data-label="参数">
      <span class="chip-mono">temp ${esc(model.temperature ?? '—')}</span>
      <span class="chip-mono">max ${esc(model.max_tokens ?? '—')}</span>
    </td>
    <td data-label="状态">
      <label class="switch" title="${enabled ? '点击停用' : '点击启用'}">
        <input type="checkbox" data-action="toggle-model" data-id="${esc(model.id)}" ${enabled ? 'checked' : ''} />
        <span class="switch-track"><span class="switch-thumb"></span></span>
        <span class="switch-label">${enabled ? '已启用' : '已停用'}</span>
      </label>
    </td>
    <td data-label="操作" class="td-actions">
      ${
        isDefault
          ? `<button class="icon-btn is-on" type="button" disabled title="当前默认模型" aria-label="当前默认模型">${icon('star')}</button>`
          : `<button class="icon-btn" type="button" data-action="set-default" data-id="${esc(model.id)}" title="设为默认" aria-label="设为默认">${icon('star')}</button>`
      }
      <button class="icon-btn" type="button" data-action="test-model" data-id="${esc(model.id)}" title="测试模型" aria-label="测试模型">${icon('play')}</button>
      <button class="icon-btn" type="button" data-action="edit-model" data-id="${esc(model.id)}" title="编辑" aria-label="编辑">${icon('edit')}</button>
      <button class="icon-btn is-danger" type="button" data-action="delete-model" data-id="${esc(model.id)}" title="删除" aria-label="删除">${icon('trash')}</button>
    </td>
  </tr>`;
}

function renderModels() {
  const holder = document.getElementById('modelsBody');
  if (!holder) return;
  const countEl = document.getElementById('modelCount');
  if (countEl) countEl.textContent = state.loaded ? `（${state.models.length}）` : '';
  if (state.modelsError) {
    holder.innerHTML = errorHtml(state.modelsError);
    return;
  }
  if (!state.loaded) {
    holder.innerHTML = loadingHtml('正在加载模型列表…');
    return;
  }
  if (!state.models.length) {
    holder.innerHTML = emptyHtml({
      icon: 'cpu',
      title: '还没有登记模型',
      desc: '先添加 Provider，再为它登记可用的模型（如 gpt-4o-mini、qwen2.5:7b）。',
      action: `<button class="btn btn-primary" type="button" data-action="add-model">${icon('plus')}<span>添加模型</span></button>`,
    });
    return;
  }
  holder.innerHTML = `<div class="table-wrap">
    <table class="table models-table">
      <thead><tr><th>模型</th><th>Provider</th><th>参数</th><th>状态</th><th class="th-actions">操作</th></tr></thead>
      <tbody>${state.models.map(modelRowHtml).join('')}</tbody>
    </table>
  </div>
  <p class="table-note">${icon('info')} 全表至多一个默认模型；停用当前默认模型会自动回退到最早创建的启用模型。</p>`;
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function loadAll({ silent = false } = {}) {
  state.loading = true;
  if (!silent) {
    state.loaded = false;
    renderProviders();
    renderModels();
    renderSummary();
  }
  const [providersResult, modelsResult, healthResult] = await Promise.allSettled([
    listProviders(),
    listModels(),
    getHealth(),
  ]);
  state.providersError =
    providersResult.status === 'fulfilled'
      ? ''
      : providersResult.reason?.message || 'Provider 列表加载失败';
  state.providers =
    providersResult.status === 'fulfilled' && Array.isArray(providersResult.value?.items)
      ? providersResult.value.items
      : [];
  state.modelsError =
    modelsResult.status === 'fulfilled' ? '' : modelsResult.reason?.message || '模型列表加载失败';
  state.models =
    modelsResult.status === 'fulfilled' && Array.isArray(modelsResult.value?.items)
      ? modelsResult.value.items
      : [];
  state.defaultModel = state.models.find((model) => Number(model.is_default) === 1) || null;
  if (!state.defaultModel && healthResult.status === 'fulfilled') {
    const healthModel = healthResult.value?.default_model;
    if (healthModel) state.defaultModel = { ...healthModel, provider_type: healthModel.provider_type };
  }
  state.loaded = true;
  state.loading = false;
  renderProviders();
  renderModels();
  renderSummary();
  if (!silent) {
    if (state.providersError) toast(state.providersError, 'error');
    else if (state.modelsError) toast(state.modelsError, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* Provider 表单                                                       */
/* ------------------------------------------------------------------ */
function providerFormHtml(provider) {
  const editing = Boolean(provider);
  const type = provider?.type || 'openai';
  const options = Object.entries(PROVIDER_TYPES)
    .map(([value, meta]) => `<option value="${value}" ${value === type ? 'selected' : ''}>${esc(meta.label)}</option>`)
    .join('');
  return `<form class="form" id="providerForm" novalidate>
    <div class="form-row">
      <label class="field">
        <span class="field-label">名称 <em>*</em></span>
        <input class="input" name="name" required maxlength="80" placeholder="如：OpenAI 官方 / 本地 Ollama" value="${esc(provider?.name || '')}" />
      </label>
      <label class="field">
        <span class="field-label">类型 <em>*</em></span>
        <select class="input select" name="type" ${editing ? 'disabled' : ''}>
          ${options}
        </select>
      </label>
    </div>
    <label class="field" data-type-field="base_url">
      <span class="field-label">Base URL</span>
      <input class="input mono" name="base_url" placeholder="https://api.openai.com/v1" value="${esc(provider?.base_url || '')}" />
    </label>
    <label class="field" data-type-field="api_key">
      <span class="field-label">API Key ${editing ? '<span class="field-tip">（留空表示不修改）</span>' : ''}</span>
      <input class="input mono" name="api_key" type="password" autocomplete="new-password" placeholder="sk-..." value="" />
    </label>
    <label class="field" data-type-field="embedding_model">
      <span class="field-label">Embedding 模型 <span class="field-tip">（可选，MVP 检索不依赖在线向量）</span></span>
      <input class="input mono" name="embedding_model" placeholder="text-embedding-3-small" value="${esc(provider?.embedding_model || '')}" />
    </label>
    <div class="form-hint" id="providerTypeHint">${icon('info')}<span>${esc(PROVIDER_TYPES[type]?.hint || '')}</span></div>
  </form>`;
}

function openProviderModal(provider) {
  const editing = Boolean(provider);
  const modal = openModal({
    title: editing ? '编辑 Provider' : '添加 Provider',
    subtitle: editing ? '修改连接信息；API Key 留空则保持原值' : '支持 OpenAI 兼容、Ollama 本地与内置演示模型',
    size: 'md',
    body: providerFormHtml(provider),
    footer: `<button class="btn btn-ghost" type="button" data-modal-cancel>取消</button>
      <button class="btn btn-primary" type="button" data-modal-submit>${editing ? '保存修改' : '添加 Provider'}</button>`,
  });

  const form = modal.body.querySelector('#providerForm');
  const typeSelect = form.querySelector('[name="type"]');
  const hint = form.querySelector('#providerTypeHint span');
  const fieldBase = form.querySelector('[data-type-field="base_url"]');
  const fieldKey = form.querySelector('[data-type-field="api_key"]');
  const fieldEmbedding = form.querySelector('[data-type-field="embedding_model"]');

  const syncTypeFields = () => {
    const type = typeSelect.value;
    const meta = PROVIDER_TYPES[type] || {};
    if (hint) hint.textContent = meta.hint || '';
    if (type === 'mock') {
      fieldBase.hidden = true;
      fieldKey.hidden = true;
      fieldEmbedding.hidden = true;
    } else if (type === 'ollama') {
      fieldBase.hidden = false;
      fieldKey.hidden = true;
      fieldEmbedding.hidden = true;
      form.querySelector('[name="base_url"]').placeholder = 'http://127.0.0.1:11434';
    } else {
      fieldBase.hidden = false;
      fieldKey.hidden = false;
      fieldEmbedding.hidden = false;
      form.querySelector('[name="base_url"]').placeholder = 'https://api.openai.com/v1';
      form.querySelector('[name="api_key"]').placeholder = editing ? '留空表示不修改' : 'sk-...';
    }
  };
  typeSelect.addEventListener('change', syncTypeFields);
  syncTypeFields();

  modal.footer.querySelector('[data-modal-cancel]')?.addEventListener('click', () => modal.close());
  modal.footer.querySelector('[data-modal-submit]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const values = {
      name: form.querySelector('[name="name"]').value.trim(),
      type: typeSelect.value,
      base_url: form.querySelector('[name="base_url"]').value.trim(),
      api_key: form.querySelector('[name="api_key"]').value,
      embedding_model: form.querySelector('[name="embedding_model"]').value.trim(),
    };
    if (!values.name) {
      toast('请填写 Provider 名称', 'error');
      form.querySelector('[name="name"]').focus();
      return;
    }
    if (values.type === 'openai') {
      if (!values.base_url) {
        toast('OpenAI 兼容 Provider 必须填写 base_url', 'error');
        return;
      }
      if (!editing && !values.api_key.trim()) {
        toast('新增 OpenAI 兼容 Provider 必须填写 API Key', 'error');
        return;
      }
    }
    if (values.type === 'ollama' && !values.base_url) {
      toast('Ollama Provider 请填写 base_url（如 http://127.0.0.1:11434）', 'error');
      return;
    }
    setButtonBusy(button, true, editing ? '保存中…' : '添加中…');
    try {
      const payload = {
        name: values.name,
        type: values.type,
        base_url: values.base_url,
        api_key: values.api_key.trim(),
        embedding_model: values.embedding_model,
      };
      if (editing) {
        if (!payload.api_key) delete payload.api_key;
        await updateProvider(provider.id, payload);
        toast('Provider 已更新', 'success');
      } else {
        await createProvider(payload);
        toast('Provider 已添加', 'success');
      }
      modal.close();
      await loadAll({ silent: true });
    } catch (err) {
      toast(err?.message || '保存 Provider 失败', 'error');
      setButtonBusy(button, false);
    }
  });
}

/* ------------------------------------------------------------------ */
/* 模型表单                                                            */
/* ------------------------------------------------------------------ */
function openModelModal(model) {
  const editing = Boolean(model);
  const providers = state.providers;
  if (!providers.length) {
    toast('请先添加一个 Provider，再登记模型', 'warning');
    return;
  }
  const selectedProviderId = model?.provider_id || providers[0].id;
  const options = providers
    .map(
      (provider) =>
        `<option value="${esc(provider.id)}" ${provider.id === selectedProviderId ? 'selected' : ''}>${esc(
          provider.name,
        )}（${esc(providerMeta(provider.type).label)}）</option>`,
    )
    .join('');
  const modal = openModal({
    title: editing ? '编辑模型' : '添加模型',
    subtitle: '模型名称需与 Provider 服务端一致，例如 gpt-4o-mini、qwen2.5:7b',
    size: 'md',
    body: `<form class="form" id="modelForm" novalidate>
      <label class="field">
        <span class="field-label">Provider <em>*</em></span>
        <select class="input select" name="provider_id">${options}</select>
      </label>
      <div class="form-row">
        <label class="field">
          <span class="field-label">模型名 <em>*</em></span>
          <input class="input mono" name="model_name" required placeholder="gpt-4o-mini" value="${esc(model?.model_name || '')}" />
        </label>
        <label class="field">
          <span class="field-label">显示名称</span>
          <input class="input" name="display_name" placeholder="如：GPT-4o mini" value="${esc(model?.display_name || '')}" />
        </label>
      </div>
      <div class="form-row">
        <label class="field">
          <span class="field-label">Temperature</span>
          <input class="input" name="temperature" type="number" step="0.1" min="0" max="2" value="${esc(model?.temperature ?? 0.3)}" />
        </label>
        <label class="field">
          <span class="field-label">最大 tokens</span>
          <input class="input" name="max_tokens" type="number" step="1" min="1" max="32768" value="${esc(model?.max_tokens ?? 1200)}" />
        </label>
      </div>
      <label class="field field-inline">
        <input type="checkbox" name="is_default" ${model && Number(model.is_default) === 1 ? 'checked' : ''} />
        <span>设为默认模型</span>
      </label>
      <div class="form-hint">${icon('info')}<span>默认模型会用于 Web 对话与机器人回复；无可用模型时系统自动降级为知识库抽取式回答。</span></div>
    </form>`,
    footer: `<button class="btn btn-ghost" type="button" data-modal-cancel>取消</button>
      <button class="btn btn-primary" type="button" data-modal-submit>${editing ? '保存修改' : '添加模型'}</button>`,
  });

  const form = modal.body.querySelector('#modelForm');
  modal.footer.querySelector('[data-modal-cancel]')?.addEventListener('click', () => modal.close());
  modal.footer.querySelector('[data-modal-submit]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const payload = {
      provider_id: form.querySelector('[name="provider_id"]').value,
      model_name: form.querySelector('[name="model_name"]').value.trim(),
      display_name: form.querySelector('[name="display_name"]').value.trim(),
      temperature: Number(form.querySelector('[name="temperature"]').value || 0.3),
      max_tokens: Number(form.querySelector('[name="max_tokens"]').value || 1200),
      is_default: form.querySelector('[name="is_default"]').checked,
    };
    if (!payload.model_name) {
      toast('请填写模型名', 'error');
      form.querySelector('[name="model_name"]').focus();
      return;
    }
    if (!Number.isFinite(payload.temperature) || payload.temperature < 0 || payload.temperature > 2) {
      toast('Temperature 需在 0 ~ 2 之间', 'error');
      return;
    }
    if (!Number.isInteger(payload.max_tokens) || payload.max_tokens < 1) {
      toast('最大 tokens 需为大于 0 的整数', 'error');
      return;
    }
    setButtonBusy(button, true, editing ? '保存中…' : '添加中…');
    try {
      if (editing) {
        await updateModel(model.id, payload);
        toast('模型已更新', 'success');
      } else {
        await createModel(payload);
        toast('模型已添加', 'success');
      }
      modal.close();
      await loadAll({ silent: true });
    } catch (err) {
      toast(err?.message || '保存模型失败', 'error');
      setButtonBusy(button, false);
    }
  });
}

/* ------------------------------------------------------------------ */
/* Provider 测试                                                       */
/* ------------------------------------------------------------------ */
function paintProviderTest(card, result, startedAt) {
  if (!card) return;
  const holder = card.querySelector('.provider-test-result');
  if (!holder) return;
  const ok = Boolean(result?.ok);
  holder.hidden = false;
  holder.className = `provider-test-result ${ok ? 'is-ok' : 'is-bad'}`;
  const latency = result?.latency_ms ?? Date.now() - startedAt;
  const models = Array.isArray(result?.models) ? result.models : [];
  holder.innerHTML = `<div class="test-line">
      ${icon(ok ? 'check' : 'alert')}
      <strong>${ok ? '连接成功' : '连接失败'}</strong>
      <span class="muted">${esc(result?.message || '')}</span>
      <span class="test-latency">${Math.round(Number(latency) || 0)} ms</span>
    </div>
    ${
      models.length
        ? `<div class="test-models">${models
            .slice(0, 8)
            .map((name) => `<span class="chip-mono">${esc(name)}</span>`)
            .join('')}${models.length > 8 ? `<span class="muted">等 ${models.length} 个</span>` : ''}</div>`
        : ''
    }`;
}

async function runProviderTest(button) {
  const id = button.dataset.id;
  const card = rootProviderCard(button);
  setButtonBusy(button, true);
  const startedAt = Date.now();
  try {
    const result = await testProvider(id);
    paintProviderTest(card, result, startedAt);
    toast(result?.message || (result?.ok ? 'Provider 连接正常' : 'Provider 连接失败'), result?.ok ? 'success' : 'error', {
      title: result?.ok ? '连接测试' : '连接测试失败',
    });
  } catch (err) {
    paintProviderTest(card, { ok: false, message: err?.message || '测试失败' }, startedAt);
    toast(err?.message || '测试连接失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

function rootProviderCard(button) {
  return button.closest('.provider-card');
}

/* ------------------------------------------------------------------ */
/* 模型操作                                                            */
/* ------------------------------------------------------------------ */
async function runModelTest(button) {
  const id = button.dataset.id;
  const model = state.models.find((item) => item.id === id);
  setButtonBusy(button, true);
  try {
    const result = await testModel(id);
    const ok = Boolean(result?.ok);
    const modal = openModal({
      title: ok ? '模型测试成功' : '模型测试失败',
      subtitle: model?.display_name || model?.model_name || '',
      size: 'md',
      body: `<div class="test-detail ${ok ? 'is-ok' : 'is-bad'}">
        <div class="test-line">${icon(ok ? 'check' : 'alert')}<strong>${ok ? 'OK' : '失败'}</strong>
          <span class="muted">${esc(result?.message || '')}</span>
          ${result?.latency_ms !== undefined ? `<span class="test-latency">${Math.round(Number(result.latency_ms) || 0)} ms</span>` : ''}
        </div>
        ${result?.sample ? `<div class="test-sample"><span class="field-label">示例回复</span><pre>${esc(result.sample)}</pre></div>` : ''}
      </div>`,
      footer: '<button class="btn btn-primary" type="button" data-close-modal>知道了</button>',
    });
    modal.footer.querySelector('[data-close-modal]')?.addEventListener('click', () => modal.close());
    toast(result?.message || (ok ? '模型测试成功' : '模型测试失败'), ok ? 'success' : 'error');
  } catch (err) {
    toast(err?.message || '模型测试失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

async function deleteModelItem(id) {
  const model = state.models.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除模型',
    message: `确定删除模型「${model?.display_name || model?.model_name || id}」吗？`,
    detail:
      Number(model?.is_default) === 1
        ? '这是当前默认模型，删除后系统会自动把最早创建的启用模型设为默认。'
        : '删除后不可恢复。',
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteModel(id);
    toast('模型已删除', 'success');
    await loadAll({ silent: true });
  } catch (err) {
    toast(err?.message || '删除模型失败', 'error');
  }
}

async function deleteProviderItem(id) {
  const provider = state.providers.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除 Provider',
    message: `确定删除 Provider「${provider?.name || id}」吗？`,
    detail: '该 Provider 下的全部模型会一并删除，正在使用时会导致对话降级。',
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteProvider(id);
    toast('Provider 已删除', 'success');
    await loadAll({ silent: true });
  } catch (err) {
    toast(err?.message || '删除 Provider 失败', 'error');
  }
}

/* ------------------------------------------------------------------ */
/* 视图入口                                                            */
/* ------------------------------------------------------------------ */
export function mount(root) {
  let alive = true;

  root.innerHTML = `<div class="page models-page">
    <section class="card summary-card" id="modelsSummary">${loadingHtml('正在加载模型配置…', 'state-compact')}</section>

    <section class="card">
      <header class="card-head">
        <div>
          <h2>${icon('cpu')}Provider 列表</h2>
          <p>OpenAI 兼容 / Ollama / 内置演示模型，API Key 仅保存在服务端并脱敏展示</p>
        </div>
        <div class="card-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-action="refresh">${icon('refresh')}<span>刷新</span></button>
          <button class="btn btn-primary btn-sm" type="button" data-action="add-provider">${icon('plus')}<span>添加 Provider</span></button>
        </div>
      </header>
      <div id="providerGrid">${loadingHtml('正在加载 Provider…')}</div>
    </section>

    <section class="card">
      <header class="card-head">
        <div>
          <h2>${icon('sparkles')}模型列表 <span class="count" id="modelCount"></span></h2>
          <p>可设置默认模型、启用 / 停用、测试连通性；停用后不参与默认模型选择</p>
        </div>
        <div class="card-actions">
          <button class="btn btn-primary btn-sm" type="button" data-action="add-model">${icon('plus')}<span>添加模型</span></button>
        </div>
      </header>
      <div id="modelsBody">${loadingHtml('正在加载模型列表…')}</div>
    </section>
  </div>`;

  const page = root.querySelector('.models-page');

  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'refresh':
        loadAll();
        toast('正在刷新模型配置…', 'info', { duration: 1600 });
        break;
      case 'add-provider':
        openProviderModal(null);
        break;
      case 'edit-provider':
        openProviderModal(findProvider(trigger.dataset.id));
        break;
      case 'delete-provider':
        deleteProviderItem(trigger.dataset.id);
        break;
      case 'test-provider':
        runProviderTest(trigger);
        break;
      case 'add-model':
        openModelModal(null);
        break;
      case 'edit-model':
        openModelModal(state.models.find((item) => item.id === trigger.dataset.id) || null);
        break;
      case 'delete-model':
        deleteModelItem(trigger.dataset.id);
        break;
      case 'test-model':
        runModelTest(trigger);
        break;
      case 'set-default':
        setDefault(trigger.dataset.id);
        break;
      case 'retry':
        loadAll();
        break;
      default:
        break;
    }
  });

  page.addEventListener('change', (event) => {
    const input = event.target.closest('[data-action="toggle-model"]');
    if (!input) return;
    toggleModel(input);
  });

  async function setDefault(id) {
    try {
      await setDefaultModel(id);
      toast('已设为默认模型', 'success');
      await loadAll({ silent: true });
    } catch (err) {
      toast(err?.message || '设置默认模型失败', 'error');
    }
  }

  async function toggleModel(input) {
    const id = input.dataset.id;
    const enabled = input.checked;
    input.disabled = true;
    try {
      await updateModel(id, { enabled });
      toast(enabled ? '模型已启用' : '模型已停用', 'success');
      await loadAll({ silent: true });
    } catch (err) {
      input.checked = !enabled;
      toast(err?.message || '切换模型状态失败', 'error');
    } finally {
      input.disabled = false;
    }
  }

  loadAll();

  return () => {
    alive = false;
  };
}

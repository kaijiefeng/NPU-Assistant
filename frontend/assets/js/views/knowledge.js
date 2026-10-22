/**
 * views/knowledge.js — 知识库页
 *
 * 拖拽上传、文档表格（状态徽标）、详情抽屉（切片预览）、检索测试、
 * 删除 / 重建索引 / 一键导入示例资料 / 统计卡片。
 * 接口：/api/knowledge/*
 */
import {
  deleteDocument,
  getDocument,
  getDocumentChunks,
  getKnowledgeStats,
  getSystemInfo,
  listDocuments,
  reindexDocument,
  searchKnowledge,
  seedKnowledge,
  uploadDocument,
} from '../api.js';
import {
  confirmDialog,
  emptyHtml,
  errorHtml,
  esc,
  fmtBytes,
  fmtNumber,
  fmtRelative,
  fmtScore,
  icon,
  loadingHtml,
  openDrawer,
  setButtonBusy,
  toast,
} from '../ui.js';

const FALLBACK_EXTS = ['.md', '.markdown', '.txt', '.pdf', '.docx', '.xlsx', '.csv', '.json', '.html', '.htm'];
const TOP_K_OPTIONS = [1, 2, 3, 4, 5, 6, 8, 10];
const DOC_STATUS = {
  pending: { label: '等待处理', tone: 'slate' },
  processing: { label: '解析中', tone: 'amber' },
  ready: { label: '已就绪', tone: 'green' },
  failed: { label: '解析失败', tone: 'red' },
};

const state = {
  docs: [],
  docsLoaded: false,
  docsError: '',
  stats: null,
  info: null,
  uploads: [],
  searching: false,
  searched: false,
  searchError: '',
  searchQuery: '',
  searchResults: [],
  searchLatency: null,
};

function uid() {
  return `u_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function extOf(filename) {
  const name = String(filename || '');
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index).toLowerCase() : '';
}

function extLabel(ext) {
  return String(ext || '').replace('.', '').toUpperCase().slice(0, 4) || 'FILE';
}

function extClass(ext) {
  return extLabel(ext).toLowerCase().replace(/[^a-z0-9]/g, '') || 'file';
}

function statusMeta(status) {
  return DOC_STATUS[status] || { label: status || '未知', tone: 'slate' };
}

function statusBadge(status, error) {
  const meta = statusMeta(status);
  return `<span class="badge badge-${meta.tone} status-badge" title="${esc(error || meta.label)}">
    <span class="badge-dot"></span>${esc(meta.label)}</span>`;
}

function savedTopK() {
  try {
    const value = Number(localStorage.getItem('npu:searchTopK'));
    if (TOP_K_OPTIONS.includes(value)) return value;
  } catch {
    /* ignore */
  }
  return 4;
}

/* ------------------------------------------------------------------ */
/* 统计卡片                                                            */
/* ------------------------------------------------------------------ */
function computedStats() {
  const docs = state.docs;
  const byType = {};
  let chunks = 0;
  let chars = 0;
  let ready = 0;
  docs.forEach((doc) => {
    chunks += Number(doc.chunk_count || 0);
    chars += Number(doc.char_count || 0);
    if (doc.status === 'ready') ready += 1;
    const ext = doc.file_ext || extOf(doc.filename);
    byType[ext] = (byType[ext] || 0) + 1;
  });
  return { documents: docs.length, ready_documents: ready, chunks, chars, by_file_type: byType };
}

function statCard({ iconName, label, value, hint, extra = '' }) {
  return `<article class="stat-card">
    <div class="stat-icon">${icon(iconName)}</div>
    <div class="stat-body">
      <span class="stat-label">${esc(label)}</span>
      <strong class="stat-value">${value}</strong>
      ${hint ? `<span class="stat-hint">${esc(hint)}</span>` : ''}
    </div>
    ${extra}
  </article>`;
}

function renderStats() {
  const holder = document.getElementById('knowledgeStats');
  if (!holder) return;
  if (!state.docsLoaded && !state.stats) {
    holder.innerHTML = Array.from({ length: 4 })
      .map(
        () => `<article class="stat-card is-skeleton"><span class="skeleton-line w-40"></span><span class="skeleton-line w-70"></span></article>`,
      )
      .join('');
    return;
  }
  const stats = state.stats || computedStats();
  const byType = stats.by_file_type || {};
  const typeChips = Object.entries(byType)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([ext, count]) => `<span class="type-chip">${esc(extLabel(ext))}<b>${Number(count)}</b></span>`)
    .join('');
  const ready = stats.ready_documents ?? computedStats().ready_documents;
  holder.innerHTML = [
    statCard({
      iconName: 'file',
      label: '文档总数',
      value: fmtNumber(stats.documents || 0),
      hint: `已就绪 ${fmtNumber(ready || 0)} 篇`,
    }),
    statCard({
      iconName: 'database',
      label: '知识切片',
      value: fmtNumber(stats.chunks || 0),
      hint: '用于检索与引用',
    }),
    statCard({
      iconName: 'book',
      label: '文本总量',
      value: `${fmtNumber(stats.chars || 0)}`,
      hint: '字符（含标点）',
    }),
    statCard({
      iconName: 'folder',
      label: '文件类型',
      value: String(Object.keys(byType).length || 0),
      hint: '',
      extra: `<div class="stat-types">${typeChips || '<span class="stat-hint">暂无</span>'}</div>`,
    }),
  ].join('');
}

/* ------------------------------------------------------------------ */
/* 上传                                                                */
/* ------------------------------------------------------------------ */
function uploadItemHtml(item) {
  const toneClass = item.status === 'uploading' ? 'is-uploading' : item.status === 'error' ? 'is-error' : item.status === 'warning' ? 'is-warning' : 'is-success';
  const statusText =
    item.status === 'uploading'
      ? `上传中 ${item.percent}%`
      : item.status === 'success'
        ? item.message || '上传成功'
        : item.status === 'warning'
          ? item.message || '解析失败'
          : item.status === 'error'
            ? item.message || '上传失败'
            : '';
  const statusIcon =
    item.status === 'uploading'
      ? '<span class="spinner spinner-xs"></span>'
      : item.status === 'success'
        ? icon('check')
        : icon('alert');
  return `<div class="upload-item ${toneClass}" data-upload="${esc(item.id)}">
    <span class="file-ico ext-${esc(extClass(item.ext))}">${esc(extLabel(item.ext))}</span>
    <div class="upload-item-main">
      <div class="upload-item-head">
        <strong title="${esc(item.name)}">${esc(item.name)}</strong>
        <span class="upload-status">${statusIcon}<span>${esc(statusText)}</span></span>
      </div>
      <div class="progress"><span style="width:${Math.max(2, Math.min(100, Number(item.percent) || 0))}%"></span></div>
    </div>
    ${item.size ? `<span class="upload-size">${fmtBytes(item.size)}</span>` : ''}
  </div>`;
}

function renderUploads() {
  const holder = document.getElementById('uploadQueue');
  if (!holder) return;
  if (!state.uploads.length) {
    holder.hidden = true;
    holder.innerHTML = '';
    return;
  }
  holder.hidden = false;
  const hasDone = state.uploads.some((item) => item.status !== 'uploading');
  holder.innerHTML = `<div class="upload-queue-head">
      <span>${state.uploads.length} 个文件</span>
      ${hasDone ? `<button class="link-btn" type="button" data-action="clear-uploads">清除已完成</button>` : ''}
    </div>${state.uploads.map(uploadItemHtml).join('')}`;
}

function updateUpload(id, patch) {
  const item = state.uploads.find((entry) => entry.id === id);
  if (!item) return;
  Object.assign(item, patch);
  renderUploads();
}

async function handleFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const allowed = new Set(
    ((state.info && state.info.supported_exts) || FALLBACK_EXTS).map((ext) => String(ext).toLowerCase()),
  );
  const maxMb = Number(state.info?.max_upload_mb) || 20;
  let queued = 0;

  for (const file of files) {
    const ext = extOf(file.name);
    if (!allowed.has(ext)) {
      toast(`不支持的文件类型「${ext || '无扩展名'}」：${file.name}`, 'error');
      continue;
    }
    if (file.size > maxMb * 1024 * 1024) {
      toast(`文件超过 ${maxMb}MB 上限：${file.name}`, 'error');
      continue;
    }
    queued += 1;
    const id = uid();
    state.uploads.unshift({ id, name: file.name, size: file.size, ext, status: 'uploading', percent: 0 });
    renderUploads();
    uploadDocument(file, {
      onProgress: (percent) => updateUpload(id, { percent }),
    })
      .then((doc) => {
        if (doc?.status === 'failed') {
          updateUpload(id, {
            percent: 100,
            status: 'warning',
            message: doc.error || '解析失败',
          });
          toast(`《${doc.title || file.name}》解析失败：${doc.error || '未知原因'}`, 'error');
        } else {
          updateUpload(id, {
            percent: 100,
            status: 'success',
            message: `已入库 · ${Number(doc?.chunk_count || 0)} 个切片`,
          });
          toast(`《${doc?.title || file.name}》上传成功`, 'success');
        }
      })
      .catch((err) => {
        updateUpload(id, { status: 'error', message: err.message || '上传失败' });
        toast(`${file.name}：${err.message || '上传失败'}`, 'error');
      });
  }

  if (queued) {
    // 等上传全部结束再刷新列表与统计（最多等待 120 秒）
    const started = Date.now();
    const timer = window.setInterval(() => {
      const pending = state.uploads.some((item) => item.status === 'uploading');
      if (!pending || Date.now() - started > 120000) {
        window.clearInterval(timer);
        loadDocuments({ silent: true });
        loadStats();
      }
    }, 600);
  }
}

/* ------------------------------------------------------------------ */
/* 文档列表                                                            */
/* ------------------------------------------------------------------ */
function renderDocs() {
  const holder = document.getElementById('docsBody');
  if (!holder) return;
  const countEl = document.getElementById('docCount');
  if (countEl) countEl.textContent = state.docsLoaded ? `（${state.docs.length}）` : '';

  if (state.docsError) {
    holder.innerHTML = errorHtml(state.docsError);
    return;
  }
  if (!state.docsLoaded) {
    holder.innerHTML = loadingHtml('正在加载文档列表…');
    return;
  }
  if (!state.docs.length) {
    holder.innerHTML = emptyHtml({
      icon: 'book',
      title: '知识库还没有文档',
      desc: '上传校园通知、学生手册或制度文件，或直接导入示例资料快速体验。',
      action: `<button class="btn btn-primary" type="button" data-action="pick-file">${icon('upload')}<span>上传文档</span></button>
        <button class="btn btn-ghost" type="button" data-action="seed">${icon('download')}<span>一键导入示例资料</span></button>`,
    });
    return;
  }

  const rows = state.docs
    .map((doc) => {
      const ext = doc.file_ext || extOf(doc.filename);
      const meta = statusMeta(doc.status);
      const errorLine = doc.error
        ? `<span class="doc-error" title="${esc(doc.error)}">${esc(doc.error)}</span>`
        : '';
      return `<tr data-id="${esc(doc.id)}" class="${doc.status === 'failed' ? 'is-failed' : ''}">
        <td data-label="文档">
          <div class="doc-cell">
            <span class="file-ico ext-${esc(extClass(ext))}">${esc(extLabel(ext))}</span>
            <div class="doc-cell-main">
              <strong>${esc(doc.title || doc.filename)}</strong>
              <span class="doc-filename">${esc(doc.filename)}</span>
              ${errorLine}
            </div>
          </div>
        </td>
        <td data-label="类型"><span class="badge badge-blue">${esc(extLabel(ext))}</span></td>
        <td data-label="状态">${statusBadge(doc.status, doc.error)}</td>
        <td data-label="切片">${fmtNumber(doc.chunk_count || 0)}</td>
        <td data-label="大小">${fmtBytes(doc.size_bytes)}</td>
        <td data-label="上传时间"><span title="${esc(doc.created_at || '')}">${esc(fmtRelative(doc.updated_at || doc.created_at))}</span></td>
        <td data-label="操作" class="td-actions">
          <button class="icon-btn" type="button" data-action="doc-detail" data-id="${esc(doc.id)}" title="查看详情" aria-label="查看详情">${icon('eye')}</button>
          <button class="icon-btn" type="button" data-action="reindex" data-id="${esc(doc.id)}" title="重建索引" aria-label="重建索引">${icon('refresh')}</button>
          <button class="icon-btn is-danger" type="button" data-action="doc-delete" data-id="${esc(doc.id)}" title="删除文档" aria-label="删除文档">${icon('trash')}</button>
        </td>
      </tr>`;
    })
    .join('');

  holder.innerHTML = `<div class="table-wrap">
    <table class="table docs-table">
      <thead><tr>
        <th>文档</th><th>类型</th><th>状态</th><th>切片</th><th>大小</th><th>上传时间</th><th class="th-actions">操作</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <p class="table-note">${icon('info')} 上传后自动解析切片，对话回答引用命中切片中的原文；解析失败可尝试「重建索引」。</p>`;
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function loadDocuments({ silent = false } = {}) {
  if (!silent) {
    state.docsLoaded = false;
    state.docsError = '';
    renderDocs();
  }
  try {
    const data = await listDocuments();
    state.docs = Array.isArray(data?.items) ? data.items : [];
    state.docsError = '';
    state.docsLoaded = true;
    renderDocs();
    renderStats();
  } catch (err) {
    state.docsLoaded = false;
    state.docsError = err?.message || '文档列表加载失败';
    renderDocs();
    renderStats();
    if (!silent) toast(state.docsError, 'error');
  }
}

async function loadStats() {
  try {
    state.stats = await getKnowledgeStats();
    renderStats();
  } catch {
    state.stats = state.docsLoaded ? computedStats() : null;
    renderStats();
  }
}

async function loadSystemInfo() {
  try {
    state.info = await getSystemInfo();
    const hint = document.getElementById('uploadHint');
    if (hint) {
      const preferred = ['.md', '.txt', '.pdf', '.docx', '.xlsx', '.csv', '.json', '.html'];
      const available = new Set(
        (state.info.supported_exts || FALLBACK_EXTS).map((ext) => String(ext).toLowerCase()),
      );
      const labels = preferred
        .filter((ext) => available.has(ext))
        .map((ext) => ext.replace('.', '').toUpperCase());
      hint.textContent = `支持 ${labels.join(' / ')} 等格式，单个文件 ≤ ${state.info.max_upload_mb || 20}MB；解析完成后自动切片入库。`;
    }
    const fileInput = document.getElementById('fileInput');
    if (fileInput) {
      fileInput.accept = ((state.info.supported_exts || FALLBACK_EXTS) || []).join(',');
    }
  } catch {
    state.info = null;
  }
}

/* ------------------------------------------------------------------ */
/* 检索测试                                                            */
/* ------------------------------------------------------------------ */
function renderSearch() {
  const holder = document.getElementById('searchResults');
  if (!holder) return;
  if (state.searching) {
    holder.innerHTML = loadingHtml('正在检索知识库…', 'state-compact');
    return;
  }
  if (state.searchError) {
    holder.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(state.searchError)}</span></div>`;
    return;
  }
  if (!state.searched) {
    const examples = [
      '国家奖学金申请条件',
      '请假办理流程',
      '心理中心预约方式',
    ];
    holder.innerHTML = `<div class="search-hint">${icon('search')}<p>输入一个问题，测试知识库召回效果</p>
      <div class="search-examples">${examples
        .map(
          (query) =>
            `<button class="example-chip" type="button" data-action="example-search" data-query="${esc(
              query,
            )}">${esc(query)}</button>`,
        )
        .join('')}</div>
    </div>`;
    return;
  }
  if (!state.searchResults.length) {
    holder.innerHTML = emptyHtml({
      icon: 'search',
      title: '没有命中相关内容',
      desc: '换个关键词，或先上传 / 导入与问题相关的校园资料。',
    });
    return;
  }
  const hits = state.searchResults
    .map((hit, index) => {
      const title = hit.document_title || '未命名文档';
      return `<article class="search-hit" data-hit="${index}">
        <div class="search-hit-head">
          <span class="rank">${index + 1}</span>
          <strong>${esc(title)}</strong>
          <span class="score">相关度 ${fmtScore(hit.score)}</span>
        </div>
        ${hit.section ? `<div class="search-hit-section">${icon('book')}<span>${esc(hit.section)}</span></div>` : ''}
        <p class="search-hit-snippet">${esc(hit.snippet || '')}</p>
        <div class="search-hit-full" hidden>${esc(hit.content || '')}</div>
        <div class="search-hit-actions">
          <button class="link-btn" type="button" data-action="toggle-hit" data-hit="${index}">
            ${icon('chevron-down')}<span>展开原文</span>
          </button>
          ${
            hit.document_id
              ? `<button class="link-btn" type="button" data-action="open-doc" data-id="${esc(hit.document_id)}">${icon('eye')}<span>查看文档</span></button>`
              : ''
          }
        </div>
      </article>`;
    })
    .join('');
  holder.innerHTML = `<div class="search-summary">命中 <b>${state.searchResults.length}</b> 条${
    Number.isFinite(Number(state.searchLatency)) ? ` · 耗时 ${Math.round(Number(state.searchLatency))} ms` : ''
  }</div>${hits}`;
}

async function runSearch(query, topK) {
  state.searching = true;
  state.searchError = '';
  state.searched = true;
  state.searchQuery = query;
  renderSearch();
  try {
    const data = await searchKnowledge(query, topK);
    state.searchResults = Array.isArray(data?.items) ? data.items : [];
    state.searchLatency = data?.latency_ms ?? null;
    state.searching = false;
    renderSearch();
  } catch (err) {
    state.searching = false;
    state.searchResults = [];
    state.searchError = err?.message || '检索失败';
    renderSearch();
    toast(state.searchError, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* 文档详情抽屉                                                        */
/* ------------------------------------------------------------------ */
function detailBodyHtml(detail) {
  const doc = detail.doc || {};
  if (detail.error) {
    return errorHtml(detail.error, { retryAction: '', retryLabel: '' });
  }
  const ext = doc.file_ext || extOf(doc.filename);
  const chunks = detail.chunks || [];
  const metaItems = [
    ['状态', statusBadge(doc.status, doc.error)],
    ['类型', `<span class="badge badge-blue">${esc(extLabel(ext))}</span>`],
    ['大小', fmtBytes(doc.size_bytes)],
    ['字符数', fmtNumber(doc.char_count || 0)],
    ['切片数', fmtNumber(doc.chunk_count || 0)],
    ['更新时间', esc(fmtRelative(doc.updated_at || doc.created_at))],
  ]
    .map(
      ([label, value]) =>
        `<div class="detail-meta-item"><span>${esc(label)}</span><div>${value}</div></div>`,
    )
    .join('');
  const tags = Array.isArray(doc.tags) && doc.tags.length
    ? doc.tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join('')
    : '<span class="muted">未设置标签</span>';
  const chunkList = chunks.length
    ? chunks
        .map(
          (chunk) => `<article class="chunk-card">
            <header><span class="chunk-seq">#${Number(chunk.seq ?? 0) + 1}</span>
              ${chunk.section ? `<span class="chunk-section">${esc(chunk.section)}</span>` : ''}
              <span class="chunk-tokens">${fmtNumber(chunk.tokens || 0)} tokens</span>
            </header>
            <p>${esc(chunk.content || '')}</p>
          </article>`,
        )
        .join('')
    : `<div class="state state-empty state-compact">${icon('file')}<p>该文档暂无切片</p></div>`;
  const more = detail.total > chunks.length
    ? `<button class="btn btn-ghost btn-block" type="button" data-action="detail-more">${icon('chevron-down')}<span>加载更多切片（已显示 ${chunks.length} / ${detail.total}）</span></button>`
    : '';
  return `<div class="detail-top">
      <div class="detail-file">
        <span class="file-ico file-ico-lg ext-${esc(extClass(ext))}">${esc(extLabel(ext))}</span>
        <div><strong>${esc(doc.title || doc.filename || '未命名文档')}</strong>
        <span class="doc-filename">${esc(doc.filename || '')}</span></div>
      </div>
      <div class="detail-tags">${tags}</div>
    </div>
    <div class="detail-meta">${metaItems}</div>
    ${doc.error ? `<div class="alert alert-error">${icon('alert')}<div><strong>解析失败</strong><p>${esc(doc.error)}</p></div></div>` : ''}
    <div class="detail-actions">
      <button class="btn btn-sm btn-ghost" type="button" data-action="detail-reindex" data-id="${esc(doc.id)}">${icon('refresh')}<span>重建索引</span></button>
      <button class="btn btn-sm btn-ghost" type="button" data-action="detail-delete" data-id="${esc(doc.id)}">${icon('trash')}<span>删除文档</span></button>
      <a class="btn btn-sm btn-ghost" href="#/chat" data-action="detail-chat">${icon('chat')}<span>去对话测试</span></a>
    </div>
    <h3 class="detail-section-title">切片预览 <span>（${chunks.length} / ${fmtNumber(detail.total || chunks.length)}）</span></h3>
    <div class="chunk-list">${chunkList}</div>
    ${more}`;
}

async function openDocDetail(id) {
  const detail = { doc: null, chunks: [], total: 0, offset: 0, error: '', loadingMore: false };
  const drawer = openDrawer({
    title: '文档详情',
    subtitle: '正在加载…',
    size: 'lg',
    body: loadingHtml('正在加载文档详情…'),
  });

  const paint = () => {
    if (!drawer.overlay.isConnected) return;
    if (detail.doc) {
      const titleEl = drawer.overlay.querySelector('.drawer-title');
      const subtitleEl = drawer.overlay.querySelector('.drawer-subtitle');
      if (titleEl) titleEl.textContent = detail.doc.title || '文档详情';
      if (subtitleEl) subtitleEl.textContent = detail.doc.filename || '';
    }
    drawer.body.innerHTML = detailBodyHtml(detail);
  };

  const loadMore = () => {
    if (detail.loadingMore) return;
    detail.loadingMore = true;
    getDocumentChunks(id, detail.chunks.length, 50)
      .then((data) => {
        const items = Array.isArray(data?.items) ? data.items : [];
        detail.chunks = detail.chunks.concat(items);
        detail.total = Number(data?.total || detail.total || detail.chunks.length);
        detail.loadingMore = false;
        paint();
      })
      .catch((err) => {
        detail.loadingMore = false;
        toast(err?.message || '切片加载失败', 'error');
      });
  };

  drawer.body.addEventListener('click', async (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    if (action === 'detail-more') {
      loadMore();
      return;
    }
    if (action === 'detail-reindex') {
      setButtonBusy(trigger, true, '重建中…');
      try {
        const updated = await reindexDocument(detail.doc.id);
        toast(`《${updated?.title || detail.doc.title}》已重建索引`, 'success');
        detail.doc = updated || detail.doc;
        detail.chunks = [];
        detail.offset = 0;
        const chunksData = await getDocumentChunks(detail.doc.id, 0, 50);
        detail.chunks = Array.isArray(chunksData?.items) ? chunksData.items : [];
        detail.total = Number(chunksData?.total || detail.chunks.length);
        paint();
        loadDocuments({ silent: true });
        loadStats();
      } catch (err) {
        toast(err?.message || '重建索引失败', 'error');
        setButtonBusy(trigger, false);
      }
      return;
    }
    if (action === 'detail-delete') {
      const ok = await confirmDialog({
        title: '删除文档',
        message: `确定删除《${detail.doc.title || detail.doc.filename}》吗？`,
        detail: '文档的物理文件与全部切片会一并删除，且不可恢复。',
        confirmText: '删除',
        danger: true,
      });
      if (!ok) return;
      try {
        await deleteDocument(detail.doc.id);
        toast('文档已删除', 'success');
        drawer.close();
        loadDocuments({ silent: true });
        loadStats();
      } catch (err) {
        toast(err?.message || '删除失败', 'error');
      }
      return;
    }
    if (action === 'detail-chat') {
      drawer.close();
    }
  });

  try {
    const [detailResult, chunksResult] = await Promise.allSettled([
      getDocument(id),
      getDocumentChunks(id, 0, 50),
    ]);
    if (detailResult.status === 'rejected') {
      detail.error = detailResult.reason?.message || '文档详情加载失败';
    } else {
      detail.doc = detailResult.value || {};
    }
    if (chunksResult.status === 'fulfilled') {
      detail.chunks = Array.isArray(chunksResult.value?.items) ? chunksResult.value.items : [];
      detail.total = Number(chunksResult.value?.total || detail.chunks.length);
    } else if (!detail.error) {
      detail.error = chunksResult.reason?.message || '切片加载失败';
    }
    paint();
  } catch (err) {
    detail.error = err?.message || '文档详情加载失败';
    paint();
  }
}

/* ------------------------------------------------------------------ */
/* 删除 / 重建 / seed                                                   */
/* ------------------------------------------------------------------ */
async function removeDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除文档',
    message: `确定删除《${doc?.title || doc?.filename || '该文档'}》吗？`,
    detail: '文档的物理文件与全部切片会一并删除，且不可恢复。',
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteDocument(id);
    toast('文档已删除', 'success');
    loadDocuments({ silent: true });
    loadStats();
  } catch (err) {
    toast(err?.message || '删除失败', 'error');
  }
}

async function reindexDoc(id, button) {
  const doc = state.docs.find((item) => item.id === id);
  setButtonBusy(button, true, '重建中…');
  try {
    const updated = await reindexDocument(id);
    toast(
      updated?.status === 'failed'
        ? `《${updated?.title || doc?.title || '文档'}》重建失败：${updated?.error || '未知原因'}`
        : `《${updated?.title || doc?.title || '文档'}》已重建索引`,
      updated?.status === 'failed' ? 'error' : 'success',
    );
    loadDocuments({ silent: true });
    loadStats();
  } catch (err) {
    toast(err?.message || '重建索引失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

async function runSeed(button) {
  setButtonBusy(button, true, '导入中…');
  try {
    const result = await seedKnowledge();
    const ingested = Array.isArray(result?.ingested) ? result.ingested.length : 0;
    const skipped = Array.isArray(result?.skipped) ? result.skipped.length : 0;
    toast(
      `示例资料导入完成：新增 ${ingested} 篇，跳过已存在 ${skipped} 篇${
        result?.total_documents !== undefined ? `，当前共 ${result.total_documents} 篇` : ''
      }`,
      'success',
      { title: '导入完成' },
    );
    loadDocuments({ silent: true });
    loadStats();
  } catch (err) {
    toast(err?.message || '示例资料导入失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

/* ------------------------------------------------------------------ */
/* 视图入口                                                            */
/* ------------------------------------------------------------------ */
export function mount(root) {
  let alive = true;
  const topK = savedTopK();

  root.innerHTML = `<div class="page knowledge-page">
    <section class="stats-grid" id="knowledgeStats" aria-label="知识库统计"></section>

    <div class="grid-2 knowledge-tools">
      <section class="card upload-card">
        <header class="card-head">
          <div>
            <h2>${icon('upload')}上传知识文档</h2>
            <p id="uploadHint">支持 md / txt / pdf / docx / xlsx / csv / json / html，单个文件 ≤ 20MB</p>
          </div>
        </header>
        <div class="dropzone" id="dropzone" role="button" tabindex="0" aria-label="上传知识文档">
          <div class="dropzone-icon">${icon('upload')}</div>
          <strong>拖拽文件到这里，或点击选择文件</strong>
          <span>解析 → 切片 → 建立索引，完成后即可在对话中引用</span>
          <input type="file" id="fileInput" multiple hidden />
        </div>
        <div class="upload-queue" id="uploadQueue" hidden></div>
      </section>

      <section class="card search-card">
        <header class="card-head">
          <div>
            <h2>${icon('search')}检索测试</h2>
            <p>输入问题，查看知识库实际召回的内容与相关度</p>
          </div>
        </header>
        <form class="search-bar" id="searchForm">
          <input class="input search-input" id="searchInput" type="search" placeholder="例如：国家奖学金申请条件" aria-label="检索关键词" />
          <select class="input select topk-select" id="searchTopK" aria-label="召回数量">
            ${TOP_K_OPTIONS.map(
              (value) => `<option value="${value}" ${value === topK ? 'selected' : ''}>top ${value}</option>`,
            ).join('')}
          </select>
          <button class="btn btn-primary" type="submit" id="searchBtn">${icon('search')}<span>搜索</span></button>
        </form>
        <div class="search-results" id="searchResults"></div>
      </section>
    </div>

    <section class="card docs-card">
      <header class="card-head">
        <div>
          <h2>${icon('book')}文档列表 <span class="count" id="docCount"></span></h2>
          <p>解析失败的文档可尝试重建索引；删除会同时移除切片</p>
        </div>
        <div class="card-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-action="refresh-docs">${icon('refresh')}<span>刷新</span></button>
          <button class="btn btn-primary btn-sm" type="button" data-action="seed">${icon('download')}<span>一键导入示例资料</span></button>
        </div>
      </header>
      <div id="docsBody">${loadingHtml('正在加载文档列表…')}</div>
    </section>
  </div>`;

  const dropzone = root.querySelector('#dropzone');
  const page = root.querySelector('.knowledge-page');
  const fileInput = root.querySelector('#fileInput');
  const searchForm = root.querySelector('#searchForm');
  const searchInput = root.querySelector('#searchInput');
  const searchTopK = root.querySelector('#searchTopK');
  const searchBtn = root.querySelector('#searchBtn');

  /* 上传交互 */
  const pickFile = () => fileInput?.click();
  dropzone?.addEventListener('click', pickFile);
  dropzone?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      pickFile();
    }
  });
  fileInput?.addEventListener('change', () => {
    handleFiles(fileInput.files);
    fileInput.value = '';
  });
  ['dragenter', 'dragover'].forEach((type) => {
    dropzone?.addEventListener(type, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropzone.classList.add('is-dragover');
    });
  });
  ['dragleave', 'dragend'].forEach((type) => {
    dropzone?.addEventListener(type, (event) => {
      event.preventDefault();
      if (event.relatedTarget && dropzone.contains(event.relatedTarget)) return;
      dropzone.classList.remove('is-dragover');
    });
  });
  dropzone?.addEventListener('drop', (event) => {
    event.preventDefault();
    event.stopPropagation();
    dropzone.classList.remove('is-dragover');
    if (event.dataTransfer?.files?.length) handleFiles(event.dataTransfer.files);
  });

  /* 检索交互 */
  searchForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    if (!query) {
      toast('请输入检索关键词', 'warning');
      searchInput.focus();
      return;
    }
    const topKValue = Number(searchTopK.value) || 4;
    try {
      localStorage.setItem('npu:searchTopK', String(topKValue));
    } catch {
      /* ignore */
    }
    runSearch(query, topKValue);
  });

  /* 全局委托 */
  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'refresh-docs':
        loadDocuments();
        loadStats();
        toast('正在刷新文档列表…', 'info', { duration: 1600 });
        break;
      case 'seed':
        runSeed(trigger);
        break;
      case 'pick-file':
        pickFile();
        break;
      case 'doc-detail':
        openDocDetail(trigger.dataset.id);
        break;
      case 'doc-delete':
        removeDoc(trigger.dataset.id);
        break;
      case 'reindex':
        reindexDoc(trigger.dataset.id, trigger);
        break;
      case 'clear-uploads':
        state.uploads = state.uploads.filter((item) => item.status === 'uploading');
        renderUploads();
        break;
      case 'toggle-hit': {
        const hit = root.querySelector(`[data-hit="${trigger.dataset.hit}"]`);
        if (!hit) break;
        const full = hit.querySelector('.search-hit-full');
        const open = full.hidden;
        full.hidden = !open;
        hit.classList.toggle('is-open', open);
        trigger.innerHTML = open
          ? `${icon('chevron-down')}<span>收起原文</span>`
          : `${icon('chevron-right')}<span>展开原文</span>`;
        break;
      }
      case 'open-doc':
        openDocDetail(trigger.dataset.id);
        break;
      case 'example-search': {
        const query = trigger.dataset.query || '';
        searchInput.value = query;
        const topKValue = Number(searchTopK.value) || 4;
        runSearch(query, topKValue);
        break;
      }
      case 'retry':
        loadDocuments();
        loadStats();
        break;
      default:
        break;
    }
  });

  /* 首次加载 */
  renderStats();
  renderUploads();
  renderSearch();
  Promise.all([loadSystemInfo(), loadDocuments(), loadStats()]);

  return () => {
    alive = false;
  };
}

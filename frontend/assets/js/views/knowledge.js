/**
 * views/knowledge.js — LLM Wiki 知识库页
 *
 * 双 Tab：
 *  - 词条：/api/wiki/search 导航搜索（method/分数）、标签筛选、词条卡片、详情抽屉
 *          （body_html、别名/标签、来源锚点、相关词条、编辑/删除）；
 *  - 源文件：拖拽上传（LLM 同步编译）、文档表格（状态/词条数）、单篇重建、删除、
 *            导入预编译快照、全部重建（轮询 /api/wiki/builds/{id}）。
 *
 * 接口：/api/wiki/*、/api/knowledge/*
 */
import {
  deleteDocument,
  deleteWikiPage,
  documentDownloadUrl,
  getDocument,
  getKnowledgeStats,
  getSystemInfo,
  getWikiBuild,
  getWikiPage,
  getWikiStats,
  getWikiTree,
  importBatch,
  importText,
  importUrl,
  listDocuments,
  listWikiPages,
  rebuildWiki,
  reindexDocument,
  seedWiki,
  searchWiki,
  updateWikiPage,
  uploadDocument,
} from '../api.js';
import {
  badge,
  confirmDialog,
  copyText,
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
  openModal,
  setButtonBusy,
  toast,
} from '../ui.js';

const FALLBACK_EXTS = ['.md', '.markdown', '.txt', '.pdf', '.docx', '.doc', '.xlsx', '.csv', '.json', '.html', '.htm'];
const DOC_STATUS = {
  pending: { label: '等待编译', tone: 'slate' },
  processing: { label: '编译中', tone: 'amber' },
  ready: { label: '已就绪', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
};
const PAGE_STATUS = {
  ready: { label: '可问答', tone: 'green' },
  needs_review: { label: '待校对', tone: 'amber' },
};
const METHOD_META = {
  exact: { label: '标题/别名精确命中', tone: 'green' },
  keyword: { label: '关键词匹配', tone: 'blue' },
  llm: { label: 'LLM 选页', tone: 'violet' },
  none: { label: '未命中', tone: 'slate' },
};
const BUILD_STATUS = {
  running: { label: '编译中', tone: 'amber' },
  pending: { label: '排队中', tone: 'amber' },
  ready: { label: '已完成', tone: 'green' },
  completed: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
};

const state = {
  tab: 'pages',
  readOnly: true,
  manageTab: 'capture',
  wikiStats: null,
  kStats: null,
  info: null,
  tree: [],
  treeReady: false,
  pages: [],
  pagesTotal: 0,
  pagesLoaded: false,
  pagesLoading: false,
  pagesError: '',
  activeTag: '',
  search: { query: '', running: false, result: null, error: '' },
  docs: [],
  docsLoaded: false,
  docsError: '',
  uploads: [],
  uploadTimer: 0,
  build: null,
  buildTimer: 0,
  capture: {
    mode: 'links',
    collapsed: false,
    importingLinks: false,
    importingText: false,
    linkResults: [],
    textResult: null,
  },
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

function docStatus(status) {
  return DOC_STATUS[status] || { label: status || '未知', tone: 'slate' };
}

function pageStatus(status) {
  return PAGE_STATUS[status] || { label: status || '未知', tone: 'slate' };
}

function docStatusBadge(status, error) {
  const meta = docStatus(status);
  return `<span class="badge badge-${meta.tone} status-badge" title="${esc(error || meta.label)}"><span class="badge-dot"></span>${esc(meta.label)}</span>`;
}

function docSourceBadge(doc) {
  const title = String(doc.title || '');
  const isExample = /（示例）/.test(title) || normalizeList(doc.tags).includes('示例');
  if (doc.source_url) {
    const host = (() => {
      try {
        return new URL(doc.source_url).hostname;
      } catch {
        return '网页';
      }
    })();
    return `<span class="badge badge-blue" title="${esc(doc.source_url)}">网页</span> <a class="doc-source-link" href="${esc(
      doc.source_url,
    )}" target="_blank" rel="noopener noreferrer" title="${esc(doc.source_url)}">${esc(host)}</a>`;
  }
  if (isExample) {
    return '<span class="badge badge-amber" title="示例数据，非真实来源">示例</span>';
  }
  return '<span class="badge badge-slate" title="本地文件">本地</span>';
}

function pageStatusBadge(status) {
  const meta = pageStatus(status);
  return `<span class="badge badge-${meta.tone}"><span class="badge-dot"></span>${esc(meta.label)}</span>`;
}

function normalizeList(value) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (typeof value === 'string') {
    return value
      .split(/[,，]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function tagChips(tags, limit = 4) {
  const list = normalizeList(tags);
  if (!list.length) return '';
  return list
    .slice(0, limit)
    .map((tag) => `<span class="tag-chip">${esc(tag)}</span>`)
    .join('');
}

function spinnerHtml(cls = 'spinner-sm') {
  return `<span class="spinner ${cls}" aria-hidden="true"></span>`;
}

/* ------------------------------------------------------------------ */
/* 统计卡片                                                            */
/* ------------------------------------------------------------------ */
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

function lastBuildInfo() {
  const build = state.wikiStats?.last_build;
  if (!build) return { value: '—', hint: '暂无编译记录' };
  if (typeof build === 'string') return { value: '有记录', hint: build };
  const meta = BUILD_STATUS[build.status] || { label: build.status || '未知', tone: 'slate' };
  const time = build.finished_at || build.started_at;
  const parts = [];
  if (build.pages_created) parts.push(`新增 ${build.pages_created}`);
  if (build.pages_updated) parts.push(`更新 ${build.pages_updated}`);
  if (build.pages_removed) parts.push(`删除 ${build.pages_removed}`);
  return {
    value: time ? fmtRelative(time) : '—',
    label: meta.label,
    tone: meta.tone,
    hint: parts.length ? parts.join(' / ') : `${build.model || ''}`.trim() || '编译任务记录',
  };
}

function renderStats() {
  const holder = document.getElementById('wikiStats');
  if (!holder) return;
  if (!state.wikiStats && !state.kStats) {
    holder.innerHTML = Array.from({ length: 4 })
      .map(
        () => `<article class="stat-card is-skeleton"><span class="skeleton-line w-40"></span><span class="skeleton-line w-70"></span></article>`,
      )
      .join('');
    return;
  }
  const pages = state.wikiStats?.pages ?? state.kStats?.pages ?? state.pagesTotal ?? 0;
  const sources = state.wikiStats?.sources ?? state.kStats?.sources ?? 0;
  const documents = state.wikiStats?.documents ?? state.kStats?.documents ?? state.docs.length ?? 0;
  const build = lastBuildInfo();
  const readyCount = state.kStats?.ready_documents;
  holder.innerHTML = [
    statCard({
      iconName: 'book',
      label: 'Wiki 词条',
      value: fmtNumber(pages),
      hint: '由源文件编译的结构化页面',
    }),
    statCard({
      iconName: 'link',
      label: '来源锚点',
      value: fmtNumber(sources),
      hint: '文件 · 章节 · 原文摘录',
    }),
    statCard({
      iconName: 'file',
      label: '源文件',
      value: fmtNumber(documents),
      hint: readyCount !== undefined ? `已就绪 ${fmtNumber(readyCount)} 篇` : '校园原始资料',
    }),
    statCard({
      iconName: 'refresh',
      label: '最近构建',
      value: esc(build.value),
      hint: build.hint,
      extra: build.label ? `<div class="stat-types">${badge(build.label, build.tone || 'slate')}</div>` : '',
    }),
  ].join('');
  const tabPages = document.getElementById('tabPageCount');
  if (tabPages) tabPages.textContent = state.pagesTotal ? `· ${state.pagesTotal}` : '';
  const tabDocs = document.getElementById('tabDocCount');
  const docTotal = state.docsLoaded
    ? state.docs.length
    : state.wikiStats?.documents ?? state.kStats?.documents ?? 0;
  if (tabDocs) tabDocs.textContent = docTotal ? `· ${docTotal}` : '';
  const docCount = document.getElementById('docCount');
  if (docCount) docCount.textContent = state.docsLoaded ? `（${state.docs.length}）` : '';
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function loadStats() {
  const [wikiResult, knowledgeResult] = await Promise.allSettled([getWikiStats(), getKnowledgeStats()]);
  if (wikiResult.status === 'fulfilled') state.wikiStats = wikiResult.value || null;
  if (knowledgeResult.status === 'fulfilled') state.kStats = knowledgeResult.value || null;
  renderStats();
}

async function loadTree() {
  try {
    const data = await getWikiTree();
    state.tree = Array.isArray(data?.groups) ? data.groups : [];
    state.treeReady = true;
  } catch {
    state.tree = [];
    state.treeReady = false;
  }
  renderTagFilter();
}

async function loadPages({ silent = false } = {}) {
  state.pagesLoading = true;
  if (!silent) {
    state.pagesLoaded = false;
    state.pagesError = '';
  }
  renderPages();
  try {
    const data = await listWikiPages({ tag: state.activeTag, limit: 50, offset: 0 });
    state.pages = Array.isArray(data?.items) ? data.items : [];
    state.pagesTotal = Number(data?.total ?? state.pages.length);
    state.pagesError = '';
    state.pagesLoaded = true;
    state.pagesLoading = false;
    renderPages();
    renderStats();
  } catch (err) {
    state.pagesLoading = false;
    state.pagesLoaded = false;
    state.pagesError = err?.message || '词条列表加载失败';
    renderPages();
    if (!silent) toast(state.pagesError, 'error');
  }
}

async function loadDocs({ silent = false } = {}) {
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
    state.docsError = err?.message || '源文件列表加载失败';
    renderDocs();
    if (!silent) toast(state.docsError, 'error');
  }
}

async function loadSystemInfo() {
  try {
    state.info = await getSystemInfo();
    const hint = document.getElementById('uploadHint');
    if (hint) {
      const preferred = ['.md', '.txt', '.pdf', '.docx', '.doc', '.xlsx', '.csv', '.json', '.html'];
      const available = new Set(
        (state.info?.supported_exts || FALLBACK_EXTS).map((ext) => String(ext).toLowerCase()),
      );
      const labels = preferred.filter((ext) => available.has(ext)).map((ext) => ext.replace('.', '').toUpperCase());
      hint.textContent = `支持 ${labels.join(' / ')} 等格式，单个 ≤ ${
        state.info?.max_upload_mb || 20
      }MB；上传后由 LLM 同步编译为 Wiki 词条。`;
    }
    const fileInput = document.getElementById('fileInput');
    if (fileInput) fileInput.accept = (state.info?.supported_exts || FALLBACK_EXTS).join(',');
  } catch {
    state.info = null;
  }
}

/* ------------------------------------------------------------------ */
/* 标签筛选                                                            */
/* ------------------------------------------------------------------ */
function renderTagFilter() {
  const holder = document.getElementById('tagFilter');
  if (!holder) return;
  if (!state.treeReady || !state.tree.length) {
    holder.hidden = true;
    holder.innerHTML = '';
    return;
  }
  holder.hidden = false;
  const total = state.tree.reduce((sum, group) => sum + Number(group.count || 0), 0);
  holder.innerHTML = `<button class="tag-filter-item ${state.activeTag ? '' : 'is-active'}" type="button" data-action="filter-tag" data-tag="">全部<b>${fmtNumber(
    total,
  )}</b></button>${state.tree
    .map(
      (group) =>
        `<button class="tag-filter-item ${
          state.activeTag === group.tag ? 'is-active' : ''
        }" type="button" data-action="filter-tag" data-tag="${esc(group.tag)}">${esc(group.tag)}<b>${fmtNumber(
          group.count || 0,
        )}</b></button>`,
    )
    .join('')}`;
}

/* ------------------------------------------------------------------ */
/* 词条 Tab                                                            */
/* ------------------------------------------------------------------ */
function pageCardHtml(page) {
  const status = pageStatus(page.status);
  return `<article class="wiki-card ${page.status === 'needs_review' ? 'is-review' : ''}" role="button" tabindex="0"
      data-action="open-page" data-id="${esc(page.id)}" aria-label="打开词条 ${esc(page.title)}">
    <div class="wiki-card-head">
      <h3>${esc(page.title || '未命名词条')}</h3>
      ${page.status === 'needs_review' ? badge(status.label, status.tone) : ''}
    </div>
    <p class="wiki-summary">${esc(page.summary || '暂无摘要，点击查看正文与来源。')}</p>
    <div class="wiki-card-foot">
      <div class="wiki-tags">${tagChips(page.tags)}</div>
      <span class="wiki-meta">${fmtNumber(page.source_count || 0)} 个来源 · ${esc(fmtRelative(page.updated_at))}</span>
    </div>
  </article>`;
}

function methodBadge(method) {
  const meta = METHOD_META[method] || { label: method || '未知方式', tone: 'slate' };
  return `<span class="badge badge-${meta.tone}"><span class="badge-dot"></span>${esc(meta.label)}</span>`;
}

function candidateCardHtml(item, index, selectedIds) {
  const id = item.page_id || item.id;
  const selected = selectedIds.has(id);
  return `<article class="candidate-card ${selected ? 'is-selected' : ''}" role="button" tabindex="0"
      data-action="open-page" data-id="${esc(id)}">
    <div class="candidate-head">
      <span class="rank">${index + 1}</span>
      <strong>${esc(item.title || '未命名词条')}</strong>
      ${item.score !== undefined && item.score !== null ? `<span class="score">${fmtScore(item.score)}</span>` : ''}
      ${selected ? badge('LLM 选中', 'violet') : ''}
    </div>
    <p class="candidate-summary">${esc(item.summary || '')}</p>
    ${item.reason ? `<p class="candidate-reason">${icon('sparkles')}<span>${esc(item.reason)}</span></p>` : ''}
  </article>`;
}

function renderSearchResult() {
  const holder = document.getElementById('wikiSearchResult');
  if (!holder) return;
  const { running, result, error, query } = state.search;
  if (!running && !result && !error) {
    holder.hidden = true;
    holder.innerHTML = '';
    return;
  }
  holder.hidden = false;
  if (running) {
    holder.innerHTML = `<div class="card search-result-card">${loadingHtml(
      `正在通过 Wiki 导航定位「${query}」…`,
      'state-compact',
    )}</div>`;
    return;
  }
  if (error) {
    holder.innerHTML = `<div class="card search-result-card"><div class="inline-error">${icon('alert')}<span>${esc(
      error,
    )}</span></div><button class="btn btn-sm btn-ghost" type="button" data-action="clear-search">${icon(
      'x',
    )}<span>清除搜索</span></button></div>`;
    return;
  }
  const data = result || {};
  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  const selected = Array.isArray(data.selected) ? data.selected : [];
  const selectedIds = new Set(selected.map((item) => item.page_id || item.id));
  const latency = Number.isFinite(Number(data.latency_ms)) ? ` · ${Math.round(Number(data.latency_ms))} ms` : '';
  const body =
    candidates.length || selected.length
      ? `<div class="search-result-candidates">${candidates
          .map((item, index) => candidateCardHtml(item, index, selectedIds))
          .join('')}</div>
        ${
          selected.length
            ? `<div class="search-result-selected"><span class="field-label">对话时会送入词条</span><div class="wiki-tags">${selected
                .map((item) => `<span class="tag-chip is-selected">${esc(item.title || '')}</span>`)
                .join('')}</div></div>`
            : ''
        }`
      : `<div class="search-hint">${icon('search')}<p>未命中任何词条</p><span>换个说法，或先在「源文件」Tab 上传 / 导入快照。</span></div>`;
  holder.innerHTML = `<div class="card search-result-card">
    <header class="search-result-head">
      <div class="search-result-title">
        ${icon('search')}<strong>「${esc(data.query || query)}」的 Wiki 导航结果</strong>
        ${methodBadge(data.method)}<span class="muted">${latency}</span>
      </div>
      <button class="btn btn-sm btn-ghost" type="button" data-action="clear-search">${icon('x')}<span>清除</span></button>
    </header>
    ${body}
  </div>`;
}

function renderPages() {
  const body = document.getElementById('wikiPagesBody');
  const resultHolder = document.getElementById('wikiSearchResult');
  if (!body) return;
  const searching = Boolean(state.search.result || state.search.running || state.search.error);
  if (resultHolder) renderSearchResult();
  body.hidden = searching;
  if (searching) return;

  if (state.pagesError) {
    body.innerHTML = errorHtml(state.pagesError);
    return;
  }
  if (!state.pagesLoaded) {
    body.innerHTML = loadingHtml('正在加载 Wiki 词条…');
    return;
  }
  if (!state.pages.length) {
    body.innerHTML = state.readOnly
      ? emptyHtml({
          icon: 'book',
          title: state.activeTag ? `标签「${state.activeTag}」下暂无词条` : '校园 Wiki 还没有词条',
          desc: '词条由管理员在「内容管理」中收录并编译，稍后再来看看。',
          action: `<button class="btn btn-primary" type="button" data-action="go-manage">${icon(
            'database',
          )}<span>去内容管理</span></button>`,
        })
      : emptyHtml({
          icon: 'book',
          title: state.activeTag ? `标签「${state.activeTag}」下暂无词条` : '校园 Wiki 还没有词条',
          desc: '上传源文件后由 LLM 编译为结构化词条，或导入预编译快照立即体验浏览与问答。',
          action: `<button class="btn btn-primary" type="button" data-action="manage-tab" data-tab="sources">${icon(
            'upload',
          )}<span>去上传源文件</span></button>
        <button class="btn btn-ghost" type="button" data-action="seed">${icon('download')}<span>导入预编译快照</span></button>`,
        });
    return;
  }
  body.innerHTML = `<div class="wiki-grid">${state.pages.map(pageCardHtml).join('')}</div>
    <p class="table-note">${icon(
      'info',
    )} 词条由 LLM 从源文件编译生成，点击进入详情可查看「文件 · 章节 · 原文摘录」来源锚点。</p>`;
}

async function runWikiSearch(query) {
  state.search = { query, running: true, result: null, error: '' };
  renderPages();
  try {
    const data = await searchWiki(query);
    state.search = { query, running: false, result: data || {}, error: '' };
    renderPages();
  } catch (err) {
    state.search = { query, running: false, result: null, error: err?.message || '搜索失败' };
    renderPages();
    toast(state.search.error, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* 词条详情抽屉                                                        */
/* ------------------------------------------------------------------ */
function sourceAnchorHtml(source) {
  const title = source.document_title || source.source_file || '未知来源';
  return `<article class="source-anchor">
    <header class="source-anchor-head">${icon('file')}
      <strong>${esc(title)}</strong>
      ${source.section ? `<span class="source-section">${icon('chevron-right')}${esc(source.section)}</span>` : ''}
    </header>
    ${source.source_file ? `<div class="source-file mono">${esc(source.source_file)}</div>` : ''}
    ${source.quote ? `<blockquote class="source-quote">${esc(source.quote)}</blockquote>` : ''}
  </article>`;
}

function wikiDetailHtml(data) {
  const page = data?.page || data || {};
  const sources = Array.isArray(data?.sources) ? data.sources : Array.isArray(page.sources) ? page.sources : [];
  const links = Array.isArray(data?.links) ? data.links : [];
  const aliases = normalizeList(page.aliases);
  const tags = normalizeList(page.tags);
  return `<div class="wiki-detail">
    <header class="wiki-detail-head">
      <div>
        <h2>${esc(page.title || '未命名词条')}</h2>
        ${page.summary ? `<p class="wiki-detail-sub">${esc(page.summary)}</p>` : ''}
      </div>
      <div class="wiki-detail-actions">
        ${
          state.readOnly
            ? ''
            : `<button class="btn btn-sm btn-ghost" type="button" data-action="edit-page" data-id="${esc(
                page.id,
              )}">${icon('edit')}<span>编辑</span></button>
        <button class="btn btn-sm btn-ghost is-danger" type="button" data-action="delete-page" data-id="${esc(
          page.id,
        )}">${icon('trash')}<span>删除</span></button>`
        }
      </div>
    </header>
    <div class="wiki-detail-meta">
      ${page.status ? pageStatusBadge(page.status) : ''}
      <span class="chip-mono">${fmtNumber(page.source_count || sources.length)} 个来源锚点</span>
      ${page.updated_at ? `<span class="muted">更新于 ${esc(fmtRelative(page.updated_at))}</span>` : ''}
      ${page.version ? `<span class="chip-mono">v${esc(page.version)}</span>` : ''}
    </div>
    ${
      aliases.length || tags.length
        ? `<div class="wiki-alias-rows">
            ${
              aliases.length
                ? `<div class="wiki-alias-row"><span class="field-label">${icon('link')}别名</span><div class="wiki-tags">${aliases
                    .map((alias) => `<span class="tag-chip">${esc(alias)}</span>`)
                    .join('')}</div></div>`
                : ''
            }
            ${
              tags.length
                ? `<div class="wiki-alias-row"><span class="field-label">${icon('folder')}标签</span><div class="wiki-tags">${tags
                    .map((tag) => `<span class="tag-chip is-tag">${esc(tag)}</span>`)
                    .join('')}</div></div>`
                : ''
            }
          </div>`
        : ''
    }
    <section class="wiki-content">
      <h3 class="wiki-section-title">${icon('book')}词条正文</h3>
      <div class="wiki-body">${
        data?.body_html ||
        page.body_html ||
        `<p class="muted">（正文为空${state.readOnly ? '' : '，可点击「编辑」补充'}）</p>`
      }</div>
    </section>
    <section class="wiki-sources">
      <h3 class="wiki-section-title">${icon('link')}来源锚点 <span>${sources.length}</span></h3>
      ${
        sources.length
          ? sources.map(sourceAnchorHtml).join('')
          : '<p class="muted wiki-empty-note">该词条暂无来源锚点，可能由人工创建或待重建。</p>'
      }
    </section>
    ${
      links.length
        ? `<section class="wiki-links">
            <h3 class="wiki-section-title">${icon('sparkles')}相关词条</h3>
            <div class="wiki-link-list">${links
              .map(
                (link) =>
                  `<button class="tag-chip is-link" type="button" data-action="open-page" data-id="${esc(
                    link.page_id || link.id,
                  )}">${icon('chevron-right')}${esc(link.title || '未命名关联')}</button>`,
              )
              .join('')}</div>
          </section>`
        : ''
    }
  </div>`;
}

function clearHashPage() {
  const raw = String(location.hash || '');
  if (!raw.includes('?')) return;
  const [path] = raw.split('?');
  try {
    history.replaceState(null, '', path || '#/knowledge');
  } catch {
    /* ignore */
  }
}

async function openWikiPage(id, { fromHash = false } = {}) {
  if (!id) return;
  let current = { id, data: null };
  const drawer = openDrawer({
    title: '词条详情',
    subtitle: '正在加载…',
    size: 'lg',
    body: loadingHtml('正在加载词条…'),
    onClose: () => {
      if (fromHash) clearHashPage();
    },
  });

  const paint = () => {
    if (!drawer.overlay.isConnected) return;
    const page = current.data?.page || current.data || {};
    const titleEl = drawer.overlay.querySelector('.drawer-title');
    const subtitleEl = drawer.overlay.querySelector('.drawer-subtitle');
    if (titleEl) titleEl.textContent = page.title || '词条详情';
    if (subtitleEl) subtitleEl.textContent = current.data?.summary || page.summary || '';
    drawer.body.innerHTML = wikiDetailHtml(current.data);
  };

  const load = async () => {
    drawer.body.innerHTML = loadingHtml('正在加载词条…');
    try {
      current.data = await getWikiPage(id);
      paint();
    } catch (err) {
      current.data = null;
      drawer.body.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(
        err?.message || '词条加载失败',
      )}</span></div><button class="btn btn-ghost btn-sm" type="button" data-action="retry-page" data-id="${esc(
        id,
      )}">${icon('refresh')}<span>重新加载</span></button>`;
    }
  };

  drawer.overlay.addEventListener('click', async (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    if (action === 'retry-page') {
      load();
      return;
    }
    if (action === 'edit-page') {
      openEditPageModal(current.data?.page || current.data, async () => {
        await load();
        refreshWiki({ silent: true });
      });
      return;
    }
    if (action === 'delete-page') {
      const page = current.data?.page || current.data || {};
      const ok = await confirmDialog({
        title: '删除词条',
        message: `确定删除词条「${page.title || ''}」吗？`,
        detail: '词条与来源锚点会一并删除；源文件本身不受影响，可通过重建重新生成。',
        confirmText: '删除',
        danger: true,
      });
      if (!ok) return;
      try {
        await deleteWikiPage(page.id || id);
        toast('词条已删除', 'success');
        drawer.close();
        refreshWiki({ silent: true });
      } catch (err) {
        toast(err?.message || '删除词条失败', 'error');
      }
      return;
    }
    if (action === 'open-page') {
      const targetId = trigger.dataset.id;
      if (targetId && targetId !== id) {
        drawer.close();
        openWikiPage(targetId);
      }
    }
  });

  await load();
}

/* ------------------------------------------------------------------ */
/* 词条编辑                                                            */
/* ------------------------------------------------------------------ */
function openEditPageModal(page, onSaved) {
  const data = page || {};
  const modal = openModal({
    title: '编辑词条',
    subtitle: '保存后版本号 +1，并立即用于对话问答',
    size: 'xl',
    body: `<form class="form" id="wikiEditForm" novalidate>
      <div class="form-row">
        <label class="field">
          <span class="field-label">标题 <em>*</em></span>
          <input class="input" name="title" maxlength="120" value="${esc(data.title || '')}" />
        </label>
        <label class="field">
          <span class="field-label">摘要</span>
          <input class="input" name="summary" maxlength="240" value="${esc(data.summary || '')}" />
        </label>
      </div>
      <div class="form-row">
        <label class="field">
          <span class="field-label">别名 <span class="field-tip">（逗号分隔，用于精确匹配）</span></span>
          <input class="input" name="aliases" value="${esc(normalizeList(data.aliases).join(', '))}" />
        </label>
        <label class="field">
          <span class="field-label">标签 <span class="field-tip">（逗号分隔，用于目录分组）</span></span>
          <input class="input" name="tags" value="${esc(normalizeList(data.tags).join(', '))}" />
        </label>
      </div>
      <label class="field">
        <span class="field-label">正文（Markdown）<em>*</em></span>
        <textarea class="input textarea-code wiki-edit-textarea" name="body_md" rows="18" spellcheck="false">${esc(
          data.body_md || '',
        )}</textarea>
      </label>
      <div class="form-hint">${icon('info')}<span>支持标准 Markdown（标题、列表、表格、引用）；保存后由后端渲染为 HTML 展示。</span></div>
    </form>`,
    footer: `<button class="btn btn-ghost" type="button" data-modal-cancel>取消</button>
      <button class="btn btn-primary" type="button" data-modal-submit>${icon('check')}<span>保存修改</span></button>`,
  });
  const form = modal.body.querySelector('#wikiEditForm');
  modal.footer.querySelector('[data-modal-cancel]')?.addEventListener('click', () => modal.close());
  modal.footer.querySelector('[data-modal-submit]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const payload = {
      title: form.querySelector('[name="title"]').value.trim(),
      summary: form.querySelector('[name="summary"]').value.trim(),
      body_md: form.querySelector('[name="body_md"]').value,
      aliases: normalizeList(form.querySelector('[name="aliases"]').value),
      tags: normalizeList(form.querySelector('[name="tags"]').value),
    };
    if (!payload.title) {
      toast('词条标题不能为空', 'error');
      form.querySelector('[name="title"]').focus();
      return;
    }
    if (!payload.body_md.trim()) {
      toast('词条正文不能为空', 'error');
      form.querySelector('[name="body_md"]').focus();
      return;
    }
    setButtonBusy(button, true, '保存中…');
    try {
      await updateWikiPage(data.id, payload);
      toast('词条已保存，版本号 +1', 'success');
      modal.close();
      onSaved?.();
    } catch (err) {
      toast(err?.message || '保存词条失败', 'error');
      setButtonBusy(button, false);
    }
  });
}

/* ------------------------------------------------------------------ */
/* 源文件 Tab：上传                                                    */
/* ------------------------------------------------------------------ */
function uploadItemHtml(item) {
  const toneClass =
    item.status === 'uploading'
      ? 'is-uploading'
      : item.status === 'error'
        ? 'is-error'
        : item.status === 'warning'
          ? 'is-warning'
          : 'is-success';
  const compiling = item.status === 'uploading' && item.percent >= 99;
  const statusText = compiling
    ? '编译中（LLM 生成词条）…'
    : item.status === 'uploading'
      ? `上传中 ${item.percent}%`
      : item.status === 'success'
        ? item.message || '已就绪'
        : item.status === 'warning'
          ? item.message || '编译失败'
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

/** 上传进度只更新对应节点，避免每个 progress 事件都整表重绘。 */
function updateUploadProgress(id, percent) {
  const item = state.uploads.find((entry) => entry.id === id);
  if (!item) return;
  item.percent = percent;
  const node = document.querySelector(`[data-upload="${id}"]`);
  if (!node) return;
  const bar = node.querySelector('.progress span');
  if (bar) bar.style.width = `${Math.max(2, Math.min(100, Number(percent) || 0))}%`;
  const statusText = node.querySelector('.upload-status span:last-child');
  if (statusText) {
    statusText.textContent =
      percent >= 99 ? '编译中（LLM 生成词条）…' : `上传中 ${Math.round(Number(percent) || 0)}%`;
  }
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
      onProgress: (percent) => updateUploadProgress(id, percent),
    })
      .then((doc) => {
        if (doc?.status === 'failed') {
          updateUpload(id, { percent: 100, status: 'warning', message: doc.error || '编译失败' });
          toast(`《${doc.title || file.name}》编译失败：${doc.error || '未知原因'}`, 'error');
        } else {
          updateUpload(id, {
            percent: 100,
            status: 'success',
            message: `已编译 · ${Number(doc?.page_count || 0)} 个词条`,
          });
          toast(`《${doc?.title || file.name}》已编译 ${Number(doc?.page_count || 0)} 个词条`, 'success');
        }
      })
      .catch((err) => {
        updateUpload(id, { status: 'error', message: err.message || '上传失败' });
        toast(`${file.name}：${err.message || '上传失败'}`, 'error');
      });
  }

  if (queued) {
    if (state.uploadTimer) window.clearInterval(state.uploadTimer);
    const started = Date.now();
    const timerId = window.setInterval(() => {
      const pending = state.uploads.some((item) => item.status === 'uploading');
      if (!pending || Date.now() - started > 600000) {
        window.clearInterval(timerId);
        if (state.uploadTimer === timerId) state.uploadTimer = 0;
        refreshWiki({ silent: true });
      }
    }, 800);
    state.uploadTimer = timerId;
  }
}

/* ------------------------------------------------------------------ */
/* 源文件表格                                                          */
/* ------------------------------------------------------------------ */
function renderDocs() {
  const holder = document.getElementById('docsBody');
  if (!holder) return;
  if (state.docsError) {
    holder.innerHTML = errorHtml(state.docsError);
    return;
  }
  if (!state.docsLoaded) {
    holder.innerHTML = loadingHtml('正在加载源文件…');
    return;
  }
  if (!state.docs.length) {
    holder.innerHTML = emptyHtml({
      icon: 'file',
      title: '还没有源文件',
      desc: '上传校园通知、学生手册或制度文件，LLM 会把它编译成结构化 Wiki 词条。',
      action: `<button class="btn btn-primary" type="button" data-action="pick-file">${icon('upload')}<span>上传源文件</span></button>
        <button class="btn btn-ghost" type="button" data-action="seed">${icon('download')}<span>导入预编译快照</span></button>`,
    });
    return;
  }
  holder.innerHTML = `<div class="table-wrap">
    <table class="table docs-table">
      <thead><tr>
        <th>源文件</th><th>来源</th><th>类型</th><th>状态</th><th>词条数</th><th>大小</th><th>更新时间</th><th class="th-actions">操作</th>
      </tr></thead>
      <tbody>${state.docs
        .map((doc) => {
          const ext = doc.file_ext || extOf(doc.filename);
          const errorLine = doc.error
            ? `<span class="doc-error" title="${esc(doc.error)}">${esc(doc.error)}</span>`
            : '';
          return `<tr data-id="${esc(doc.id)}" class="${doc.status === 'failed' ? 'is-failed' : ''}">
            <td data-label="源文件">
              <div class="doc-cell">
                <span class="file-ico ext-${esc(extClass(ext))}">${esc(extLabel(ext))}</span>
                <div class="doc-cell-main">
                  <strong title="${esc(doc.title || doc.filename)}">${esc(doc.title || doc.filename)}</strong>
                  <span class="doc-filename" title="${esc(doc.filename)}">${esc(doc.filename)}</span>
                  ${errorLine}
                </div>
              </div>
            </td>
            <td data-label="来源">${docSourceBadge(doc)}</td>
            <td data-label="类型"><span class="badge badge-blue">${esc(extLabel(ext))}</span></td>
            <td data-label="状态">${docStatusBadge(doc.status, doc.error)}</td>
            <td data-label="词条数">${fmtNumber(doc.page_count || 0)}</td>
            <td data-label="大小">${fmtBytes(doc.size_bytes)}</td>
            <td data-label="更新时间"><span title="${esc(doc.updated_at || '')}">${esc(
              fmtRelative(doc.updated_at || doc.created_at),
            )}</span></td>
            <td data-label="操作" class="td-actions">
              <button class="icon-btn" type="button" data-action="doc-pages" data-id="${esc(doc.id)}" title="查看词条" aria-label="查看词条">${icon(
                'eye',
              )}</button>
              <button class="icon-btn" type="button" data-action="reindex" data-id="${esc(doc.id)}" title="重新编译" aria-label="重新编译">${icon(
                'refresh',
              )}</button>
              <button class="icon-btn" type="button" data-action="doc-download" data-id="${esc(
                doc.id,
              )}" title="下载源文件" aria-label="下载源文件">${icon('download')}</button>
              <button class="icon-btn is-danger" type="button" data-action="doc-delete" data-id="${esc(
                doc.id,
              )}" title="删除源文件" aria-label="删除源文件">${icon('trash')}</button>
            </td>
          </tr>`;
        })
        .join('')}</tbody>
    </table>
  </div>
  <p class="table-note">${icon(
    'info',
  )} 上传后同步调用 LLM 编译词条；失败时展示错误原因，可点击「重新编译」重试。</p>`;
}

async function openDocPages(docId) {
  const doc = state.docs.find((item) => item.id === docId);
  const modal = openModal({
    title: doc?.title || '源文件词条',
    subtitle: doc?.filename || '',
    size: 'lg',
    body: loadingHtml('正在加载该源文件编译的词条…'),
  });
  try {
    const data = await getDocument(docId);
    const pages = Array.isArray(data?.wiki_pages) ? data.wiki_pages : [];
    modal.body.innerHTML = `<div class="doc-pages-list">${
      pages.length
        ? pages
            .map(
              (page) => `<button class="doc-page-item" type="button" data-page-id="${esc(page.id)}">
                <span class="doc-page-title">${esc(page.title || '未命名词条')}</span>
                ${pageStatusBadge(page.status)}
                ${icon('chevron-right')}
              </button>`,
            )
            .join('')
        : `<div class="state state-empty state-compact">${icon(
            'book',
          )}<p>该源文件暂未编译出词条</p><span>可点击「重新编译」重试，或检查模型配置。</span></div>`
    }</div>
    ${
      data?.error
        ? `<div class="alert alert-error">${icon('alert')}<div><strong>编译错误</strong><p>${esc(
            data.error,
          )}</p></div></div>`
        : ''
    }`;
    modal.body.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-page-id]');
      if (!trigger) return;
      modal.close();
      openWikiPage(trigger.dataset.pageId);
    });
  } catch (err) {
    modal.body.innerHTML = `<div class="inline-error">${icon('alert')}<span>${esc(
      err?.message || '加载失败',
    )}</span></div>`;
  }
}

/* ------------------------------------------------------------------ */
/* 构建（全部重建）                                                    */
/* ------------------------------------------------------------------ */
function stopBuildPolling() {
  if (state.buildTimer) {
    window.clearInterval(state.buildTimer);
    state.buildTimer = 0;
  }
}

function renderBuildBanner() {
  const holder = document.getElementById('buildBanner');
  if (!holder) return;
  const build = state.build;
  if (!build) {
    holder.hidden = true;
    holder.innerHTML = '';
    return;
  }
  holder.hidden = false;
  const meta = BUILD_STATUS[build.status] || { label: build.status || '处理中', tone: 'slate' };
  const running = build.status === 'running' || build.status === 'pending';
  const parts = [];
  if (build.model) parts.push(esc(build.model));
  if (build.pages_created !== undefined) parts.push(`新增 ${fmtNumber(build.pages_created)}`);
  if (build.pages_updated !== undefined) parts.push(`更新 ${fmtNumber(build.pages_updated)}`);
  if (build.pages_removed !== undefined) parts.push(`删除 ${fmtNumber(build.pages_removed)}`);
  holder.innerHTML = `<div class="build-banner ${
    running ? 'is-running' : build.status === 'failed' ? 'is-failed' : 'is-done'
  }">
    ${running ? spinnerHtml() : icon(build.status === 'failed' ? 'alert' : 'check')}
    <div class="build-banner-main">
      <strong>全部重建：${esc(meta.label)}</strong>
      <span>${parts.join(' · ') || '正在重新编译全部源文件，请稍候…'}</span>
      ${build.error ? `<span class="build-error">${esc(build.error)}</span>` : ''}
    </div>
    ${running ? '' : `<button class="btn btn-sm btn-ghost" type="button" data-action="dismiss-build">关闭</button>`}
  </div>`;
}

function pollBuild(buildId) {
  stopBuildPolling();
  let attempts = 0;
  state.buildTimer = window.setInterval(async () => {
    attempts += 1;
    if (attempts > 240) {
      stopBuildPolling();
      state.build = { ...(state.build || {}), status: 'failed', error: '构建轮询超时，请稍后刷新查看结果。' };
      renderBuildBanner();
      return;
    }
    try {
      const build = await getWikiBuild(buildId);
      state.build = build || state.build;
      renderBuildBanner();
      const status = build?.status;
      if (status && status !== 'running' && status !== 'pending') {
        stopBuildPolling();
        if (status === 'failed') toast(build?.error || '全部重建失败', 'error');
        else toast('全部重建完成，Wiki 词条已更新', 'success');
        refreshWiki({ silent: true });
      }
    } catch (err) {
      if (attempts > 20) {
        stopBuildPolling();
        state.build = { ...(state.build || {}), status: 'failed', error: err?.message || '构建状态查询失败' };
        renderBuildBanner();
      }
    }
  }, 2500);
}

async function runRebuildAll(button) {
  setButtonBusy(button, true, '提交中…');
  try {
    const result = await rebuildWiki({});
    const buildId = result?.build_id || result?.id;
    state.build = {
      id: buildId,
      status: result?.status || 'running',
      pages_created: 0,
      pages_updated: 0,
      pages_removed: 0,
    };
    renderBuildBanner();
    toast('已提交全部重建任务，编译期间可继续其他操作', 'info');
    if (buildId) pollBuild(buildId);
    else {
      state.build = { ...state.build, status: 'failed', error: '接口未返回 build_id，无法轮询状态' };
      renderBuildBanner();
    }
  } catch (err) {
    toast(err?.message || '提交重建任务失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

/* ------------------------------------------------------------------ */
/* 源文件操作                                                          */
/* ------------------------------------------------------------------ */
async function reindexDoc(id, button) {
  setButtonBusy(button, true, '编译中…');
  try {
    const result = await reindexDocument(id);
    const build = result?.build || result;
    const doc = result?.document || result;
    if (build?.status === 'failed' || doc?.status === 'failed') {
      toast(`重新编译失败：${build?.error || doc?.error || '未知原因'}`, 'error');
    } else {
      toast(`已重新编译，生成 ${Number(doc?.page_count ?? build?.pages_created ?? 0)} 个词条`, 'success');
    }
    refreshWiki({ silent: true });
  } catch (err) {
    toast(err?.message || '重新编译失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

function downloadDoc(id) {
  if (!id) return;
  // 浏览器直接下载（服务端返回 Content-Disposition: attachment）
  const link = document.createElement('a');
  link.href = documentDownloadUrl(id);
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function removeDoc(id) {
  const doc = state.docs.find((item) => item.id === id);
  const ok = await confirmDialog({
    title: '删除源文件',
    message: `确定删除《${doc?.title || doc?.filename || '该文件'}》吗？`,
    detail: '其独占词条会一并删除；共享词条只移除该来源，物理文件不可恢复。',
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    const result = await deleteDocument(id);
    toast(
      `源文件已删除${result?.pages_removed ? `，移除 ${result.pages_removed} 个独占词条` : ''}`,
      'success',
    );
    refreshWiki({ silent: true });
  } catch (err) {
    toast(err?.message || '删除源文件失败', 'error');
  }
}

async function runSeed(button) {
  setButtonBusy(button, true, '导入中…');
  try {
    const result = await seedWiki();
    const parts = [];
    if (result?.imported !== undefined) parts.push(`导入 ${result.imported} 条`);
    if (result?.documents !== undefined) parts.push(`源文件 ${result.documents}`);
    if (result?.pages !== undefined) parts.push(`词条 ${result.pages}`);
    if (result?.sources !== undefined) parts.push(`来源 ${result.sources}`);
    if (result?.skipped !== undefined) parts.push(`跳过 ${result.skipped}`);
    toast(`预编译快照导入完成${parts.length ? `：${parts.join(' / ')}` : ''}`, 'success', {
      title: '导入完成',
    });
    refreshWiki({ silent: true });
  } catch (err) {
    toast(err?.message || '导入预编译快照失败', 'error');
  } finally {
    setButtonBusy(button, false);
  }
}

function refreshWiki({ silent = false } = {}) {
  loadStats();
  loadTree();
  loadPages({ silent });
  loadDocs({ silent });
}

/* ------------------------------------------------------------------ */
/* 快速收录（链接 / 文本 / 文件）                                      */
/* ------------------------------------------------------------------ */
function parseTagsInput(value) {
  return String(value || '')
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 10);
}

const URL_PATTERN = /https?:\/\/[^\s<>"'）】》」』]+/gi;
const URL_TRAILING = /[，。；、,;:：!！?？…"'）)\]】》」』]+$/;

function normalizeCapturedUrl(raw) {
  return String(raw || '')
    .trim()
    .replace(URL_TRAILING, '')
    .replace(URL_TRAILING, '');
}

/**
 * 容错解析多行链接：
 * - 支持「标题 + 链接」、行首序号（1. / 1、/（1））、全角空格、全角冒号、链接末尾中文标点；
 * - 一行多个链接按空白分隔提取；
 * - 不含链接的非空行标记为 invalid，不阻断整批；
 * - 去重，超过 5 条截断并返回 truncated 数量。
 */
function parseCaptureUrls(text) {
  const lines = String(text || '').split(/\r?\n/);
  const entries = [];
  lines.forEach((line) => {
    const normalizedLine = line.replace(/\u3000/g, ' ').trim();
    if (!normalizedLine) return;
    const matches = normalizedLine.match(URL_PATTERN) || [];
    if (!matches.length) {
      entries.push({ url: '', raw: normalizedLine, valid: false });
      return;
    }
    matches.forEach((match) => {
      const url = normalizeCapturedUrl(match);
      if (url) entries.push({ url, raw: normalizedLine, valid: true });
    });
  });
  const seen = new Set();
  const unique = [];
  entries.forEach((entry) => {
    if (!entry.valid) {
      unique.push(entry);
      return;
    }
    if (seen.has(entry.url)) return;
    seen.add(entry.url);
    unique.push(entry);
  });
  const truncated = Math.max(0, unique.length - 5);
  return { entries: unique.slice(0, 5), truncated, urls: unique.slice(0, 5).filter((e) => e.valid).map((e) => e.url) };
}

function importPageChips(pages) {
  if (!Array.isArray(pages) || !pages.length) return '';
  return `<div class="capture-pages">${pages
    .filter((page) => page && page.id)
    .map(
      (page) =>
        `<button class="tag-chip is-link" type="button" data-action="open-page" data-id="${esc(
          page.id,
        )}">${icon('chevron-right')}${esc(page.title || '查看词条')}</button>`,
    )
    .join('')}</div>`;
}

function linkResultHtml(item) {
  const meta = {
    pending: { icon: 'clock', cls: 'is-pending', text: item.message || '等待收录' },
    fetching: { icon: 'sparkles', cls: 'is-fetching', text: item.message || '抓取正文 / 编译词条中…' },
    success: { icon: 'check', cls: 'is-success', text: item.message || '已收录' },
    duplicated: { icon: 'copy', cls: 'is-duplicated', text: item.message || '已收录过，跳过编译' },
    error: { icon: 'alert', cls: 'is-error', text: item.message || '收录失败' },
  }[item.status] || { icon: 'clock', cls: 'is-pending', text: '' };
  const iconHtml =
    item.status === 'fetching'
      ? '<span class="spinner spinner-xs" aria-hidden="true"></span>'
      : icon(meta.icon);
  const copyBtn = item.url
    ? `<button class="icon-btn capture-copy" type="button" data-action="copy-capture-url" data-url="${esc(
        item.url,
      )}" title="复制链接" aria-label="复制链接">${icon('copy')}</button>`
    : '';
  const fallbackBtn =
    item.sizeLimit && item.status === 'error'
      ? `<button class="link-btn capture-text-fallback" type="button" data-action="use-text-import">${icon('text')}改用文本收录</button>`
      : '';
  return `<div class="capture-result-item ${meta.cls}">
    <span class="capture-result-icon">${iconHtml}</span>
    <div class="capture-result-main">
      <span class="capture-result-url" title="${esc(item.url || '')}">${esc(item.url || '')}</span>
      <span class="capture-result-msg">${esc(meta.text)}</span>
      ${item.title ? `<span class="capture-result-doc">源文件：${esc(item.title)}</span>` : ''}
      ${fallbackBtn}
      ${importPageChips(item.pages)}
    </div>
    ${copyBtn}
  </div>`;
}

function renderCaptureResults() {
  const linkHolder = document.getElementById('captureLinkResults');
  if (linkHolder) {
    linkHolder.innerHTML = state.capture.linkResults.length
      ? state.capture.linkResults.map(linkResultHtml).join('')
      : '';
  }
  const textHolder = document.getElementById('captureTextResults');
  if (textHolder) {
    const result = state.capture.textResult;
    if (!result) {
      textHolder.innerHTML = '';
    } else if (result.status === 'fetching') {
      textHolder.innerHTML = `<div class="capture-result-item is-fetching"><span class="capture-result-icon"><span class="spinner spinner-xs"></span></span><div class="capture-result-main"><span class="capture-result-msg">正在编译词条，请稍候…</span></div></div>`;
    } else if (result.status === 'success' || result.status === 'duplicated') {
      textHolder.innerHTML = `<div class="capture-result-item ${result.status === 'duplicated' ? 'is-duplicated' : 'is-success'}">
        <span class="capture-result-icon">${icon(result.status === 'duplicated' ? 'copy' : 'check')}</span>
        <div class="capture-result-main">
          <span class="capture-result-msg">${esc(result.message)}</span>
          ${result.title ? `<span class="capture-result-doc">源文件：${esc(result.title)}</span>` : ''}
          ${importPageChips(result.pages)}
        </div>
      </div>`;
    } else {
      textHolder.innerHTML = `<div class="capture-result-item is-error"><span class="capture-result-icon">${icon(
        'alert',
      )}</span><div class="capture-result-main"><span class="capture-result-msg">${esc(
        result.message || '收录失败',
      )}</span>${
        result.sizeLimit
          ? `<button class="link-btn capture-text-fallback" type="button" data-action="use-text-import">${icon('text')}改用文本收录</button>`
          : ''
      }</div></div>`;
    }
  }
}

const IMPORT_SIZE_HINT = '可改用「文本」收录粘贴正文，或联系管理员调大 NPU_URL_IMPORT_MAX_BYTES';

function importErrorInfo(err) {
  const raw = typeof err === 'string' ? err : err?.message || '';
  const status = typeof err === 'object' && err ? err.status : 0;
  if (status === 404 || status === 405) {
    return { message: '收录接口暂不可用（后端可能仍在部署），请稍后重试或直接上传文件', sizeLimit: false };
  }
  // 网页抓取上限（默认 8MB）超限时给出可执行的替代方案；后端 detail 已含环境变量名则不重复追加
  if (/过大|MAX_BYTES|抓取上限|上限/i.test(raw)) {
    const hint = /NPU_URL_IMPORT_MAX_BYTES/i.test(raw) ? '' : `；${IMPORT_SIZE_HINT}`;
    return { message: `${raw || '网页内容过大，抓取失败'}${hint}`, sizeLimit: true };
  }
  return { message: raw || '收录失败', sizeLimit: false };
}

function importErrorMessage(err) {
  return importErrorInfo(err).message;
}

function normalizeSingleImport(data) {
  const document = data?.document || {};
  const pages = Array.isArray(data?.pages) ? data.pages : [];
  const duplicated = Boolean(data?.duplicated);
  return {
    status: duplicated ? 'duplicated' : 'success',
    title: document.title || '',
    pages,
    message: duplicated
      ? `已收录过，跳过编译${pages.length ? ` · 现有 ${pages.length} 个词条` : ''}`
      : `已收录《${document.title || '新资料'}》${pages.length ? ` · 生成 ${pages.length} 个词条` : ''}`,
  };
}

async function runImportLinks(button) {
  if (state.capture.importingLinks) return;
  const urlsInput = document.getElementById('captureUrls');
  const tagsInput = document.getElementById('captureUrlTags');
  const parsed = parseCaptureUrls(urlsInput?.value || '');
  if (!parsed.entries.length) {
    toast('请粘贴至少一个网页链接（一行一个，可带标题）', 'warning');
    urlsInput?.focus();
    return;
  }
  if (parsed.truncated) {
    toast(`一次最多收录 5 条链接，已截断后面的 ${parsed.truncated} 条`, 'warning');
  }
  const tags = parseTagsInput(tagsInput?.value);
  const invalidEntries = parsed.entries.filter((entry) => !entry.valid);
  const validEntries = parsed.entries.filter((entry) => entry.valid);
  const invalidResult = (entry) => ({
    url: entry.raw || '（无法识别）',
    status: 'error',
    message: '不是有效的 http(s) 链接，请检查后重试',
    pages: [],
  });
  state.capture.importingLinks = true;
  state.capture.linkResults = parsed.entries.map((entry) =>
    entry.valid
      ? {
          url: entry.url,
          status: 'fetching',
          message:
            validEntries.length === 1 ? '抓取正文 / 编译词条中…' : '排队收录中（抓取 → 编译词条）…',
          pages: [],
        }
      : invalidResult(entry),
  );
  renderCaptureResults();
  if (!validEntries.length) {
    state.capture.importingLinks = false;
    toast('未识别到有效链接：请粘贴 http(s) 开头的网页地址', 'error');
    return;
  }
  setButtonBusy(button, true, '收录中…');
  try {
    let resultByUrl = new Map();
    if (validEntries.length === 1) {
      const data = await importUrl({ url: validEntries[0].url, ...(tags.length ? { tags } : {}) });
      const normalized = normalizeSingleImport(data);
      resultByUrl.set(validEntries[0].url, { url: validEntries[0].url, ...normalized });
    } else {
      const urls = validEntries.map((entry) => entry.url);
      const data = await importBatch({ urls, ...(tags.length ? { tags } : {}) });
      const items = Array.isArray(data?.items) ? data.items : [];
      validEntries.forEach((entry, index) => {
        const item = items[index] || items.find((candidate) => candidate?.url === entry.url) || {};
        if (item.ok === false || item.error) {
          const info = importErrorInfo(item.error || '收录失败');
          resultByUrl.set(entry.url, {
            url: entry.url,
            status: 'error',
            message: info.message,
            sizeLimit: info.sizeLimit,
            pages: [],
          });
          return;
        }
        const pages = Array.isArray(item.pages) ? item.pages : [];
        resultByUrl.set(entry.url, {
          url: entry.url,
          status: item.duplicated ? 'duplicated' : 'success',
          title: item.document_title || item.title || '',
          pages,
          message: item.duplicated
            ? '已收录过，跳过编译'
            : `已收录${pages.length ? ` · 生成 ${pages.length} 个词条` : ''}`,
        });
      });
    }
    state.capture.linkResults = parsed.entries.map((entry) =>
      entry.valid ? resultByUrl.get(entry.url) || { url: entry.url, status: 'error', message: '未返回结果', pages: [] } : invalidResult(entry),
    );
    const okCount = state.capture.linkResults.filter((item) => item.status === 'success').length;
    const dupCount = state.capture.linkResults.filter((item) => item.status === 'duplicated').length;
    const failCount = state.capture.linkResults.filter((item) => item.status === 'error').length;
    if (okCount) {
      toast(
        `链接收录完成：新增 ${okCount} 条${dupCount ? `，重复 ${dupCount} 条` : ''}${failCount ? `，失败 ${failCount} 条` : ''}`,
        'success',
      );
    } else if (dupCount) {
      toast(`所选链接均已收录过（${dupCount} 条），未重复编译`, 'info');
    } else {
      toast('链接收录失败，请查看逐条原因', 'error');
    }
    if (invalidEntries.length) {
      toast(`有 ${invalidEntries.length} 行不是有效链接，已跳过`, 'info');
    }
    if (state.capture.linkResults.some((item) => item.sizeLimit)) {
      toast('有链接抓取超限，可点「改用文本收录」粘贴正文', 'warning', { duration: 7000 });
    }
    if (okCount || dupCount) {
      if (urlsInput) urlsInput.value = '';
      refreshWiki({ silent: true });
    }
  } catch (err) {
    const info = importErrorInfo(err);
    state.capture.linkResults = state.capture.linkResults.map((item) => ({
      ...item,
      status: 'error',
      message: item.status === 'error' ? item.message : info.message,
      sizeLimit: info.sizeLimit,
    }));
    toast(info.message, 'error');
  } finally {
    state.capture.importingLinks = false;
    setButtonBusy(button, false);
    renderCaptureResults();
  }
}

async function runImportText(button) {
  if (state.capture.importingText) return;
  const titleInput = document.getElementById('captureTitle');
  const textInput = document.getElementById('captureText');
  const tagsInput = document.getElementById('captureTextTags');
  const text = String(textInput?.value || '').trim();
  const title = String(titleInput?.value || '').trim();
  const tags = parseTagsInput(tagsInput?.value);
  if (text.length < 30) {
    toast('正文至少 30 字，请粘贴完整通知内容', 'warning');
    textInput?.focus();
    return;
  }
  if (text.length > 200000) {
    toast('正文超过 200000 字上限，请拆分后收录', 'error');
    return;
  }
  state.capture.importingText = true;
  state.capture.textResult = { status: 'fetching' };
  renderCaptureResults();
  setButtonBusy(button, true, '编译中…');
  try {
    const data = await importText({ title, text, ...(tags.length ? { tags } : {}) });
    const normalized = normalizeSingleImport(data);
    state.capture.textResult = { ...normalized };
    toast(
      normalized.status === 'duplicated' ? '该文本资料已收录过，跳过编译' : `文本收录成功${normalized.pages.length ? `，生成 ${normalized.pages.length} 个词条` : ''}`,
      normalized.status === 'duplicated' ? 'info' : 'success',
    );
    if (textInput) textInput.value = '';
    if (titleInput) titleInput.value = '';
    refreshWiki({ silent: true });
  } catch (err) {
    const info = importErrorInfo(err);
    state.capture.textResult = { status: 'error', message: info.message, sizeLimit: info.sizeLimit };
    toast(info.message, 'error');
  } finally {
    state.capture.importingText = false;
    setButtonBusy(button, false);
    renderCaptureResults();
  }
}

function setCaptureMode(mode) {
  const next = ['links', 'text', 'file'].includes(mode) ? mode : 'links';
  state.capture.mode = next;
  const panels = {
    links: document.getElementById('captureLinksPanel'),
    text: document.getElementById('captureTextPanel'),
    file: document.getElementById('captureFilePanel'),
  };
  Object.entries(panels).forEach(([key, panel]) => {
    if (panel) panel.hidden = key !== next;
  });
  document.querySelectorAll('.capture-tab').forEach((tab) => {
    const active = tab.dataset.mode === next;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

function setCaptureCollapsed(collapsed) {
  state.capture.collapsed = Boolean(collapsed);
  const card = document.getElementById('quickCapture');
  const body = document.getElementById('quickCaptureBody');
  const toggle = document.querySelector('[data-action="toggle-capture"]');
  if (card) card.classList.toggle('is-collapsed', state.capture.collapsed);
  if (body) body.hidden = state.capture.collapsed;
  if (toggle) toggle.setAttribute('aria-expanded', state.capture.collapsed ? 'false' : 'true');
  try {
    localStorage.setItem('npu.capture.collapsed', state.capture.collapsed ? '1' : '0');
  } catch {
    /* ignore */
  }
}


/* ------------------------------------------------------------------ */
/* 视图入口                                                            */
/* ------------------------------------------------------------------ */
export function mountManage(root, context = {}) {
  let alive = true;
  const initialPageId = context?.query?.get('page') || '';
  state.readOnly = false;
  state.manageTab = 'capture';
  state.tab = 'pages';
  state.search = { query: '', running: false, result: null, error: '' };
  state.build = null;
  stopBuildPolling();

  root.innerHTML = `<div class="page manage-page">
    <div class="manage-tabs" role="tablist" aria-label="内容管理">
      <button class="seg-tab is-active" type="button" role="tab" aria-selected="true" data-action="manage-tab" data-tab="capture">收录资料</button>
      <button class="seg-tab" type="button" role="tab" aria-selected="false" data-action="manage-tab" data-tab="sources">源文件</button>
      <button class="seg-tab" type="button" role="tab" aria-selected="false" data-action="manage-tab" data-tab="pages">词条维护</button>
    </div>

    <section class="manage-panel" id="manageCapturePanel">
    <section class="card quick-capture" id="quickCapture">
      <header class="quick-capture-head">
        <div class="quick-capture-title">
          <span class="quick-capture-icon">${icon('plus')}</span>
          <div>
            <h2>快速收录</h2>
            <p>粘贴链接或文字，或上传文件；LLM 自动编译为 Wiki 词条</p>
          </div>
        </div>
        <button class="icon-btn quick-capture-toggle" type="button" data-action="toggle-capture" aria-expanded="true" aria-controls="quickCaptureBody" title="收起 / 展开快速收录">
          ${icon('chevron-down')}
        </button>
      </header>
      <div class="quick-capture-body" id="quickCaptureBody">
        <div class="capture-tabs" role="tablist" aria-label="收录方式">
          <button class="capture-tab is-active" type="button" role="tab" aria-selected="true" data-action="capture-mode" data-mode="links">${icon(
            'link',
          )}<span>链接</span></button>
          <button class="capture-tab" type="button" role="tab" aria-selected="false" data-action="capture-mode" data-mode="text">${icon(
            'message',
          )}<span>文本</span></button>
          <button class="capture-tab" type="button" role="tab" aria-selected="false" data-action="capture-mode" data-mode="file">${icon(
            'upload',
          )}<span>文件</span></button>
        </div>

        <div class="capture-panel" id="captureLinksPanel">
          <textarea class="input capture-textarea" id="captureUrls" rows="3" placeholder="一行一个链接，最多 5 个&#10;例如：https://mp.weixin.qq.com/s/xxxx"></textarea>
          <div class="capture-row">
            <input class="input" id="captureUrlTags" placeholder="标签（可选，逗号分隔）" />
            <button class="btn btn-primary capture-submit" type="button" data-action="import-links" id="importLinksBtn">${icon(
              'link',
            )}<span>收录链接</span></button>
          </div>
          <p class="capture-hint">${icon('info')}<span>微信 / QQ 可直接把链接转发给机器人（需管理员）；手机访问请到「设置 → 移动访问与安全」扫码，再在手机上粘贴链接即可。</span></p>
          <div class="capture-results" id="captureLinkResults"></div>
        </div>

        <div class="capture-panel" id="captureTextPanel" hidden>
          <input class="input" id="captureTitle" maxlength="120" placeholder="标题（可选，留空取正文首行）" />
          <textarea class="input capture-textarea" id="captureText" rows="5" placeholder="粘贴微信 / QQ 转发的文字资料（30–200000 字）"></textarea>
          <div class="capture-row">
            <input class="input" id="captureTextTags" placeholder="标签（可选，逗号分隔）" />
            <button class="btn btn-primary capture-submit" type="button" data-action="import-text" id="importTextBtn">${icon(
              'file',
            )}<span>收录文本</span></button>
          </div>
          <p class="capture-hint">${icon('info')}<span>适合群通知、口头通知等无链接内容；会生成源文件并编译词条。</span></p>
          <div class="capture-results" id="captureTextResults"></div>
        </div>

        <div class="capture-panel" id="captureFilePanel" hidden>
          <div class="dropzone capture-dropzone" id="dropzone" role="button" tabindex="0" aria-label="上传源文件">
            <div class="dropzone-icon">${icon('upload')}</div>
            <strong>拖拽文件到这里，或点击选择文件</strong>
            <span>保存 → 解析 → LLM 编译词条（需要可用模型）</span>
            <input type="file" id="fileInput" multiple hidden />
          </div>
          <p class="capture-hint" id="uploadHint">支持 md / txt / pdf / docx / xlsx / csv / json / html，单个文件 ≤ 20MB；上传后由 LLM 同步编译为 Wiki 词条。</p>
          <div class="upload-queue" id="uploadQueue" hidden></div>
        </div>
      </div>
    </section>
    </section>

    <section class="manage-panel" id="manageSourcesPanel" hidden>
      <section class="stats-grid manage-stats" id="wikiStats" aria-label="Wiki 统计"></section>
      <section class="card manage-toolbar-card">
        <div class="manage-toolbar">
          <button class="btn btn-primary btn-sm" type="button" data-action="pick-file">${icon('upload')}<span>上传源文件</span></button>
          <button class="btn btn-ghost btn-sm" type="button" data-action="seed">${icon('download')}<span>导入快照</span></button>
          <button class="btn btn-ghost btn-sm" type="button" data-action="rebuild-all">${icon('refresh')}<span>全部重建</span></button>
          <button class="btn btn-ghost btn-sm" type="button" data-action="refresh-docs">${icon('refresh')}<span>刷新</span></button>
        </div>
      </section>
      <div class="build-banner" id="buildBanner" hidden></div>
      <section class="card docs-card">
        <header class="card-head">
          <div>
            <h2>${icon('folder')}源文件 <span class="count" id="docCount"></span></h2>
            <p>失败时展示错误原因并可重新编译</p>
          </div>
        </header>
        <div id="docsBody">${loadingHtml('正在加载源文件…')}</div>
      </section>
    </section>

    <section class="manage-panel" id="managePagesPanel" hidden>
      <section class="card manage-toolbar-card">
        <div class="manage-toolbar">
          <form class="wiki-search" id="wikiSearchForm">
            <input class="input" id="wikiSearchInput" type="search" placeholder="搜索词条，如：国家奖学金怎么申请" aria-label="搜索词条" />
            <button class="btn btn-primary" type="submit" id="wikiSearchBtn">${icon('search')}<span>搜索</span></button>
          </form>
          <button class="btn btn-ghost btn-sm" type="button" data-action="refresh-pages">${icon('refresh')}<span>刷新</span></button>
        </div>
        <div class="tag-filter" id="tagFilter" hidden></div>
      </section>
      <div id="wikiSearchResult" hidden></div>
      <div id="wikiPagesBody">${loadingHtml('正在加载 Wiki 词条…')}</div>
    </section>
  </div>`;

  const page = root.querySelector('.manage-page');
  const managePanels = {
    capture: root.querySelector('#manageCapturePanel'),
    sources: root.querySelector('#manageSourcesPanel'),
    pages: root.querySelector('#managePagesPanel'),
  };
  const searchForm = root.querySelector('#wikiSearchForm');
  const searchInput = root.querySelector('#wikiSearchInput');
  const dropzone = root.querySelector('#dropzone');
  const fileInput = root.querySelector('#fileInput');

  /* 快速收录：恢复折叠状态 / 默认模式 */
  try {
    state.capture.collapsed = localStorage.getItem('npu.capture.collapsed') === '1';
  } catch {
    state.capture.collapsed = false;
  }
  setCaptureCollapsed(state.capture.collapsed);
  setCaptureMode('links');
  renderCaptureResults();

  const switchManageTab = (tab) => {
    state.manageTab = ['capture', 'sources', 'pages'].includes(tab) ? tab : 'capture';
    Object.entries(managePanels).forEach(([key, panel]) => {
      if (panel) panel.hidden = key !== state.manageTab;
    });
    page.querySelectorAll('.seg-tab').forEach((btn) => {
      const active = btn.dataset.tab === state.manageTab;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    if (state.manageTab === 'sources' && !state.docsLoaded && !state.docsError) loadDocs();
    if (state.manageTab === 'pages' && !state.pagesLoaded) loadPages();
  };
  switchManageTab('capture');

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

  /* 搜索 */
  searchForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    if (!query) {
      toast('请输入搜索关键词', 'warning');
      searchInput.focus();
      return;
    }
    runWikiSearch(query);
  });

  page.addEventListener('click', async (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'manage-tab':
        switchManageTab(trigger.dataset.tab);
        break;
      case 'refresh-pages':
        loadPages();
        loadTree();
        loadStats();
        break;
      case 'filter-tag':
        state.activeTag = trigger.dataset.tag || '';
        renderTagFilter();
        loadPages();
        break;
      case 'open-page':
        openWikiPage(trigger.dataset.id);
        break;
      case 'clear-search':
        state.search = { query: '', running: false, result: null, error: '' };
        if (searchInput) searchInput.value = '';
        renderPages();
        break;
      case 'seed':
        runSeed(trigger);
        break;
      case 'pick-file':
        setCaptureCollapsed(false);
        setCaptureMode('file');
        window.setTimeout(pickFile, 60);
        break;
      case 'toggle-capture':
        setCaptureCollapsed(!state.capture.collapsed);
        break;
      case 'capture-mode':
        setCaptureMode(trigger.dataset.mode);
        break;
      case 'import-links':
        runImportLinks(trigger);
        break;
      case 'import-text':
        runImportText(trigger);
        break;
      case 'copy-capture-url': {
        const ok = await copyText(trigger.dataset.url || '');
        toast(ok ? '链接已复制' : '复制失败，请手动选择链接复制', ok ? 'success' : 'error', { duration: 2400 });
        break;
      }
      case 'use-text-import': {
        setCaptureCollapsed(false);
        setCaptureMode('text');
        const textInput = document.getElementById('captureText');
        textInput?.focus();
        toast('已切换到「文本」收录：粘贴正文即可，无需担心网页抓取上限', 'info', { duration: 4000 });
        break;
      }
      case 'refresh-docs':
        loadDocs();
        break;
      case 'rebuild-all':
        runRebuildAll(trigger);
        break;
      case 'doc-pages':
        openDocPages(trigger.dataset.id);
        break;
      case 'reindex':
        reindexDoc(trigger.dataset.id, trigger);
        break;
      case 'doc-delete':
        removeDoc(trigger.dataset.id);
        break;
      case 'doc-download':
        downloadDoc(trigger.dataset.id, trigger);
        break;
      case 'clear-uploads':
        state.uploads = state.uploads.filter((item) => item.status === 'uploading');
        renderUploads();
        break;
      case 'dismiss-build':
        state.build = null;
        renderBuildBanner();
        break;
      case 'retry':
        loadPages();
        loadDocs();
        loadStats();
        break;
      default:
        break;
    }
  });

  page.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const trigger = event.target.closest('[data-action="open-page"]');
    if (!trigger) return;
    event.preventDefault();
    openWikiPage(trigger.dataset.id);
  });

  /* 初始加载 */
  renderStats();
  renderTagFilter();
  renderDocs();
  loadStats();
  loadTree();
  loadPages();
  loadSystemInfo();
  loadDocs({ silent: true });
  if (initialPageId) openWikiPage(initialPageId, { fromHash: true });

  return () => {
    alive = false;
    stopBuildPolling();
    if (state.uploadTimer) {
      window.clearInterval(state.uploadTimer);
      state.uploadTimer = 0;
    }
  };
}

/* ------------------------------------------------------------------ */
/* 知识库（学生浏览页，只读）                                          */
/* ------------------------------------------------------------------ */
export function mount(root, context = {}) {
  let alive = true;
  const initialPageId = context?.query?.get('page') || '';
  state.readOnly = true;
  state.tab = 'pages';
  state.search = { query: '', running: false, result: null, error: '' };
  state.build = null;
  stopBuildPolling();

  root.innerHTML = `<div class="page knowledge-page browse-page">
    <section class="browse-search-card">
      <form class="wiki-search browse-search" id="wikiSearchForm" role="search">
        <input class="input" id="wikiSearchInput" type="search" placeholder="搜索校园知识，如：国家奖学金、请假流程" aria-label="搜索词条" />
        <button class="btn btn-primary" type="submit" id="wikiSearchBtn">${icon('search')}<span>搜索</span></button>
      </form>
    </section>
    <div class="tag-filter browse-tags" id="tagFilter" hidden></div>
    <div id="wikiSearchResult" hidden></div>
    <div id="wikiPagesBody">${loadingHtml('正在加载知识词条…')}</div>
  </div>`;

  const page = root.querySelector('.browse-page');
  const searchForm = root.querySelector('#wikiSearchForm');
  const searchInput = root.querySelector('#wikiSearchInput');

  searchForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    if (!query) {
      toast('请输入搜索关键词', 'warning');
      searchInput.focus();
      return;
    }
    runWikiSearch(query);
  });

  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'filter-tag':
        state.activeTag = trigger.dataset.tag || '';
        renderTagFilter();
        loadPages();
        break;
      case 'open-page':
        openWikiPage(trigger.dataset.id);
        break;
      case 'clear-search':
        state.search = { query: '', running: false, result: null, error: '' };
        if (searchInput) searchInput.value = '';
        renderPages();
        break;
      case 'go-manage':
        location.hash = '#/manage';
        break;
      case 'retry':
        loadPages();
        loadTree();
        break;
      default:
        break;
    }
  });

  page.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const trigger = event.target.closest('[data-action="open-page"]');
    if (!trigger) return;
    event.preventDefault();
    openWikiPage(trigger.dataset.id);
  });

  renderTagFilter();
  loadTree();
  loadPages();
  if (initialPageId) openWikiPage(initialPageId, { fromHash: true });

  return () => {
    alive = false;
    stopBuildPolling();
  };
}

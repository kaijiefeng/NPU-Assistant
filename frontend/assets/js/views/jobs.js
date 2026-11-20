/** views/jobs.js — 紧凑任务与告警列表。 */
import { ackAlert, cancelJob, listAlerts, listJobs, resolveAlert, retryJob } from '../api.js';
import { badge, confirmDialog, esc, fmtDateTime, fmtRelative, icon, setButtonBusy, toast } from '../ui.js';
import { alertTitle, categoryLabel, jobLabel } from '../ops-labels.js';

const STATUS_LABEL = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' };
const STATUS_TONE = { queued: 'slate', running: 'blue', succeeded: 'green', failed: 'red', cancelled: 'slate' };
const LEVEL_LABEL = { info: '提示', warning: '警告', error: '错误', critical: '严重' };
const LEVEL_TONE = { info: 'blue', warning: 'amber', error: 'red', critical: 'red' };
const ALERT_STATUS = { open: '待处理', acknowledged: '已确认', resolved: '已解决' };
const state = { tab: 'jobs', filter: '', jobs: [], alerts: [], summary: {}, loading: false, error: '' };

function jobHtml(job, opened) {
  const active = ['queued', 'running'].includes(job.status);
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const type = jobLabel(job.type);
  const message = job.message && job.message !== job.type ? alertTitle(job.message) : STATUS_LABEL[job.status] || '等待处理';
  const retry = ['failed', 'cancelled'].includes(job.status) && job.attempts < job.max_attempts;
  return `<div class="ops-list-row ops-job-row" role="row">
    <div class="ops-cell ops-primary" role="cell"><strong>${esc(type)}</strong><span class="ops-description" title="${esc(message)}">${esc(message)}</span></div>
    <div class="ops-cell" role="cell" data-label="状态">${badge(STATUS_LABEL[job.status] || '未知状态', STATUS_TONE[job.status] || 'slate')}</div>
    <div class="ops-cell ops-progress-cell" role="cell" data-label="进度"><span class="ops-progress"><span style="width:${progress}%"></span></span><span>${progress}%</span></div>
    <div class="ops-cell ops-time" role="cell" data-label="更新于" title="${esc(fmtDateTime(job.updated_at || job.created_at))}">${esc(fmtRelative(job.updated_at || job.created_at))}</div>
    <div class="ops-cell ops-actions" role="cell">
      ${retry ? `<button class="btn btn-xs btn-ghost" type="button" data-action="job-retry" data-id="${esc(job.id)}">重试</button>` : ''}
      ${active ? `<button class="btn btn-xs btn-ghost" type="button" data-action="job-cancel" data-id="${esc(job.id)}">取消</button>` : ''}
    </div>
    <details class="ops-detail" data-detail-key="job-${esc(job.id)}" ${opened.has(`job-${job.id}`) ? 'open' : ''}><summary>详情</summary><dl>
        <div><dt>任务编号</dt><dd>${esc(job.id)}</dd></div><div><dt>任务类型</dt><dd>${esc(job.type)}</dd></div>
        <div><dt>执行次数</dt><dd>${Number(job.attempts || 0)} / ${Number(job.max_attempts || 2)}</dd></div>
        <div><dt>发起人</dt><dd>${esc(job.created_by || '系统')}</dd></div>
        <div><dt>创建时间</dt><dd>${esc(fmtDateTime(job.created_at))}</dd></div>
        ${job.error ? `<div class="is-error"><dt>失败原因</dt><dd>${esc(job.error)}</dd></div>` : ''}
      </dl></details>
  </div>`;
}

function alertHtml(item, opened) {
  return `<div class="ops-list-row ops-alert-row" role="row">
    <div class="ops-cell ops-primary" role="cell"><strong>${esc(alertTitle(item.title))}</strong><span class="ops-description">${esc(categoryLabel(item.category))}</span></div>
    <div class="ops-cell" role="cell" data-label="级别">${badge(LEVEL_LABEL[item.level] || '提示', LEVEL_TONE[item.level] || 'slate')}</div>
    <div class="ops-cell" role="cell" data-label="状态">${badge(ALERT_STATUS[item.status] || '已记录', item.status === 'resolved' ? 'green' : 'slate')}</div>
    <div class="ops-cell ops-time" role="cell" data-label="发生于" title="${esc(fmtDateTime(item.created_at))}">${esc(fmtRelative(item.created_at))}</div>
    <div class="ops-cell ops-actions" role="cell">
      ${item.status === 'open' ? `<button class="btn btn-xs btn-ghost" type="button" data-action="alert-ack" data-id="${esc(item.id)}">确认</button>` : ''}
      ${item.status !== 'resolved' ? `<button class="btn btn-xs btn-ghost" type="button" data-action="alert-resolve" data-id="${esc(item.id)}">解决</button>` : ''}
    </div>
    <details class="ops-detail" data-detail-key="alert-${esc(item.id)}" ${opened.has(`alert-${item.id}`) ? 'open' : ''}><summary>详情</summary><dl>
        <div><dt>事件编号</dt><dd>${esc(item.id)}</dd></div><div><dt>发生时间</dt><dd>${esc(fmtDateTime(item.created_at))}</dd></div>
        <div><dt>关联对象</dt><dd>${esc(item.resource_id || '—')}</dd></div><div><dt>详细说明</dt><dd>${esc(item.detail || '暂无补充说明')}</dd></div>
      </dl></details>
  </div>`;
}

function bodyHtml(opened) {
  const alerts = state.tab === 'alerts';
  const all = alerts ? state.alerts : state.jobs;
  const items = all.filter((item) => !state.filter || item.status === state.filter);
  const labels = alerts ? ALERT_STATUS : STATUS_LABEL;
  const columns = alerts ? ['告警内容', '级别', '状态', '发生时间', '操作'] : ['任务名称', '状态', '进度', '更新时间', '操作'];
  return `<section class="ops-board" aria-label="${alerts ? '系统告警' : '后台任务'}">
    <header class="ops-toolbar"><div class="ops-board-title"><h2>${alerts ? '系统告警' : '后台任务'}</h2><span class="ops-count">${items.length} 条</span></div>
      <button class="btn btn-ghost btn-sm" type="button" data-action="jobs-reload" ${state.loading ? 'disabled' : ''}>${icon('refresh')}<span>${state.loading ? '刷新中' : '刷新'}</span></button>
    </header>
    <div class="ops-filterbar" role="group" aria-label="状态筛选">${Object.entries({ '': '全部', ...labels }).map(([key, label]) => `<button class="filter-chip ${state.filter === key ? 'is-active' : ''}" type="button" data-action="filter-status" data-status="${key}" aria-pressed="${state.filter === key}">${label}</button>`).join('')}
      ${!alerts ? `<span class="ops-filter-meta">排队 ${state.summary.queued || 0} · 执行 ${state.summary.running || 0}</span>` : ''}
    </div>
    ${state.error ? `<div class="ops-inline-error" role="alert">${esc(state.error)}</div>` : ''}
    <div class="ops-list ${alerts ? 'is-alerts' : 'is-jobs'}" role="table" aria-label="${alerts ? '告警列表' : '任务列表'}">
      <div class="ops-list-head ${alerts ? 'ops-alert-row' : 'ops-job-row'}" role="row">${columns.map((column) => `<span role="columnheader">${column}</span>`).join('')}</div>
      ${items.length ? items.map((item) => alerts ? alertHtml(item, opened) : jobHtml(item, opened)).join('') : `<div class="ops-empty">${state.loading ? '正在加载…' : alerts ? '所选状态下暂无告警' : '所选状态下暂无任务'}${!state.loading && !alerts ? '<span>上传资料后，可在这里跟进编译进度。</span>' : ''}</div>`}
    </div>
    <footer class="ops-list-foot">最近 ${all.length} 条${alerts ? ' · 处理后请标记确认或解决' : ' · 后台任务不受页面关闭影响'}</footer>
  </section>`;
}

export function mount(root, options = {}) {
  let alive = true;
  let refreshing = false;
  let timer = 0;
  const opened = new Set();
  state.tab = options.query?.get('tab') === 'alerts' ? 'alerts' : 'jobs';
  state.filter = '';
  state.error = '';
  root.innerHTML = '<div class="ops-page" id="jobsPage"><div id="jobsBody"></div></div>';
  const page = root.querySelector('#jobsPage');
  const body = root.querySelector('#jobsBody');
  function rememberDetails() {
    body.querySelectorAll('[data-detail-key]').forEach((node) => {
      if (node.open) opened.add(node.dataset.detailKey);
      else opened.delete(node.dataset.detailKey);
    });
  }
  function render() {
    rememberDetails();
    body.innerHTML = bodyHtml(opened);
  }
  async function load({ silent = false } = {}) {
    if (refreshing) return;
    refreshing = true;
    if (!silent) { state.loading = true; render(); }
    try {
      const [jobs, alerts] = await Promise.all([listJobs({ limit: 50 }), listAlerts({ limit: 50 })]);
      if (!alive) return;
      state.jobs = jobs.items || [];
      state.summary = jobs;
      state.alerts = alerts.items || [];
      state.error = '';
    } catch (err) {
      if (alive && !silent) state.error = err?.message || '加载失败，请刷新重试';
    } finally {
      refreshing = false;
      if (alive) { state.loading = false; render(); }
    }
  }
  async function runAction(button, request, text) {
    setButtonBusy(button, true);
    try { await request(); if (alive) { toast(text, 'success'); await load({ silent: true }); } }
    catch (err) { if (alive) toast(err?.message || '操作失败', 'error'); }
    finally { if (button.isConnected) setButtonBusy(button, false); }
  }
  page.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const id = button.dataset.id;
    switch (button.dataset.action) {
      case 'filter-status': state.filter = button.dataset.status || ''; render(); break;
      case 'jobs-reload': await load(); break;
      case 'job-retry': await runAction(button, () => retryJob(id), '已加入重试队列'); break;
      case 'job-cancel': {
        const ok = await confirmDialog({ title: '取消任务', message: '排队中的任务会立即取消；执行中的任务会尽快停止。', confirmText: '取消任务', danger: true });
        if (ok && alive) await runAction(button, () => cancelJob(id), '已请求取消');
        break;
      }
      case 'alert-ack': await runAction(button, () => ackAlert(id), '告警已确认'); break;
      case 'alert-resolve': await runAction(button, () => resolveAlert(id), '告警已解决'); break;
      default: break;
    }
  });
  void load();
  timer = window.setInterval(() => {
    if (document.visibilityState !== 'hidden' && state.jobs.some((job) => ['queued', 'running'].includes(job.status))) void load({ silent: true });
  }, 5000);
  return () => { alive = false; window.clearInterval(timer); };
}

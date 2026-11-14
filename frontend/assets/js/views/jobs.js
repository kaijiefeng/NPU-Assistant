/**
 * views/jobs.js — 后台任务与系统告警（v0.2.0 M4）
 */
import {
  ackAlert,
  cancelJob,
  listAlerts,
  listJobs,
  resolveAlert,
  retryJob,
} from '../api.js';
import {
  confirmDialog,
  esc,
  fmtRelative,
  icon,
  loadingHtml,
  toast,
} from '../ui.js';

const TYPE_LABEL = {
  ingest_upload: '上传编译',
  reindex: '重建词条',
  rebuild: 'Wiki 重建',
};
const STATUS_LABEL = {
  queued: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
};
const LEVEL_LABEL = { info: '提示', warning: '警告', error: '错误', critical: '严重' };
const ALERT_STATUS = { open: '待处理', acknowledged: '已确认', resolved: '已解决' };

const state = {
  tab: 'jobs',
  jobs: [],
  alerts: [],
  summary: {},
  loading: false,
  refreshing: false,
  timer: 0,
};

function tabsHtml() {
  const active = state.jobs.filter((job) => ['queued', 'running'].includes(job.status)).length;
  const openAlerts = state.alerts.filter((item) => item.status === 'open').length;
  return `<div class="insights-tabs">
    <button class="insights-tab ${state.tab === 'jobs' ? 'is-active' : ''}" type="button" data-action="jobs-tab" data-tab="jobs">任务${active ? ` (${active})` : ''}</button>
    <button class="insights-tab ${state.tab === 'alerts' ? 'is-active' : ''}" type="button" data-action="jobs-tab" data-tab="alerts">告警${openAlerts ? ` (${openAlerts})` : ''}</button>
  </div>`;
}

function jobHtml(job) {
  const tone =
    job.status === 'failed'
      ? 'var(--red)'
      : job.status === 'succeeded'
        ? 'var(--green)'
        : job.status === 'running'
          ? 'var(--blue-600, #2b6cb0)'
          : 'var(--line)';
  const doc = job.result?.document || {};
  const active = ['queued', 'running'].includes(job.status);
  const canRetry =
    ['failed', 'cancelled'].includes(job.status) && job.attempts < job.max_attempts;
  return `<div class="gap-card" style="border-left-color:${tone}">
    <div class="gap-card-head">
      <div>
        <div class="gap-card-title">${esc(TYPE_LABEL[job.type] || job.type)} · ${esc(
          job.message || STATUS_LABEL[job.status] || job.status,
        )}</div>
        <div class="gap-card-meta">${esc(STATUS_LABEL[job.status] || job.status)} · 第 ${job.attempts}/${
          job.max_attempts
        } 次 · ${esc(fmtRelative(job.created_at) || job.created_at || '')}${
          job.created_by ? ` · ${esc(job.created_by)}` : ''
        }</div>
      </div>
      <span class="badge ${job.status === 'failed' ? 'badge-red' : job.status === 'succeeded' ? 'badge-green' : ''}">${esc(
        STATUS_LABEL[job.status] || job.status,
      )}</span>
    </div>
    <div class="progress job-progress"><span style="width:${Math.max(
      2,
      Math.min(100, Number(job.progress) || 0),
    )}%"></span></div>
    ${job.error ? `<p class="muted job-error">${esc(job.error)}</p>` : ''}
    <div class="gap-card-actions">
      ${
        doc.id
          ? `<a class="btn btn-xs btn-ghost" href="#/knowledge?doc=${encodeURIComponent(
              doc.id,
            )}">${icon('file')}<span>查看文档</span></a>`
          : ''
      }
      ${
        canRetry
          ? `<button class="btn btn-xs btn-ghost" type="button" data-action="job-retry" data-id="${esc(
              job.id,
            )}">${icon('refresh')}<span>重试</span></button>`
          : ''
      }
      ${
        active
          ? `<button class="btn btn-xs btn-ghost" type="button" data-action="job-cancel" data-id="${esc(
              job.id,
            )}">${icon('x')}<span>取消</span></button>`
          : ''
      }
    </div>
  </div>`;
}

function jobsHtml() {
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('clock')} 后台任务</h3>
      <div class="chip-row">
        <span class="muted">排队 ${state.summary.queued || 0} · 执行 ${state.summary.running || 0}</span>
        <button class="btn btn-xs btn-ghost" type="button" data-action="jobs-reload">${icon(
          'refresh',
        )}<span>刷新</span></button>
      </div>
    </div>
    <p class="muted">上传大文件后在后台解析/编译；关闭页面不会中断。失败的任务可重试，最多 2 次。</p>
    ${
      state.jobs.length
        ? `<div class="gap-list">${state.jobs.map(jobHtml).join('')}</div>`
        : `<p class="muted">暂无任务。到「内容管理」上传文件即可创建。</p>`
    }
  </section>`;
}

function alertHtml(item) {
  const tone =
    item.level === 'critical' || item.level === 'error'
      ? 'var(--red)'
      : item.level === 'warning'
        ? 'var(--amber)'
        : 'var(--line)';
  return `<div class="gap-card" style="border-left-color:${tone}">
    <div class="gap-card-head">
      <div>
        <div class="gap-card-title">${esc(item.title)}</div>
        <div class="gap-card-meta">${esc(LEVEL_LABEL[item.level] || item.level)} · ${esc(
          item.category,
        )} · ${esc(fmtRelative(item.created_at) || '')}</div>
      </div>
      <span class="badge">${esc(ALERT_STATUS[item.status] || item.status)}</span>
    </div>
    ${item.detail ? `<p style="margin:8px 0 0">${esc(item.detail)}</p>` : ''}
    <div class="gap-card-actions">
      ${
        item.status === 'open'
          ? `<button class="btn btn-xs btn-ghost" type="button" data-action="alert-ack" data-id="${esc(
              item.id,
            )}">${icon('eye')}<span>已确认</span></button>`
          : ''
      }
      ${
        item.status !== 'resolved'
          ? `<button class="btn btn-xs btn-ghost" type="button" data-action="alert-resolve" data-id="${esc(
              item.id,
            )}">${icon('check')}<span>已解决</span></button>`
          : ''
      }
    </div>
  </div>`;
}

function alertsHtml() {
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('alert')} 系统告警</h3>
      <button class="btn btn-xs btn-ghost" type="button" data-action="jobs-reload">${icon(
        'refresh',
      )}<span>刷新</span></button>
    </div>
    <p class="muted">任务失败、服务中断等事件会出现在这里；处理后请标记确认/解决。</p>
    ${
      state.alerts.length
        ? `<div class="gap-list">${state.alerts.map(alertHtml).join('')}</div>`
        : `<p class="muted">暂无告警。</p>`
    }
  </section>`;
}

function bodyHtml() {
  if (state.loading && !state.jobs.length && !state.alerts.length) {
    return loadingHtml('加载任务与告警…');
  }
  return state.tab === 'alerts' ? alertsHtml() : jobsHtml();
}

export function mount(root) {
  let alive = true;
  root.innerHTML = `<div class="insights-page" id="jobsPage">
    <div class="card" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div>
        <h3 style="margin:0">${icon('clock')} 任务与告警</h3>
        <p class="muted" style="margin:4px 0 0">后台编译进度、失败重试与服务健康告警。</p>
      </div>
      <button class="btn btn-ghost btn-sm" type="button" data-action="jobs-reload">${icon(
        'refresh',
      )}<span>刷新</span></button>
    </div>
    <div id="jobsTabs"></div>
    <div id="jobsBody"></div>
  </div>`;
  const page = root.querySelector('#jobsPage');
  const tabs = root.querySelector('#jobsTabs');
  const body = root.querySelector('#jobsBody');

  function render() {
    tabs.innerHTML = tabsHtml();
    body.innerHTML = bodyHtml();
  }

  async function load({ silent = false } = {}) {
    if (state.refreshing) return;
    state.refreshing = true;
    if (!silent) {
      state.loading = true;
      render();
    }
    try {
      const [jobData, alertData] = await Promise.all([
        listJobs({ limit: 50 }),
        listAlerts({ limit: 50 }),
      ]);
      if (!alive) return;
      state.jobs = jobData.items || [];
      state.summary = jobData;
      state.alerts = alertData.items || [];
    } catch (err) {
      if (alive && !silent) toast(err?.message || '加载任务失败', 'error');
    } finally {
      // refreshing 必须无条件复位：请求进行中离开页面时 alive=false，否则再进入会永久空白
      state.refreshing = false;
      if (alive) {
        state.loading = false;
        render();
      }
    }
  }

  function schedule() {
    window.clearInterval(state.timer);
    state.timer = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      const active = state.jobs.some((job) => ['queued', 'running'].includes(job.status));
      if (active) load({ silent: true });
    }, 5000);
  }

  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'jobs-tab':
        state.tab = trigger.dataset.tab || 'jobs';
        render();
        break;
      case 'jobs-reload':
        load();
        break;
      case 'job-retry':
        retryJob(trigger.dataset.id)
          .then(() => {
            toast('已加入重试队列', 'success');
            return load();
          })
          .catch((err) => toast(err?.message || '重试失败', 'error'));
        break;
      case 'job-cancel':
        confirmDialog({
          title: '取消任务',
          message: '排队中的任务会立即取消；执行中的任务会尽快停止。',
          confirmText: '取消任务',
          danger: true,
        }).then(async (ok) => {
          if (!ok) return;
          try {
            await cancelJob(trigger.dataset.id);
            toast('已请求取消', 'info');
            await load();
          } catch (err) {
            toast(err?.message || '取消失败', 'error');
          }
        });
        break;
      case 'alert-ack':
        ackAlert(trigger.dataset.id)
          .then(() => {
            toast('告警已确认', 'success');
            return load();
          })
          .catch((err) => toast(err?.message || '操作失败', 'error'));
        break;
      case 'alert-resolve':
        resolveAlert(trigger.dataset.id)
          .then(() => {
            toast('告警已解决', 'success');
            return load();
          })
          .catch((err) => toast(err?.message || '操作失败', 'error'));
        break;
      default:
        break;
    }
  });

  load();
  schedule();
  return () => {
    alive = false;
    window.clearInterval(state.timer);
  };
}

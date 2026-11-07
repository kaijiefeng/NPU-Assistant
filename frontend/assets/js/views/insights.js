/**
 * views/insights.js — 服务台（v0.2.0 RSI）
 *
 * 运营视图：概览 / 知识缺口 / 反馈 / 时效。
 * 数据来自 /api/insights/*；需登录账号或管理令牌。
 */
import {
  claimHandoff,
  getFreshness,
  getInsightsOverview,
  ignoreGap,
  listCrisisEvents,
  listFeedback,
  listGaps,
  listHandoffs,
  replyHandoff,
  resolveGap,
  resolveHandoff,
  updateCrisisStatus,
  updateFeedbackStatus,
} from '../api.js';
import { esc, fmtRelative, icon, loadingHtml, openModal, toast } from '../ui.js';

const state = {
  tab: 'overview',
  days: 7,
  overview: null,
  gaps: [],
  feedback: [],
  freshness: null,
  handoffs: [],
  crisis: [],
  loading: false,
  authRequired: false,
};

const HANDOFF_STATUS = {
  pending: '待认领',
  claimed: '处理中',
  replied: '已回复',
  resolved: '已解决',
  closed: '已关闭',
};
const CRISIS_CATEGORY = {
  suicide: '自杀风险',
  violence: '暴力风险',
  sexual: '性侵/骚扰',
  fraud: '诈骗',
  medical: '医疗急症',
  bullying: '霸凌/勒索',
};
const CRISIS_STATUS = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已处理',
  false_positive: '误报',
};

function activeHandoffs() {
  return state.handoffs.filter((item) => !['resolved', 'closed'].includes(item.status));
}

function kpiHtml(label, value, hint = '') {
  return `<div class="insights-kpi"><strong>${esc(value)}</strong><span>${esc(label)}${
    hint ? ` · ${esc(hint)}` : ''
  }</span></div>`;
}

function statusLabel(status) {
  return (
    {
      open: '待处理',
      linked: '已关联',
      resolved: '已解决',
      ignored: '已忽略',
      triaged: '处理中',
    }[status] || status
  );
}

function reasonLabel(reason) {
  return (
    {
      no_hit: '无命中',
      incomplete: '不完整',
      remote: '远程兜底',
      fallback: '降级回答',
      negative_feedback: '负反馈',
      not_found: '没找到',
      expired: '已过期',
      bad_citation: '引用不对',
      other: '其他',
    }[reason] || reason
  );
}

function tabsHtml() {
  const openCrisis = state.crisis.filter((item) => item.status === 'open').length;
  const tabs = [
    ['overview', '概览'],
    ['handoffs', `待人工${activeHandoffs().length ? ` (${activeHandoffs().length})` : ''}`],
    ['crisis', `危机${openCrisis ? ` (${openCrisis})` : ''}`],
    ['gaps', `知识缺口${state.gaps.length ? ` (${state.gaps.length})` : ''}`],
    ['feedback', `反馈${state.feedback.length ? ` (${state.feedback.length})` : ''}`],
    ['freshness', '时效'],
  ];
  return `<div class="insights-tabs">${tabs
    .map(
      ([key, label]) =>
        `<button class="insights-tab ${state.tab === key ? 'is-active' : ''}" type="button" data-action="insights-tab" data-tab="${key}">${esc(label)}</button>`,
    )
    .join('')}</div>`;
}

function overviewHtml() {
  const data = state.overview;
  if (!data) return loadingHtml('加载运营概览…', 'state-compact');
  const helpful =
    data.feedback.helpful_rate === null ? '—' : `${Math.round(data.feedback.helpful_rate * 100)}%`;
  const top = data.top_gaps || [];
  return `
    <div class="insights-kpis">
      ${kpiHtml('近 7 天回答', data.answers)}
      ${kpiHtml('有帮助率', helpful, `${data.feedback.total} 条反馈`)}
      ${kpiHtml('未解决缺口', data.gaps.open, `已关闭 ${data.gaps.resolved}`)}
      ${kpiHtml('没帮助', data.feedback.down)}
    </div>
    <section class="card">
      <div class="card-head"><h3>${icon('sparkles')} 最该补的 10 个问题</h3></div>
      ${
        top.length
          ? `<div class="gap-list">${top
              .map(
                (gap) => `<div class="gap-card ${gap.priority ? 'is-high' : ''}">
                  <div class="gap-card-head">
                    <div>
                      <div class="gap-card-title">${esc(gap.question)}</div>
                      <div class="gap-card-meta">${gap.count} 次 · ${esc(gap.channel)} · 最近 ${esc(
                        fmtRelative(gap.last_seen_at) || gap.last_seen_at || '',
                      )}</div>
                    </div>
                    <span class="badge">${esc(statusLabel(gap.status))}</span>
                  </div>
                  <div class="gap-reasons">${(gap.reasons || [])
                    .map((reason) => `<span class="gap-reason">${esc(reasonLabel(reason))}</span>`)
                    .join('')}</div>
                  <div class="gap-card-actions">
                    <button class="btn btn-xs btn-ghost" type="button" data-action="gap-copy" data-question="${esc(
                      gap.question,
                    )}">${icon('copy')}<span>复制问题去补资料</span></button>
                    <button class="btn btn-xs btn-ghost" type="button" data-action="gap-resolve" data-id="${esc(
                      gap.id,
                    )}">${icon('check')}<span>标记已解决</span></button>
                  </div>
                </div>`,
              )
              .join('')}</div>`
          : `<p class="muted">暂无未解决缺口。学生反馈或 Wiki 无命中后会自动出现在这里。</p>`
      }
    </section>`;
}

function gapCardHtml(gap) {
  return `<div class="gap-card ${gap.priority ? 'is-high' : ''}">
    <div class="gap-card-head">
      <div>
        <div class="gap-card-title">${esc(gap.question)}</div>
        <div class="gap-card-meta">${gap.count} 次 · ${esc(gap.channel)} · 首次 ${esc(
          fmtRelative(gap.first_seen_at) || '',
        )} · 最近 ${esc(fmtRelative(gap.last_seen_at) || '')}</div>
      </div>
      <span class="badge">${esc(statusLabel(gap.status))}</span>
    </div>
    <div class="gap-reasons">${(gap.reasons || [])
      .map((reason) => `<span class="gap-reason">${esc(reasonLabel(reason))}</span>`)
      .join('')}</div>
    ${
      gap.note
        ? `<p class="muted" style="margin:8px 0 0">备注：${esc(gap.note)}</p>`
        : ''
    }
    <div class="gap-card-actions">
      <button class="btn btn-xs btn-ghost" type="button" data-action="gap-copy" data-question="${esc(
        gap.question,
      )}">${icon('copy')}<span>复制问题</span></button>
      <button class="btn btn-xs btn-ghost" type="button" data-action="gap-resolve" data-id="${esc(
        gap.id,
      )}">${icon('check')}<span>已解决</span></button>
      <button class="btn btn-xs btn-ghost" type="button" data-action="gap-ignore" data-id="${esc(
        gap.id,
      )}">${icon('x')}<span>忽略</span></button>
    </div>
  </div>`;
}

function gapsHtml() {
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('alert')} 未解决问题</h3>
      <div class="chip-row">
        <button class="btn btn-xs btn-ghost" type="button" data-action="insights-reload">${icon(
          'refresh',
        )}<span>刷新</span></button>
      </div>
    </div>
    ${
      state.gaps.length
        ? `<div class="gap-list">${state.gaps.map(gapCardHtml).join('')}</div>`
        : `<p class="muted">暂无缺口。学生点“没帮助”或 Wiki 无命中时会自动生成。</p>`
    }
  </section>`;
}

function feedbackHtml() {
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('message')} 回答反馈</h3>
      <button class="btn btn-xs btn-ghost" type="button" data-action="insights-reload">${icon(
        'refresh',
      )}<span>刷新</span></button>
    </div>
    ${
      state.feedback.length
        ? `<div class="gap-list">${state.feedback
            .map(
              (item) => `<div class="gap-card" style="border-left-color:${
                item.rating === 1 ? 'var(--green)' : 'var(--red)'
              }">
                <div class="gap-card-head">
                  <div>
                    <div class="gap-card-title">${item.rating === 1 ? '👍' : '👎'} ${esc(
                      item.question || '（未找到原问题）',
                    )}</div>
                    <div class="gap-card-meta">${esc(item.channel)} · ${esc(
                      item.reason_label || '未填写原因',
                    )} · ${esc(fmtRelative(item.created_at) || '')}</div>
                  </div>
                  <span class="badge">${esc(statusLabel(item.status))}</span>
                </div>
                ${item.comment ? `<p class="muted" style="margin:6px 0 0">${esc(item.comment)}</p>` : ''}
                ${
                  item.reply
                    ? `<blockquote class="citation-source-quote">${esc(item.reply)}</blockquote>`
                    : ''
                }
                <div class="gap-card-actions">
                  <button class="btn btn-xs btn-ghost" type="button" data-action="feedback-resolve" data-id="${esc(
                    item.id,
                  )}">${icon('check')}<span>已处理</span></button>
                  <button class="btn btn-xs btn-ghost" type="button" data-action="feedback-ignore" data-id="${esc(
                    item.id,
                  )}">${icon('x')}<span>忽略</span></button>
                </div>
              </div>`,
            )
            .join('')}</div>`
        : `<p class="muted">暂无反馈。学生可在回答下方点“有帮助/没帮助”。</p>`
    }
  </section>`;
}

function freshnessHtml() {
  const data = state.freshness;
  if (!data) return loadingHtml('加载时效数据…', 'state-compact');
  const block = (title, items, tone) =>
    `<div class="card" style="margin-top:12px">
      <div class="card-head"><h3>${esc(title)} · ${items.length}</h3></div>
      ${
        items.length
          ? `<div class="gap-list">${items
              .map(
                (item) => `<div class="gap-card" style="border-left-color:${tone}">
                  <div class="gap-card-title">${esc(item.title)}</div>
                  <div class="gap-card-meta">${esc(item.freshness.label)} · 有效期 ${
                    esc(item.freshness.expires_at || '—')
                  } · 核对 ${esc(item.freshness.verified_at || '—')}</div>
                  <div class="gap-card-actions">
                    <button class="btn btn-xs btn-ghost" type="button" data-action="open-wiki-page" data-page-id="${esc(
                      item.id,
                    )}">${icon('book')}<span>查看/核对</span></button>
                  </div>
                </div>`,
              )
              .join('')}</div>`
          : `<p class="muted">暂无。</p>`
      }
    </div>`;
  return `<section class="card">
      <div class="card-head"><h3>${icon('clock')} 时效看板</h3></div>
      <p class="muted">过期内容默认不参与问答；没有替代时会带警告回答。建议优先处理“已过期”和“可能过时”。</p>
    </section>
    ${block('已过期', data.expired, 'var(--red)')}
    ${block('可能过时', data.stale, 'var(--amber)')}
    ${block('即将过期', data.expiring, 'var(--amber)')}`;
}

function handoffCardHtml(item) {
  const urgent = item.priority === 'urgent';
  const closed = ['resolved', 'closed'].includes(item.status);
  return `<div class="gap-card ${urgent ? 'is-high' : ''}" style="border-left-color:${
    urgent ? 'var(--red)' : 'var(--line)'
  }">
    <div class="gap-card-head">
      <div>
        <div class="gap-card-title">${urgent ? '🚨 ' : ''}${esc(item.ticket_no)} · ${esc(
          item.reason_label || '',
        )}</div>
        <div class="gap-card-meta">${esc(item.channel)} · ${esc(
          item.user_label || item.session_key || '匿名',
        )} · ${esc(fmtRelative(item.created_at) || item.created_at || '')}${
          item.contact ? ` · 联系方式 ${esc(item.contact)}` : ''
        }</div>
      </div>
      <span class="badge">${esc(HANDOFF_STATUS[item.status] || item.status)}</span>
    </div>
    ${item.summary ? `<p style="margin:8px 0 0">${esc(item.summary)}</p>` : ''}
    ${
      item.resolution
        ? `<p class="muted" style="margin:6px 0 0">处理结果：${esc(item.resolution)}</p>`
        : ''
    }
    <div class="gap-card-actions">
      ${
        item.status === 'pending'
          ? `<button class="btn btn-xs btn-ghost" type="button" data-action="handoff-claim" data-id="${esc(
              item.id,
            )}">${icon('user')}<span>认领</span></button>`
          : ''
      }
      ${
        !closed
          ? `<button class="btn btn-xs btn-ghost" type="button" data-action="handoff-reply" data-id="${esc(
              item.id,
            )}" data-ticket="${esc(item.ticket_no)}">${icon('message')}<span>回复学生</span></button>
             <button class="btn btn-xs btn-ghost" type="button" data-action="handoff-resolve" data-id="${esc(
               item.id,
             )}">${icon('check')}<span>标记解决</span></button>`
          : ''
      }
      <button class="btn btn-xs btn-ghost" type="button" data-action="handoff-copy" data-ticket="${esc(
        item.ticket_no,
      )}">${icon('copy')}<span>复制单号</span></button>
    </div>
  </div>`;
}

function handoffsHtml() {
  const items = activeHandoffs();
  const done = state.handoffs.length - items.length;
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('user')} 待人工工单</h3>
      <div class="chip-row">
        ${done ? `<span class="muted">已结束 ${done}</span>` : ''}
        <button class="btn btn-xs btn-ghost" type="button" data-action="insights-reload">${icon(
          'refresh',
        )}<span>刷新</span></button>
      </div>
    </div>
    <p class="muted">学生点「联系辅导员」或触发危机规则后生成。紧急工单排在最前，请在 SLA 内处理。</p>
    ${
      items.length
        ? `<div class="gap-list">${items.map(handoffCardHtml).join('')}</div>`
        : `<p class="muted">暂无待处理工单。</p>`
    }
  </section>`;
}

function crisisHtml() {
  return `<section class="card">
    <div class="card-head">
      <h3>${icon('shield')} 危机事件</h3>
      <button class="btn btn-xs btn-ghost" type="button" data-action="insights-reload">${icon(
        'refresh',
      )}<span>刷新</span></button>
    </div>
    <p class="muted">危机提示由规则命中，未经大模型。务必按学校应急预案线下核实、联系家长并留存记录。</p>
    ${
      state.crisis.length
        ? `<div class="gap-list">${state.crisis
            .map(
              (item) => `<div class="gap-card ${
                item.status === 'open' ? 'is-high' : ''
              }">
                <div class="gap-card-head">
                  <div>
                    <div class="gap-card-title">${esc(
                      CRISIS_CATEGORY[item.category] || item.category,
                    )} · ${esc(item.severity || '')}</div>
                    <div class="gap-card-meta">${esc(fmtRelative(item.created_at) || '')}${
                      (item.matched_terms || []).length
                        ? ` · 命中 ${esc((item.matched_terms || []).join('、'))}`
                        : ''
                    }</div>
                  </div>
                  <span class="badge">${esc(CRISIS_STATUS[item.status] || item.status)}</span>
                </div>
                ${
                  item.notify_result
                    ? `<p class="muted" style="margin:6px 0 0">通知：${esc(
                        item.notify_result,
                      )}</p>`
                    : ''
                }
                <div class="gap-card-actions">
                  ${
                    item.status === 'open'
                      ? `<button class="btn btn-xs btn-ghost" type="button" data-action="crisis-status" data-id="${esc(
                          item.id,
                        )}" data-status="acknowledged">${icon('check')}<span>已确认</span></button>`
                      : ''
                  }
                  ${
                    !['resolved', 'false_positive'].includes(item.status)
                      ? `<button class="btn btn-xs btn-ghost" type="button" data-action="crisis-status" data-id="${esc(
                          item.id,
                        )}" data-status="resolved">${icon('check')}<span>已处置</span></button>
                         <button class="btn btn-xs btn-ghost" type="button" data-action="crisis-status" data-id="${esc(
                           item.id,
                         )}" data-status="false_positive">${icon('x')}<span>误报</span></button>`
                      : ''
                  }
                </div>
              </div>`,
            )
            .join('')}</div>`
        : `<p class="muted">暂无危机事件记录。</p>`
    }
  </section>`;
}

function openReplyModal(id, ticketNo, reload) {
  const form = document.createElement('form');
  form.innerHTML = `<label class="field">
      <span class="field-label">回复内容</span>
      <textarea class="input" name="content" rows="5" maxlength="4000" placeholder="例如：截止时间是本周五 17:00，请把材料交到学院办公室 302。"></textarea>
    </label>
    <p class="muted">回复会出现在学生的对话里，并更新工单状态。</p>`;
  const footer = document.createElement('div');
  footer.className = 'modal-actions';
  footer.innerHTML = `<button class="btn btn-ghost" type="button" data-action="cancel">取消</button>
    <button class="btn btn-primary" type="submit">发送回复</button>`;
  form.appendChild(footer);
  const modal = openModal({
    title: `回复工单 ${ticketNo || ''}`,
    subtitle: '以辅导员身份回复',
    body: form,
    size: 'md',
  });
  footer.querySelector('[data-action="cancel"]').addEventListener('click', modal.close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const content = form.querySelector('textarea').value.trim();
    if (!content) {
      toast('请填写回复内容', 'warning');
      return;
    }
    try {
      await replyHandoff(id, { content });
      modal.close();
      toast('已回复学生', 'success');
      await reload();
    } catch (err) {
      toast(err?.message || '回复失败', 'error');
    }
  });
}


function bodyHtml() {
  if (state.authRequired) {
    return `<div class="state state-empty">
      <div class="state-icon">${icon('shield')}</div>
      <h3>请先登录服务台</h3>
      <p>服务台需要辅导员或管理员账号；登录后即可查看反馈、缺口与工单。</p>
      <button class="btn btn-primary" type="button" data-action="go-login">去登录</button>
    </div>`;
  }
  if (state.loading) return loadingHtml('加载服务台…');
  if (state.tab === 'handoffs') return handoffsHtml();
  if (state.tab === 'crisis') return crisisHtml();
  if (state.tab === 'gaps') return gapsHtml();
  if (state.tab === 'feedback') return feedbackHtml();
  if (state.tab === 'freshness') return freshnessHtml();
  return overviewHtml();
}

export function mount(root) {
  let alive = true;
  root.innerHTML = `<div class="insights-page" id="insightsPage">
    <div class="card" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div>
        <h3 style="margin:0">${icon('sparkles')} 服务台</h3>
        <p class="muted" style="margin:4px 0 0">学生反馈 → 知识缺口 → 补资料 → 回答变好。数据按最近 30 天统计。</p>
      </div>
      <button class="btn btn-ghost btn-sm" type="button" data-action="insights-reload">${icon(
        'refresh',
      )}<span>刷新</span></button>
    </div>
    <div id="insightsTabs"></div>
    <div id="insightsBody"></div>
  </div>`;
  const page = root.querySelector('#insightsPage');
  const tabs = root.querySelector('#insightsTabs');
  const body = root.querySelector('#insightsBody');

  function render() {
    tabs.innerHTML = tabsHtml();
    body.innerHTML = bodyHtml();
  }

  async function load() {
    state.loading = true;
    state.authRequired = false;
    render();
    try {
      const [overview, gaps, feedback, fresh, handoffs, crisis] = await Promise.all([
        getInsightsOverview(state.days),
        listGaps({ status: 'open', days: 30, limit: 50 }),
        listFeedback({ days: 30, limit: 50 }),
        getFreshness(30),
        listHandoffs({ days: 30, limit: 100 }),
        listCrisisEvents({ limit: 50 }),
      ]);
      if (!alive) return;
      state.overview = overview;
      state.gaps = gaps.items || [];
      state.feedback = feedback.items || [];
      state.freshness = fresh;
      state.handoffs = handoffs.items || [];
      state.crisis = crisis.items || [];
    } catch (err) {
      if (!alive) return;
      if (err?.status === 401) state.authRequired = true;
      else toast(err?.message || '加载服务台失败', 'error');
    } finally {
      if (alive) {
        state.loading = false;
        render();
      }
    }
  }

  async function runAction(promise, successText) {
    try {
      await promise;
      toast(successText, 'success');
      await load();
    } catch (err) {
      toast(err?.message || '操作失败', 'error');
    }
  }

  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'insights-tab':
        state.tab = trigger.dataset.tab || 'overview';
        render();
        break;
      case 'insights-reload':
        load();
        break;
      case 'go-login':
        location.hash = `#/login?next=${encodeURIComponent(location.hash)}`;
        break;
      case 'handoff-claim':
        runAction(claimHandoff(trigger.dataset.id, { assignee_id: 'admin' }), '已认领工单');
        break;
      case 'handoff-reply':
        openReplyModal(trigger.dataset.id, trigger.dataset.ticket, load);
        break;
      case 'handoff-resolve':
        runAction(resolveHandoff(trigger.dataset.id, { resolution: '服务台标记已解决' }), '工单已解决');
        break;
      case 'handoff-copy':
        navigator.clipboard
          ?.writeText(trigger.dataset.ticket || '')
          .then(() => toast('单号已复制', 'info'))
          .catch(() => toast('复制失败，请手动选择', 'warning'));
        break;
      case 'crisis-status':
        runAction(
          updateCrisisStatus(trigger.dataset.id, {
            status: trigger.dataset.status,
            note: '服务台更新',
          }),
          '危机事件已更新',
        );
        break;
      case 'gap-resolve':
        runAction(resolveGap(trigger.dataset.id, { note: '服务台标记已解决' }), '缺口已标记解决');
        break;
      case 'gap-ignore':
        runAction(ignoreGap(trigger.dataset.id, { note: '服务台忽略' }), '缺口已忽略');
        break;
      case 'gap-copy':
        navigator.clipboard
          ?.writeText(trigger.dataset.question || '')
          .then(() => toast('问题已复制，去内容管理补资料', 'info'))
          .catch(() => toast('复制失败，请手动选择', 'warning'));
        break;
      case 'feedback-resolve':
        runAction(
          updateFeedbackStatus(trigger.dataset.id, { status: 'resolved' }),
          '反馈已标记处理',
        );
        break;
      case 'feedback-ignore':
        runAction(
          updateFeedbackStatus(trigger.dataset.id, { status: 'ignored' }),
          '反馈已忽略',
        );
        break;
      case 'open-wiki-page':
        if (trigger.dataset.pageId) {
          location.hash = `#/knowledge?page=${encodeURIComponent(trigger.dataset.pageId)}`;
        }
        break;
      default:
        break;
    }
  });

  load();
  return () => {
    alive = false;
  };
}

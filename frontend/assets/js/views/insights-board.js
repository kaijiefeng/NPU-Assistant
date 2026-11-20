/** 服务台渲染：统一工作队列、紧凑指标与按需展开详情。 */
import { badge, esc, fmtDateTime, fmtRelative, icon } from '../ui.js';

const HANDOFF_STATUS = { pending: '待认领', claimed: '处理中', replied: '已回复', resolved: '已解决', closed: '已关闭' };
const CRISIS_CATEGORY = { suicide: '自杀风险', violence: '暴力风险', sexual: '性侵/骚扰', fraud: '诈骗', medical: '医疗急症', bullying: '霸凌/勒索', other: '需核实' };
const CRISIS_STATUS = { open: '待处理', acknowledged: '已确认', resolved: '已处置', false_positive: '误报' };
const STATUS = { open: '待处理', linked: '已关联', resolved: '已解决', ignored: '已忽略', triaged: '处理中' };
const REASON = { no_hit: '无命中', incomplete: '回答不完整', remote: '远程兜底', fallback: '降级回答', negative_feedback: '负面反馈', not_found: '未找到资料', expired: '内容过期', bad_citation: '引用有误', other: '其他' };
const CHANNEL = { web: '网页', console: '调试', qq_official: 'QQ', wechat_mp: '微信' };
const SUB_STATUS = { ok: '检查成功', 'robots-disallowed': '网站不允许抓取', 'fetch-failed': '连接失败', 'parse-failed': '解析失败', pending: '等待检查', running: '检查中' };

function button(action, text, attrs = '', primary = false) {
  return `<button class="btn btn-xs ${primary ? 'btn-primary' : 'btn-ghost'}" type="button" data-action="${action}" ${attrs}>${text}</button>`;
}
function idAttr(id) { return `data-id="${esc(id)}"`; }
function status(value, labels = STATUS) {
  return badge(labels[value] || '待核实', ['resolved', 'closed'].includes(value) ? 'green' : value === 'ignored' || value === 'false_positive' ? 'slate' : value === 'pending' || value === 'open' ? 'amber' : 'blue');
}
function time(value) { return `<span class="desk-time" title="${esc(fmtDateTime(value))}">${esc(value ? fmtRelative(value) : '—')}</span>`; }
function fields(items) {
  return `<dl class="desk-fields">${items.filter(([, value]) => value !== '' && value !== null && value !== undefined).map(([name, value]) => `<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
}
function paragraph(label, value) {
  return value ? `<section class="desk-detail-section"><h4>${esc(label)}</h4><p>${esc(value)}</p></section>` : '';
}
function primary(title, subtitle = '') {
  return `<div class="desk-primary"><strong title="${esc(title)}">${esc(title)}</strong>${subtitle ? `<span class="desk-subtitle">${esc(subtitle)}</span>` : ''}</div>`;
}
function row({ key, kind, title, subtitle, cells = [], actions = '', detail = '', urgent = false, detailLabel = '详情' }) {
  return `<div class="desk-row desk-grid-${kind}${urgent ? ' is-urgent' : ''}" role="row">
    <div class="desk-cell desk-main" role="cell">${primary(title, subtitle)}</div>
    ${cells.map((cell) => `<div class="desk-cell" role="cell">${cell}</div>`).join('')}
    <div class="desk-cell desk-actions" role="cell">${actions}</div>
    <details class="desk-detail" data-desk-detail="${esc(key)}"><summary aria-label="查看${esc(title)}详情">${esc(detailLabel)}</summary><div class="desk-detail-body">${detail}</div></details>
  </div>`;
}
function board({ title, count, kind, columns, rows, empty = '当前没有待处理记录', note = '', tools = '', footer = '' }) {
  return `<section class="ops-board desk-board" aria-label="${esc(title)}">
    <header class="ops-toolbar"><div class="ops-board-title"><h2>${esc(title)}</h2><span class="ops-count">${Number(count || 0)} 条</span></div>
      <div class="desk-toolbar-actions">${tools}${button('insights-reload', `${icon('refresh')}<span>刷新</span>`)}</div>
    </header>
    ${note ? `<p class="desk-board-note">${esc(note)}</p>` : ''}
    <div class="desk-list" role="table" aria-label="${esc(title)}列表">
      <div class="desk-list-head desk-grid-${kind}" role="row">${columns.map((label) => `<span role="columnheader">${esc(label)}</span>`).join('')}</div>
      ${rows || `<div class="desk-empty">${esc(empty)}</div>`}
    </div>${footer ? `<footer class="ops-list-foot">${esc(footer)}</footer>` : ''}
  </section>`;
}
function metric(label, value, hint = '', tone = '') {
  return `<div class="desk-metric ${tone ? `tone-${tone}` : ''}"><span>${esc(label)}</span><strong>${esc(value)}</strong>${hint ? `<small>${esc(hint)}</small>` : ''}</div>`;
}

function suggestionsHtml(gap) {
  const items = gap.suggestions || [];
  if (!items.length) return '';
  return `<section class="desk-detail-section"><h4>备选资料 · ${items.length} 条</h4><div class="desk-resources">${items.map((item, index) => `<div class="desk-resource">
    <div><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title || item.url)}</a><p>${esc([item.source, item.reason].filter(Boolean).join(' · '))}</p></div>
    ${button('gap-collect', '收录', `${idAttr(gap.id)} data-index="${index}"`, true)}
  </div>`).join('')}</div></section>`;
}
function gapRow(gap, state, overview = false) {
  const suggestions = gap.suggestions || [];
  const reasons = (gap.reasons || []).map((reason) => REASON[reason] || '其他').join(' · ');
  const attrs = idAttr(gap.id);
  return row({ key: `gap-${gap.id}`, kind: 'gaps', title: gap.question, urgent: Boolean(gap.priority),
    subtitle: reasons || '等待补充相关资料',
    cells: [`<span class="desk-number">${Number(gap.count || 1)} 次</span>`, status(gap.status), time(gap.last_seen_at)],
    actions: (overview ? button('insights-tab', '去处理', 'data-tab="gaps"') : button('gap-suggest', state.gapSuggesting === gap.id ? '检索中…' : '找资料', `${attrs} ${state.gapSuggesting === gap.id ? 'disabled' : ''}`)) + button('gap-resolve', '解决', attrs),
    detailLabel: suggestions.length ? `备选 ${suggestions.length}` : '详情',
    detail: fields([['来源', CHANNEL[gap.channel] || '其他渠道'], ['首次出现', fmtDateTime(gap.first_seen_at)], ['最近出现', fmtDateTime(gap.last_seen_at)], ['优先级', gap.priority ? '优先补充' : '正常'], ['负责人', gap.assignee_id || '未分配']]) + paragraph('备注', gap.note) + suggestionsHtml(gap) + `<div class="desk-detail-actions">${button('gap-copy', '复制问题', `data-question="${esc(gap.question)}"`)}${!overview ? button('gap-ignore', '忽略', attrs) : ''}</div>`,
  });
}
function overviewHtml(state) {
  const data = state.overview || {};
  const feedback = data.feedback || {};
  const gaps = data.gaps || {};
  const top = data.top_gaps || [];
  const helpful = feedback.helpful_rate === null || feedback.helpful_rate === undefined ? '—' : `${Math.round(feedback.helpful_rate * 100)}%`;
  return `<div class="desk-overview-metrics">${metric(`近 ${data.days || state.days} 天回答`, data.answers || 0)}${metric('有帮助率', helpful, `${feedback.total || 0} 条反馈`)}${metric('未解决缺口', gaps.open || 0, `已关闭 ${gaps.resolved || 0}`, 'amber')}${metric('待人工工单', state.handoffs.filter((item) => !['resolved', 'closed'].includes(item.status)).length)}</div>` + board({ title: '优先补充的问题', kind: 'gaps', count: top.length, columns: ['问题 / 缺口原因', '出现次数', '状态', '最近出现', '处理'], rows: top.map((gap) => gapRow(gap, state, true)).join(''), empty: '暂无未解决缺口', footer: `统计区间：最近 ${data.days || state.days} 天 · 优先问题按频次与紧急程度排列` });
}
function gapsHtml(state) {
  return board({ title: '知识缺口', kind: 'gaps', count: state.gaps.length, columns: ['问题 / 缺口原因', '出现次数', '状态', '最近出现', '处理'], rows: state.gaps.map((gap) => gapRow(gap, state)).join(''), empty: '暂无知识缺口；无命中和负面反馈会自动进入此队列', footer: '备选资料需核对后收录；标记解决不会自动添加知识内容' });
}
function handoffsHtml(state) {
  const items = state.handoffs.filter((item) => !['resolved', 'closed'].includes(item.status));
  return board({ title: '待人工工单', kind: 'handoffs', count: items.length, columns: ['工单 / 诉求', '优先级', '状态', '提交时间', '处理'], rows: items.map((item) => row({
    key: `handoff-${item.id}`, kind: 'handoffs', title: item.summary || item.reason_label || '待人工咨询', subtitle: `${item.ticket_no} · ${CHANNEL[item.channel] || '其他渠道'} · ${item.user_label || '匿名'}`, urgent: item.priority === 'urgent',
    cells: [badge(item.priority === 'urgent' ? '紧急' : '正常', item.priority === 'urgent' ? 'red' : 'slate'), status(item.status, HANDOFF_STATUS), time(item.created_at)],
    actions: (item.status === 'pending' ? button('handoff-claim', '认领', idAttr(item.id)) : '') + button('handoff-reply', '回复', `${idAttr(item.id)} data-ticket="${esc(item.ticket_no)}"`, true),
    detail: fields([['工单号', item.ticket_no], ['咨询原因', item.reason_label], ['联系方式', item.contact || '未提供'], ['负责人', item.assignee_id || '未认领'], ['提交时间', fmtDateTime(item.created_at)], ['首次回复', item.first_response_at ? fmtDateTime(item.first_response_at) : '待回复']]) + paragraph('诉求摘要', item.summary) + paragraph('处理结果', item.resolution) + `<div class="desk-detail-actions">${button('handoff-resolve', '标记解决', idAttr(item.id))}${button('handoff-copy', '复制单号', `data-ticket="${esc(item.ticket_no)}"`)}</div>`,
  })).join(''), empty: '暂无待处理工单', footer: `已结束 ${state.handoffs.length - items.length} 条 · 紧急工单优先，按学校服务时限处理` });
}
function crisisHtml(state) {
  const severity = { high: '高风险', medium: '需关注', low: '一般', critical: '紧急' };
  return board({ title: '危机事件', kind: 'crisis', count: state.crisis.length, columns: ['事件类型 / 线索', '风险', '状态', '发生时间', '处置'], note: '请按学校应急预案线下核实并及时处置；系统提示不能替代人工判断。', rows: state.crisis.map((item) => {
    const active = !['resolved', 'false_positive'].includes(item.status);
    return row({ key: `crisis-${item.id}`, kind: 'crisis', urgent: item.status === 'open', title: CRISIS_CATEGORY[item.category] || '需核实事件', subtitle: (item.matched_terms || []).length ? `命中线索：${item.matched_terms.join('、')}` : '等待线下核实',
      cells: [badge(severity[item.severity] || '需核实', ['high', 'critical'].includes(item.severity) ? 'red' : 'amber'), status(item.status, CRISIS_STATUS), time(item.created_at)],
      actions: (item.status === 'open' ? button('crisis-status', '确认', `${idAttr(item.id)} data-status="acknowledged"`) : '') + (active ? button('crisis-status', '已处置', `${idAttr(item.id)} data-status="resolved"`, true) : ''),
      detail: fields([['发生时间', fmtDateTime(item.created_at)], ['关联工单', item.handoff_id || '—'], ['处理人', item.handled_by || '待安排'], ['处理时间', item.handled_at ? fmtDateTime(item.handled_at) : '待处理']]) + paragraph('通知结果', item.notify_result) + paragraph('处置备注', item.note) + (active ? `<div class="desk-detail-actions">${button('crisis-status', '标记误报', `${idAttr(item.id)} data-status="false_positive"`)}</div>` : ''),
    });
  }).join(''), empty: '暂无危机事件记录' });
}
function feedbackHtml(state) {
  return board({ title: '回答反馈', kind: 'feedback', count: state.feedback.length, columns: ['问题 / 反馈原因', '评价', '状态', '反馈时间', '处理'], rows: state.feedback.map((item) => row({
    key: `feedback-${item.id}`, kind: 'feedback', title: item.question || '未找到原问题', subtitle: item.comment || item.reason_label || '未填写补充意见',
    cells: [badge(item.rating === 1 ? '有帮助' : '没帮助', item.rating === 1 ? 'green' : 'red'), status(item.status), time(item.created_at)],
    actions: !['resolved', 'ignored'].includes(item.status) ? button('feedback-resolve', '已处理', idAttr(item.id)) : '',
    detail: fields([['反馈原因', item.reason_label || REASON[item.reason_code] || '未填写'], ['来源渠道', CHANNEL[item.channel] || '其他渠道'], ['反馈时间', fmtDateTime(item.created_at)]]) + paragraph('学生意见', item.comment) + paragraph('原始回答', item.reply) + (!['resolved', 'ignored'].includes(item.status) ? `<div class="desk-detail-actions">${button('feedback-ignore', '忽略反馈', idAttr(item.id))}</div>` : ''),
  })).join(''), empty: '暂无回答反馈', footer: '展开详情查看完整意见和原始回答' });
}
function freshnessHtml(state) {
  const data = state.freshness || {};
  const groups = [['expired', '已过期', 'red'], ['stale', '可能过时', 'amber'], ['expiring', '即将过期', 'amber']];
  const rows = groups.flatMap(([key, label, tone]) => (data[key] || []).map((item) => ({ ...item, groupLabel: label, tone })));
  const unique = [...new Map(rows.map((item) => [item.id, item])).values()];
  return `<div class="desk-freshness-summary">${groups.map(([key, label, tone]) => `<span>${badge(label, tone)}<strong>${(data[key] || []).length}</strong></span>`).join('')}</div>` + board({ title: '知识时效', kind: 'freshness', count: unique.length, columns: ['词条', '时效状态', '有效期', '最近核对', '处理'], rows: unique.map((item) => row({
    key: `fresh-${item.id}`, kind: 'freshness', title: item.title || '未命名词条', subtitle: item.summary || '',
    cells: [badge(item.groupLabel, item.tone), `<span class="desk-time">${esc(item.freshness?.expires_at || '未设置')}</span>`, `<span class="desk-time">${esc(item.freshness?.verified_at || '未核对')}</span>`],
    actions: button('open-wiki-page', '查看 / 核对', `data-page-id="${esc(item.id)}"`),
    detail: fields([['有效期', item.freshness?.expires_at || '未设置'], ['最近核对', item.freshness?.verified_at || '未核对'], ['学年', item.freshness?.academic_year || '未指定']]) + paragraph('摘要', item.summary),
  })).join(''), empty: '当前没有需要核对时效的词条', footer: '过期内容默认不参与问答；请优先核对已过期和可能过时的词条' });
}
function discoveriesHtml(sub) {
  return `<div class="desk-resources">${(sub.discoveries || []).map((item) => `<div class="desk-resource">
    <div><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title || item.url)}</a><p>${esc(item.summary || '')}</p>${item.date ? `<small>${esc(item.date)}</small>` : ''}</div>
    <div class="desk-resource-actions">${button('sub-collect', '收录', `${idAttr(sub.id)} data-url="${esc(item.url)}" data-title="${esc(item.title || '')}"`, true)}${button('sub-ignore', '忽略', `${idAttr(sub.id)} data-url="${esc(item.url)}"`)}</div>
  </div>`).join('')}</div>`;
}
function subscriptionsHtml(state) {
  return board({ title: '订阅发现', kind: 'subscriptions', count: state.subscriptions.length, tools: button('sub-add-open', `${icon('plus')}<span>添加订阅</span>`, '', true), columns: ['订阅名称 / 检查结果', '待收录', '状态', '最近检查', '操作'], rows: state.subscriptions.map((sub) => row({
    key: `sub-${sub.id}`, kind: 'subscriptions', title: sub.name || sub.url, subtitle: sub.last_status ? SUB_STATUS[sub.last_status] || '检查结果待核对' : '尚未检查',
    cells: [`<span class="desk-number">${(sub.discoveries || []).length} 条</span>`, badge(sub.enabled ? '启用' : '停用', sub.enabled ? 'green' : 'slate'), time(sub.last_checked_at)],
    actions: button('sub-check', state.subChecking === sub.id ? '检查中…' : '立即检查', `${idAttr(sub.id)} ${state.subChecking === sub.id ? 'disabled' : ''}`),
    detailLabel: (sub.discoveries || []).length ? `新内容 ${sub.discoveries.length}` : '详情',
    detail: fields([['订阅地址', sub.url], ['最近检查', sub.last_checked_at ? fmtDateTime(sub.last_checked_at) : '从未检查']]) + discoveriesHtml(sub) + `<div class="desk-detail-actions">${button('sub-toggle', sub.enabled ? '停用订阅' : '启用订阅', `${idAttr(sub.id)} data-enabled="${sub.enabled ? '1' : '0'}"`)}${button('sub-delete', '删除订阅', idAttr(sub.id))}</div>`,
  })).join(''), empty: '暂无订阅；添加学校或学院通知列表页后检查更新', footer: '仅抓取允许访问的公开页面；新内容经核对后才收录' });
}

export function renderServiceDesk(state) {
  const views = { overview: overviewHtml, handoffs: handoffsHtml, crisis: crisisHtml, gaps: gapsHtml, feedback: feedbackHtml, freshness: freshnessHtml, subscriptions: subscriptionsHtml };
  return (views[state.tab] || overviewHtml)(state);
}

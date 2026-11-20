/**
 * views/insights.js — 服务台（v0.2.0 RSI）
 *
 * 运营视图：概览 / 知识缺口 / 反馈 / 时效。
 * 数据来自 /api/insights/*；需登录账号或管理令牌。
 */
import {
  claimHandoff,
  checkSubscription,
  collectGapSuggestion,
  collectSubscription,
  createSubscription,
  deleteSubscription,
  getFreshness,
  getInsightsOverview,
  getJob,
  ignoreGap,
  ignoreSubscription,
  listCrisisEvents,
  listFeedback,
  listGaps,
  listHandoffs,
  listSubscriptions,
  replyHandoff,
  resolveGap,
  resolveHandoff,
  suggestForGap,
  toggleSubscription,
  updateCrisisStatus,
  updateFeedbackStatus,
} from '../api.js';
import { confirmDialog, copyText, esc, icon, loadingHtml, openModal, setButtonBusy, toast } from '../ui.js';
import { renderServiceDesk } from './insights-board.js';

function newState(section) {
  return {
    tab: section || 'overview', days: 7, overview: null, gaps: [], feedback: [],
    freshness: null, handoffs: [], crisis: [], subscriptions: [], subChecking: '',
    loading: false, loaded: false, error: '', authRequired: false, gapSuggesting: '',
  };
}

function tabsHtml(state) {
  return `<div class="insights-tabs">${[
    ['overview', '概览'], ['handoffs', '待人工'], ['crisis', '危机'],
    ['gaps', '知识缺口'], ['feedback', '反馈'], ['freshness', '时效'], ['subscriptions', '订阅发现'],
  ].map(([key, label]) => `<button class="insights-tab ${state.tab === key ? 'is-active' : ''}" type="button" data-action="insights-tab" data-tab="${key}">${label}</button>`).join('')}</div>`;
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
    const button = footer.querySelector('[type="submit"]');
    if (button.disabled) return;
    setButtonBusy(button, true, '发送中…');
    try {
      await replyHandoff(id, { content });
      modal.close();
      toast('已回复学生', 'success');
      await reload();
    } catch (err) {
      toast(err?.message || '回复失败', 'error');
      setButtonBusy(button, false);
    }
  });
}


function bodyHtml(state) {
  if (state.authRequired) return '<div class="desk-empty">请先登录服务台 <button class="btn btn-primary btn-sm" type="button" data-action="go-login">去登录</button></div>';
  if (state.loading && !state.loaded) return loadingHtml('加载服务台…', 'state-compact');
  const error = state.error ? `<div class="ops-inline-error" role="alert">${esc(state.error)} <button class="link-btn" type="button" data-action="insights-reload">重试</button></div>` : '';
  return error + renderServiceDesk(state);
}

export function mount(root, options = {}) {
  let alive = true;
  let refreshing = false;
  const opened = new Set();
  const busyActions = new Set();
  const state = newState(options.query?.get('section'));
  // 外部投射模式：由 hub 子导航控制页面，不再渲染内部 tab 与标题卡
  const embed = options?.query?.get('embed') === '1';
  root.innerHTML = `<div class="insights-page service-desk" id="insightsPage">
    <div id="insightsTabs"${embed ? ' hidden' : ''}></div>
    <div id="insightsBody"></div>
  </div>`;
  const page = root.querySelector('#insightsPage');
  const tabs = root.querySelector('#insightsTabs');
  const body = root.querySelector('#insightsBody');

  function render() {
    if (!alive) return;
    body.querySelectorAll('[data-desk-detail]').forEach((node) => {
      if (node.open) opened.add(node.dataset.deskDetail); else opened.delete(node.dataset.deskDetail);
    });
    const focus = body.contains(document.activeElement) ? document.activeElement : null;
    const focusId = focus?.dataset.id;
    const focusAction = focus?.dataset.action;
    tabs.innerHTML = tabsHtml(state);
    body.innerHTML = bodyHtml(state);
    body.querySelectorAll('[data-desk-detail]').forEach((node) => { node.open = opened.has(node.dataset.deskDetail); });
    body.querySelectorAll('[data-action="insights-reload"]').forEach((button) => { button.disabled = state.loading; });
    if (focusAction) [...body.querySelectorAll('[data-action]')].find((node) => node.dataset.action === focusAction && node.dataset.id === focusId)?.focus({ preventScroll: true });
  }

  async function load() {
    if (!alive || refreshing) return;
    refreshing = true;
    state.loading = true;
    state.authRequired = false;
    render();
    try {
      const [overview, gaps, feedback, fresh, handoffs, crisis, subscriptions] = await Promise.all([
        getInsightsOverview(state.days),
        listGaps({ status: 'open', days: 30, limit: 50 }),
        listFeedback({ days: 30, limit: 50 }),
        getFreshness(30),
        listHandoffs({ days: 30, limit: 100 }),
        listCrisisEvents({ limit: 50 }),
        listSubscriptions(),
      ]);
      if (!alive) return;
      state.loaded = true;
      state.error = '';
      state.overview = overview;
      state.gaps = gaps.items || [];
      state.feedback = feedback.items || [];
      state.freshness = fresh;
      state.handoffs = handoffs.items || [];
      state.crisis = crisis.items || [];
      state.subscriptions = subscriptions.items || [];
    } catch (err) {
      if (!alive) return;
      if (err?.status === 401) state.authRequired = true;
      else state.error = err?.message || '加载服务台失败，请重试';
    } finally {
      refreshing = false;
      if (alive) {
        state.loading = false;
        render();
      }
    }
  }

  async function runAction(request, successText, trigger) {
    const key = trigger ? `${trigger.dataset.action}:${trigger.dataset.id || ''}` : 'operation';
    if (!alive || busyActions.has(key)) return;
    busyActions.add(key);
    setButtonBusy(trigger, true);
    try {
      await request();
      if (!alive) return;
      toast(successText, 'success');
      await load();
    } catch (err) {
      if (alive) toast(err?.message || '操作失败', 'error');
    } finally {
      busyActions.delete(key);
      if (trigger?.isConnected) setButtonBusy(trigger, false);
    }
  }

  async function runGapSuggest(gapId) {
    if (state.gapSuggesting) return;
    state.gapSuggesting = gapId;
    render();
    try {
      const started = await suggestForGap(gapId);
      if (!alive) return;
      toast(started.message || 'AI 备选检索已开始', 'info');
      let job = null;
      for (let i = 0; i < 90; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (!alive) return;
        try {
          job = await getJob(started.job_id);
        } catch (err) {
          if (err?.status === 401) break;
          continue;
        }
        if (job && ['succeeded', 'failed', 'cancelled'].includes(job.status)) break;
      }
      if (!alive) return;
      state.gapSuggesting = '';
      if (job && job.status === 'succeeded') {
        const count = (job.result?.suggestions || []).length;
        toast(
          count
            ? `AI 找到 ${count} 条备选，请确认后一键收录`
            : 'AI 未找到相关备选，可手动补资料或关联词条',
          count ? 'success' : 'info',
          { duration: 6000 },
        );
      } else {
        toast(job?.error || 'AI 备选任务失败或被取消', 'error', { duration: 6000 });
      }
      await load();
    } catch (err) {
      if (!alive) return;
      state.gapSuggesting = '';
      render();
      toast(err?.message || 'AI 找备选失败', 'error');
    }
  }

  async function runGapCollect(gapId, index, trigger) {
    const key = `gap-collect:${gapId}:${index}`;
    if (!alive || busyActions.has(key)) return;
    const gap = state.gaps.find((g) => g.id === gapId);
    const item = (gap?.suggestions || [])[index];
    if (!item?.url) return;
    busyActions.add(key);
    setButtonBusy(trigger, true);
    try {
      const result = await collectGapSuggestion(gapId, { url: item.url, title: item.title });
      if (!alive) return;
      toast(
        result.duplicated ? '该链接已收录过，缺口已关联到现有文档' : '收录成功，缺口已关联词条来源',
        'success',
      );
      await load();
    } catch (err) {
      if (!alive) return;
      toast(err?.message || '收录失败', 'error');
    } finally {
      busyActions.delete(key);
      if (trigger?.isConnected) setButtonBusy(trigger, false);
    }
  }

  async function runSubCheck(subId) {
    if (state.subChecking) return;
    state.subChecking = subId;
    render();
    try {
      const started = await checkSubscription(subId);
      if (!alive) return;
      toast(started.message || '订阅检查已开始', 'info');
      let job = null;
      for (let i = 0; i < 90; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (!alive) return;
        try {
          job = await getJob(started.job_id);
        } catch (err) {
          if (err?.status === 401) break;
          continue;
        }
        if (job && ['succeeded', 'failed', 'cancelled'].includes(job.status)) break;
      }
      if (!alive) return;
      state.subChecking = '';
      if (job && job.status === 'succeeded') {
        toast(job.result?.message || '订阅检查完成', 'info', { duration: 6000 });
      } else {
        toast(job?.error || '订阅检查失败或被取消', 'error', { duration: 6000 });
      }
      await load();
    } catch (err) {
      if (!alive) return;
      state.subChecking = '';
      render();
      toast(err?.message || '订阅检查失败', 'error');
    }
  }

  function openSubscriptionModal() {
    const form = document.createElement('form');
    form.className = 'form';
    form.innerHTML = `<label class="field"><span class="field-label">订阅名称</span><input class="input" id="subName" name="name" placeholder="例如：学院通知" maxlength="100" /></label>
      <label class="field"><span class="field-label">通知列表页地址</span><input class="input" id="subUrl" name="url" type="url" required placeholder="https://学院官网/通知列表" /></label>
      <p class="field-help">仅支持可公开访问的通知列表页，新内容经核对后收录。</p>
      <div class="modal-actions"><button class="btn btn-ghost" type="button" data-cancel>取消</button><button class="btn btn-primary" type="submit" data-action="sub-add">添加订阅</button></div>`;
    const modal = openModal({ title: '添加订阅', body: form, size: 'md' });
    form.querySelector('[data-cancel]').addEventListener('click', modal.close);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      setButtonBusy(button, true, '添加中…');
      try {
        await createSubscription({ name: form.elements.name.value.trim(), url: form.elements.url.value.trim() });
        modal.close();
        if (alive) { toast('订阅已添加，可检查新内容', 'success'); await load(); }
      } catch (err) {
        if (form.isConnected) { toast(err?.message || '添加失败', 'error'); setButtonBusy(button, false); }
      }
    });
  }

  page.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger || trigger.disabled || !page.contains(trigger)) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'insights-tab':
        if (embed) { location.hash = `#/kb-admin?tab=${trigger.dataset.tab || 'overview'}`; }
        else { state.tab = trigger.dataset.tab || 'overview'; render(); }
        break;
      case 'insights-reload':
        load();
        break;
      case 'go-login':
        location.hash = `#/login?next=${encodeURIComponent(location.hash)}`;
        break;
      case 'handoff-claim':
        runAction(() => claimHandoff(trigger.dataset.id, { assignee_id: 'admin' }), '已认领工单', trigger);
        break;
      case 'handoff-reply':
        openReplyModal(trigger.dataset.id, trigger.dataset.ticket, load);
        break;
      case 'handoff-resolve':
        runAction(() => resolveHandoff(trigger.dataset.id, { resolution: '服务台标记已解决' }), '工单已解决', trigger);
        break;
      case 'handoff-copy':
        copyText(trigger.dataset.ticket || '').then((ok) => toast(ok ? '单号已复制' : '复制失败，请手动选择', ok ? 'success' : 'warning'));
        break;
      case 'crisis-status':
        runAction(
          () => updateCrisisStatus(trigger.dataset.id, {
            status: trigger.dataset.status,
            note: '服务台更新',
          }),
          '危机事件已更新', trigger,
        );
        break;
      case 'gap-suggest':
        runGapSuggest(trigger.dataset.id);
        break;
      case 'gap-collect':
        runGapCollect(trigger.dataset.id, Number(trigger.dataset.index || 0), trigger);
        break;
      case 'sub-add-open':
        openSubscriptionModal();
        break;
      case 'sub-check':
        runSubCheck(trigger.dataset.id);
        break;
      case 'sub-toggle':
        runAction(
          () => toggleSubscription(trigger.dataset.id, { enabled: trigger.dataset.enabled !== '1' }),
          trigger.dataset.enabled === '1' ? '订阅已停用' : '订阅已启用', trigger,
        );
        break;
      case 'sub-delete':
        confirmDialog({ title: '删除订阅', message: '删除该订阅后将不再检查新内容，已收录资料会保留。', confirmText: '删除订阅', danger: true }).then((ok) => { if (ok) return runAction(() => deleteSubscription(trigger.dataset.id), '订阅已删除', trigger); });
        break;
      case 'sub-collect':
        runAction(
          () => collectSubscription(trigger.dataset.id, {
            url: trigger.dataset.url,
            title: trigger.dataset.title || '',
          }),
          '收录成功，已从发现列表移除', trigger,
        );
        break;
      case 'sub-ignore':
        runAction(() => ignoreSubscription(trigger.dataset.id, { url: trigger.dataset.url }), '已忽略该条', trigger);
        break;
      case 'gap-resolve':
        runAction(() => resolveGap(trigger.dataset.id, { note: '服务台标记已解决' }), '缺口已标记解决', trigger);
        break;
      case 'gap-ignore':
        runAction(() => ignoreGap(trigger.dataset.id, { note: '服务台忽略' }), '缺口已忽略', trigger);
        break;
      case 'gap-copy':
        copyText(trigger.dataset.question || '').then((ok) => toast(ok ? '问题已复制，可交给管理助手补资料' : '复制失败，请手动选择', ok ? 'success' : 'warning'));
        break;
      case 'feedback-resolve':
        runAction(
          () => updateFeedbackStatus(trigger.dataset.id, { status: 'resolved' }),
          '反馈已标记处理', trigger,
        );
        break;
      case 'feedback-ignore':
        runAction(
          () => updateFeedbackStatus(trigger.dataset.id, { status: 'ignored' }),
          '反馈已忽略', trigger,
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

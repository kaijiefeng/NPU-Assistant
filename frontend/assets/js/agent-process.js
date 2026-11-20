/** 管理助手处理记录：只展示真实阶段和工具摘要，不展示内部推理。 */
import { esc } from './ui.js';

const LABELS = {
  get_scan_directory: '查询扫描目录', set_scan_directory: '保存扫描目录', scan_dir: '扫描文件夹',
  system_stats: '查看系统概览', list_documents: '查询源文件', get_document: '查看文档',
  list_wiki_pages: '查询知识词条', search_wiki: '搜索知识库', get_wiki_page: '查看知识词条',
  list_gaps: '查询知识缺口', gap_suggest: '查找备选资料', gap_resolve: '解决知识缺口',
  list_subscriptions: '查看订阅', subscription_check: '检查订阅', import_url: '收录链接', import_text: '收录文本',
  read_logs: '读取运行日志', read_file: '读取源码文件', get_job: '查看任务进度', list_jobs: '查询后台任务',
  list_alerts: '查看告警', retry_job: '重试任务', cancel_job: '取消任务', delete_document: '删除源文件',
  rebuild_wiki: '重建知识库', ops_task_create: '分析故障', ops_task_approve: '批准修复',
};

export function toolLabel(name) {
  const value = String(name || '');
  return LABELS[value] || (/^上传 /.test(value) ? value : '管理工具');
}
function jsonText(value) {
  return typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2);
}
function stepStatus(step, active) {
  if (step.pending) return active ? '执行中' : '结果未确认';
  if (step.status === 'confirmation') return '待确认';
  if (step.status === 'reused') return '已复用';
  return step.ok === false ? '失败' : step.ok === true ? '完成' : '已记录';
}
function duration(ms) {
  return ms === undefined ? '' : `${Math.max(.1, Number(ms) / 1000).toFixed(1)} 秒`;
}
function toolHtml(step, index, active) {
  const failed = step.ok === false && step.status !== 'confirmation';
  const mark = step.pending ? '…' : failed ? '!' : step.status === 'confirmation' ? '?' : '✓';
  return `<details class="agent-tool-record ${failed ? 'is-error' : ''}" data-tool-key="${esc(step.id || `step-${index}`)}">
    <summary><span class="agent-tool-mark">${mark}</span><span class="agent-tool-label">${esc(toolLabel(step.name))}</span><span class="agent-tool-state">${stepStatus(step, active)}</span></summary>
    ${step.summary ? `<p class="agent-tool-note">${esc(step.summary)}</p>` : ''}
    <dl class="agent-tool-meta"><div><dt>工具</dt><dd><code>${esc(step.name)}</code></dd></div>${step.elapsed_ms !== undefined ? `<div><dt>耗时</dt><dd>${duration(step.elapsed_ms)}</dd></div>` : ''}</dl>
    ${step.args ? `<section><h4>参数</h4><pre>${esc(jsonText(step.args))}</pre></section>` : ''}
    ${step.result ? `<section><h4>结果摘要</h4><pre>${esc(jsonText(step.result))}</pre></section>` : ''}
  </details>`;
}

export function processSummary(message) {
  const steps = message.steps || [];
  if (message.active) return message.phaseText || (steps.some(step => step.pending) ? '工具执行中' : '正在整理信息');
  if (steps.some(step => step.status === 'confirmation')) return `等待确认 · ${steps.length} 个工具`;
  if (message.stopReason && message.stopReason !== 'completed') return `处理已结束 · ${steps.length} 个工具`;
  const failed = steps.filter(step => step.ok === false).length;
  return steps.length ? `已使用 ${steps.length} 个工具${failed ? ` · ${failed} 项异常` : ''}` : '处理已完成';
}

export function updateAgentProcess(host, message) {
  let details = host.querySelector('.agent-process');
  const steps = message.steps || [];
  const show = steps.length || message.active || message.stopReason;
  host.hidden = !show;
  if (!show) return;
  if (!details) {
    host.innerHTML = '<details class="agent-process"><summary><span class="agent-process-indicator"></span><span data-process-summary></span><span class="agent-process-chevron">⌄</span></summary><div class="agent-process-body"><p class="agent-process-caption">处理过程 · 阶段与工具记录</p><div data-process-stage></div><div data-process-tools></div></div></details>';
    details = host.firstElementChild;
    details.addEventListener('click', event => {
      if (event.target.closest('summary') === details.firstElementChild) details._userToggled = true;
    });
    details.addEventListener('keydown', event => {
      if (['Enter', ' '].includes(event.key) && event.target === details.firstElementChild) details._userToggled = true;
    });
  }
  if (!details._userToggled) details.open = Boolean(message.active);
  details.classList.toggle('is-active', Boolean(message.active));
  details.classList.toggle('has-error', steps.some(step => step.ok === false) || Boolean(message.stopReason && message.stopReason !== 'completed'));
  host.querySelector('[data-process-summary]').textContent = processSummary(message);
  const stage = host.querySelector('[data-process-stage]');
  stage.textContent = message.active ? message.phaseText || '正在整理信息' : message.elapsedMs !== undefined ? `总耗时 ${duration(message.elapsedMs)}` : '';
  stage.hidden = !stage.textContent;
  const tools = host.querySelector('[data-process-tools]');
  const html = steps.map((step, index) => toolHtml(step, index, message.active)).join('');
  if (tools._html !== html) {
    const opened = new Set([...tools.querySelectorAll('[data-tool-key][open]')].map(node => node.dataset.toolKey));
    tools.innerHTML = html;
    tools.querySelectorAll('[data-tool-key]').forEach(node => { node.open = opened.has(node.dataset.toolKey); });
    tools._html = html;
  }
}

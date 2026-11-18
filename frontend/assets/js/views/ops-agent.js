/* 运维 Agent（仅超级管理员）：报错 → LLM 定位/方案 → 许可 → 工作树修复 → 测试 → PR。
 * 布局：左侧任务列表 + 右侧详情（分析卡片 / 许可操作 / 实时日志 / 结果）。
 */
import {
  approveOpsTask,
  cancelOpsTask,
  cleanupOpsTask,
  createOpsTask,
  getOpsTask,
  listOpsTasks,
} from '../api.js';
import { esc, icon, toast } from '../ui.js';

const state = {
  tasks: [],
  selected: null, // 当前详情（完整任务对象）
  filter: '',
  creating: false,
  pollTimer: null,
};

const STATUS_LABEL = {
  analyzing: '分析中',
  planned: '待许可',
  executing: '执行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

export async function mount(root) {
  state.selected = null;
  root.innerHTML = `<div class="ops-agent-page">
    <div class="ops-agent-layout">
      <section class="card ops-agent-list">
        <div class="card-head"><h3>${icon('terminal')} 修复任务</h3></div>
        <button class="btn btn-primary" type="button" data-action="ops-new" style="width:100%">${icon(
          'edit',
        )}<span>新建修复任务</span></button>
        <div id="opsTaskList" class="ops-task-list">${loadingHtml()}</div>
      </section>
      <section class="card ops-agent-detail" id="opsDetail">
        <div class="state state-empty">
          <div class="state-icon">${icon('terminal')}</div>
          <h3>运维 Agent</h3>
          <p>粘贴运维报错（pytest 失败、接口 500 等），Agent 会给出定位与修复方案；批准后在一次性 git 工作树内自动修复、测试并推送分支/PR，完成后清理现场并通知你。</p>
        </div>
      </section>
    </div>
  </div>`;

  pageEvents(root);
  await refreshList();
  const poll = async () => {
    if (!root.isConnected) return;
    if (state.selected && ['analyzing', 'executing'].includes(state.selected.status)) {
      await refreshDetail(state.selected.id, { silent: true });
    }
    state.pollTimer = setTimeout(poll, 2500);
  };
  poll();
  return () => {
    if (state.pollTimer) clearTimeout(state.pollTimer);
  };
}

function loadingHtml() {
  return `<p class="muted" style="padding:8px 0">${esc('正在加载任务…')}</p>`;
}

/* ------------------------------------------------------------------ */
async function refreshList() {
  try {
    const result = await listOpsTasks({ status: state.filter, limit: 100 });
    state.tasks = result.items || [];
    const holder = document.getElementById('opsTaskList');
    if (!holder) return;
    if (!state.tasks.length) {
      holder.innerHTML = `<p class="muted" style="padding:8px 0">还没有任务。</p>`;
      return;
    }
    holder.innerHTML = state.tasks
      .map(
        (task) => `
        <button class="ops-task-item ${state.selected?.id === task.id ? 'is-active' : ''}" type="button"
          data-action="ops-select" data-id="${esc(task.id)}">
          <span class="ops-task-title">${esc(task.title)}</span>
          <span class="badge ${task.status === 'failed' ? 'is-danger' : ''}">${esc(
            STATUS_LABEL[task.status] || task.status,
          )}</span>
        </button>`,
      )
      .join('');
  } catch (err) {
    toast(err?.message || '加载任务失败', 'error');
  }
}

async function refreshDetail(taskId, { silent = false } = {}) {
  try {
    const detail = await getOpsTask(taskId);
    state.selected = detail;
    renderDetail();
    if (!silent) {
      const holder = document.getElementById('opsTaskList');
      if (holder) await refreshList();
    }
  } catch (err) {
    if (!silent) toast(err?.message || '加载任务详情失败', 'error');
  }
}

/* ------------------------------------------------------------------ */
function renderDetail() {
  const holder = document.getElementById('opsDetail');
  if (!holder) return;
  const task = state.selected;
  if (!task) {
    holder.innerHTML = `<p class="muted">选择左侧任务查看详情。</p>`;
    return;
  }
  const analysis = task.analysis || {};
  const plan = task.plan || [];
  const active = ['analyzing', 'executing'].includes(task.status);
  holder.innerHTML = `
    <div class="card-head">
      <h3>${icon('terminal')} ${esc(task.title)}</h3>
      <span class="badge ${task.status === 'failed' ? 'is-danger' : ''}">${esc(
        STATUS_LABEL[task.status] || task.status,
      )}</span>
    </div>
    <p class="muted" style="margin:0 0 10px">${esc(task.created_at || '')} · ${esc(task.branch || '未建分支')}${
    task.pr_url ? ` · <a href="${esc(task.pr_url)}" target="_blank" rel="noopener noreferrer">PR / 分支链接</a>` : ''
  }</p>

    ${
      task.status === 'analyzing'
        ? `<p class="muted">${icon('info')} LLM 正在分析报错，完成后此处显示定位与方案。</p>`
        : ''
    }

    ${
      analysis.root_cause || task.status === 'planned'
        ? `<div class="ops-block">
            <h4>定位</h4><p>${esc(analysis.root_cause || '（等待分析）')}</p>
            <h4>修复方案</h4><ul>${(analysis.fix_plan || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
            <h4>测试方案</h4><ul>${(analysis.test_plan || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
            <h4>风险</h4><p>${esc(analysis.risk || '—')}</p>
          </div>`
        : ''
    }

    ${
      task.status === 'planned'
        ? `<div class="ops-approve">
            <label class="scan-mode-item"><input type="checkbox" id="opsRunE2e" /><span>同时执行端到端验证（rsi_verify，较慢）</span></label>
            <div class="gap-card-actions">
              <button class="btn btn-primary" type="button" data-action="ops-approve" data-id="${esc(task.id)}">${icon(
                'check',
              )}<span>批准执行（在工作树内自动修复 + 测试 + 推送）</span></button>
              <button class="btn btn-ghost" type="button" data-action="ops-cancel" data-id="${esc(task.id)}">${icon(
                'x',
              )}<span>取消任务</span></button>
            </div>
          </div>`
        : ''
    }

    ${
      ['failed', 'done'].includes(task.status) && task.worktree_dir
        ? `<div class="gap-card-actions"><button class="btn btn-ghost" type="button" data-action="ops-cleanup" data-id="${esc(
            task.id,
          )}">${icon('trash')}<span>清理残留工作树</span></button></div>`
        : ''
    }

    ${
      plan.length
        ? `<div class="ops-block"><h4>已应用修改</h4><ul>${plan
            .map((p) => `<li><code>${esc(p.path || '')}</code>（${esc(p.action || '')}）</li>`)
            .join('')}</ul></div>`
        : ''
    }

    ${task.test_summary ? `<div class="ops-block"><h4>测试结果</h4><p>${esc(task.test_summary)}</p></div>` : ''}

    <div class="ops-block">
      <h4>执行日志</h4>
      <pre class="ops-log">${esc(task.log || '（暂无日志）')}</pre>
    </div>
  `;
}

/* ------------------------------------------------------------------ */
function pageEvents(root) {
  root.addEventListener('click', async (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    const id = trigger.dataset.id;
    try {
      if (action === 'ops-new') return showCreateForm(root);
      if (action === 'ops-create-submit') return submitCreate(root);
      if (action === 'ops-select') {
        state.selected = null;
        renderDetail();
        await refreshDetail(id);
        return;
      }
      if (action === 'ops-approve') {
        trigger.disabled = true;
        const runE2e = Boolean(document.getElementById('opsRunE2e')?.checked);
        const started = await approveOpsTask(id, { run_e2e: runE2e });
        toast(started.message || '已批准，执行中', 'info');
        await refreshDetail(id, { silent: true });
        await refreshList();
        return;
      }
      if (action === 'ops-cancel') {
        await cancelOpsTask(id);
        toast('任务已取消，工作树已清理', 'success');
        await refreshDetail(id);
        await refreshList();
        return;
      }
      if (action === 'ops-cleanup') {
        await cleanupOpsTask(id);
        toast('工作树已清理', 'success');
        await refreshDetail(id);
        return;
      }
    } catch (err) {
      toast(err?.message || '操作失败', 'error');
      if (trigger) trigger.disabled = false;
    }
  });
}

function showCreateForm(root) {
  const detail = document.getElementById('opsDetail');
  if (!detail) return;
  state.creating = true;
  detail.innerHTML = `
    <div class="card-head"><h3>${icon('terminal')} 新建修复任务</h3></div>
    <p class="muted" style="margin:0 0 10px">粘贴完整报错（pytest 失败输出、堆栈、接口错误等）。Agent 先给出定位与方案，<strong>经你批准后</strong>才会在一次性 git 工作树内执行修复。</p>
    <input class="input" id="opsTitle" placeholder="任务标题（可留空）" style="margin-bottom:8px" />
    <textarea class="input" id="opsError" rows="10" placeholder="粘贴报错信息（≥20 字）……" style="font-family:var(--mono, monospace)"></textarea>
    <div class="gap-card-actions" style="margin-top:10px">
      <button class="btn btn-primary" type="button" data-action="ops-create-submit" ${state.creating ? 'disabled' : ''}>${icon(
        'send',
      )}<span>提交并开始分析</span></button>
    </div>
  `;
}

async function submitCreate(root) {
  const errorInput = document.getElementById('opsError')?.value || '';
  const title = document.getElementById('opsTitle')?.value || '';
  try {
    const started = await createOpsTask({ error_input: errorInput, title });
    toast(started.message || '任务已创建，分析中', 'info');
    await refreshList();
    await refreshDetail(started.task_id);
  } catch (err) {
    toast(err?.message || '创建任务失败', 'error');
  }
}

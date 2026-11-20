/** 源文件页扫描入口：服务器目录预览、默认目录保存及后台收录。 */
import { previewScanDirectory, saveScanDirectory, scanKnowledgeFolder } from './api.js';
import { loadScanDirectory, scanErrorMessage } from './scan-compat.js';
import { esc, fmtBytes, loadingHtml, openModal, setButtonBusy, toast } from './ui.js';

export async function openScanFolderModal() {
  let alive = true;
  const modal = openModal({ title: '扫描文件夹', subtitle: '扫描运行服务的电脑上的资料目录', body: loadingHtml('正在读取扫描目录…', 'state-compact'), size: 'lg', onClose: () => { alive = false; } });
  let info;
  try { info = await loadScanDirectory(); }
  catch (err) {
    if (!modal.panel.isConnected) return;
    modal.body.innerHTML = `<div class="inline-error" role="alert">${esc(scanErrorMessage(err))}</div>`;
    return;
  }
  if (!modal.panel.isConnected) return;
  const form = document.createElement('form');
  form.className = 'form';
  form.innerHTML = `<p class="field-help">${info.legacy ? '填写运行服务的电脑上的完整文件夹路径，浏览器不能选择服务器文件夹。留空可尝试服务端已配置的默认目录。' : '这里填写服务器上的路径，浏览器不能选择服务器文件夹。相对路径以项目根目录为基准。'}</p>
    <label class="field"><span class="field-label">扫描目录</span><input class="input" name="path" aria-label="扫描目录" maxlength="4096" placeholder="留空使用默认扫描目录" value="${esc(info.saved_path || info.path || '')}" /></label>
    ${info.legacy
      ? '<div class="inline-error is-warn" role="status">当前服务尚未加载新版扫描目录接口。可以继续填写服务器上的完整路径并开始扫描；目录预览和默认目录保存需重启后端后使用。</div>'
      : `<div class="field-help"><strong>默认来源：</strong><span data-directory-source>${info.source === 'saved' ? '已保存目录' : '系统默认目录'}</span><br /><strong>项目根目录：</strong><span style="overflow-wrap:anywhere">${esc(info.project_root)}</span><br /><strong>系统默认：</strong><span style="overflow-wrap:anywhere">${esc(info.default_path)}</span>${info.allowed_roots?.length ? `<br /><strong>允许范围：</strong>${esc(info.allowed_roots.join('；'))}` : ''}<br />每次最多 ${Number(info.max_files)} 个文件，递归最多 ${Number(info.max_depth)} 层。</div>`}
    ${!info.accessible ? `<div class="inline-error" role="alert">${esc(info.error)}</div>` : ''}
    <div class="form-row"><label class="field"><span class="field-label">收录方式</span><select class="input" name="mode"><option value="skip">只导入新文件（推荐）</option><option value="update">更新已变更文件并重新编译</option></select></label>
      <label class="field field-inline"><input type="checkbox" name="recursive" checked /><span>包含子文件夹</span></label></div>
    ${!info.legacy ? '<div class="card-actions" style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-ghost btn-sm" type="button" data-preview>预览文件</button><button class="btn btn-ghost btn-sm" type="button" data-save>保存为默认目录</button></div>' : ''}
    <div data-result aria-live="polite"></div>
    <div class="modal-actions"><button class="btn btn-ghost" type="button" data-close>关闭</button><button class="btn btn-primary" type="submit">开始扫描</button></div>`;
  modal.body.replaceChildren(form);
  const result = form.querySelector('[data-result]');
  let busy = false;
  function payload() { return { path: form.elements.path.value.trim(), recursive: form.elements.recursive.checked, mode: form.elements.mode.value }; }
  function freeze(value) {
    busy = value;
    form.querySelectorAll('input, select, [data-preview], [data-save], [type="submit"]').forEach((node) => { node.disabled = value; });
  }
  async function action(button, work) {
    if (busy) return;
    freeze(true);
    setButtonBusy(button, true);
    try { await work(payload()); }
    catch (err) { if (alive && form.isConnected) result.innerHTML = `<div class="inline-error" role="alert">${esc(scanErrorMessage(err))}</div>`; }
    finally { if (form.isConnected) { freeze(false); setButtonBusy(button, false); } }
  }
  form.querySelector('[data-close]').addEventListener('click', modal.close);
  form.addEventListener('input', () => { result.replaceChildren(); });
  form.addEventListener('change', () => { result.replaceChildren(); });
  form.querySelector('[data-preview]')?.addEventListener('click', (event) => action(event.currentTarget, async (data) => {
    const preview = await previewScanDirectory(data);
    if (!alive || !form.isConnected) return;
    result.innerHTML = `<div class="sub-card"><strong>本次可处理 ${Number(preview.files)} 个文件${preview.truncated ? '（已达本次上限）' : ''}</strong><p class="field-help" style="overflow-wrap:anywhere">${esc(preview.path)}</p>${preview.samples?.length ? `<ul style="padding-left:18px;margin-top:8px;font-size:12px">${preview.samples.map((item) => `<li style="overflow-wrap:anywhere">${esc(item.name)} · ${item.size_bytes === null ? '大小未知' : esc(fmtBytes(item.size_bytes))}</li>`).join('')}</ul>` : '<p class="field-help">没有符合扩展名和大小限制的文件，请检查目录与子目录选项。</p>'}</div>`;
  }));
  form.querySelector('[data-save]')?.addEventListener('click', (event) => action(event.currentTarget, async (data) => {
    const saved = await saveScanDirectory({ path: data.path });
    if (!alive || !form.isConnected) return;
    info = saved;
    form.elements.path.value = saved.path;
    const source = form.querySelector('[data-directory-source]');
    if (source) source.textContent = saved.source === 'saved' ? '已保存目录' : '系统默认目录';
    result.innerHTML = `<p class="field-help">已保存默认目录：${esc(saved.path)}。管理助手可通过目录查询工具读取。</p>`;
    toast('默认扫描目录已保存', 'success');
  }));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    void action(button, async (data) => {
      const started = await scanKnowledgeFolder(data);
      if (!alive || !form.isConnected) return;
      result.innerHTML = `<div class="sub-card"><strong>已提交后台扫描</strong><p class="field-help" style="overflow-wrap:anywhere">目录：${esc(started.path || data.path)}<br />任务号：${esc(started.job_id)}<br />${esc(started.message || '')}</p><a class="btn btn-ghost btn-sm" href="#/ops?tab=jobs" data-task-link>查看任务进度</a></div>`;
      result.querySelector('[data-task-link]')?.addEventListener('click', modal.close);
      toast('扫描任务已提交，可在运维管理查看进度', 'success');
    });
  });
}

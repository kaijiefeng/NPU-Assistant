/**
 * mobile-access.js — 全局「手机扫码访问」弹窗
 *
 * 数据来自 GET /api/mobile/access：
 *  - urls/preferred_url/qr：首选地址二维码；
 *  - 切换地址后改用 GET /api/mobile/qr.png?url=... 重新生成；
 *  - tunnel：一键 Cloudflare 临时隧道（start/stop），未安装 cloudflared 时给出安装提示。
 */
import { getMobileAccess, mobileQrUrl, startMobileTunnel, stopMobileTunnel } from './api.js';
import { copyText, esc, icon, openModal, setButtonBusy, toast } from './ui.js';

const WINGET_CMD = 'winget install Cloudflare.cloudflared';

let active = null; // { modal, state }
let state = null;

function selectedUrl() {
  const access = state.access || {};
  const urls = Array.isArray(access.urls) ? access.urls : [];
  const wanted = state.selectedUrl || access.preferred_url || urls[0]?.url || '';
  return wanted;
}

function selectedMeta() {
  const access = state.access || {};
  const urls = Array.isArray(access.urls) ? access.urls : [];
  const url = selectedUrl();
  return urls.find((item) => item.url === url) || { url, label: '当前地址', kind: 'current' };
}

function qrSource() {
  const access = state.access || {};
  if (state.qrSrc) return state.qrSrc;
  if (access.qr) return access.qr;
  const url = selectedUrl();
  return url ? mobileQrUrl(url) : '';
}

function badgeHtml(meta) {
  const badges = [];
  const url = String(meta.url || '');
  if (/^https:/i.test(url) || meta.secure) {
    badges.push(`<span class="mobile-badge is-ok">${icon('shield')}HTTPS · 可安装 PWA</span>`);
  } else {
    badges.push(`<span class="mobile-badge">${icon('info')}HTTP · 仅局域网可访问</span>`);
  }
  if (state.access?.token_required) {
    badges.push(`<span class="mobile-badge is-warn">${icon('key')}需要管理令牌（扫码后到设置页填写）</span>`);
  } else {
    badges.push(`<span class="mobile-badge">${icon('check')}本机演示无需令牌</span>`);
  }
  return badges.join('');
}

function tunnelHtml() {
  const tunnel = state.access?.tunnel || {};
  if (state.tunnelError) {
    return `<h3>${icon('globe')}公网访问</h3>
      <div class="inline-error">${icon('alert')}<span>${esc(state.tunnelError)}</span></div>
      <div class="mobile-tunnel-actions">
        <button class="btn btn-ghost btn-sm" type="button" data-action="refresh">${icon('refresh')}<span>重试</span></button>
      </div>`;
  }
  if (tunnel.running && tunnel.public_url) {
    return `<h3>${icon('globe')}公网访问已开启</h3>
      <p class="field-help">Cloudflare 临时隧道运行中，手机不在同一 Wi-Fi 时也可扫码访问。</p>
      <div class="mobile-url-row">
        <code class="mobile-url" title="${esc(tunnel.public_url)}">${esc(tunnel.public_url)}</code>
        <button class="icon-btn" type="button" data-action="copy-tunnel" title="复制公网地址" aria-label="复制公网地址">${icon('copy')}</button>
      </div>
      <div class="mobile-tunnel-actions">
        <button class="btn btn-ghost btn-sm is-danger" type="button" data-action="stop-tunnel">${icon('power')}<span>关闭公网访问</span></button>
      </div>`;
  }
  if (tunnel.available) {
    // 后端要求服务端已设置 NPU_ADMIN_TOKEN 才允许开启隧道：未启用令牌时置灰并引导去设置
    if (!state.access?.token_required) {
      return `<h3>${icon('globe')}公网访问（需先启用服务端令牌）</h3>
        <p class="field-help">服务端未启用管理令牌，暂时无法开启公网访问。请先到「设置 → 移动端与管理令牌」输入 ≥16 位随机令牌并点「保存令牌」（或「启用服务端令牌」），启用后再回来开启。仅在手机设置页填令牌不会改变服务端配置。</p>
        <div class="mobile-tunnel-actions">
          <button class="btn btn-primary btn-sm" type="button" data-action="go-settings">${icon('key')}<span>去设置启用令牌</span></button>
          <button class="btn btn-ghost btn-sm" type="button" data-action="start-tunnel" disabled>${icon('globe')}<span>一键开启公网访问</span></button>
        </div>`;
    }
    return `<h3>${icon('globe')}公网访问（可选）</h3>
      <p class="field-help">手机和电脑不在同一 Wi-Fi 时，可一键开启 Cloudflare 临时隧道：无需域名、临时 HTTPS 地址、电脑需保持联网。</p>
      <div class="mobile-tunnel-actions">
        <button class="btn btn-primary btn-sm" type="button" data-action="start-tunnel">${icon('globe')}<span>一键开启公网访问</span></button>
      </div>`;
  }
  return `<h3>${icon('globe')}公网访问不可用</h3>
    <p class="field-help">${esc(tunnel.install_hint || '未检测到 cloudflared，无法开启公网隧道。')}</p>
    <div class="mobile-url-row">
      <code class="mobile-url" title="${esc(WINGET_CMD)}">${esc(WINGET_CMD)}</code>
      <button class="icon-btn" type="button" data-action="copy-cmd" title="复制安装命令" aria-label="复制安装命令">${icon('copy')}</button>
    </div>
    <p class="field-help">Windows 安装后重启后端服务，再点击「刷新」即可开启公网访问；也可从 Cloudflare 官网下载 cloudflared 并加入 PATH。</p>`;
}

function bodyHtml() {
  if (state.error) {
    return `<div class="mobile-access-error">
      <div class="state state-error">
        <div class="state-icon">${icon('alert')}</div>
        <h3>手机访问信息加载失败</h3>
        <p>${esc(state.error)}</p>
        <div class="state-action"><button class="btn btn-primary" type="button" data-action="refresh">${icon('refresh')}<span>重试</span></button></div>
      </div>
    </div>`;
  }
  if (!state.access) {
    return `<div class="mobile-access-loading"><div class="spinner spinner-lg" aria-hidden="true"></div><p>正在获取手机访问地址…</p></div>`;
  }
  const access = state.access;
  const urls = Array.isArray(access.urls) ? access.urls : [];
  const meta = selectedMeta();
  const url = selectedUrl();
  const src = qrSource();
  const chips = urls.length
    ? `<div class="mobile-url-chips">${urls
        .map(
          (item) => `<button class="mobile-url-chip ${item.url === url ? 'is-active' : ''}" type="button"
            data-action="select-url" data-url="${esc(item.url)}" title="${esc(item.url)}">
            <span class="chip-kind kind-${esc(item.kind || 'current')}">${esc(kindLabel(item.kind))}</span>
            <span class="chip-url">${esc(item.url)}</span>
          </button>`,
        )
        .join('')}</div>`
    : `<p class="field-help">暂未获取到可用地址，请确认后端服务已启动。</p>`;
  return `<div class="mobile-access">
    <div class="mobile-qr-panel">
      <div class="mobile-qr-frame">
        ${
          src
            ? `<img class="mobile-qr" src="${esc(src)}" alt="手机扫码访问二维码" />`
            : `<div class="mobile-qr-empty">${icon('qr')}<p>暂未生成二维码</p><span>请点击刷新重试，或确认后端服务正常</span></div>`
        }
      </div>
      <p class="mobile-qr-caption">${url ? esc(url) : '未获取到访问地址'}</p>
    </div>
    <div class="mobile-access-info">
      <ol class="mobile-steps">
        <li>手机扫码，用浏览器打开链接</li>
        <li>需要管理操作时，到「设置 → 移动端与管理令牌」填写令牌</li>
        <li>HTTPS 地址可「添加到主屏幕」，像 App 一样使用</li>
      </ol>
      <div class="mobile-section">
        <h3>${icon('link')}访问地址</h3>
        ${chips}
        <div class="mobile-url-row">
          <code class="mobile-url" title="${esc(url)}">${esc(url || '—')}</code>
          <button class="icon-btn" type="button" data-action="copy-url" title="复制访问地址" aria-label="复制访问地址">${icon('copy')}</button>
        </div>
        <div class="mobile-tags">${badgeHtml(meta)}</div>
      </div>
      <div class="mobile-section mobile-tunnel-section">${tunnelHtml()}</div>
    </div>
  </div>`;
}

function kindLabel(kind) {
  return (
    { tunnel: '公网', public: '公网', lan: '局域网', current: '当前', local: '本机' }[kind] ||
    '地址'
  );
}

function render() {
  if (!active) return;
  active.modal.body.innerHTML = bodyHtml();
  active.modal.overlay.querySelector('.modal-foot')?.classList.toggle('is-busy', state.busy);
  const startBtn = active.modal.overlay.querySelector('[data-action="start-tunnel"]');
  if (startBtn) startBtn.disabled = state.busy || !state.access?.token_required;
  const stopBtn = active.modal.overlay.querySelector('[data-action="stop-tunnel"]');
  if (stopBtn) stopBtn.disabled = state.busy;
  const img = active.modal.body.querySelector('.mobile-qr');
  if (img) {
    img.addEventListener(
      'error',
      () => {
        const frame = img.closest('.mobile-qr-frame');
        if (frame) {
          frame.innerHTML = `<div class="mobile-qr-empty">${icon('qr')}<p>二维码生成失败</p><span>请点击刷新重试，或直接复制下方地址在手机打开</span></div>`;
        }
      },
      { once: true },
    );
  }
}

async function loadAccess({ keepSelection = false } = {}) {
  if (!active) return;
  const previous = state.selectedUrl;
  state.error = '';
  render();
  try {
    const access = await getMobileAccess();
    state.access = access;
    const urls = Array.isArray(access?.urls) ? access.urls : [];
    const preferred = access?.preferred_url || urls[0]?.url || '';
    const tunnelUrl = access?.tunnel?.public_url || '';
    const keep =
      keepSelection && previous && (urls.some((item) => item.url === previous) || tunnelUrl === previous)
        ? previous
        : '';
    state.selectedUrl = keep || (access?.tunnel?.running && tunnelUrl ? tunnelUrl : preferred);
    state.qrSrc = access?.qr || (state.selectedUrl ? mobileQrUrl(state.selectedUrl) : '');
    if (access?.tunnel?.running && access.tunnel.public_url) state.qrSrc = mobileQrUrl(access.tunnel.public_url);
    render();
  } catch (err) {
    state.access = null;
    state.error = err?.message || '无法获取手机访问地址';
    render();
  }
}

function selectUrl(url) {
  state.selectedUrl = url;
  state.qrSrc = url ? mobileQrUrl(url) : '';
  render();
}

async function toggleTunnel(action) {
  if (state.busy) return;
  if (action === 'start' && !state.access?.token_required) {
    toast('开启公网访问前，请先在「设置 → 移动端与管理令牌」填写管理员令牌（服务端需配置 NPU_ADMIN_TOKEN）', 'warning', {
      title: '需要管理员令牌',
    });
    return;
  }
  state.busy = true;
  state.tunnelError = '';
  render();
  try {
    const access =
      action === 'start' ? await startMobileTunnel() : await stopMobileTunnel();
    state.access = access || state.access;
    if (action === 'start') {
      const publicUrl = access?.tunnel?.public_url || '';
      if (publicUrl) {
        state.selectedUrl = publicUrl;
        state.qrSrc = mobileQrUrl(publicUrl);
      }
      toast('公网访问已开启，二维码已切换到公网地址', 'success', { title: '公网访问' });
    } else {
      const urls = Array.isArray(state.access?.urls) ? state.access.urls : [];
      state.selectedUrl = state.access?.preferred_url || urls[0]?.url || '';
      state.qrSrc = state.access?.qr || (state.selectedUrl ? mobileQrUrl(state.selectedUrl) : '');
      toast('已关闭公网访问', 'success');
    }
    state.busy = false;
    render();
  } catch (err) {
    state.busy = false;
    state.tunnelError = err?.message || '操作失败，请稍后重试';
    render();
    toast(state.tunnelError, 'error', { title: '公网访问' });
  }
}

async function copy(value, message) {
  const ok = await copyText(value || '');
  toast(ok ? message : '复制失败，请手动选择复制', ok ? 'success' : 'error', { duration: 2400 });
}

export function openMobileAccessModal() {
  if (active) {
    // 已打开：聚焦到弹窗而不是重复创建
    active.modal.panel.classList.add('is-pulse');
    window.setTimeout(() => active?.modal.panel.classList.remove('is-pulse'), 400);
    return;
  }
  state = { access: null, selectedUrl: '', qrSrc: '', error: '', tunnelError: '', busy: false };
  const modal = openModal({
    title: '手机扫码访问',
    subtitle: '扫码即可在手机上打开；同 Wi-Fi 选局域网，外网访问可一键开启公网隧道。',
    size: 'lg',
    body: bodyHtml(),
    footer: `<button class="btn btn-ghost" type="button" data-action="refresh">${icon('refresh')}<span>刷新</span></button>
      <button class="btn btn-primary" type="button" data-action="close">完成</button>`,
    onClose: () => {
      active = null;
      state = null;
    },
  });
  active = { modal, state };
  modal.panel.classList.add('mobile-access-modal');
  modal.overlay.classList.add('is-mobile-access-backdrop');
  modal.overlay.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    switch (action) {
      case 'refresh':
        loadAccess({ keepSelection: true });
        break;
      case 'select-url':
        selectUrl(trigger.dataset.url || '');
        break;
      case 'copy-url':
        copy(selectedUrl(), '访问地址已复制');
        break;
      case 'copy-tunnel':
        copy(state.access?.tunnel?.public_url || '', '公网地址已复制');
        break;
      case 'copy-cmd':
        copy(WINGET_CMD, '安装命令已复制');
        break;
      case 'start-tunnel':
        toggleTunnel('start');
        break;
      case 'stop-tunnel':
        toggleTunnel('stop');
        break;
      case 'go-settings':
        active?.modal.close();
        location.hash = '#/settings';
        break;
      case 'close':
        active?.modal.close();
        break;
      default:
        break;
    }
  });
  loadAccess();
}

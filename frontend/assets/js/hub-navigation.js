/**
 * hub-navigation.js — 管理页的紧凑分组导航。
 * 只展开当前分组的子页，切换确认与权限仍由所属页面处理。
 */
import { esc } from './ui.js';

export function pageOptionsHtml(items, selected) {
  const option = (item) => `<option value="${esc(item.key)}" ${item.key === selected ? 'selected' : ''}>${esc(item.label)}</option>`;
  const groups = [];
  for (const item of items) {
    const name = item.section || '';
    const previous = groups[groups.length - 1];
    if (name && previous?.label === name) previous.items.push(item);
    else groups.push({ label: name, items: [item] });
  }
  return groups.map((entry) => entry.label
    ? `<optgroup label="${esc(entry.label)}">${entry.items.map(option).join('')}</optgroup>`
    : entry.items.map(option).join('')).join('');
}

export function mountHubNavigation(root, { groups, current, onSelect, label = '页面导航' }) {
  const remembered = new Map();
  let selected = current;

  function render() {
    const group = groups.find((entry) => entry.items.some((item) => item.key === selected)) || groups[0];
    if (!group) return;
    remembered.set(group.key, selected);
    const focused = root.contains(document.activeElement);
    const focusGroup = document.activeElement?.dataset.hubGroup;
    root.setAttribute('aria-label', label);
    root.innerHTML = `<div class="hub-groups" role="group" aria-label="功能分组">
      ${groups.map((entry) => `<button class="hub-group-button ${entry === group ? 'is-active' : ''}" type="button" data-hub-group="${esc(entry.key)}" aria-pressed="${entry === group}">${esc(entry.label)}</button>`).join('')}
    </div>
    ${group.items.length > 1 ? `<label class="hub-page-picker"><span>${esc(group.label)}</span>
      <select class="input" data-hub-select aria-label="${esc(group.label)}子页">
        ${pageOptionsHtml(group.items, selected)}
      </select>
    </label>` : ''}`;
    if (focused) {
      const target = focusGroup
        ? [...root.querySelectorAll('[data-hub-group]')].find((button) => button.dataset.hubGroup === focusGroup)
        : root.querySelector('[data-hub-select]');
      target?.focus({ preventScroll: true });
    }
  }

  function select(key) {
    if (key === selected) return;
    // 导航由页面在切换成功或取消后更新，避免未保存确认前显示错误选中态。
    onSelect(key);
  }

  const click = (event) => {
    const button = event.target.closest('[data-hub-group]');
    if (!button || !root.contains(button)) return;
    const group = groups.find((entry) => entry.key === button.dataset.hubGroup);
    if (group) select(remembered.get(group.key) || group.items[0].key);
  };
  const change = (event) => {
    if (event.target.matches('[data-hub-select]')) select(event.target.value);
  };
  root.addEventListener('click', click);
  root.addEventListener('change', change);
  render();

  return {
    update(key) { selected = key; render(); },
    destroy() {
      root.removeEventListener('click', click);
      root.removeEventListener('change', change);
    },
  };
}

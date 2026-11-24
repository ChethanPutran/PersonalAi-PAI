// Tasks — every task (any status) with filters, detail dialog and pause / resume / cancel (legacy /system/tasks API).
import { h, clear, debounce } from '../dom.js';
import { icon } from '../icons.js';
import { api } from '../api.js';
import { btn, statusChip, confirmDialog, infoDialog, codeBlock, errorState, pageHead, skeletonRows } from '../ui.js';
import { fmtDateTime } from '../format.js';

const STATUSES = ['', 'pending', 'running', 'paused', 'completed', 'failed', 'cancelled'];

export async function mount(container, ctx) {
  let alive = true; let seq = 0;
  const status = h('select', { class: 'select', id: 'task-filter-status', 'aria-label': 'Filter by status', onchange: () => load() },
    STATUSES.map((s) => h('option', { value: s }, s || 'all statuses')));
  const user = h('input', { class: 'input', id: 'task-filter-user', placeholder: 'user_id', 'aria-label': 'Filter by user id', oninput: debounce(() => load(), 300) });
  const refresh = btn('Refresh', { ic: 'refresh', onclick: () => load() });
  refresh.id = 'btn-refresh-tasks';
  const content = h('div', { id: 'tasks-content' });
  const count = h('span', { class: 'sub' });
  container.append(h('div', { class: 'page' }, pageHead('Tasks', 'Every task the runtime has seen, across all users. Use Active Tasks for what is running now.', refresh),
    h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'All tasks'), count,
      h('div', { class: 'right' }, status, h('div', { class: 'search' }, icon('search'), user))), content)));

  async function load() {
    const my = ++seq;
    const q = { limit: 200 };
    if (status.value) q.status = status.value;
    if (user.value.trim()) q.user_id = user.value.trim();
    if (!content.firstChild) content.append(skeletonRows(6, 7));
    try {
      const data = await api('GET', '/system/tasks', { params: q });
      if (!alive || my !== seq) return;
      count.textContent = `${data.total ?? (data.tasks || []).length} total`;
      ctx.store.set({ tasksTotal: data.total ?? null });
      render(data.tasks || []);
    } catch (e) { if (alive && my === seq) { clear(content); content.append(errorState(e, load)); } }
  }

  async function act(action, t) {
    if (action === 'cancel' && !(await confirmDialog({ title: 'Cancel task?', message: `Cancel task ${t.id.slice(0, 12)}?`, confirmLabel: 'Cancel task', cancelLabel: 'Keep', danger: true }))) return;
    try { await api('POST', `/system/tasks/${t.id}/${action}`); ctx.toast({ cancel: 'Task cancelled', pause: 'Task paused', resume: 'Task resumed' }[action], 'success'); load(); }
    catch (e) { ctx.toast(e.message, 'error'); }
  }

  async function detail(t) {
    try {
      const d = await api('GET', `/system/tasks/${t.id}`);
      const rows = [['Status', statusChip(d.status || 'unknown')], ['Capability', d.capability || '—'], ['User', h('code', {}, d.user_id || '—')], ['Device', h('code', {}, d.device_id || '—')],
        ['Executor', h('code', {}, d.executor_id || '—')], ['Created', fmtDateTime(d.created_at)], ['Started', fmtDateTime(d.started_at)], ['Completed', fmtDateTime(d.completed_at)]];
      if (d.error) rows.push(['Error', h('span', { class: 'err-text' }, d.error)]);
      infoDialog({ title: `Task ${t.id.slice(0, 16)}`, body: h('div', { class: 'stack' },
        h('dl', { class: 'kv' }, rows.map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]).flat()),
        d.parameters ? h('div', { class: 'stack' }, h('div', { class: 'section-title' }, 'Parameters'), codeBlock(JSON.stringify(d.parameters, null, 2))) : null,
        d.result ? h('div', { class: 'stack' }, h('div', { class: 'section-title' }, 'Result'), codeBlock(JSON.stringify(d.result, null, 2))) : null) });
    } catch (e) { ctx.toast(e.message, 'error'); }
  }

  function render(tasks) {
    clear(content);
    if (!tasks.length) { content.append(h('div', { class: 'empty-inline' }, 'No tasks found.')); return; }
    content.append(h('div', { class: 'table-wrap table-scroll' }, h('table', { class: 'data', id: 'tasks-table' },
      h('thead', {}, h('tr', {}, ['ID', 'Capability', 'User', 'Status', 'Device', 'Created', 'Actions'].map((c) => h('th', { scope: 'col' }, c)))),
      h('tbody', {}, tasks.map((t) => h('tr', {},
        h('td', {}, h('code', {}, t.id.slice(0, 12))), h('td', {}, t.capability || '—'), h('td', {}, h('code', {}, t.user_id ? t.user_id.slice(0, 8) : '—')),
        h('td', {}, statusChip(t.status || 'unknown')), h('td', {}, h('code', {}, t.device_id || '—')), h('td', { title: t.created_at || '' }, fmtDateTime(t.created_at)),
        h('td', {}, h('div', { class: 'row-actions' }, btn('View', { size: 'sm', onclick: () => detail(t) }),
          t.status === 'running' ? btn('Pause', { size: 'sm', variant: 'warn', ic: 'pause', onclick: () => act('pause', t) }) : null,
          t.status === 'paused' ? btn('Resume', { size: 'sm', variant: 'ok', ic: 'play', onclick: () => act('resume', t) }) : null,
          ['pending', 'running', 'paused'].includes(t.status) ? btn('Cancel', { size: 'sm', variant: 'danger', ic: 'stop', onclick: () => act('cancel', t) }) : null))))))));
  }

  load();
  const off = ctx.adminData.subscribe((s) => { if (!s.loading && s.at) load(); });   // follows the page's poll interval, like the legacy view
  return { unmount() { alive = false; off(); } };
}

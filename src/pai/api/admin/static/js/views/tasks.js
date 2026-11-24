import { h, clear, debounce } from '../dom.js';
import { icon } from '../icons.js';
import { get, post, ApiError } from '../api.js';
import { statusChip, btn, busy, toast, confirmDialog, skeletonRows, emptyState, errorState, pageHead } from '../ui.js';
import { fmtDuration, fmtAgo, shortId, truncate } from '../format.js';
import { affectsLists } from '../state.js';

export async function mount(container, ctx) {
  let alive = true; let items = []; let total = 0; let loading = true; let error = null; let tick = null;
  const filter = { user: '', capability: '' };
  const tbody = h('div', { class: 'table-wrap' });
  const count = h('span', { class: 'sub' });
  const userIn = h('input', { class: 'input', placeholder: 'Filter by user id', 'aria-label': 'Filter by user id', oninput: debounce((e) => { filter.user = e.target.value.trim(); load(); }, 300) });
  const capIn = h('input', { class: 'input', placeholder: 'Filter by capability', 'aria-label': 'Filter by capability', oninput: debounce((e) => { filter.capability = e.target.value.trim(); load(); }, 300) });
  const refresh = btn('Refresh', { ic: 'refresh', onclick: () => busy(refresh, () => load()) });

  const card = h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', {}, 'Not finished yet'), count, h('div', { class: 'right' }, h('span', { class: 'conn' }, h('span', { class: 'dot live' }), 'live'))),
    h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), userIn), h('div', { class: 'search' }, icon('search'), capIn)), tbody);
  container.append(h('div', { class: 'page' }, pageHead('Active Tasks', 'Every task that is queued, running, paused or retrying — across all users.', refresh), card));

  async function act(task, action, button) {
    if (action === 'kill') {
      const ok = await confirmDialog({ title: 'Kill this task?', danger: true, confirmLabel: 'Kill task',
        message: `“${task.name}” (${shortId(task.id)}) will be cancelled${task.device_id && task.device_id !== 'self' ? ' and its device told to stop' : ''}. If it belongs to a DAG, tasks that depend on it will be skipped.` });
      if (!ok) return;
    }
    await busy(button, async () => {
      try { await post(`/admin/tasks/${task.id}/${action}`); toast(`${task.name}: ${action} done`, 'success'); await load(); }
      catch (e) { toast(e.message, 'error', { title: `Could not ${action} task` }); }
    });
  }

  function row(t) {
    const live = t.status === 'running';
    const kill = btn('Kill', { variant: 'danger', size: 'sm', ic: 'stop', onclick: () => act(t, 'kill', kill), ariaLabel: `Kill task ${t.name}` });
    const pauseBtn = t.status === 'paused'
      ? btn('Resume', { size: 'sm', ic: 'play', onclick: () => act(t, 'resume', pauseBtn), ariaLabel: `Resume task ${t.name}` })
      : btn('Pause', { size: 'sm', ic: 'pause', disabled: !live, onclick: () => act(t, 'pause', pauseBtn), ariaLabel: `Pause task ${t.name}` });
    return h('tr', {},
      h('td', {}, h('div', { class: 'cell-main' }, h('span', { class: 't', title: t.name }, t.name), h('span', { class: 's' }, t.capability || '—'))),
      h('td', {}, statusChip(t.status)),
      h('td', {}, h('span', { class: 'id', title: t.user_id || '' }, shortId(t.user_id))),
      h('td', {}, t.request_id ? h('a', { href: `#/dags/${t.request_id}`, class: 'id', title: 'Open DAG' }, `${shortId(t.request_id, 12)}${t.spec_id ? ' › ' + t.spec_id : ''}`) : h('span', { class: 'faint' }, '—')),
      h('td', { class: 'num', 'data-started': t.started_at || '' }, t.started_at ? (t.completed_at ? fmtDuration(t.duration_ms) : fmtDuration(Date.now() - new Date(t.started_at).getTime())) : h('span', { class: 'faint' }, fmtAgo(t.created_at))),
      h('td', { class: 'num' }, t.retry_count ? `${t.retry_count}/${t.max_retries}` : '—'),
      h('td', {}, h('div', { class: 'actions' }, pauseBtn, kill)));
  }

  function render() {
    clear(tbody);
    count.textContent = loading && !items.length ? '' : `${total} task${total === 1 ? '' : 's'}`;
    if (loading && !items.length) { tbody.append(skeletonRows(6, 6)); return; }
    if (error && !items.length) { tbody.append(errorState(error, () => load())); return; }
    if (!items.length) { tbody.append(emptyState('Nothing is running', 'No active tasks right now. Start something from the Playground and it will show up here instantly.', 'tasks',
      btn('Open Playground', { variant: 'primary', ic: 'flask', onclick: () => ctx.navigate('playground') }))); return; }
    tbody.append(h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, ['Task', 'Status', 'User', 'DAG run', 'Elapsed', 'Retries', ''].map((c, i) => h('th', { class: i >= 4 && i < 6 ? 'num' : '' }, c)))),
      h('tbody', {}, items.map(row))));
  }

  async function load() {
    try {
      const page = await get('/admin/tasks/active', { params: { limit: 200, user_id: filter.user, capability: filter.capability } });
      if (!alive) return;
      items = page.items; total = page.total; error = null;
    } catch (e) { if (!alive) return; error = e; if (items.length) toast(e.message, 'error', { title: 'Refresh failed' }); }
    loading = false; render();
  }

  const reload = debounce(load, 250);
  const onEvent = (e) => { if (affectsLists(e.detail.event)) reload(); };
  ctx.bus.addEventListener('event', onEvent);
  ctx.bus.addEventListener('reconnected', reload);
  render(); load();
  const poll = setInterval(load, 5000);
  tick = setInterval(() => { // keep elapsed timers moving without refetching
    tbody.querySelectorAll('td[data-started]').forEach((td) => { if (td.dataset.started) td.textContent = fmtDuration(Date.now() - new Date(td.dataset.started).getTime()); });
  }, 1000);
  return { unmount() { alive = false; clearInterval(poll); clearInterval(tick); reload.cancel(); ctx.bus.removeEventListener('event', onEvent); ctx.bus.removeEventListener('reconnected', reload); } };
}

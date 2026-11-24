// Overview — the legacy page's first tab: summary cards from /system/admin/data, plus shortcuts.
import { h, clear } from '../dom.js';
import { icon } from '../icons.js';
import { btn, errorState, pageHead } from '../ui.js';
import { humanDuration, fmtNumber } from '../format.js';

function statCard(label, ic, value, foot) {
  return h('div', { class: 'card stat' },
    h('div', { class: 'stat-label' }, icon(ic), label),
    h('div', { class: 'stat-value' }, value ?? '—'),
    h('div', { class: 'stat-foot' }, h('span', {}, foot)));
}

export async function mount(container, ctx) {
  const body = h('div', { class: 'stack' });
  const refresh = btn('Refresh', { ic: 'refresh', onclick: () => ctx.adminData.refresh() });
  container.append(h('div', { class: 'page' }, pageHead('Overview', 'Devices, plugins, users and uptime across the whole installation.', refresh), body));

  function render({ data, error }) {
    clear(body);
    if (!data) { body.append(error ? h('div', { class: 'card' }, errorState(error, () => ctx.adminData.refresh())) : h('div', { class: 'empty-inline', role: 'status' }, 'Loading…')); return; }
    const s = data.summary || {};
    body.append(
      h('div', { class: 'grid stats', id: 'cards' },
        statCard('Devices online', 'devices', fmtNumber(s.devices_online), `of ${s.devices_total ?? '—'} registered`),
        statCard('Plugins catalog', 'puzzle', fmtNumber(s.plugins_catalog), 'available to install'),
        statCard('Plugin installs', 'bolt', fmtNumber(s.plugin_installations), 'across all devices'),
        statCard('Users', 'users', fmtNumber(s.users_total), 'in the database'),
        statCard('Uptime', 'clock', humanDuration(s.uptime_seconds), 'since boot')),
      h('div', { class: 'grid two' },
        h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Jump to')),
          h('div', { class: 'card-body stack' },
            h('p', { class: 'faint' }, 'Run something, then watch it execute as a live graph.'),
            h('div', { class: 'row' },
              btn('Playground', { variant: 'primary', ic: 'flask', onclick: () => ctx.navigate('playground') }),
              btn('Live DAGs', { ic: 'dags', onclick: () => ctx.navigate('dags') }),
              btn('Active tasks', { ic: 'tasks', onclick: () => ctx.navigate('active') }),
              btn('Traces', { ic: 'activity', onclick: () => ctx.navigate('traces') })))),
        h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Summary')),
          h('div', { class: 'card-body' }, h('dl', { class: 'kv' },
            Object.entries(s).map(([k, v]) => [h('dt', {}, k.replace(/_/g, ' ')), h('dd', { class: 'mono' }, typeof v === 'object' ? JSON.stringify(v) : String(v))]).flat())))));
  }
  render(ctx.adminData.get());
  const off = ctx.adminData.subscribe(render);
  return { unmount() { off(); } };
}

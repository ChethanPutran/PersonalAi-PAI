// Devices — list from /system/admin/data; Disconnect (live devices) and Delete, exactly as the legacy page.
import { h, clear } from '../dom.js';
import { btn, statusChip, confirmDialog, errorState, pageHead } from '../ui.js';
import { api } from '../api.js';
import { fmtDateTime } from '../format.js';

export async function mount(container, ctx) {
  const body = h('div', { class: 'card' });
  container.append(h('div', { class: 'page' }, pageHead('Devices', 'Phones, desktops and servers registered with this backend.', btn('Refresh', { ic: 'refresh', onclick: () => ctx.adminData.refresh() })), body));

  async function act(fn, ok) {
    try { await fn(); ctx.toast(ok, 'success'); ctx.adminData.refresh(); } catch (e) { ctx.toast(e.message, 'error'); }
  }
  async function del(d) {
    if (await confirmDialog({ title: 'Delete device?', message: `Delete device "${d.name}"?`, confirmLabel: 'Delete', danger: true }))
      act(() => api('DELETE', `/system/devices/${d.id}`), 'Device deleted');
  }
  function render({ data, error }) {
    clear(body);
    if (!data) { body.append(error ? errorState(error, () => ctx.adminData.refresh()) : h('div', { class: 'empty-inline', role: 'status' }, 'Loading…')); return; }
    const devices = data.devices || [];
    if (!devices.length) { body.append(h('div', { class: 'empty-inline' }, 'No devices registered yet.')); return; }
    body.append(h('div', { class: 'table-wrap table-scroll' }, h('table', { class: 'data', id: 'devices-table' },
      h('thead', {}, h('tr', {}, ['Name', 'ID', 'Platform', 'Status', 'Connected', 'Last seen', 'Actions'].map((c) => h('th', { scope: 'col' }, c)))),
      h('tbody', {}, devices.map((d) => h('tr', {},
        h('td', {}, h('strong', {}, d.name), d.hostname ? h('span', { class: 'sub-line' }, d.hostname) : null),
        h('td', {}, h('code', {}, d.id)),
        h('td', {}, `${d.platform || ''}/${d.architecture || ''}`),
        h('td', {}, statusChip(d.status === 'online' ? 'online' : (d.status || 'offline'))),
        h('td', {}, d.connected ? statusChip('online') : h('span', { class: 'faint' }, '—')),
        h('td', { title: d.last_seen || '' }, fmtDateTime(d.last_seen)),
        h('td', {}, h('div', { class: 'row-actions' },
          d.connected ? btn('Disconnect', { size: 'sm', variant: 'warn', ic: 'power', onclick: () => act(() => api('POST', `/system/devices/${d.id}/disconnect`), 'Device disconnected') }) : null,
          btn('Delete', { size: 'sm', variant: 'danger', ic: 'trash', onclick: () => del(d) })))))))));
  }
  render(ctx.adminData.get());
  const off = ctx.adminData.subscribe(render);
  return { unmount() { off(); } };
}

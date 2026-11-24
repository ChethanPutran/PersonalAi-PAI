// Plugins — catalog with installs and per-device state, as the legacy page.
import { h, clear } from '../dom.js';
import { btn, statusChip, errorState, pageHead } from '../ui.js';

export async function mount(container, ctx) {
  const body = h('div', { class: 'card' });
  container.append(h('div', { class: 'page' }, pageHead('Plugins', 'Catalog, installations and where each plugin is enabled.', btn('Refresh', { ic: 'refresh', onclick: () => ctx.adminData.refresh() })), body));
  const open = new Set();                                  // keep <details> open across polls

  function render({ data, error }) {
    clear(body);
    if (!data) { body.append(error ? errorState(error, () => ctx.adminData.refresh()) : h('div', { class: 'empty-inline', role: 'status' }, 'Loading…')); return; }
    const plugins = data.plugins || [];
    if (!plugins.length) { body.append(h('div', { class: 'empty-inline' }, 'No plugins in catalog.')); return; }
    body.append(h('div', { class: 'table-wrap table-scroll' }, h('table', { class: 'data', id: 'plugins-table' },
      h('thead', {}, h('tr', {}, ['Plugin', 'Version', 'Platforms', 'Installs', 'Enabled', 'Details'].map((c) => h('th', { scope: 'col' }, c)))),
      h('tbody', {}, plugins.map((p) => {
        const det = h('details', { class: 'disc', open: open.has(p.id) },
          h('summary', {}, `${(p.devices || []).length} device(s)`),
          h('ul', {}, (p.devices || []).map((d) => h('li', {}, h('code', {}, d.device_id), ` @ ${d.version ?? ''}`,
            d.is_installed ? statusChip('completed', 'installed') : statusChip('offline', 'not installed'),
            d.enabled_on_device ? statusChip('enabled') : null, d.last_error ? statusChip(d.last_error, 'error') : null))));
        det.addEventListener('toggle', () => (det.open ? open.add(p.id) : open.delete(p.id)));
        return h('tr', {}, h('td', {}, h('strong', {}, p.name), h('span', { class: 'sub-line' }, h('code', {}, p.id))),
          h('td', {}, p.version || ''), h('td', {}, (p.platforms || []).join(', ')), h('td', { class: 'num' }, String(p.installations ?? 0)),
          h('td', { class: 'num' }, String(p.enabled_on_devices ?? 0)), h('td', {}, det));
      })))));
  }
  render(ctx.adminData.get());
  const off = ctx.adminData.subscribe(render);
  return { unmount() { off(); } };
}

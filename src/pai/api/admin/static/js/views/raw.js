// Raw JSON — the aggregate payload exactly as the API returned it.
import { h } from '../dom.js';
import { btn, pageHead, copyTextButton } from '../ui.js';

export async function mount(container, ctx) {
  const pre = h('pre', { class: 'raw mono', id: 'raw-pre', tabindex: '0', 'aria-label': 'Raw admin data JSON' });
  const text = () => (ctx.adminData.get().data ? JSON.stringify(ctx.adminData.get().data, null, 2) : '');
  container.append(h('div', { class: 'page' },
    pageHead('Raw JSON', 'GET /api/v1/system/admin/data — the single payload behind Overview, Devices, Plugins and Users.',
      copyTextButton(text), btn('Refresh', { ic: 'refresh', onclick: () => ctx.adminData.refresh() })),
    h('div', { class: 'card' }, pre)));
  const render = () => { pre.textContent = text() || 'Loading…'; };
  render();
  const off = ctx.adminData.subscribe(render);
  return { unmount() { off(); } };
}

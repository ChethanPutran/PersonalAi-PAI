// Users — list + create / edit / delete modals + per-user plugin toggles (same endpoints as the legacy page).
import { h, clear } from '../dom.js';
import { btn, statusChip, confirmDialog, formDialog, errorState, pageHead } from '../ui.js';
import { api } from '../api.js';
import { fmtDateTime } from '../format.js';

export async function mount(container, ctx) {
  const body = h('div', { class: 'card' });
  const create = btn('Create user', { variant: 'primary', ic: 'plus', onclick: () => openCreate() });
  create.id = 'btn-create-user';
  container.append(h('div', { class: 'page' }, pageHead('Users', 'Accounts, admin access and which plugins each user has enabled.', create, btn('Refresh', { ic: 'refresh', onclick: () => ctx.adminData.refresh() })), body));
  const open = new Set();

  const done = (msg) => { ctx.toast(msg, 'success'); ctx.adminData.refresh(); };

  function openCreate() {
    formDialog({ title: 'Create user', submitLabel: 'Create', fields: [
      { name: 'email', label: 'Email', type: 'email', autocomplete: 'off' },
      { name: 'password', label: 'Password (min 8 chars)', type: 'password', autocomplete: 'new-password' },
      { name: 'is_admin', label: 'Grant admin access', type: 'checkbox' }],
    onSubmit: async (v) => { await api('POST', '/system/users', { body: { email: v.email.trim(), password: v.password, is_admin: v.is_admin } }); done('User created'); } });
  }
  function openEdit(u) {
    formDialog({ title: 'Edit user', submitLabel: 'Save', fields: [
      { name: 'email', label: 'Email', type: 'email', value: u.email || '' },
      { name: 'password', label: 'New password (leave blank to keep)', type: 'password', autocomplete: 'new-password' },
      { name: 'is_admin', label: 'Admin', type: 'checkbox', value: u.is_admin }],
    onSubmit: async (v) => {
      const patch = { email: v.email.trim(), is_admin: v.is_admin };
      if (v.password) patch.password = v.password;
      await api('PATCH', `/system/users/${u.id}`, { body: patch }); done('User updated');
    } });
  }
  async function del(u) {
    if (!(await confirmDialog({ title: 'Delete user?', message: `Delete user "${u.email || u.id}"? This cannot be undone.`, confirmLabel: 'Delete', danger: true }))) return;
    try { await api('DELETE', `/system/users/${u.id}`); done('User deleted'); } catch (e) { ctx.toast(e.message, 'error'); }
  }
  async function toggle(u, p) {
    try { await api('POST', `/system/users/${u.id}/plugins/${p.plugin_id}/toggle`, { body: { enabled: !p.is_enabled } }); done(`Plugin ${!p.is_enabled ? 'enabled' : 'disabled'}`); }
    catch (e) { ctx.toast(e.message, 'error'); }
  }

  function render({ data, error }) {
    clear(body);
    if (!data) { body.append(error ? errorState(error, () => ctx.adminData.refresh()) : h('div', { class: 'empty-inline', role: 'status' }, 'Loading…')); return; }
    const users = data.users || [];
    if (!users.length) { body.append(h('div', { class: 'empty-inline' }, 'No users yet.')); return; }
    body.append(h('div', { class: 'table-wrap table-scroll' }, h('table', { class: 'data', id: 'users-table' },
      h('thead', {}, h('tr', {}, ['User', 'Created', 'Enabled', 'Total plugins', 'Details', 'Actions'].map((c) => h('th', { scope: 'col' }, c)))),
      h('tbody', {}, users.map((u) => {
        const det = h('details', { class: 'disc', open: open.has(u.id) }, h('summary', {}, 'view'),
          h('ul', {}, (u.plugins || []).map((p) => h('li', {}, h('code', {}, p.plugin_id), p.is_enabled ? statusChip('enabled') : statusChip('disabled'),
            btn(p.is_enabled ? 'Disable' : 'Enable', { size: 'sm', onclick: () => toggle(u, p) })))));
        det.addEventListener('toggle', () => (det.open ? open.add(u.id) : open.delete(u.id)));
        return h('tr', {},
          h('td', {}, h('strong', {}, u.email || u.id), ' ', u.is_admin ? statusChip('admin') : null, h('span', { class: 'sub-line' }, h('code', {}, u.id))),
          h('td', { title: u.created_at || '' }, fmtDateTime(u.created_at)), h('td', { class: 'num' }, String(u.plugins_enabled ?? 0)), h('td', { class: 'num' }, String(u.plugins_total ?? 0)),
          h('td', {}, det),
          h('td', {}, h('div', { class: 'row-actions' }, btn('Edit', { size: 'sm', ic: 'edit', onclick: () => openEdit(u) }),
            btn('Delete', { size: 'sm', variant: 'danger', ic: 'trash', onclick: () => del(u) }))));
      })))));
  }
  render(ctx.adminData.get());
  const off = ctx.adminData.subscribe(render);
  return { unmount() { off(); } };
}

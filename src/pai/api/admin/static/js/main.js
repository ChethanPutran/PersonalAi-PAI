// App shell: login gate (same token key as the legacy page), sidebar + top bar, hash router, shared live connections.
import { h, s as svg, clear } from './dom.js';
import { icon } from './icons.js';
import { session, login, whoAmI, get, openAdminEvents, ApiError, AUTH_EVENT } from './api.js';
import { toast, btn, busy } from './ui.js';
import { initials } from './format.js';
import { createStore } from './state.js';
import { createAdminData, fmtInterval } from './admindata.js';

export const ROUTES = [
  { group: 'Observe' },
  { id: 'overview', label: 'Overview', ic: 'overview', title: 'Overview', mod: 'admin_overview' },
  { id: 'dags', label: 'Live DAGs', ic: 'dags', title: 'Live DAGs', mod: 'dags', badge: 'activeDags', hot: true },
  { id: 'active', label: 'Active Tasks', ic: 'tasks', title: 'Active Tasks', mod: 'tasks', badge: 'activeTasks', hot: true },
  { id: 'tasks', label: 'Tasks', ic: 'clock', title: 'Tasks', mod: 'alltasks', badge: 'tasksTotal' },
  { id: 'traces', label: 'Traces', ic: 'activity', title: 'Traces', mod: 'traces', badge: 'tracesTotal' },
  { id: 'usage', label: 'Usage & Cost', ic: 'coins', title: 'LLM Usage & Cost', mod: 'usage' },
  { id: 'events', label: 'Events', ic: 'bolt', title: 'Live Events', mod: 'events' },
  { group: 'Manage' },
  { id: 'devices', label: 'Devices', ic: 'devices', title: 'Devices', mod: 'devices', badge: 'devicesTotal' },
  { id: 'plugins', label: 'Plugins', ic: 'puzzle', title: 'Plugins', mod: 'plugins', badge: 'pluginsTotal' },
  { id: 'users', label: 'Users', ic: 'users', title: 'Users', mod: 'users', badge: 'usersTotal' },
  { id: 'settings', label: 'Settings', ic: 'sliders', title: 'Settings', mod: 'settings' },
  { group: 'Develop' },
  { id: 'playground', label: 'Playground', ic: 'flask', title: 'Playground', mod: 'playground' },
  { id: 'resources', label: 'Resources', ic: 'cpu', title: 'Resources', mod: 'resources' },
  { id: 'raw', label: 'Raw JSON', ic: 'code', title: 'Raw JSON', mod: 'raw' },
];
const PAGES = ROUTES.filter((r) => r.id);

const root = document.getElementById('root');
const store = createStore({ conn: 'connecting', activeTasks: 0, activeDags: 0, tasksTotal: null, tracesTotal: null, devicesTotal: null, pluginsTotal: null, usersTotal: null, env: '' });
const bus = new EventTarget();                       // 'event' CustomEvents carry {event, data}
const adminData = createAdminData();
let stream = null; let current = null; let pollTimer = null; let started = false;

// ------------------------------------------------------------------ brand mark (arc-reactor style)
export function reactor() {
  const ring = (r, dash, cls, w = 1.4) => svg('circle', { cx: 32, cy: 32, r, fill: 'none', stroke: 'currentColor', 'stroke-width': w, 'stroke-dasharray': dash, class: cls, opacity: 0.8 });
  const ticks = svg('g', { stroke: 'currentColor', 'stroke-width': 1.2, opacity: 0.45 });
  for (let i = 0; i < 36; i++) {
    const a = (i / 36) * Math.PI * 2; const long = i % 3 === 0; const r1 = 28.5; const r2 = long ? 31.5 : 30;
    ticks.append(svg('line', { x1: 32 + Math.cos(a) * r1, y1: 32 + Math.sin(a) * r1, x2: 32 + Math.cos(a) * r2, y2: 32 + Math.sin(a) * r2 }));
  }
  const hex = svg('path', { d: 'M32 20l10.4 6v12L32 44l-10.4-6V26z', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.4, opacity: 0.55 });
  return svg('svg', { class: 'reactor', viewBox: '0 0 64 64', role: 'img', 'aria-label': 'PAI' }, ticks, ring(24, '30 12 18 12', 'ring-a', 2), ring(18, '4 6', 'ring-b', 1.2), hex,
    svg('circle', { cx: 32, cy: 32, r: 5.5, fill: 'currentColor', class: 'core' }));
}

// ------------------------------------------------------------------ login (ids match the legacy page)
function renderLogin(message = '') {
  stopLive(); clear(root);
  const email = h('input', { class: 'input', id: 'login-email', type: 'email', autocomplete: 'username', required: true, placeholder: 'admin@example.com' });
  const pass = h('input', { class: 'input', id: 'login-password', type: 'password', autocomplete: 'current-password', required: true });
  const err = h('div', { class: 'callout danger hidden', id: 'login-error', role: 'alert' });
  const submit = btn('Sign in', { variant: 'primary' }); submit.type = 'submit'; submit.id = 'login-btn';
  const form = h('form', { class: 'stack', onsubmit: async (e) => {
    e.preventDefault(); err.classList.add('hidden');
    await busy(submit, async () => {
      try { await login(email.value.trim(), pass.value); pass.value = ''; start(); }
      catch (ex) { err.textContent = ex instanceof ApiError ? ex.message : 'Sign-in failed'; err.classList.remove('hidden'); }
    });
  } },
  h('div', { class: 'field' }, h('label', { for: 'login-email' }, 'Email'), email),
  h('div', { class: 'field' }, h('label', { for: 'login-password' }, 'Password'), pass), err, submit);
  root.append(h('main', { class: 'login', id: 'login-overlay' }, h('div', { class: 'card login-card' }, reactor(),
    h('div', {}, h('h1', {}, 'PAI Admin'), h('p', { class: 'sub' }, message || 'Sign in with an administrator account.')), form)));
  document.title = 'Sign in · PAI Admin';
  email.focus();
}

// ------------------------------------------------------------------ shell
const DOT = { live: ['live', 'Live'], connecting: ['warn', 'Connecting…'], retrying: ['warn', 'Reconnecting…'], closed: ['bad', 'Offline'] };
const INTERVALS = [0, 1000, 2000, 5000, 10000, 30000, 60000];

function renderShell() {
  clear(root);
  const nav = h('nav', { class: 'nav', 'aria-label': 'Primary' }, ROUTES.map((r) => r.group
    ? h('div', { class: 'nav-label' }, r.group)
    : h('a', { class: 'nav-item', href: `#/${r.id}`, 'data-view': r.id, 'data-route': r.id, title: r.label },
      icon(r.ic), h('span', { class: 'label' }, r.label), r.badge ? h('span', { class: `nav-badge${r.hot ? ' hot' : ''}`, 'data-badge': r.badge, id: `count-${r.id}` }) : null)));
  const sidebar = h('aside', { class: 'sidebar' },
    h('div', { class: 'brand' }, reactor(), h('div', { class: 'brand-text' }, h('span', { class: 'brand-name' }, 'PAI'), h('span', { class: 'brand-sub' }, 'Admin console'))),
    nav, h('div', { class: 'sidebar-foot' }, 'Personal AI runtime'));

  const dot = h('span', { class: 'dot', id: 'live-dot' }); const connText = h('span', { class: 'txt', id: 'live-text' }, 'Connecting…');
  const interval = h('select', { class: 'select', id: 'refresh-interval', 'aria-label': 'Auto-refresh interval', title: 'Auto-refresh interval for admin data',
    onchange: (e) => { const ms = parseInt(e.target.value, 10) || 0; adminData.setInterval(ms); toast(ms ? `Polling every ${fmtInterval(ms)}` : 'Polling disabled — use refresh', 'success'); } },
  INTERVALS.map((ms) => h('option', { value: String(ms) }, ms ? fmtInterval(ms) : 'off')));
  interval.value = String(adminData.interval());
  const refreshNow = btn('', { ic: 'refresh', title: 'Refresh now', size: 'sm', onclick: () => adminData.refresh() }); refreshNow.id = 'btn-refresh-now'; refreshNow.setAttribute('aria-label', 'Refresh now');
  const email = session.user?.email || 'admin';
  const menuBtn = h('button', { class: 'user-btn', 'aria-haspopup': 'menu', 'aria-expanded': 'false', id: 'user-btn' }, h('span', { class: 'avatar' }, initials(email)), h('span', { id: 'admin-email' }, email));
  const menu = h('div', { class: 'menu hidden', role: 'menu' }, h('div', { class: 'menu-head' }, email),
    h('a', { class: 'menu-item', role: 'menuitem', href: '/api/v1/system/admin/legacy' }, icon('code'), 'Legacy admin page'),
    h('button', { class: 'menu-item', role: 'menuitem', id: 'logout-btn', onclick: signOut }, icon('logout'), 'Sign out'));
  const closeMenu = () => { menu.classList.add('hidden'); menuBtn.setAttribute('aria-expanded', 'false'); };
  menuBtn.addEventListener('click', () => { const open = menu.classList.toggle('hidden') === false; menuBtn.setAttribute('aria-expanded', String(open)); if (open) menu.querySelector('a,button')?.focus(); });
  menu.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeMenu(); menuBtn.focus(); } });
  document.addEventListener('click', (e) => { if (!menuBtn.contains(e.target) && !menu.contains(e.target)) closeMenu(); });

  const topbar = h('header', { class: 'topbar' }, h('h1', { class: 'topbar-title', id: 'page-title' }), h('div', { class: 'topbar-spacer' }),
    h('span', { class: 'env-pill', id: 'env-pill' }, '—'),
    h('label', { class: 'poll', for: 'refresh-interval' }, h('span', { class: 'lbl' }, 'Poll'), interval, refreshNow),
    h('span', { class: 'conn', role: 'status' }, dot, connText), h('div', { class: 'user-menu' }, menuBtn, menu));
  root.append(h('div', { class: 'app', id: 'app' }, sidebar, topbar, h('main', { class: 'main', id: 'view', tabindex: '-1' })));
}

function paintStatus(st) {
  const [kind, text] = DOT[st.conn] || DOT.closed;
  const dot = document.getElementById('live-dot'); const label = document.getElementById('live-text');
  if (dot) dot.className = `dot ${kind}`;
  if (label) label.textContent = text;
  document.querySelectorAll('[data-badge]').forEach((b) => {
    const v = st[b.dataset.badge]; const hot = b.classList.contains('hot');
    b.textContent = v == null || (hot && v === 0) ? '' : String(v);
  });
  const env = document.getElementById('env-pill'); if (env && st.env) env.textContent = st.env;
}

// ------------------------------------------------------------------ router
export function navigate(path) { location.hash = `#/${path}`; }

async function route() {
  const [id, ...rest] = (location.hash.replace(/^#\/?/, '') || 'overview').split('/');
  const def = PAGES.find((r) => r.id === id) || PAGES[0];
  document.querySelectorAll('.nav-item').forEach((a) => (a.dataset.route === def.id ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  const title = document.getElementById('page-title'); if (title) title.textContent = def.title;
  document.title = `${def.title} · PAI Admin`;
  const container = document.getElementById('view'); if (!container) return;
  current?.unmount?.(); current = null; clear(container);
  const ticket = Symbol(); route.ticket = ticket;
  try {
    const mod = await import(`./views/${def.mod}.js`);
    if (route.ticket !== ticket) return;
    const view = await mod.mount(container, { bus, store, adminData, navigate, replace: (p) => history.replaceState(null, '', `#/${p}`), param: rest[0] || null, toast });
    if (route.ticket !== ticket) { view?.unmount?.(); return; }
    current = view;
    container.focus({ preventScroll: true });
  } catch (e) {
    console.error('[admin] view failed to load', def.id, e);
    container.append(h('div', { class: 'state error', role: 'alert' }, icon('alert'), h('h3', {}, 'This view failed to load'), h('p', {}, String(e.message || e))));
  }
}

// ------------------------------------------------------------------ live data
function startLive() {
  stream = openAdminEvents((evt) => bus.dispatchEvent(new CustomEvent('event', { detail: evt })),
    (st) => { store.set({ conn: st }); if (st === 'live') bus.dispatchEvent(new CustomEvent('reconnected')); });
  adminData.start();
  const poll = async () => {
    try {
      const o = await get('/admin/overview');
      store.set({ activeTasks: o.active_tasks, activeDags: o.active_dags, env: o.test_capabilities_enabled ? 'dev · test caps' : 'production' });
    } catch (e) { if (e.status !== 401 && e.status !== 403) console.error('[admin] overview poll failed', e); }
  };
  poll(); pollTimer = setInterval(poll, 4000);
}
function stopLive() {
  stream?.close(); stream = null; clearInterval(pollTimer); pollTimer = null; adminData.reset();
  current?.unmount?.(); current = null; started = false;
}

function signOut() { session.clear(); renderLogin('Signed out.'); }

async function start() {
  if (started) return;
  started = true;
  renderShell();
  paintStatus(store.get());
  startLive();
  window.removeEventListener('hashchange', route); window.addEventListener('hashchange', route);
  route();
}

store.subscribe(paintStatus);
adminData.subscribe(({ data }) => {
  const sm = data?.summary; if (sm) store.set({ devicesTotal: sm.devices_total ?? null, pluginsTotal: sm.plugins_catalog ?? null, usersTotal: sm.users_total ?? null });
});

window.addEventListener(AUTH_EVENT, () => { if (started || session.token) { session.clear(); toast('Your session expired — please sign in again.', 'error'); renderLogin('Session expired.'); } });
window.addEventListener('unhandledrejection', (e) => console.error('[admin] unhandled rejection', e.reason));

(async function boot() {
  if (!session.load()) { renderLogin(); return; }
  try { await whoAmI(); start(); } catch { session.clear(); renderLogin(); }
})();

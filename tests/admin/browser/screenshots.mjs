// Seeds realistic demo data through the API, then screenshots EVERY tab of the admin console
// (GET /api/v1/system/admin) in headless Chrome and prints PASS/FAIL per check (exit 1 on any FAIL).
//   node screenshots.mjs http://127.0.0.1:8765            (writes to ./screenshots/, committed like Flutter goldens)
//   node screenshots.mjs http://127.0.0.1:8765 /some/dir
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchChrome } from './cdp.mjs';

const BASE = process.argv[2]; const OUT = process.argv[3] || join(dirname(fileURLToPath(import.meta.url)), 'screenshots');
mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function http(method, path, token, body) {
  const r = await fetch(BASE + path, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch { /* */ }
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status} ${t}`);
  return j;
}
async function waitDag(token, rid, pred, timeout = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) { const s = await http('GET', `/api/v1/admin/dags/${rid}`, token); if (pred(s)) return s; await sleep(150); }
  throw new Error('dag wait timeout');
}
const node = (id, cap, params, deps = [], type) => ({ id, capability: cap, parameters: params, depends_on: deps, ...(type ? { type } : {}) });
const sl = (id, s, deps, type) => node(id, 'admin.test.sleep', { seconds: s }, deps, type);

// ---- seed
await http('POST', '/api/v1/auth/register', null, { email: 'admin@example.com', password: 'password123' }).catch(() => {});
const login = await http('POST', '/api/v1/auth/login', null, { email: 'admin@example.com', password: 'password123' });
const token = login.access_token;
const A = '/api/v1/admin';
const fan = await http('POST', `${A}/run/demo/fanout?scale=0.1`, token);
await waitDag(token, fan.request_id, (s) => s.status !== 'running');
const fs_ = await http('POST', `${A}/run/demo/failure_skip?scale=0.1`, token);
await waitDag(token, fs_.request_id, (s) => s.status !== 'running');
const pipe = await http('POST', `${A}/run/dag`, token, { label: 'nightly pipeline', tasks: [
  sl('ingest', 1.2), sl('clean', 60, ['ingest']), sl('enrich', 1.5, ['ingest']), sl('thumbs', 60, ['ingest'], 'long_running'),
  node('risky', 'admin.test.fail', { after_seconds: 1.8, message: 'upstream API returned 503' }, ['ingest']),
  node('after_risky', 'admin.test.echo', { message: 'x' }, ['risky']), sl('audit', 1.0),
  node('join', 'admin.test.echo', { message: 'joined' }, ['clean', 'enrich']), sl('publish', 4, ['join']), node('notify', 'admin.test.echo', { message: 'done' }, ['publish']) ] });
await waitDag(token, pipe.request_id, (s) => { const n = Object.fromEntries(s.nodes.map((x) => [x.spec_id, x.status])); return n.enrich === 'completed' && n.risky === 'failed' && n.audit === 'completed' && n.clean === 'running'; });
await http('POST', `${A}/dags/${pipe.request_id}/tasks/thumbs/kill`, token);
const live2 = await http('POST', `${A}/run/demo/long_running`, token);
const live3 = await http('POST', `${A}/run/demo/chain`, token);
await sleep(600);

// ---- browser
const PAGE = `${BASE}/api/v1/system/admin`;
const chrome = await launchChrome({ width: 1440, height: 900 });
const shots = [];
const snap = async (name) => { const p = `${OUT}/${name}.png`; await chrome.screenshot(p); shots.push(p); console.log('shot', p); };
const q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const tab = async (name) => { await chrome.eval(`${q(`.nav-item[data-view="${name}"]`)}.click()`); await chrome.sleep(250); };
const checks = [];
const check = async (label, expr, timeout = 12000) => { try { await chrome.waitFor(expr, timeout); checks.push([label, true]); } catch { checks.push([label, false]); } };
try {
  // the previous single-file page must still work as a safety net: sign in through ITS form and load its data
  await chrome.goto(`${PAGE}/legacy`); await chrome.eval(`localStorage.clear()`); await chrome.goto(`${PAGE}/legacy`);
  await check('legacy page: login overlay renders', `!!${q('#login-overlay')} && !${q('#login-overlay')}.classList.contains('hidden')`, 4000);
  await chrome.eval(`(() => { ${q('#login-email')}.value = 'admin@example.com'; ${q('#login-password')}.value = 'password123'; ${q('#login-btn')}.click(); })()`);
  await check('legacy page: signs in and renders overview cards + traces tab', `${q('#app')}.classList.contains('visible') && document.querySelectorAll('#cards .card').length >= 5`, 8000);
  await chrome.eval(`${q('.nav-item[data-view="traces"]')}.click()`);
  await check('legacy page: traces list loads', `document.querySelectorAll('#trace-list .trace-row').length >= 1`, 6000); await snap('00-legacy-traces');
  await chrome.eval(`localStorage.clear()`);
  await chrome.goto(PAGE);
  await chrome.eval(`localStorage.clear()`); await chrome.goto(PAGE);
  await check('login: form shown', `!!${q('#login-email')} && !!${q('#login-btn')}`); await chrome.sleep(500); await snap('01-login');
  // wrong password is rejected inline
  await chrome.eval(`(() => { ${q('#login-email')}.value = 'admin@example.com'; ${q('#login-password')}.value = 'nope-nope'; ${q('#login-btn')}.click(); })()`);
  await check('login: wrong password shows an error', `!${q('#login-error')}.classList.contains('hidden')`, 5000);
  await chrome.eval(`(() => { ${q('#login-password')}.value = 'password123'; ${q('#login-btn')}.click(); })()`);
  await check('login: shell shows after sign-in', `!!${q('#app')} && !!${q('.sidebar')}`);
  await check('login: token stored under pai_admin_token', `!!localStorage.getItem('pai_admin_token')`, 2000);
  const NAV = ['overview', 'dags', 'active', 'tasks', 'traces', 'events', 'devices', 'plugins', 'users', 'playground', 'resources', 'raw'];
  await check('sidebar has all 12 tabs', `${JSON.stringify(NAV)}.every((v) => !!document.querySelector('.nav-item[data-view="' + v + '"]'))`, 3000);

  // ---- Overview
  await check('overview: 5 stat cards', `document.querySelectorAll('#view .stat').length >= 5`); await chrome.sleep(1200); await snap('02-overview');
  // ---- Devices
  await tab('devices'); await check('devices: table or empty state', `!!${q('#devices-table')} || document.querySelector('#view').textContent.includes('No devices')`); await snap('03-devices');
  // ---- Plugins
  await tab('plugins'); await check('plugins: catalog table', `!!${q('#plugins-table')}`);
  await chrome.eval(`(() => { const d = document.querySelector('#plugins-table details'); if (d) d.open = true; })()`); await chrome.sleep(300); await snap('04-plugins');
  // ---- Users (list + create modal + edit modal + delete confirm)
  await tab('users'); await check('users: lists the admin', `!!${q('#users-table')} && ${q('#users-table')}.textContent.includes('admin@example.com')`);
  await chrome.eval(`${q('#btn-create-user')}.click()`); await check('users: create dialog opens', `!!${q('.dialog')}`, 3000); await chrome.sleep(300); await snap('05-users-create');
  await chrome.eval(`(() => { const d = document.querySelector('.dialog'); const i = d.querySelectorAll('input'); i[0].value = 'shot-user@example.com'; i[1].value = 'password123'; [...d.querySelectorAll('button')].find(b => b.textContent.trim() === 'Create').click(); })()`);
  await check('users: created user appears', `${q('#users-table')} && ${q('#users-table')}.textContent.includes('shot-user@example.com')`, 8000);
  await chrome.eval(`(() => { const r = [...document.querySelectorAll('#users-table tbody tr')].find(x => x.textContent.includes('shot-user@example.com')); [...r.querySelectorAll('button')].find(b => b.textContent.trim() === 'Edit').click(); })()`);
  await check('users: edit dialog opens', `!!${q('.dialog')} && ${q('.dialog h2')}.textContent === 'Edit user'`, 3000); await chrome.sleep(200); await snap('06-users-edit');
  await chrome.eval(`[...document.querySelectorAll('.dialog button')].find(b => b.textContent.trim() === 'Cancel').click()`);
  await chrome.eval(`(() => { const r = [...document.querySelectorAll('#users-table tbody tr')].find(x => x.textContent.includes('shot-user@example.com')); [...r.querySelectorAll('button')].find(b => b.textContent.trim() === 'Delete').click(); })()`);
  await check('users: delete asks for confirmation', `!!${q('.dialog')} && ${q('.dialog')}.textContent.includes('cannot be undone')`, 3000); await chrome.sleep(200); await snap('07-users-delete-confirm');
  await chrome.eval(`[...document.querySelectorAll('.dialog button')].find(b => b.textContent.trim() === 'Delete').click()`);
  await check('users: deleted user disappears', `!${q('#users-table')}.textContent.includes('shot-user@example.com')`, 8000);
  // ---- Tasks (all) : filter + detail
  await tab('tasks'); await check('tasks: table renders', `!!${q('#tasks-table')}`);
  await chrome.eval(`(() => { const s = ${q('#task-filter-status')}; s.value = 'completed'; s.dispatchEvent(new Event('change')); })()`); await chrome.sleep(900);
  await check('tasks: status filter applies', `[...document.querySelectorAll('#tasks-table tbody .chip')].every(c => c.textContent.trim().toLowerCase() === 'completed')`);
  await chrome.eval(`[...document.querySelectorAll('#tasks-table button')].find(b => b.textContent.trim() === 'View').click()`);
  await check('tasks: detail dialog opens', `!!${q('.dialog')} && ${q('.dialog h2')}.textContent.startsWith('Task')`, 4000); await chrome.sleep(300); await snap('08-task-detail');
  await chrome.eval(`[...document.querySelectorAll('.dialog button')].find(b => b.textContent.trim() === 'Close').click()`);
  await chrome.eval(`(() => { const s = ${q('#task-filter-status')}; s.value = ''; s.dispatchEvent(new Event('change')); })()`); await chrome.sleep(700); await snap('09-tasks');
  // ---- Traces: list, open, tree, spans, llm/tool detail, filter, splitters, live stream
  await tab('traces'); await check('traces: list renders', `document.querySelectorAll('#trace-list .trace-row').length >= 4`);
  await chrome.eval(`document.querySelectorAll('#trace-list .trace-row')[3].click()`);
  await check('traces: header + stats + tree', `!!${q('#trace-header h2')} && !${q('#trace-stats')}.classList.contains('hidden') && document.querySelectorAll('#tree-container .tree-row').length >= 3`);
  await chrome.eval(`${q('#btn-expand-all')}.click()`); await chrome.sleep(300);
  await chrome.eval(`(() => { const r = [...document.querySelectorAll('#tree-container .tree-row')].find(x => x.querySelector('.k-llm')); if (r) r.click(); })()`);
  await check('traces: LLM node shows prompt/response/tokens', `!!${q('#trace-detail-pane .usage-grid')} && !!${q('#trace-detail-pane pre.prompt')} && !!${q('#trace-detail-pane pre.response')}`, 4000); await chrome.sleep(400); await snap('10-traces');
  await chrome.eval(`(() => { const r = [...document.querySelectorAll('#tree-container .tree-row')].find(x => x.querySelector('.k-tool')); if (r) r.click(); })()`);
  await check('traces: tool node shows parameters/result', `${q('#trace-detail-pane')}.textContent.includes('Parameters (input)')`, 4000); await chrome.sleep(300); await snap('11-traces-tool');
  await chrome.eval(`${q('#tree-search')}.value = 'dag'; ${q('#tree-search')}.dispatchEvent(new Event('input'))`);
  await check('traces: tree filter narrows rows', `document.querySelectorAll('#tree-container .tree-row').length >= 1 && document.querySelectorAll('#tree-container .tree-row').length < 8`, 3000);
  await chrome.eval(`${q('#tree-search')}.value = ''; ${q('#tree-search')}.dispatchEvent(new Event('input'))`);
  const w0 = await chrome.eval(`${q('.traces-list')}.getBoundingClientRect().width`);
  await chrome.eval(`${q('#splitter-aside')}.focus(); ${q('#splitter-aside')}.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', shiftKey: true, bubbles: true }))`);
  await check('traces: splitter resizes the list', `${q('.traces-list')}.getBoundingClientRect().width > ${w0} + 50`, 2000);
  await chrome.eval(`(() => { const f = ${q('#filter-status')}; f.value = 'failed'; f.dispatchEvent(new Event('change')); })()`); await chrome.sleep(900);
  await check('traces: status filter (failed)', `document.querySelectorAll('#trace-list .trace-row').length === 1`);
  await chrome.eval(`(() => { const f = ${q('#filter-status')}; f.value = ''; f.dispatchEvent(new Event('change')); })()`); await chrome.sleep(500);
  await chrome.eval(`document.querySelectorAll('#trace-list .trace-row')[0].click()`);
  await chrome.sleep(900);
  // live: append a new event to the open (running) trace via the API-free path is not possible here; assert the stream is open
  await check('traces: live stream connected', `!!${q('#filter-live')} && ${q('#filter-live')}.checked`, 2000);
  await chrome.eval(`document.querySelectorAll('#trace-list .trace-row')[2].click()`); await chrome.sleep(900);
  await chrome.eval(`(() => { const r = [...document.querySelectorAll('#tree-container .tree-row')].find(x => x.classList.contains('error')); if (r) r.click(); })()`); await chrome.sleep(500); await snap('12-traces-failed');
  // ---- Raw JSON
  await tab('raw'); await check('raw: JSON shown', `${q('#raw-pre')}.textContent.includes('"summary"')`); await snap('13-raw');

  // ---- Operate tabs (new)
  await tab('active'); await check('active tasks: table', `!!${q('table.data')}`); await chrome.sleep(500); await snap('14-active-tasks');
  await tab('dags'); await check('dags: run list', `document.querySelectorAll('.run-item').length > 0`);
  await chrome.eval(`(() => { const b = [...document.querySelectorAll('.run-item')].find(x => x.textContent.includes('nightly')) || document.querySelector('.run-item'); b.click(); })()`);
  await check('dags: graph nodes drawn', `document.querySelectorAll('.node-g').length >= 6`); await chrome.sleep(900); await snap('15-dag');
  await chrome.eval(`(() => { const g = [...document.querySelectorAll('.node-g')].find(x => (x.getAttribute('aria-label')||'').startsWith('Node risky')); if (g) g.dispatchEvent(new MouseEvent('click', { bubbles: true })); })()`); await chrome.sleep(500); await snap('16-dag-node-failed');
  await chrome.eval(`(() => { const g = [...document.querySelectorAll('.node-g')].find(x => (x.getAttribute('aria-label')||'').startsWith('Node clean')); if (g) g.dispatchEvent(new MouseEvent('click', { bubbles: true })); })()`); await chrome.sleep(500);
  await chrome.eval(`${q('.dag-detail .btn.danger')}.click()`); await check('dags: kill confirm dialog', `!!${q('.dialog')}`, 3000); await chrome.sleep(300); await snap('17-kill-confirm');
  await chrome.eval(`${q('.dialog .btn:not(.danger)')}.click()`);
  await tab('playground'); await check('playground: editor', `!!${q('.editor')}`); await chrome.sleep(500); await snap('18-playground');
  await chrome.eval(`(() => { const t = ${q('#dag-json')}; t.value = '{ "tasks": [ { "id": "a", "capability": "admin.test.echo" '; t.dispatchEvent(new Event('input')); })()`);
  await chrome.sleep(300); await chrome.eval(`${q('.editor')}.scrollIntoView({block:'center'})`); await chrome.sleep(200); await snap('19-editor-error');
  await tab('events'); await check('events: panel', `!!${q('.events')}`);
  await http('POST', `${A}/run/demo/fanout?scale=0.1`, token); await chrome.sleep(1500); await snap('20-events');
  await check('events: live rows arrive', `document.querySelectorAll('.ev').length > 0`, 5000);
  await tab('resources'); await check('resources: stat cards', `document.querySelectorAll('#view .stat-value').length >= 3`); await chrome.sleep(900); await snap('21-resources');
  await check('sidebar badges update', `${q('#count-active')}.textContent.length > 0 || ${q('#count-dags')}.textContent.length > 0`, 8000);
  await tab('active');
  await chrome.eval(`(() => { const a = document.querySelector('a[href^="#/dags/"]'); if (a) a.click(); })()`);
  await check('deep link opens the DAG view', `location.hash.startsWith('#/dags/')`, 4000);

  // ---- keyboard + a11y probes
  await chrome.eval(`document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)`);
  for (const type of ['rawKeyDown', 'keyUp']) await chrome.send('Input.dispatchKeyEvent', { type, key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });   // a real Tab -> :focus-visible
  await chrome.sleep(200);
  await check('a11y: Tab moves focus and shows a visible ring', `document.activeElement !== document.body && getComputedStyle(document.activeElement).boxShadow !== 'none'`, 2000);
  await check('a11y: landmarks + current page', `!!${q('nav[aria-label]')} && !!${q('main#view')} && !!${q('.nav-item[aria-current="page"]')}`, 2000);
  await check('a11y: every button has an accessible name', `[...document.querySelectorAll('button')].every(b => (b.textContent || '').trim() || b.getAttribute('aria-label') || b.title)`, 2000);

  // ---- responsive
  await chrome.setViewport(820, 1100);
  await tab('dags'); await chrome.sleep(1200); await snap('22-tablet-dag');
  await tab('traces'); await chrome.sleep(900); await snap('23-tablet-traces');
  await tab('overview'); await chrome.sleep(900); await snap('24-tablet-overview');
  await chrome.setViewport(1440, 900);
  // ---- reduced motion
  await chrome.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await check('reduced motion: scanline hidden', `getComputedStyle(${q('.hud-scan')}).display === 'none'`, 2000);
  await chrome.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });

  // ---- sign out via the user menu
  await chrome.eval(`${q('#user-btn')}.click()`); await chrome.sleep(250); await snap('25-user-menu');
  await chrome.eval(`${q('#logout-btn')}.click()`);
  await check('logout: login screen returns', `!!${q('#login-email')}`, 4000);
  await check('logout: token removed', `localStorage.getItem('pai_admin_token') === null`, 2000);
  await snap('26-after-logout');
} finally {
  console.log('--- checks ---'); for (const [l, ok] of checks) console.log(ok ? 'PASS' : 'FAIL', l);
  console.log('--- browser console ---'); chrome.logs.filter((l) => !l.startsWith('verbose')).forEach((l) => console.log(l));
  await chrome.close();
  for (const rid of [pipe.request_id, live2.request_id, live3.request_id]) await http('POST', `${A}/dags/${rid}/kill`, token).catch(() => {});
  if (checks.some(([, ok]) => !ok)) process.exitCode = 1;
}

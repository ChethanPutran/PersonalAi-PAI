import { h, s, clear } from '../dom.js';
import { icon } from '../icons.js';
import { get } from '../api.js';
import { sparkline, skeletonRows, errorState, pageHead, btn } from '../ui.js';
import { fmtDuration, fmtNumber } from '../format.js';

const MAX_POINTS = 60;
const STATUS_ORDER = ['running', 'queued', 'initialized', 'created', 'pending', 'paused', 'retrying', 'completed', 'failed', 'cancelled'];
const STATUS_COLOR = { running: 'var(--running)', queued: 'var(--info)', initialized: 'var(--info)', created: 'var(--pending)', pending: 'var(--pending)',
  paused: 'var(--warn)', retrying: 'var(--info)', completed: 'var(--ok)', failed: 'var(--danger)', cancelled: 'var(--cancelled)' };

function statCard(label, ic, value, unit, spark, foot) {
  return h('div', { class: 'card stat' },
    h('div', { class: 'stat-label' }, icon(ic), label),
    h('div', { class: 'stat-value' }, value, unit ? h('small', {}, unit) : null),
    h('div', { class: 'stat-foot' }, h('span', {}, foot || ''), spark || null));
}

function lineChart(series, { width = 640, height = 160, pad = 28 } = {}) {
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': 'Resource usage over time' });
  const longest = Math.max(...series.map((x) => x.values.length), 0);
  for (let i = 0; i <= 3; i++) {
    const y = pad / 2 + i * ((height - pad) / 3);
    svg.append(s('line', { class: 'grid-line', x1: 0, x2: width, y1: y, y2: y }));
  }
  if (longest < 2) return svg;
  series.forEach((ser, idx) => {
    const vals = ser.values; const min = Math.min(...vals), max = Math.max(...vals); const span = max - min || 1;
    const pts = vals.map((v, i) => [(i / (MAX_POINTS - 1)) * width, height - pad / 2 - ((v - min) / span) * (height - pad)]);
    const off = width - (vals.length - 1) / (MAX_POINTS - 1) * width;
    const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${(x + off - (pts[0][0])).toFixed(1)},${y.toFixed(1)}`).join(' ');
    if (idx === 0) svg.append(s('path', { class: 'a1', d: `${d} L${width},${height} L${off.toFixed(1)},${height} Z` }));
    svg.append(s('path', { class: idx === 0 ? 'l1' : 'l2', d }));
    svg.append(s('text', { class: 'axis', x: 4, y: idx === 0 ? 11 : height - 4 }, `${ser.label}: ${max.toFixed(ser.digits ?? 0)} max`));
  });
  return svg;
}

export async function mount(container, ctx) {
  const hist = { rss: [], tasks: [], active: [] };
  const page = h('div', { class: 'page' });
  container.append(page);
  let timer = null; let alive = true;

  const header = pageHead('Resources', 'Live health of the runtime: tasks, DAGs, devices, voice and process resources.',
    btn('Refresh', { ic: 'refresh', onclick: () => load(true) }));
  const body = h('div', { class: 'stack' }, h('div', { class: 'grid stats' }, [1, 2, 3, 4, 5].map(() => h('div', { class: 'card stat' }, h('div', { class: 'skeleton skel-stat' })))),
    h('div', { class: 'card' }, skeletonRows(4, 4)));
  page.append(header, body);

  async function load(force = false) {
    try {
      const o = await get('/admin/overview');
      if (!alive) return;
      const push = (arr, v) => { arr.push(v); if (arr.length > MAX_POINTS) arr.shift(); };
      push(hist.rss, o.process.rss_mb); push(hist.tasks, o.process.asyncio_tasks); push(hist.active, o.active_tasks);
      render(o);
    } catch (e) {
      if (!alive) return;
      clear(body); body.append(h('div', { class: 'card' }, errorState(e, () => load(true))));
      if (force) console.error('[admin] overview', e);
    }
  }

  function render(o) {
    clear(body);
    const t = o.tasks;
    const stats = h('div', { class: 'grid stats' },
      statCard('Active tasks', 'tasks', fmtNumber(o.active_tasks), '', sparkline(hist.active), `${t.running} running · ${t.queued + t.initialized + t.pending + t.created} waiting`),
      statCard('Running DAGs', 'dags', fmtNumber(o.active_dags), '', null, `${fmtNumber(o.dags_in_history)} finished in history`),
      statCard('Devices online', 'devices', `${o.devices.connected}`, `/ ${o.devices.total}`, null, o.devices.connected ? 'connected now' : 'no device connected'),
      statCard('Voice sessions', 'mic', fmtNumber(o.voice.active_sessions), '', null, o.voice.available ? `${o.voice.stt} → ${o.voice.tts}` : 'unavailable'),
      statCard('Memory (RSS)', 'cpu', o.process.rss_mb.toFixed(0), 'MB', sparkline(hist.rss), `${o.process.asyncio_tasks} tasks · ${o.process.threads} threads`));

    // task status distribution
    const total = STATUS_ORDER.reduce((a, k) => a + (t[k] || 0), 0) || 1;
    const bar = s('svg', { viewBox: '0 0 100 3', preserveAspectRatio: 'none', width: '100%', height: 12, role: 'img', 'aria-label': 'Task status distribution' });
    let x = 0;
    STATUS_ORDER.forEach((k) => { const w = ((t[k] || 0) / total) * 100; if (w > 0) { bar.append(s('rect', { x, y: 0, width: w, height: 3, fill: STATUS_COLOR[k] })); x += w; } });
    const dist = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Task status'), h('span', { class: 'sub' }, `${fmtNumber(total)} total`)),
      h('div', { class: 'card-body stack' }, bar,
        h('div', { class: 'row' }, STATUS_ORDER.filter((k) => t[k]).map((k) => h('span', { class: 'row' }, h('span', { class: `chip ${k}` }, k), h('b', {}, fmtNumber(t[k])))))));

    const chart = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Process resources'), h('span', { class: 'sub' }, 'last ~3 min'),
      h('div', { class: 'right legend-inline' }, h('span', {}, h('i', { class: 'l1' }), 'RSS MB'), h('span', {}, h('i', { class: 'l2' }), 'async tasks'))),
      h('div', { class: 'card-body' }, hist.rss.length < 2
        ? h('div', { class: 'state chart-wait' }, icon('activity'), h('p', {}, 'Collecting samples — the chart fills in every few seconds.'))
        : lineChart([{ label: 'RSS MB', values: hist.rss, digits: 0 }, { label: 'async tasks', values: hist.tasks }])));

    const dot = (ok) => h('span', { class: `dot ${ok ? 'ok' : 'bad'}` });
    const comps = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Components')),
      h('div', { class: 'card-body flush' },
        o.executors.map((e) => h('div', { class: 'list-row' }, dot(e.running), h('div', { class: 'grow' }, h('b', {}, e.name), h('div', { class: 'faint' }, e.kind)), h('span', { class: `chip ${e.running ? 'completed' : 'failed'}` }, e.running ? 'running' : 'stopped'))),
        h('div', { class: 'list-row' }, dot(o.voice.available), h('div', { class: 'grow' }, h('b', {}, 'Voice'), h('div', { class: 'faint' }, o.voice.available ? `STT ${o.voice.stt} · TTS ${o.voice.tts}` : o.voice.reason || 'unavailable')), h('span', { class: `chip ${o.voice.available ? 'completed' : 'failed'}` }, o.voice.available ? 'ready' : 'off')),
        h('div', { class: 'list-row' }, dot(o.test_capabilities_enabled), h('div', { class: 'grow' }, h('b', {}, 'Test capabilities'), h('div', { class: 'faint' }, 'admin.test.* for device-free demos')), h('span', { class: `chip ${o.test_capabilities_enabled ? 'info' : 'neutral'}` }, o.test_capabilities_enabled ? 'enabled' : 'disabled'))));

    const p = o.process;
    const vit = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Process vitals')),
      h('div', { class: 'card-body' }, h('dl', { class: 'kv' },
        h('dt', {}, 'Uptime'), h('dd', {}, fmtDuration(p.uptime_seconds * 1000)), h('dt', {}, 'Resident memory'), h('dd', {}, `${p.rss_mb} MB`),
        h('dt', {}, 'Asyncio tasks'), h('dd', {}, String(p.asyncio_tasks)), h('dt', {}, 'Threads'), h('dd', {}, String(p.threads)),
        h('dt', {}, 'Open file descriptors'), h('dd', {}, p.open_fds ?? '—'), h('dt', {}, 'Tracked Python objects'), h('dd', {}, fmtNumber(p.gc_objects)),
        ...(o.resources ? Object.entries(o.resources).slice(0, 8).flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, typeof v === 'object' ? JSON.stringify(v) : String(v))]) : []))));

    body.append(stats, h('div', { class: 'grid two' }, chart, dist), h('div', { class: 'grid two' }, comps, vit));
  }

  load();
  timer = setInterval(load, 3000);
  return { unmount() { alive = false; clearInterval(timer); } };
}

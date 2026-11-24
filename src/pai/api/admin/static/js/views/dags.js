import { h, s, clear, debounce } from '../dom.js';
import { icon } from '../icons.js';
import { get, post } from '../api.js';
import { statusChip, btn, busy, toast, confirmDialog, skeletonRows, emptyState, errorState, pageHead, copyButton } from '../ui.js';
import { fmtDuration, fmtAgo, fmtTime, shortId, truncate } from '../format.js';
import { layoutDag, edgeState, NODE_W, NODE_H } from '../layout.js';
import { affectsLists } from '../state.js';
import { windowLabel } from '../history.js';

const LEGEND = [['running', 'var(--running)'], ['completed', 'var(--ok)'], ['failed', 'var(--danger)'], ['pending', 'var(--pending)'], ['skipped', 'var(--skipped)'], ['cancelled', 'var(--cancelled)']];
const MIN_READABLE_ZOOM = 0.62;
const COUNT_ORDER = ['running', 'completed', 'failed', 'skipped', 'cancelled', 'pending'];

export async function mount(container, ctx) {
  let alive = true;
  let runs = []; let listState = 'loading'; let listError = null;
  // [label, days]: 0 = live runs only, null = everything retained (the task cleanup window)
  const WINDOWS = [['Live only', 0], ['Last hour', 1 / 24], ['Last 24 hours', 1], ['All retained', null]];
  let windowIdx = 3; let retentionDays = null;
  let selectedId = ctx.param || null; let snap = null; let snapError = null; let selectedNode = null;
  let view = { x: 0, y: 0, k: 1 }; let structureKey = ''; let lay = null; let fitted = false;
  const nodeEls = new Map(); const edgeEls = new Map();

  // ---- skeleton of the page
  const listBody = h('div', { class: 'scroll' });
  const windowSel = h('select', { class: 'select', id: 'dag-window', 'aria-label': 'How far back to list runs', onchange: (e) => { windowIdx = Number(e.target.value); loadRuns(); } });
  const fillWindow = () => { clear(windowSel); WINDOWS.forEach(([label, days], i) => { if (days == null || days === 0 || retentionDays == null || days <= retentionDays) windowSel.append(h('option', { value: String(i), selected: i === windowIdx }, days == null && retentionDays ? `All · ${retentionDays} days` : label)); }); };
  fillWindow();
  const listCard = h('div', { class: 'card dag-list' }, h('div', { class: 'card-head' }, h('h2', {}, 'Runs'), windowSel, h('div', { class: 'right' }, h('span', { class: 'dot live' }))), listBody);
  const title = h('div', { class: 'title' });
  const toolbarKill = h('span', {});
  const canvas = h('div', { class: 'dag-canvas', tabindex: '0', role: 'application', 'aria-label': 'DAG graph. Drag to pan, scroll to zoom, arrow keys to pan.' });
  const minimap = h('div', { class: 'minimap hidden' });
  const legend = h('div', { class: 'legend', 'aria-hidden': 'true' }, LEGEND.map(([n, c]) => h('span', {}, h('i', { class: `lg-${n}` }), n)));
  const zoom = h('div', { class: 'zoom' },
    btn('', { ic: 'plus', size: 'sm', variant: '', title: 'Zoom in', onclick: () => zoomBy(1.25) }),
    btn('', { ic: 'minus', size: 'sm', title: 'Zoom out', onclick: () => zoomBy(0.8) }),
    btn('', { ic: 'fit', size: 'sm', title: 'Fit to view', onclick: () => fit() }));
  const toolbarCounts = h('div', { class: 'counts' });
  const stage = h('div', { class: 'card dag-stage' }, h('div', { class: 'dag-toolbar' }, title, toolbarCounts, h('span', { class: 'grow' }), toolbarKill), canvas);
  canvas.append(legend, zoom, minimap);
  const detail = h('div', { class: 'dag-detail hidden', role: 'complementary', 'aria-label': 'Node details' });
  stage.append(detail);
  container.append(h('div', { class: 'page' },
    pageHead('DAG Runs', 'Watch a plan execute node by node. Click a node for details; kill one node without touching its independent siblings.'),
    h('div', { class: 'dag-layout' }, listCard, stage)));

  // ---- helpers
  const statusOf = (id) => snap?.nodes.find((n) => n.spec_id === id)?.status || 'pending';
  const runLabel = (r) => (r.request_id.length > 14 ? r.request_id.slice(0, 12) : r.request_id);

  async function loadRuns() {
    try {
      const days = WINDOWS[windowIdx][1];
      const res = await get('/admin/dags', { params: days === 0 ? { active_only: true } : { days: days ?? undefined } });
      if (!alive) return;
      runs = res.runs; listState = 'ready'; listError = null;
      if (res.retention_days !== retentionDays) { retentionDays = res.retention_days; fillWindow(); }
      if (!selectedId && runs.length) selectedId = (runs.find((r) => r.status === 'running') || runs[0]).request_id;
    } catch (e) { if (!alive) return; listState = 'error'; listError = e; }
    renderList();
    if (selectedId && !snap) loadSnapshot();
  }

  async function loadSnapshot() {
    if (!selectedId) { snap = null; renderStage(); return; }
    try {
      const next = await get(`/admin/dags/${selectedId}`);
      if (!alive || next.request_id !== selectedId) return;
      snap = next; snapError = null;
    } catch (e) { if (!alive) return; snap = null; snapError = e; }
    renderStage(); renderDetail();
  }

  function select(id) { selectedId = id; selectedNode = null; snap = null; structureKey = ''; fitted = false; ctx.replace?.(`dags/${id}`); renderList(); renderStage(); renderDetail(); loadSnapshot(); }

  // ---- run list
  function renderList() {
    clear(listBody);
    if (listState === 'loading') { listBody.append(skeletonRows(5, 3)); return; }
    if (listState === 'error') { listBody.append(errorState(listError, loadRuns)); return; }
    if (!runs.length) { listBody.append(emptyState(WINDOWS[windowIdx][1] === 0 ? 'No live runs' : 'No runs in this range', `Showing ${windowLabel(WINDOWS[windowIdx][1], retentionDays)}. Run a demo DAG or a goal from the Playground.`, 'dags', btn('Open Playground', { variant: 'primary', ic: 'flask', onclick: () => ctx.navigate('playground') }))); return; }
    const mk = (r) => h('button', { class: 'run-item', 'aria-selected': String(r.request_id === selectedId), onclick: () => select(r.request_id) },
      h('div', { class: 'run-top' }, statusChip(r.status), h('span', { class: 'mono' }, runLabel(r))),
      h('div', { class: 'counts' }, COUNT_ORDER.filter((k) => r.counts[k]).map((k) => h('span', { class: `count ${k}` }, `${r.counts[k]} ${k}`))),
      h('div', { class: 'run-meta' }, h('span', {}, `${r.nodes.length} nodes`), h('span', { title: new Date(r.started_at).toLocaleString() }, fmtAgo(r.started_at)), h('span', {}, fmtDuration(r.duration_ms))));
    const live = runs.filter((r) => r.status === 'running'); const done = runs.filter((r) => r.status !== 'running');
    if (live.length) listBody.append(h('div', { class: 'nav-label' }, `Live · ${live.length}`), ...live.map(mk));
    if (done.length) listBody.append(h('div', { class: 'nav-label' }, `History · ${done.length} (${windowLabel(WINDOWS[windowIdx][1], retentionDays)})`), ...done.map(mk));
  }

  // ---- graph
  function applyView() { const g = canvas.querySelector('g.viewport'); if (g) g.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`); drawMinimap(); }
  function fit() {
    if (!lay || !lay.width) return;
    const r = canvas.getBoundingClientRect();
    const kFit = Math.min(r.width / lay.width, r.height / lay.height) * 0.96;
    // Never shrink below a readable size: a wide graph is left-aligned and panned (minimap helps).
    const k = Math.min(1.15, Math.max(MIN_READABLE_ZOOM, kFit));
    view = { k, x: kFit >= MIN_READABLE_ZOOM ? (r.width - lay.width * k) / 2 : 8, y: Math.max(8, (r.height - lay.height * k) / 2) }; applyView();
  }
  function zoomBy(f, cx, cy) {
    const r = canvas.getBoundingClientRect(); cx ??= r.width / 2; cy ??= r.height / 2;
    const k = Math.min(2.2, Math.max(0.2, view.k * f));
    view = { k, x: cx - ((cx - view.x) / view.k) * k, y: cy - ((cy - view.y) / view.k) * k }; applyView();
  }

  function renderStage() {
    title.replaceChildren();
    toolbarKill.replaceChildren();
    toolbarCounts.replaceChildren();
    if (!selectedId) { canvas.querySelector('svg.dag-svg')?.remove(); showCanvasMessage(emptyState('Select a run', 'Pick a run on the left to see its graph.', 'dags')); return; }
    if (snapError && !snap) { showCanvasMessage(errorState(snapError, loadSnapshot)); return; }
    if (!snap) { showCanvasMessage(h('div', { class: 'state' }, h('div', { class: 'skeleton skel-stat' }))); return; }
    clearCanvasMessage();
    title.append(statusChip(snap.status), h('span', { class: 'mono' }, runLabel(snap)), h('span', { class: 'faint' }, `${fmtDuration(snap.duration_ms)}`));
    COUNT_ORDER.filter((k) => snap.counts[k]).forEach((k) => toolbarCounts.append(h('span', { class: `count ${k}` }, `${snap.counts[k]} ${k}`)));
    if (snap.status === 'running') {
      const k = btn('Kill run', { variant: 'danger', size: 'sm', ic: 'stop', onclick: () => killRun(k) });
      toolbarKill.append(k);
    }
    const key = snap.nodes.map((n) => n.spec_id).join('|') + '#' + snap.edges.map((e) => `${e.source}>${e.target}`).join(',');
    if (key !== structureKey) { structureKey = key; buildGraph(); }
    updateGraph();
  }

  function showCanvasMessage(node) { clearCanvasMessage(); canvas.querySelector('svg.dag-svg')?.remove(); structureKey = ''; canvas.append(h('div', { class: 'canvas-msg' }, node)); }
  function clearCanvasMessage() { canvas.querySelector('.canvas-msg')?.remove(); }

  function buildGraph() {
    canvas.querySelector('svg.dag-svg')?.remove(); nodeEls.clear(); edgeEls.clear();
    lay = layoutDag(snap.nodes, snap.edges);
    const svg = s('svg', { class: 'dag-svg', role: 'group', 'aria-label': `DAG with ${snap.nodes.length} nodes` });
    const vp = s('g', { class: 'viewport' });
    const edgeLayer = s('g'); const nodeLayer = s('g');
    for (const e of lay.edges) { const p = s('path', { class: 'edge', d: e.d }); edgeEls.set(`${e.source}>${e.target}`, p); edgeLayer.append(p); }
    for (const n of snap.nodes) {
      const p = lay.positions[n.spec_id];
      const g = s('g', { class: 'node-g pending', transform: `translate(${p.x},${p.y})`, tabindex: '0', role: 'button', 'aria-label': `Node ${n.spec_id}` });
      const pulse = s('rect', { class: 'node-pulse', x: -2, y: -2, width: NODE_W + 4, height: NODE_H + 4, rx: 14 });
      const card = s('rect', { class: 'node-card', width: NODE_W, height: NODE_H, rx: 12 });
      const bar = s('rect', { class: 'node-bar', x: 0, y: 14, width: 4, height: NODE_H - 28, rx: 2 });
      const titleT = s('text', { class: 'node-title', x: 18, y: 26 }, truncate(n.spec_id, 22));
      const sub = s('text', { class: 'node-sub', x: 18, y: 44 }, truncate(n.capability || n.task_type, 25));
      const st = s('text', { class: 'node-status', x: 18, y: 63 }, '');
      const dur = s('text', { class: 'node-dur', x: NODE_W - 14, y: 63, 'text-anchor': 'end' }, '');
      g.append(pulse, card, bar, titleT, sub, dur, st);
      g.addEventListener('click', (ev) => { ev.stopPropagation(); selectNode(n.spec_id); });
      g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectNode(n.spec_id); } });
      nodeEls.set(n.spec_id, { g, dur, st });
      nodeLayer.append(g);
    }
    vp.append(edgeLayer, nodeLayer); svg.append(vp); canvas.prepend(svg);
    if (!fitted) { fitted = true; requestAnimationFrame(fit); } else applyView();
    drawMinimap(true);
  }

  function updateGraph() {
    for (const n of snap.nodes) {
      const el = nodeEls.get(n.spec_id); if (!el) continue;
      el.g.setAttribute('class', `node-g ${n.status}${n.spec_id === selectedNode ? ' selected' : ''}`);
      const ms = n.status === 'running' && n.started_at ? Date.now() - new Date(n.started_at).getTime() : n.duration_ms;
      el.dur.textContent = ms != null ? fmtDuration(ms) : '';
      el.st.textContent = n.status;
      el.g.setAttribute('aria-label', `Node ${n.spec_id}, ${n.status}`);
    }
    for (const e of snap.edges) edgeEls.get(`${e.source}>${e.target}`)?.setAttribute('class', `edge ${edgeState(statusOf(e.source), statusOf(e.target))}`);
    drawMinimap();
  }

  function drawMinimap(rebuild) {
    if (!lay || !lay.width || !snap) { minimap.classList.add('hidden'); return; }
    minimap.classList.toggle('hidden', snap.nodes.length < 6);
    if (snap.nodes.length < 6) return;
    const W = 168, H = 104, k = Math.min(W / lay.width, H / lay.height);
    const ox = (W - lay.width * k) / 2, oy = (H - lay.height * k) / 2;
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: '100%', 'aria-hidden': 'true' });
    for (const n of snap.nodes) { const p = lay.positions[n.spec_id]; svg.append(s('rect', { class: `mm-node ${n.status}`, x: ox + p.x * k, y: oy + p.y * k, width: NODE_W * k, height: NODE_H * k, rx: 2 })); }
    const r = canvas.getBoundingClientRect();
    svg.append(s('rect', { class: 'mm-view', x: ox + (-view.x / view.k) * k, y: oy + (-view.y / view.k) * k, width: (r.width / view.k) * k, height: (r.height / view.k) * k, rx: 2 }));
    svg.addEventListener('click', (ev) => {
      const b = svg.getBoundingClientRect(); const wx = ((ev.clientX - b.left) / b.width * W - ox) / k, wy = ((ev.clientY - b.top) / b.height * H - oy) / k;
      view.x = r.width / 2 - wx * view.k; view.y = r.height / 2 - wy * view.k; applyView();
    });
    minimap.replaceChildren(svg);
  }

  // pan / zoom
  let drag = null;
  canvas.addEventListener('pointerdown', (e) => { if (e.target.closest('.node-g, .zoom, .minimap, .legend')) return; drag = { x: e.clientX - view.x, y: e.clientY - view.y }; canvas.classList.add('panning'); canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', (e) => { if (!drag) return; view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; applyView(); });
  const endDrag = (e) => { drag = null; canvas.classList.remove('panning'); try { canvas.releasePointerCapture(e.pointerId); } catch { /* not captured */ } };
  canvas.addEventListener('pointerup', endDrag); canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); const r = canvas.getBoundingClientRect(); zoomBy(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  canvas.addEventListener('keydown', (e) => {
    const step = 60; const m = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
    if (m) { e.preventDefault(); view.x += m[0]; view.y += m[1]; applyView(); } else if (e.key === '+' || e.key === '=') zoomBy(1.2); else if (e.key === '-') zoomBy(0.83); else if (e.key === '0') fit();
  });
  canvas.addEventListener('click', () => { if (selectedNode) { selectedNode = null; updateGraph(); renderDetail(); } });

  // ---- selection + detail
  function selectNode(id) { selectedNode = id; updateGraph(); renderDetail(); }

  async function killNode(n, button) {
    const ok = await confirmDialog({ title: `Kill node “${n.spec_id}”?`, danger: true, confirmLabel: 'Kill node',
      message: n.status === 'running' ? 'The running task is cancelled. Nodes that depend on it are skipped; independent nodes keep running.' : 'This node has not started. It will never run, and nodes that depend on it are skipped.' });
    if (!ok) return;
    await busy(button, async () => {
      try { const r = await post(`/admin/dags/${snap.request_id}/tasks/${n.spec_id}/kill`); toast(`${n.spec_id}: ${r.outcome.replace('_', ' ')}`, 'success'); await loadSnapshot(); loadRuns(); }
      catch (e) { toast(e.message, 'error', { title: 'Kill failed' }); }
    });
  }

  async function killRun(button) {
    const ok = await confirmDialog({ title: 'Kill the whole run?', danger: true, confirmLabel: 'Kill run', message: 'Every node that has not finished — running or waiting — is cancelled.' });
    if (!ok) return;
    await busy(button, async () => {
      try { const r = await post(`/admin/dags/${snap.request_id}/kill`); toast(`Cancelled ${r.cancelled_running} running and ${r.cancelled_pending} waiting node(s)`, 'success'); await loadSnapshot(); loadRuns(); }
      catch (e) { toast(e.message, 'error', { title: 'Kill failed' }); }
    });
  }

  function renderDetail() {
    clear(detail);
    detail.classList.toggle('hidden', !selectedNode || !snap);
    if (!snap) { detail.append(h('div', { class: 'card-head' }, h('h2', {}, 'Details')), emptyState('Nothing selected', 'Select a run to see its details.', 'info')); return; }
    const n = snap.nodes.find((x) => x.spec_id === selectedNode);
    if (!n) return;
    const killable = ['running', 'pending'].includes(n.status) && snap.status === 'running';
    const kill = btn('Kill this node', { variant: 'danger', ic: 'stop', onclick: () => killNode(n, kill) });
    detail.append(h('div', { class: 'card-head' }, h('h2', {}, n.spec_id), statusChip(n.status), h('div', { class: 'right' }, btn('', { variant: 'ghost', size: 'sm', ic: 'close', title: 'Close details', onclick: () => { selectedNode = null; updateGraph(); renderDetail(); } }))),
      h('div', { class: 'card-body stack' },
        n.error ? h('div', { class: 'callout danger', role: 'alert' }, icon('alert'), h('div', {}, n.error)) : null,
        h('dl', { class: 'kv' }, h('dt', {}, 'Capability'), h('dd', { class: 'mono' }, n.capability || '—'), h('dt', {}, 'Type'), h('dd', {}, n.task_type),
          h('dt', {}, 'Depends on'), h('dd', {}, n.depends_on.length ? n.depends_on.map((d) => h('button', { class: 'id', onclick: () => selectNode(d) }, d)) : h('span', { class: 'faint' }, 'nothing — can start immediately')),
          h('dt', {}, 'Started'), h('dd', {}, fmtTime(n.started_at)), h('dt', {}, 'Finished'), h('dd', {}, fmtTime(n.finished_at)),
          h('dt', {}, 'Duration'), h('dd', {}, n.status === 'running' && n.started_at ? fmtDuration(Date.now() - new Date(n.started_at).getTime()) : fmtDuration(n.duration_ms)),
          h('dt', {}, 'Task id'), h('dd', {}, n.runtime_task_id ? [h('span', { class: 'mono' }, shortId(n.runtime_task_id, 13)), copyButton(n.runtime_task_id)] : h('span', { class: 'faint' }, 'not created yet'))),
        killable ? kill : h('p', { class: 'hint' }, n.status === 'pending' ? 'Waiting for its dependencies.' : 'This node has finished.')));
  }

  // ---- live updates
  const refreshAll = debounce(() => { loadRuns(); if (selectedId) loadSnapshot(); }, 200);
  const onEvent = (e) => { if (affectsLists(e.detail.event)) refreshAll(); };
  ctx.bus.addEventListener('event', onEvent); ctx.bus.addEventListener('reconnected', refreshAll);
  const poll = setInterval(() => { loadRuns(); if (selectedId && (!snap || snap.status === 'running')) loadSnapshot(); }, 2000);
  const ticker = setInterval(() => { if (snap && snap.status === 'running') { updateGraph(); if (selectedNode) renderDetail(); } }, 500);
  const onResize = debounce(() => { if (lay) { drawMinimap(); } }, 150);
  window.addEventListener('resize', onResize);

  renderList(); renderStage(); renderDetail();
  loadRuns();
  return { unmount() { alive = false; clearInterval(poll); clearInterval(ticker); refreshAll.cancel(); window.removeEventListener('resize', onResize); ctx.bus.removeEventListener('event', onEvent); ctx.bus.removeEventListener('reconnected', refreshAll); } };
}

// Traces — LangSmith-style: filterable list, span/LLM/tool tree with waterfall bars, detail pane, live SSE,
// resizable splitters. Behaviour is a native port of the legacy page's Traces view (same endpoints and ids).
import { h, clear, preserveScroll } from '../dom.js';

// Element.append(null) would print "null" — always go through this.
const put = (el, ...nodes) => { for (const n of nodes.flat()) if (n != null && n !== false) el.append(n); return el; };
import { icon } from '../icons.js';
import { api, openTraceStream } from '../api.js';
import { btn, statusChip, errorState, copyTextButton, codeBlock } from '../ui.js';
import { fmtEpoch, fmtMs, fmtCost, fmtTokens, sectionShares } from '../format.js';
import { barChart, lineChart } from '../charts.js';
import { llmCallsFromEvents, speechStepsFromEvents, callLabel, cumulative, totalsOf } from '../usage_data.js';
import { buildFlatTree, kindFamily, visibleRows, traceBounds, barGeometry, traceStats, defaultExpanded, collapseAllKeepRoots, expandAll, llmOther } from '../trace_tree.js';

const KIND_ICON = { trace: 'play', span: 'bolt', llm: 'chat', tool: 'wrench', error: 'xcircle', log: 'dot' };
const LS_ASIDE = 'pai_admin_traces_aside_w';
const LS_DETAIL = 'pai_admin_traces_detail_w';

function lsGet(k) { try { return parseInt(localStorage.getItem(k), 10); } catch { return NaN; } }
function lsSet(k, v) { try { localStorage.setItem(k, String(v)); } catch { /* ignore */ } }

/** Drag (mouse/touch) or arrow keys to resize `target`. invert=true: dragging right shrinks it. */
function splitter(el, target, key, { min, max, invert }) {
  const saved = lsGet(key);
  if (Number.isFinite(saved) && saved >= min) target.style.width = `${saved}px`;
  const clamp = (w) => Math.max(min, Math.min(max(), w));
  const set = (w) => { target.style.width = `${clamp(w)}px`; };
  let startX = 0; let startW = 0; let dragging = false;
  const move = (x) => { if (dragging) set(startW + (invert ? -(x - startX) : x - startX)); };
  const onMove = (e) => move(e.clientX);
  const onTouch = (e) => { move(e.touches[0].clientX); e.preventDefault(); };
  const stop = () => {
    if (!dragging) return; dragging = false; el.classList.remove('dragging'); document.body.style.cursor = ''; document.body.style.userSelect = '';
    const w = parseInt(target.style.width, 10); if (Number.isFinite(w)) lsSet(key, w);
    window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', stop);
    window.removeEventListener('touchmove', onTouch); window.removeEventListener('touchend', stop);
  };
  const start = (x) => {
    dragging = true; startX = x; startW = target.getBoundingClientRect().width; el.classList.add('dragging');
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove); window.addEventListener('mouseup', stop);
    window.addEventListener('touchmove', onTouch, { passive: false }); window.addEventListener('touchend', stop);
  };
  el.addEventListener('mousedown', (e) => { e.preventDefault(); start(e.clientX); });
  el.addEventListener('touchstart', (e) => { e.preventDefault(); start(e.touches[0].clientX); }, { passive: false });
  el.addEventListener('keydown', (e) => {                 // keyboard: ← → resize by 24px (Shift: 96px)
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const dir = (e.key === 'ArrowRight' ? 1 : -1) * (invert ? -1 : 1) * (e.shiftKey ? 96 : 24);
    set(target.getBoundingClientRect().width + dir); lsSet(key, parseInt(target.style.width, 10));
  });
  return () => stop();
}

export async function mount(container, ctx) {
  let alive = true; let seq = 0;
  let selectedTraceId = null; let selectedNodeId = null; let summary = null; let events = []; let flat = []; let expanded = new Set(); let treeFilter = '';
  let stream = null; let listTimer = null; let traceTimer = null; let traces = [];

  // ---------------------------------------------------------------- DOM
  const fUser = h('input', { class: 'input', id: 'filter-user', placeholder: 'user_id', 'aria-label': 'Filter by user id', onchange: () => loadTraces() });
  const fStatus = h('select', { class: 'select', id: 'filter-status', 'aria-label': 'Filter by status', onchange: () => loadTraces() },
    [['', 'all statuses'], ['running', 'running'], ['completed', 'completed'], ['failed', 'failed']].map(([v, l]) => h('option', { value: v }, l)));
  const fErrors = h('input', { type: 'checkbox', id: 'filter-errors', onchange: () => loadTraces() });
  const fLive = h('input', { type: 'checkbox', id: 'filter-live', checked: true, onchange: () => (fLive.checked ? startStream() : stopStream()) });
  const refreshBtn = btn('', { ic: 'refresh', title: 'Refresh traces', size: 'sm', onclick: () => loadTraces() });
  refreshBtn.id = 'btn-refresh-traces';
  const list = h('div', { class: 'trace-scroll', id: 'trace-list', role: 'listbox', 'aria-label': 'Traces' });
  const head = h('div', { class: 'trace-head', id: 'trace-header' }, h('div', { class: 'faint' }, 'Select a trace on the left'));
  const stats = h('div', { class: 'trace-stats hidden', id: 'trace-stats' });
  const tSearch = h('input', { class: 'input', id: 'tree-search', placeholder: 'Filter by name…', 'aria-label': 'Filter spans by name', oninput: (e) => { treeFilter = e.target.value; renderTree(); } });
  const expandBtn = btn('', { ic: 'chevron', title: 'Expand all', size: 'sm', onclick: () => { expanded = expandAll(flat); renderTree(); } });
  expandBtn.id = 'btn-expand-all'; expandBtn.setAttribute('aria-label', 'Expand all');
  const collapseBtn = btn('', { ic: 'minus', title: 'Collapse all', size: 'sm', onclick: () => { expanded = collapseAllKeepRoots(flat); renderTree(); } });
  collapseBtn.id = 'btn-collapse-all'; collapseBtn.setAttribute('aria-label', 'Collapse all');
  const tree = h('div', { class: 'tree-scroll', id: 'tree-container', role: 'tree', 'aria-label': 'Trace spans' }, h('div', { class: 'empty-inline' }, '—'));
  const detail = h('div', { class: 'detail-pane', id: 'trace-detail-pane' }, h('div', { class: 'placeholder' }, 'Select a node in the tree'));
  const sp1 = h('div', { class: 'splitter', id: 'splitter-aside', role: 'separator', 'aria-orientation': 'vertical', tabindex: '0', title: 'Drag (or ← →) to resize', 'aria-label': 'Resize trace list' });
  const sp2 = h('div', { class: 'splitter', id: 'splitter-detail', role: 'separator', 'aria-orientation': 'vertical', tabindex: '0', title: 'Drag (or ← →) to resize', 'aria-label': 'Resize detail pane' });
  const listPanel = h('div', { class: 'card traces-list' },
    h('div', { class: 'traces-filters' }, h('div', { class: 'full' }, fUser), fStatus,
      h('div', { class: 'row' }, h('label', { class: 'check', for: 'filter-errors' }, fErrors, 'errors'), h('label', { class: 'check', for: 'filter-live' }, fLive, 'live'), refreshBtn)),
    list);
  // The usage aside (tokens and cost of every LLM call in this trace): opened with the Usage button, remembered per browser.
  let usageOpen = false;
  try { usageOpen = localStorage.getItem('pai.traces.usage') === '1'; } catch { /* storage blocked: closed */ }
  const usagePanel = h('aside', { class: 'goal-usage hidden', id: 'trace-usage', 'aria-label': 'Usage of this trace' });
  const usageBtn = btn('Usage', { ic: 'coins', size: 'sm', title: 'Tokens and cost of the LLM calls in this trace', onclick: () => {
    usageOpen = !usageOpen;
    try { localStorage.setItem('pai.traces.usage', usageOpen ? '1' : '0'); } catch { /* ignore */ }
    renderUsage();
  } });
  usageBtn.id = 'btn-trace-usage';
  const detailPanel = h('div', { class: 'card traces-detail' }, head, stats,
    h('div', { class: 'trace-body' }, h('div', { class: 'tree-pane' }, h('div', { class: 'tree-toolbar' }, tSearch, expandBtn, collapseBtn, usageBtn), tree), sp2, detail, usagePanel));
  container.append(h('div', { class: 'traces', id: 'view-traces-root' }, listPanel, sp1, detailPanel));
  const unsplit = [
    splitter(sp1, listPanel, LS_ASIDE, { min: 220, max: () => Math.max(300, window.innerWidth - 420), invert: false }),
    splitter(sp2, detail, LS_DETAIL, { min: 260, max: () => Math.max(300, window.innerWidth - 500), invert: true })];

  // ---------------------------------------------------------------- list
  async function loadTraces() {
    const my = ++seq;
    const q = { limit: 200 };
    if (fUser.value) q.user_id = fUser.value;
    if (fStatus.value) q.status = fStatus.value;
    if (fErrors.checked) q.has_error = 'true';
    try {
      const data = await api('GET', '/system/traces', { params: q });
      if (!alive || my !== seq) return;
      traces = data.traces || [];
      ctx.store.set({ tracesTotal: traces.length });
      renderList();
    } catch (e) { if (alive && my === seq) { clear(list); list.append(errorState(e, loadTraces)); } }
  }
  const scheduleList = () => { clearTimeout(listTimer); listTimer = setTimeout(loadTraces, 400); };

  // Rebuilding the list must not throw the user back to the top (live events refresh it often).
  function renderList() { preserveScroll(list, buildList); }
  function buildList() {
    clear(list);
    if (!traces.length) { list.append(h('div', { class: 'empty-inline' }, 'No traces yet')); return; }
    for (const t of traces) {
      const row = h('button', { class: 'trace-row', type: 'button', role: 'option', 'aria-selected': String(selectedTraceId === t.trace_id), 'data-id': t.trace_id, onclick: () => selectTrace(t.trace_id) },
        h('div', { class: 'top' }, h('span', {}, statusChip(t.status), t.mode === 'voice' ? h('span', { class: 'chip running', title: 'Started by voice', style: 'margin-left:6px' }, 'voice') : null), h('span', { class: 'time' }, fmtEpoch(t.started_at))),
        h('div', { class: 'goal' }, (t.goal || t.user_id || '(anonymous)').slice(0, 80)),
        h('div', { class: 'meta' }, h('span', {}, `${t.event_count} events`), t.error_count ? h('span', { class: 'e' }, `${t.error_count} err`) : null,
          t.has_llm ? h('span', { class: 'l', title: 'LLM calls' }, icon('chat')) : null,
          t.total_tokens ? h('span', { class: 'k', title: 'Tokens used' }, `${fmtTokens(t.total_tokens)} tok`) : null,
          t.cost_usd ? h('span', { class: 'c', title: 'Cost of the LLM calls' }, fmtCost(t.cost_usd)) : null, t.has_tool ? h('span', { class: 't', title: 'Tool calls' }, icon('wrench')) : null, h('span', {}, fmtMs(t.duration_ms))));
      row.querySelectorAll('svg').forEach((s) => { s.style.width = '13px'; s.style.height = '13px'; });
      list.append(row);
    }
  }

  // ---------------------------------------------------------------- one trace
  async function fetchTrace(id) {
    const data = await api('GET', `/system/traces/${id}`);
    summary = data.summary; events = data.events || []; flat = buildFlatTree(events);
  }
  async function selectTrace(id) {
    selectedTraceId = id; selectedNodeId = null;
    list.querySelectorAll('.trace-row').forEach((r) => r.setAttribute('aria-selected', String(r.dataset.id === id)));
    clear(head); head.append(h('div', { class: 'faint', role: 'status' }, 'Loading trace…'));
    stats.classList.add('hidden'); usagePanel.classList.add('hidden');
    clear(tree); tree.append(h('div', { class: 'empty-inline' }, 'Loading…'));
    clear(detail); detail.append(h('div', { class: 'placeholder' }, 'Select a node in the tree'));
    try {
      await fetchTrace(id);
      if (!alive || selectedTraceId !== id) return;
      expanded = defaultExpanded(flat);
      renderHead(); renderStats(); renderUsage(); renderTree();
    } catch (e) { clear(head); head.append(h('div', { class: 'err-text', role: 'alert' }, e.message)); }
  }
  function scheduleTraceRefresh() {
    clearTimeout(traceTimer);
    traceTimer = setTimeout(async () => {
      const id = selectedTraceId; if (!id) return;
      const keep = new Set(expanded); const sel = selectedNodeId;
      try { await fetchTrace(id); if (!alive || id !== selectedTraceId) return; expanded = keep; renderHead(); renderStats(); renderUsage(); renderTree(); if (sel) preserveScroll(detail, () => renderDetail(sel)); } catch { /* next event retries */ }
    }, 300);
  }

  function renderHead() {
    clear(head);
    if (!summary) { head.append(h('div', { class: 'faint' }, 'No summary')); return; }
    put(head, h('h2', {}, h('span', { class: 'play', 'aria-hidden': 'true' }, '▶'), summary.name, statusChip(summary.status)),
      summary.goal ? h('div', { class: 'goalq' }, `“${summary.goal}”`) : null,
      h('div', { class: 'sub' }, h('span', {}, 'trace ', h('code', {}, summary.trace_id)), h('span', {}, 'user ', h('code', {}, summary.user_id || '—')),
        h('span', {}, fmtEpoch(summary.started_at)), h('span', {}, fmtMs(summary.duration_ms)), h('span', {}, `${summary.event_count} events`)));
  }
  function renderStats() {
    const st = traceStats(events);
    const chip = (cls, label, value) => h('span', { class: `stat-chip ${cls}` }, label, ' ', h('b', {}, value));
    clear(stats);
    put(stats, chip('', 'Duration', fmtMs(summary?.duration_ms)), chip('', 'Spans', st.spans),
      chip('llm', 'LLM', `${st.llmCount}${st.llmMs ? ` · ${fmtMs(st.llmMs)}` : ''}`), chip('tool', 'Tools', `${st.toolCount}${st.toolMs ? ` · ${fmtMs(st.toolMs)}` : ''}`),
      summary?.total_tokens ? chip('llm', 'Tokens', fmtTokens(summary.total_tokens)) : null,
      summary?.cost_usd ? chip('cost', 'Cost', fmtCost(summary.cost_usd)) : null,
      st.errorCount ? chip('err', 'Errors', st.errorCount) : null);
    stats.classList.remove('hidden');
  }

  // Clicking a span, expanding, filtering and live refreshes all rebuild the rows: keep the scroll
  // position, and keep keyboard focus on the row the user was on (rebuilding would drop it to <body>).
  // Tokens and cost of every LLM call in this goal (planner, chat, agents and calls made while running tools).
  function renderUsage() {
    const calls = llmCallsFromEvents(events);
    const speech = speechStepsFromEvents(events);
    const has = calls.length > 0 || speech.length > 0;
    clear(usagePanel);
    usageBtn.disabled = !has;
    usageBtn.setAttribute('aria-pressed', String(usageOpen && has));
    usageBtn.classList.toggle('active', usageOpen && has);
    if (!has || !usageOpen) { usagePanel.classList.add('hidden'); return; }
    usagePanel.classList.remove('hidden');
    const t = totalsOf(calls);
    const speechCost = speech.reduce((a, x) => a + (x.cost_usd ?? 0), 0);
    const speechUnpriced = speech.filter((x) => x.cost_usd == null).length;
    const labels = calls.map(callLabel);
    const titles = calls.map((c, i) => `${labels[i]}\n${c.model}${c.span ? ` · in ${c.span}` : ''}`);
    const costs = calls.map((c) => c.cost_usd ?? 0);
    const sw = (cls, text) => h('span', {}, h('span', { class: `sw ${cls}` }), text);
    put(usagePanel,
      h('div', { class: 'usage-head' }, h('h3', { class: 'card-title' }, 'Usage'),
        btn('', { ic: 'close', size: 'sm', title: 'Hide usage', onclick: () => { usageOpen = false; try { localStorage.setItem('pai.traces.usage', '0'); } catch { /* ignore */ } renderUsage(); } })),
      h('div', { class: 'usage-totals' },
        h('div', {}, h('b', {}, String(t.calls)), ' call', t.calls === 1 ? '' : 's'), h('div', {}, h('b', {}, fmtTokens(t.total)), ' tokens'),
        h('div', {}, h('b', {}, fmtCost(t.cost + speechCost)), t.unpriced + speechUnpriced ? ` (+${t.unpriced + speechUnpriced} unpriced)` : '')),
      speech.length ? h('div', {}, h('div', { class: 'faint' }, 'Speech (billed per second of audio / per character)'),
        h('table', { class: 'data usage-calls' }, h('thead', {}, h('tr', {}, ['Step', 'Model', 'Units', 'Cost'].map((x, i) => h('th', { class: i > 1 ? 'num' : '' }, x)))),
          h('tbody', {}, speech.map((x) => h('tr', { title: x.error || '' }, h('td', {}, x.kind, x.error ? ' ⚠' : ''), h('td', { class: 'mono' }, x.model || '—'),
            h('td', { class: 'num' }, x.units == null ? '—' : `${Number(x.units.toFixed(2))} ${x.unit === 'seconds' ? 's' : 'ch'}`), h('td', { class: 'num' }, x.cost_usd == null ? '—' : fmtCost(x.cost_usd))))))) : null,
      ...(!calls.length ? [] : [h('div', {}, h('div', { class: 'faint' }, 'Tokens per call'),
        barChart({ labels, titles, width: 360, height: 150, fmt: fmtTokens, ariaLabel: 'Tokens per LLM call in this trace',
          series: [{ name: 'Prompt', values: calls.map((c) => c.prompt_tokens), cls: 'ch-s0' }, { name: 'Completion', values: calls.map((c) => c.completion_tokens), cls: 'ch-s1' }] }),
        h('div', { class: 'ch-legend' }, sw('ch-s0', 'Prompt'), sw('ch-s1', 'Completion'))),
      h('div', {}, h('div', { class: 'faint' }, 'Cost per call and running total'),
        lineChart({ labels, titles, width: 360, height: 150, fmt: fmtCost, ariaLabel: 'Cost per LLM call and running total for this trace',
          series: [{ name: 'Cost of this call', values: costs, cls: 'ch-s3' }, { name: 'Running total', values: cumulative(costs), cls: 'ch-s2' }] }),
        h('div', { class: 'ch-legend' }, sw('ch-s3', 'Cost of this call'), sw('ch-s2', 'Running total'))),
      h('table', { class: 'data usage-calls' }, h('thead', {}, h('tr', {}, ['Call', 'In', 'Out', 'Cost'].map((x, i) => h('th', { class: i ? 'num' : '' }, x)))),
        h('tbody', {}, calls.map((c, i) => h('tr', { title: titles[i] }, h('td', {}, labels[i]), h('td', { class: 'num' }, fmtTokens(c.prompt_tokens)),
          h('td', { class: 'num' }, fmtTokens(c.completion_tokens)), h('td', { class: 'num' }, c.cost_usd == null ? '—' : fmtCost(c.cost_usd))))))]));
  }

  function renderTree() {
    const hadFocus = tree.contains(document.activeElement);
    const focusId = hadFocus ? document.activeElement.dataset?.id : null;
    preserveScroll(tree, buildTree);
    if (focusId) tree.querySelector(`.tree-row[data-id="${CSS.escape(focusId)}"]`)?.focus({ preventScroll: true });
  }
  function buildTree() {
    clear(tree);
    if (!flat.length) { tree.append(h('div', { class: 'empty-inline' }, 'No events')); return; }
    const rows = visibleRows(flat, expanded, treeFilter);
    if (!rows.length) { tree.append(h('div', { class: 'empty-inline' }, 'No matches')); return; }
    const { start, totalMs } = traceBounds(events);
    const toggle = (n) => { if (!n.children.length) return; expanded.has(n.id) ? expanded.delete(n.id) : expanded.add(n.id); renderTree(); };
    for (const { node, depth } of rows) {
      const fam = kindFamily(node.kind === 'span.start' ? 'span.start' : node.kind);
      const has = node.children.length > 0;
      const g = barGeometry(node, start, totalMs);
      const fill = h('div', { class: `bar-fill bar-${fam}` }); fill.style.left = `${g.left}%`; fill.style.width = `${g.width}%`;
      const indent = h('span', { class: 'indent' }); indent.style.width = `${depth * 14}px`; indent.style.flex = 'none';
      const chev = h('span', { class: `chev${has ? '' : ' leaf'}`, 'aria-hidden': 'true', onclick: (e) => { e.stopPropagation(); toggle(node); } }, has ? (expanded.has(node.id) ? '▼' : '▶') : '·');
      const row = h('div', { class: `tree-row${node.error ? ' error' : ''}`, role: 'treeitem', tabindex: '0', 'data-id': node.id, 'aria-level': depth + 1, 'aria-selected': String(selectedNodeId === node.id),
        'aria-expanded': has ? String(expanded.has(node.id)) : null, onclick: () => selectNode(node.id),
        onkeydown: (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectNode(node.id); }
          else if (e.key === 'ArrowRight' && has && !expanded.has(node.id)) { e.preventDefault(); toggle(node); }
          else if (e.key === 'ArrowLeft' && has && expanded.has(node.id)) { e.preventDefault(); toggle(node); }
          else if (e.key === 'ArrowDown') { e.preventDefault(); row.nextElementSibling?.focus(); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); row.previousElementSibling?.focus(); }
        } },
        chev, indent, h('span', { class: `tico k-${fam}` }, icon(KIND_ICON[fam])), h('span', { class: 'name', title: node.name }, node.name),
        node.incomplete ? statusChip('failed', 'incomplete') : null, h('div', { class: 'bar-track' }, fill), h('span', { class: 'dur' }, fmtMs(node.duration_ms)));
      tree.append(row);
    }
  }

  // ---------------------------------------------------------------- node detail
  function selectNode(id) { selectedNodeId = id; renderTree(); renderDetail(id); }
  const section = (title, text, extra = {}) => h('div', { class: 'detail-section' },
    h('div', { class: 'section-head' }, h('span', { class: 'section-title' }, title), copyTextButton(() => text)), codeBlock(text, extra));
  const json = (o) => JSON.stringify(o, null, 2);
  const kvs = (pairs) => h('dl', { class: 'kv' }, pairs.map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]).flat());

  function renderDetail(id) {
    clear(detail);
    const entry = flat.find((t) => t.node.id === id);
    if (!entry) { detail.append(h('div', { class: 'placeholder' }, 'Node not found')); return; }
    const n = entry.node; const fam = kindFamily(n.kind === 'span.start' ? 'span.start' : n.kind);
    put(detail, h('div', { class: 'detail-section' },
      h('div', { class: 'detail-title' }, h('span', { class: `tico k-${fam}` }, icon(KIND_ICON[fam])), n.name),
      kvs([['Kind', n.kind], ['Started', fmtEpoch(n.startTs)], ['Duration', fmtMs(n.duration_ms)], ['Node ID', h('code', {}, n.id)], n.parentId ? ['Parent', h('code', {}, n.parentId)] : null].filter(Boolean))));
    detail.querySelectorAll('.tico svg').forEach((s) => { s.style.width = '16px'; s.style.height = '16px'; });
    if (n.error) detail.append(section('✖ Error', n.error, { cls: 'err' }));
    if (n.kind === 'llm') llmSections(n); else if (n.kind === 'tool') toolSections(n); else spanSections(n);
  }
  function llmSections(n) {
    const req = n.attributes?.request || {}; const res = n.attributes?.response || {};
    const usage = res.usage || (res.extra && res.extra.usage) || null;
    detail.append(h('div', { class: 'detail-section' }, kvs([['Provider', req.provider || res.provider || '(unknown)'], ['Model', req.model || res.model || '(unknown)']])));
    const cost = res.cost_usd;
    if (usage) detail.append(h('div', { class: 'detail-section' }, h('div', { class: 'section-title' }, 'Token usage & cost'),
      h('div', { class: 'usage-grid four' }, [['Prompt', usage.prompt_tokens], ['Completion', usage.completion_tokens], ['Total', usage.total_tokens],
        ['Cost', cost == null ? '—' : fmtCost(cost)]].map(([l, v]) =>
        h('div', { class: 'usage-chip' }, h('div', { class: 'u-label' }, l), h('div', { class: 'u-value' }, v ?? '—')))),
      res.cost_source || res.route ? h('div', { class: 'faint' }, [res.route ? `role: ${res.route}` : null, res.cost_source ? `cost: ${res.cost_source === 'configured' ? 'tokens × configured price' : 'reported by the provider'}` : (cost == null ? 'cost unknown (no price configured)' : null)].filter(Boolean).join(' · ')) : null));
    const parts = sectionShares(res.prompt_breakdown);
    if (parts.length) {
      const bar = h('div', { class: 'bd-bar', role: 'img', 'aria-label': 'Prompt tokens by part' });
      for (const p of parts) { const seg = h('div', { class: `bd-seg sec-${p.name}`, title: `${p.label}: ${p.tokens} tokens` }); seg.style.width = `${p.pct}%`; bar.append(seg); }
      detail.append(h('div', { class: 'detail-section' }, h('div', { class: 'section-title' }, 'Prompt tokens by part'), bar,
        h('div', { class: 'bd-legend' }, parts.map((p) => h('span', {}, h('span', { class: `dot sec-${p.name}` }), `${p.label} `, h('b', {}, `${fmtTokens(p.tokens)} · ${p.pct.toFixed(0)}%`)))),
        h('div', { class: 'faint' }, 'Estimated split (the provider reports one prompt total); the parts add up to the real prompt tokens.')));
    }
    if (req.prompt) detail.append(section('Prompt', req.prompt, { cls: 'prompt' }));
    if (res.response) detail.append(section('Response', res.response, { cls: 'response' }));
    const other = llmOther(req, res);
    if (Object.keys(other).length) detail.append(section('Other attributes', json(other)));
  }
  function toolSections(n) {
    const call = n.attributes?.call || {}; const result = n.attributes?.result || {};
    detail.append(h('div', { class: 'detail-section' }, kvs([['Capability', call.capability || result.capability || '(unknown)'], ['Plugin', call.plugin || result.plugin || '(unknown)'],
      ['Device', h('code', {}, call.device_id || result.device_id || '(unknown)')]])));
    if (Object.keys(call.parameters || {}).length) detail.append(section('Parameters (input)', json(call.parameters)));
    if (Object.keys(result.result || {}).length) detail.append(section('Result (output)', json(result.result)));
  }
  function spanSections(n) {
    const attrs = n.attributes || {};
    detail.append(Object.keys(attrs).length ? section('Attributes', json(attrs)) : h('div', { class: 'placeholder' }, 'No attributes'));
  }

  // ---------------------------------------------------------------- live
  function startStream() {
    if (stream) return;
    stream = openTraceStream((ev) => { scheduleList(); if (selectedTraceId && ev.trace_id === selectedTraceId) scheduleTraceRefresh(); });
  }
  function stopStream() { stream?.close(); stream = null; }

  await loadTraces();
  if (fLive.checked) startStream();
  return { unmount() { alive = false; stopStream(); clearTimeout(listTimer); clearTimeout(traceTimer); unsplit.forEach((f) => f()); } };
}

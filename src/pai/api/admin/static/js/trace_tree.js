// Pure trace logic (no DOM) — ported 1:1 from the legacy page so behaviour is identical, and unit-testable.
// A trace is a flat list of events {span_id, parent_span_id, kind, name, timestamp(s), duration_ms, attributes, error}.
// Kinds: trace.start/end, span.start/end, llm.request/llm.response, tool.call/tool.result, error, log.

/** Visual family of a node kind (drives colour + icon). */
export function kindFamily(kind) {
  if (kind === 'trace.start' || kind === 'trace.end') return 'trace';
  if (kind === 'llm') return 'llm';
  if (kind === 'tool') return 'tool';
  if (kind === 'error') return 'error';
  if (kind === 'log') return 'log';
  return 'span';
}

/**
 * Build the flat tree: one node per span, plus synthetic children for paired llm.request→llm.response,
 * tool.call→tool.result and standalone error/log events. Returns [{ node, depth }] in display order.
 */
export function buildFlatTree(events) {
  if (!events || !events.length) return [];

  const spans = new Map();
  for (const ev of events) {
    if (!spans.has(ev.span_id)) spans.set(ev.span_id, { start: null, end: null, points: [] });
    const entry = spans.get(ev.span_id);
    if (ev.kind === 'span.start' || ev.kind === 'trace.start') entry.start = ev;
    else if (ev.kind === 'span.end' || ev.kind === 'trace.end') entry.end = ev;
    else entry.points.push(ev);
  }

  const nodesById = new Map();
  for (const [spanId, { start, end }] of spans) {
    const startTs = start?.timestamp ?? end?.timestamp ?? 0;
    const endTs = end?.timestamp ?? startTs;
    nodesById.set(spanId, {
      id: spanId,
      name: start?.name ?? end?.name ?? 'unknown',
      kind: start?.kind ?? end?.kind ?? 'span.start',
      startTs, endTs,
      duration_ms: end?.duration_ms ?? Math.max(0, (endTs - startTs) * 1000),
      attributes: { ...(start?.attributes || {}), ...(end?.attributes || {}) },
      error: end?.error || start?.error || null,
      parentId: start?.parent_span_id ?? end?.parent_span_id ?? null,
      rawEvents: [start, end].filter(Boolean),
      children: [],
    });
  }

  for (const node of nodesById.values()) {
    const entry = spans.get(node.id);
    if (!entry) continue;
    const points = [...entry.points].sort((a, b) => a.timestamp - b.timestamp);
    const consumed = new Set();
    let synthIdx = 0;
    for (let i = 0; i < points.length; i++) {
      if (consumed.has(i)) continue;
      const ev = points[i];
      if (ev.kind === 'llm.request' || ev.kind === 'tool.call') {
        const isLlm = ev.kind === 'llm.request';
        const respKind = isLlm ? 'llm.response' : 'tool.result';
        let paired = null;
        for (let j = i + 1; j < points.length; j++) {
          if (consumed.has(j)) continue;
          const ev2 = points[j];
          if (ev2.kind === respKind && ev2.name === ev.name) { paired = ev2; consumed.add(i); consumed.add(j); break; }
        }
        const kind = isLlm ? 'llm' : 'tool';
        const startTs = ev.timestamp;
        const endTs = paired?.timestamp ?? ev.timestamp;
        node.children.push({
          id: `${node.id}::${kind}::${synthIdx++}`, name: ev.name, kind, startTs, endTs,
          duration_ms: paired?.duration_ms ?? Math.max(0, (endTs - startTs) * 1000),
          attributes: isLlm ? { request: ev.attributes, response: paired?.attributes || {} } : { call: ev.attributes, result: paired?.attributes || {} },
          error: paired?.error || ev.error || null,
          parentId: node.id, rawEvents: [ev, paired].filter(Boolean), children: [], incomplete: !paired,
        });
        if (!paired) consumed.add(i);
        continue;
      }
      if (ev.kind === 'error' || ev.kind === 'log') {
        consumed.add(i);
        node.children.push({
          id: `${node.id}::${ev.kind}::${synthIdx++}`, name: ev.name, kind: ev.kind, startTs: ev.timestamp, endTs: ev.timestamp,
          duration_ms: 0, attributes: ev.attributes || {}, error: ev.error || null, parentId: node.id, rawEvents: [ev], children: [],
        });
      }
    }
    node.children.sort((a, b) => a.startTs - b.startTs);
  }

  const roots = [];
  for (const node of nodesById.values()) {
    if (node.parentId && nodesById.has(node.parentId)) nodesById.get(node.parentId).children.push(node);
    else roots.push(node);
  }
  const sortRec = (arr) => { arr.sort((a, b) => a.startTs - b.startTs); arr.forEach((n) => sortRec(n.children)); };
  sortRec(roots);

  const out = [];
  const walk = (nodes, depth) => { for (const n of nodes) { out.push({ node: n, depth }); walk(n.children, depth + 1); } };
  walk(roots, 0);
  return out;
}

export function traceBounds(events) {
  if (!events.length) return { start: 0, totalMs: 0 };
  let lo = Infinity, hi = -Infinity;
  for (const e of events) { if (e.timestamp < lo) lo = e.timestamp; if (e.timestamp > hi) hi = e.timestamp; }
  return { start: lo, totalMs: Math.max(1, (hi - lo) * 1000) };
}

/** Rows to display: a node shows if its parent is expanded and (when filtering) its name/id matches. */
export function visibleRows(flat, expanded, filter = '') {
  const rows = [];
  const stack = [];
  const needle = filter.trim().toLowerCase();
  for (const { node, depth } of flat) {
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
    const parentVisible = stack.length === 0 || (stack[stack.length - 1].id && expanded.has(stack[stack.length - 1].id));
    const matches = !needle || node.name.toLowerCase().includes(needle) || node.id.toLowerCase().includes(needle);
    if (parentVisible && matches) rows.push({ node, depth });
    stack.push({ id: node.id, depth });
  }
  return rows;
}

/** Waterfall bar geometry in percent of the whole trace. */
export function barGeometry(node, traceStart, totalMs) {
  const relStart = ((node.startTs - traceStart) * 1000) / totalMs;
  const left = Math.max(0, Math.min(100, relStart * 100));
  const width = Math.max(0.5, Math.min(100 - left, (node.duration_ms / totalMs) * 100));
  return { left, width };
}

/** Aggregates shown in the stats strip. */
export function traceStats(events) {
  let llmCount = 0, llmMs = 0, toolCount = 0, toolMs = 0, errorCount = 0, spans = 0;
  for (const ev of events) {
    if (ev.kind === 'llm.response') { llmCount++; llmMs += ev.duration_ms || 0; }
    else if (ev.kind === 'tool.result') { toolCount++; toolMs += ev.duration_ms || 0; }
    else if (ev.kind === 'error') errorCount++;
    else if (ev.kind === 'span.start') spans++;
  }
  return { llmCount, llmMs, toolCount, toolMs, errorCount, spans };
}

export function defaultExpanded(flat) {
  const s = new Set();
  for (const { node, depth } of flat) if (depth === 0 && node.children.length) s.add(node.id);
  return s;
}

export function collapseAllKeepRoots(flat) {
  const s = new Set();
  for (const { node, depth } of flat) if (depth === 0) s.add(node.id);
  return s;
}

export function expandAll(flat) {
  return new Set(flat.map(({ node }) => node.id));
}

/** Legacy "other attributes" bags for an LLM node (request/response keys not shown elsewhere). */
export function llmOther(req, res) {
  const other = {};
  for (const [k, v] of Object.entries(req)) if (k !== 'prompt' && k !== 'provider' && k !== 'model') other[`req.${k}`] = v;
  // usage, cost and the prompt breakdown have their own sections in the detail pane
  const shown = ['response', 'provider', 'model', 'usage', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'cost_usd', 'cost_source', 'route', 'prompt_breakdown'];
  for (const [k, v] of Object.entries(res)) if (!shown.includes(k)) other[`res.${k}`] = v;
  return other;
}

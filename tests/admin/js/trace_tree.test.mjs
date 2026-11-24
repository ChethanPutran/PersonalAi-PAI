import test from 'node:test';
import assert from 'node:assert/strict';
import { buildFlatTree, visibleRows, barGeometry, traceBounds, traceStats, defaultExpanded, collapseAllKeepRoots, expandAll, kindFamily, llmOther } from '../../../src/pai/api/admin/static/js/trace_tree.js';
import { fmtMs, fmtEpoch, humanDuration } from '../../../src/pai/api/admin/static/js/format.js';

const ev = (span, parent, kind, name, ts, extra = {}) => ({ trace_id: 't', span_id: span, parent_span_id: parent, kind, name, timestamp: ts, attributes: {}, ...extra });
const EVENTS = [
  ev('root', null, 'trace.start', 'run', 100),
  ev('plan', 'root', 'span.start', 'planner', 100.1),
  ev('plan', 'root', 'llm.request', 'llm:x', 100.2, { attributes: { prompt: 'P', model: 'm' } }),
  ev('plan', 'root', 'llm.response', 'llm:x', 101.2, { duration_ms: 1000, attributes: { response: 'R', usage: { total_tokens: 3 } } }),
  ev('plan', 'root', 'span.end', 'planner', 101.3, { duration_ms: 1200 }),
  ev('exec', 'root', 'span.start', 'dag', 101.4),
  ev('exec', 'root', 'tool.call', 'tool:cam', 101.5, { attributes: { capability: 'cam' } }),
  ev('exec', 'root', 'tool.result', 'tool:cam', 102.5, { duration_ms: 1000, error: 'boom', attributes: { result: { ok: false } } }),
  ev('exec', 'root', 'tool.call', 'tool:never', 102.6),                      // no result -> incomplete
  ev('exec', 'root', 'error', 'dag.failed', 102.7, { error: 'x' }),
  ev('exec', 'root', 'log', 'note', 102.8),
  ev('exec', 'root', 'span.end', 'dag', 103, { duration_ms: 1600 }),
  ev('root', null, 'trace.end', 'run', 103.1, { duration_ms: 3100, attributes: { status: 'failed' } }),
];

test('buildFlatTree pairs llm/tool calls, keeps incomplete ones and nests spans', () => {
  const flat = buildFlatTree(EVENTS);
  const names = flat.map(({ node, depth }) => `${depth}:${node.kind}:${node.name}`);
  assert.deepEqual(names, ['0:trace.start:run', '1:span.start:planner', '2:llm:llm:x', '1:span.start:dag', '2:tool:tool:cam', '2:tool:tool:never', '2:error:dag.failed', '2:log:note']);
  const llm = flat[2].node;
  assert.equal(llm.duration_ms, 1000); assert.equal(llm.attributes.request.prompt, 'P'); assert.equal(llm.attributes.response.usage.total_tokens, 3);
  assert.equal(flat[4].node.error, 'boom');
  assert.equal(flat[5].node.incomplete, true);
  assert.equal(flat[0].node.duration_ms, 3100);
});

test('buildFlatTree handles empty input and orphan spans', () => {
  assert.deepEqual(buildFlatTree([]), []);
  const flat = buildFlatTree([ev('a', 'missing-parent', 'span.start', 'orphan', 5)]);
  assert.equal(flat.length, 1); assert.equal(flat[0].depth, 0);
});

test('visibleRows honours expansion state and the name filter', () => {
  const flat = buildFlatTree(EVENTS);
  assert.equal(visibleRows(flat, new Set()).length, 1);                           // collapsed: root only
  assert.equal(visibleRows(flat, defaultExpanded(flat)).length, 3);               // root expanded: root + 2 spans
  assert.equal(visibleRows(flat, expandAll(flat)).length, flat.length);
  assert.deepEqual(visibleRows(flat, expandAll(flat), 'CAM').map((r) => r.node.name), ['tool:cam']);
  assert.equal(collapseAllKeepRoots(flat).size, 1);
});

test('barGeometry stays inside the track and has a minimum width', () => {
  const { start, totalMs } = traceBounds(EVENTS);
  assert.equal(start, 100); assert.ok(Math.abs(totalMs - 3100) < 1e-6);
  const g = barGeometry({ startTs: 100.1, duration_ms: 1200 }, start, totalMs);
  assert.ok(g.left > 0 && g.left + g.width <= 100);
  assert.equal(barGeometry({ startTs: 103, duration_ms: 0 }, start, totalMs).width >= 0.5, true);
});

test('traceStats / kindFamily / llmOther', () => {
  const st = traceStats(EVENTS);
  assert.deepEqual([st.llmCount, st.toolCount, st.errorCount, st.spans], [1, 1, 1, 2]);
  assert.equal(st.llmMs, 1000);
  assert.equal(kindFamily('trace.end'), 'trace'); assert.equal(kindFamily('span.start'), 'span'); assert.equal(kindFamily('llm'), 'llm');
  assert.deepEqual(llmOther({ prompt: 'p', model: 'm', temperature: 0.2 }, { response: 'r', usage: {}, finish_reason: 'stop' }), { 'req.temperature': 0.2, 'res.finish_reason': 'stop' });
});

test('legacy-compatible formatters', () => {
  assert.equal(fmtMs(0.42), '0.42ms'); assert.equal(fmtMs(83.4), '83ms'); assert.equal(fmtMs(1200), '1.20s'); assert.equal(fmtMs(null), '');
  assert.equal(humanDuration(14), '14s'); assert.equal(humanDuration(125), '2m 5s'); assert.equal(humanDuration(3700), '1h 1m');
  assert.equal(fmtEpoch(0), '');
});

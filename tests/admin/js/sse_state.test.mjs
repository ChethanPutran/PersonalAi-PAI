import test from 'node:test';
import assert from 'node:assert/strict';
import { SseParser, backoffDelay, openStream } from '../../../src/pai/api/admin/static/js/sse.js';
import { appendEvent, matchesFilter, summarizeEvent, classifyEvent, affectsLists, createStore, MAX_EVENTS } from '../../../src/pai/api/admin/static/js/state.js';
import { fmtDuration, shortId, truncate, fmtAgo } from '../../../src/pai/api/admin/static/js/format.js';

test('SSE parser handles events split across arbitrary chunks, comments, ids and multi-line data', () => {
  const text = ': connected\n\nretry: 3000\n\nid: 1\nevent: dag.started\ndata: {"a":1}\n\nid: 2\nevent: x\ndata: line1\ndata: line2\n\n';
  for (const step of [1, 3, 7, text.length]) {
    const p = new SseParser(); const out = [];
    for (let i = 0; i < text.length; i += step) out.push(...p.push(text.slice(i, i + step)));
    assert.deepEqual(out.map((e) => [e.event, e.data, e.id]), [['dag.started', '{"a":1}', '1'], ['x', 'line1\nline2', '2']], `step ${step}`);
  }
});

test('SSE parser tolerates CRLF', () => {
  const p = new SseParser();
  assert.deepEqual(p.push('event: a\r\ndata: 1\r\n\r\n').map((e) => e.data), ['1']);
});

test('reconnect backoff doubles and is capped', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 9].map((n) => backoffDelay(n)), [1000, 2000, 4000, 8000, 15000, 15000, 15000]);
});

test('openStream delivers parsed events, reports status and reconnects after a drop', async () => {
  const enc = new TextEncoder(); let calls = 0; const events = []; const statuses = [];
  const fetchImpl = async () => {
    calls += 1;
    const body = new ReadableStream({ start(c) { c.enqueue(enc.encode(`event: task.created\ndata: {"n":${calls}}\n\n`)); c.close(); } });
    return { ok: true, status: 200, body };
  };
  const s = openStream('http://x/events', { fetchImpl, onEvent: (e) => events.push(e), onStatus: (st) => statuses.push(st) });
  await new Promise((r) => setTimeout(r, 1300));                       // first drop -> 1 s backoff -> second connection
  s.close();
  assert.ok(calls >= 2, `expected a reconnect, calls=${calls}`);
  assert.deepEqual(events.slice(0, 2).map((e) => e.data.n), [1, 2]);
  assert.ok(statuses.includes('live') && statuses.includes('retrying'));
});

test('openStream stops (no retry storm) on 401/403', async () => {
  let calls = 0; const statuses = [];
  const s = openStream('http://x', { fetchImpl: async () => { calls += 1; return { ok: false, status: 401 }; }, onEvent() {}, onStatus: (st) => statuses.push(st) });
  await new Promise((r) => setTimeout(r, 200)); s.close();
  assert.equal(calls, 1); assert.ok(statuses.includes('closed'));
});

test('the event log is bounded and never mutated', () => {
  let log = []; const before = log;
  for (let i = 0; i < MAX_EVENTS + 25; i++) log = appendEvent(log, { event: 'task.x', data: { i } });
  assert.equal(log.length, MAX_EVENTS); assert.equal(log[0].data.i, 25); assert.equal(before.length, 0);
});

test('filters, summaries and classification', () => {
  const e = { event: 'task.failed', data: { task_id: 'abcdef123456', capability: 'camera.capture', error: 'busy' } };
  assert.ok(matchesFilter(e, { text: 'camera' })); assert.ok(!matchesFilter(e, { text: 'nope' }));
  assert.ok(matchesFilter(e, { kinds: new Set(['task']) })); assert.ok(!matchesFilter(e, { kinds: new Set(['dag']) }));
  assert.match(summarizeEvent('task.failed', e.data), /abcdef12 · camera.capture · error: busy/);
  assert.equal(classifyEvent('task.failed'), 'bad'); assert.equal(classifyEvent('dag.completed'), 'ok'); assert.equal(classifyEvent('task.started'), '');
  assert.ok(affectsLists('task.cancelled') && affectsLists('dag.task_finished') && !affectsLists('chat.delta'));
});

test('store notifies subscribers', () => {
  const st = createStore({ a: 1 }); const seen = [];
  const off = st.subscribe((s) => seen.push(s.a)); st.set({ a: 2 }); off(); st.set({ a: 3 });
  assert.deepEqual(seen, [2]);
});

test('formatters', () => {
  assert.equal(fmtDuration(null), '—'); assert.equal(fmtDuration(450), '450 ms'); assert.equal(fmtDuration(2500), '2.5 s');
  assert.equal(fmtDuration(95_000), '1m 35s'); assert.equal(shortId('abcdefghijklmnop'), 'abcdefgh'); assert.equal(shortId('abcdefghijkl'), 'abcdefghijkl'); assert.equal(truncate('x'.repeat(100), 10).length, 10);
  assert.equal(fmtAgo(new Date(Date.now() - 90_000).toISOString()), '1m ago');
});

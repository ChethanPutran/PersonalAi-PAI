// Pure data prep behind the token/cost charts.
import test from 'node:test';
import assert from 'node:assert/strict';
import { niceTicks, cumulative, llmCallsFromEvents, callLabel, totalsOf, bucketLabel, seriesArrays, goalBars }
  from '../../../src/pai/api/admin/static/js/usage_data.js';
import { labelStride } from '../../../src/pai/api/admin/static/js/charts.js';

test('nice ticks start at 0, step by 1/2/2.5/5 x 10^n and cover the max', () => {
  assert.deepEqual(niceTicks(0), [0, 1]); assert.deepEqual(niceTicks(NaN), [0, 1]); assert.deepEqual(niceTicks(-5), [0, 1]);
  for (const max of [0.0003, 0.5, 7, 100, 1234, 98765]) {
    const t = niceTicks(max);
    assert.equal(t[0], 0); assert.ok(t[t.length - 1] >= max, `covers ${max}`); assert.ok(t.length >= 2 && t.length <= 9, `${max}: ${t.length} ticks`);
    const step = t[1] - t[0]; t.forEach((v, i) => assert.ok(Math.abs(v - i * step) < step * 1e-9));
  }
  assert.deepEqual(niceTicks(100, 4), [0, 25, 50, 75, 100]);
});

test('cumulative sums and treats missing values as zero', () => {
  assert.deepEqual(cumulative([1, 2, 3]), [1, 3, 6]);
  assert.deepEqual(cumulative([1, null, NaN, 2]), [1, 1, 1, 3]);
  assert.deepEqual(cumulative([]), []);
});

const ev = (kind, o = {}) => ({ kind, span_id: 's1', timestamp: 1, attributes: {}, ...o });

test('LLM calls come from llm.response events in time order, with the span they ran under', () => {
  const events = [
    ev('span.start', { span_id: 'a', name: 'planner.decompose' }), ev('span.start', { span_id: 'b', name: 'tool.run' }),
    ev('llm.response', { span_id: 'b', timestamp: 5, duration_ms: 120, attributes: { model: 'm2', route: 'agent', prompt_tokens: 40, completion_tokens: 10, total_tokens: 50, cost_usd: 0.002 } }),
    ev('llm.response', { span_id: 'a', timestamp: 2, attributes: { model: 'm1', route: 'planner', usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 } } }),
    ev('llm.request', { span_id: 'a', timestamp: 1 }),                                        // not a response
    ev('llm.response', { span_id: 'a', timestamp: 3, error: 'boom', attributes: { total_tokens: 9 } }),   // failed call
    ev('llm.response', { span_id: 'a', timestamp: 4, attributes: { model: 'x' } }),                       // reported no usage
  ];
  const calls = llmCallsFromEvents(events);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls.map((c) => [c.route, c.span, c.total_tokens, c.cost_usd]), [['planner', 'planner.decompose', 120, null], ['agent', 'tool.run', 50, 0.002]]);
  assert.deepEqual(llmCallsFromEvents(null), []);
});

test('labels, totals (unpriced calls are counted, not costed)', () => {
  assert.equal(callLabel({ route: 'planner' }, 0), '1 · planner');
  assert.equal(callLabel({ span: 'orchestrator.chat_reply' }, 2), '3 · chat_reply');
  assert.equal(callLabel({ model: 'm' }, 1), '2 · m');
  const t = totalsOf([{ prompt_tokens: 10, completion_tokens: 5, total_tokens: 15, cost_usd: 0.5 }, { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, cost_usd: null }]);
  assert.deepEqual(t, { calls: 2, prompt: 11, completion: 6, total: 17, cost: 0.5, unpriced: 1 });
});

test('bucket labels and series arrays', () => {
  const t = new Date(2026, 10, 24, 9, 5).getTime() / 1000;
  assert.equal(bucketLabel(t, 60), '09:05'); assert.equal(bucketLabel(t, 3600), '09:05'); assert.equal(bucketLabel(t, 86400), '11-24');
  const a = seriesArrays({ bucket_seconds: 3600, points: [{ t, prompt_tokens: 3, completion_tokens: 2, total_tokens: 5, cost_usd: 0.1, calls: 1 }] });
  assert.deepEqual([a.prompt, a.completion, a.total, a.cost, a.calls], [[3], [2], [5], [0.1], [1]]);
  assert.deepEqual(seriesArrays(null).labels, []);
});

test('goal bars: only goals that used tokens/cost, oldest first, capped', () => {
  const traces = [
    { trace_id: 'c', goal: 'third', started_at: 3, total_tokens: 30, cost_usd: 0.3 },
    { trace_id: 'a', goal: 'first goal with a very long name indeed', started_at: 1, total_tokens: 10, cost_usd: 0.1 },
    { trace_id: 'z', goal: 'no llm', started_at: 2, total_tokens: 0, cost_usd: 0 },
    { trace_id: 'b', name: 'named', started_at: 2.5, total_tokens: 20 },
  ];
  const g = goalBars(traces);
  assert.deepEqual(g.ids, ['a', 'b', 'c']);
  assert.equal(g.labels[0].length, 18); assert.equal(g.labels[1], 'named');
  assert.deepEqual(g.tokens, [10, 20, 30]); assert.deepEqual(g.cost, [0.1, 0, 0.3]);
  assert.deepEqual(goalBars(traces, 2).ids, ['b', 'c']);
  assert.deepEqual(goalBars(null).ids, []);
});

test('x labels are thinned so they do not overlap', () => {
  assert.equal(labelStride(5), 1); assert.equal(labelStride(8), 1); assert.equal(labelStride(24), 3); assert.equal(labelStride(60), 8); assert.equal(labelStride(0), 1);
});

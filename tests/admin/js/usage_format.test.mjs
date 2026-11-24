// Formatting + prompt-breakdown helpers behind the Usage page and the trace detail.
import test from 'node:test';
import assert from 'node:assert/strict';
import { fmtCost, fmtTokens, sectionShares, sectionLabel } from '../../../src/pai/api/admin/static/js/format.js';
import { llmOther } from '../../../src/pai/api/admin/static/js/trace_tree.js';

test('cost: unknown is a dash, never $0; small amounts stay readable; large ones use cents', () => {
  assert.equal(fmtCost(null), '—'); assert.equal(fmtCost(undefined), '—'); assert.equal(fmtCost(NaN), '—');
  assert.equal(fmtCost(0), '$0.00');
  assert.equal(fmtCost(0.00042), '$0.00042');
  assert.equal(fmtCost(0.0105), '$0.0105');
  assert.equal(fmtCost(0.5), '$0.50');
  assert.equal(fmtCost(12.3456), '$12.35');
  assert.equal(fmtCost(250), '$250.00');
  assert.equal(fmtCost(-0.25), '-$0.25');
  assert.equal(fmtCost(0.0000001), '<$0.000001');
});

test('tokens are compact', () => {
  assert.deepEqual([5, 999, 1000, 1234, 15000, 2500000].map(fmtTokens), ['5', '999', '1k', '1.2k', '15k', '2.5M']);
  assert.equal(fmtTokens(null), '—');
});

test('section shares: percentages of the total, known parts first, zeros dropped', () => {
  const s = sectionShares({ memory: 600, user_prompt: 200, system_prompt: 200, history: 0 });
  assert.deepEqual(s.map((x) => x.name), ['system_prompt', 'memory', 'user_prompt']);
  assert.deepEqual(s.map((x) => x.pct), [20, 60, 20]);
  assert.equal(s[1].label, 'Memory');
  assert.deepEqual(sectionShares({}), []); assert.deepEqual(sectionShares(null), []); assert.deepEqual(sectionShares({ a: 0 }), []);
});

test('unknown parts are still shown, with a readable label', () => {
  const s = sectionShares({ tool_data: 10, custom_part: 10 });
  assert.deepEqual(s.map((x) => x.name), ['tool_data', 'custom_part']);
  assert.equal(sectionLabel('custom_part'), 'custom part');
  assert.equal(sectionLabel('tool_data'), 'Tool data');
});

test('the LLM detail "other attributes" no longer repeats tokens, cost or the breakdown', () => {
  const other = llmOther({ prompt: 'p', model: 'm', temperature: 1 },
    { response: 'r', usage: {}, prompt_tokens: 1, total_tokens: 2, cost_usd: 0.1, cost_source: 'configured', route: 'chat',
      prompt_breakdown: { memory: 1 }, finish_reason: 'stop' });
  assert.deepEqual(Object.keys(other).sort(), ['req.temperature', 'res.finish_reason']);
});

import { fmtSize, fmtModelPrice } from '../../../src/pai/api/admin/static/js/format.js';

test('sizes: context windows read naturally', () => {
  assert.deepEqual([128000, 1100000, 1000000, 400000, 4096, 33000].map(fmtSize), ['128K', '1.1M', '1M', '400K', '4K', '33K']);
  assert.equal(fmtSize(null), '—'); assert.equal(fmtSize(NaN), '—');
});

test('model prices: tokens, audio minutes, characters, and unknown', () => {
  assert.equal(fmtModelPrice({ input_per_million: 0.1, output_per_million: 0.5 }), '$0.10 in · $0.50 out /1M');
  assert.equal(fmtModelPrice({ input_per_million: 2, output_per_million: 10 }), '$2.00 in · $10.00 out /1M');
  assert.equal(fmtModelPrice({ per_minute_cost: 0.0017 }), '$0.0017 /min of audio');
  assert.equal(fmtModelPrice({ per_1k_chars_cost: 0.015 }), '$0.015 /1K chars');
  assert.equal(fmtModelPrice({ per_minute_cost: null }), 'no price');
  assert.equal(fmtModelPrice({ name: 'x' }), 'no price');
  assert.equal(fmtModelPrice(null), '—');
});

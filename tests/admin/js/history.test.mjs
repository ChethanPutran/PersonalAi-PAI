// Pure helpers behind the history-aware Events and DAG views.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeHistory, windowLabel, RANGES } from '../../../src/pai/api/admin/static/js/history.js';
import { fmtWhen } from '../../../src/pai/api/admin/static/js/format.js';

const e = (event, id, at = 't') => ({ event, data: { id }, at });

test('history then live events, oldest first, each event once', () => {
  const past = [e('task.created', 1), e('task.started', 1)];
  const live = [e('task.started', 1), e('task.completed', 1), e('task.created', 2)];       // the first live one is already in history
  assert.deepEqual(mergeHistory(past, live).map((x) => `${x.event}:${x.data.id}`), ['task.created:1', 'task.started:1', 'task.completed:1', 'task.created:2']);
});

test('two genuinely identical events are both kept (only history/live overlap is removed)', () => {
  const same = e('task.stream', 7);
  assert.equal(mergeHistory([same, same], [same, same, same]).length, 3);                   // 2 in history + 1 new live
  assert.equal(mergeHistory([], [same, same]).length, 2);
});

test('the merged log keeps the newest `max`', () => {
  const past = Array.from({ length: 5 }, (_, i) => e('a.b', i));
  assert.deepEqual(mergeHistory(past, [e('a.b', 9)], 3).map((x) => x.data.id), [3, 4, 9]);
  assert.deepEqual(mergeHistory([], []), []);
});

test('ranges: live only, last hour, 24h, everything retained', () => {
  assert.deepEqual(RANGES.map((r) => r[1]), [0, 1 / 24, 1, null]);
});

test('window labels', () => {
  assert.equal(windowLabel(null, 7), 'all, 7 days'); assert.equal(windowLabel(null, null), 'all retained');
  assert.equal(windowLabel(0), 'active only'); assert.equal(windowLabel(1), 'last 24 hours');
  assert.equal(windowLabel(1 / 24), 'last 1 hour'); assert.equal(windowLabel(3), 'last 3 days');
});

test('when: time of day today, date and time for earlier days', () => {
  const now = new Date(2026, 10, 24, 15, 0, 0);
  const today = new Date(2026, 10, 24, 9, 5, 7).toISOString();
  const earlier = new Date(2026, 10, 21, 9, 5, 7).toISOString();
  assert.match(fmtWhen(today, now), /^\d{2}:\d{2}:\d{2}$/);
  assert.match(fmtWhen(earlier, now), /^11-21 \d{2}:\d{2}:\d{2}$/);
  assert.equal(fmtWhen(null), '—'); assert.equal(fmtWhen('nope'), '—');
});

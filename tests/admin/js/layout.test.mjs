import test from 'node:test';
import assert from 'node:assert/strict';
import { assignLayers, layoutDag, edgeState, NODE_W, NODE_H } from '../../../src/pai/api/admin/static/js/layout.js';

const N = (...ids) => ids.map((spec_id) => ({ spec_id }));
const E = (...pairs) => pairs.map(([source, target]) => ({ source, target }));

test('layers follow the longest path from a source', () => {
  const layers = assignLayers(N('a', 'b', 'c', 'd'), E(['a', 'b'], ['b', 'c'], ['a', 'c'], ['a', 'd']));
  assert.deepEqual([...layers.entries()].sort(), [['a', 0], ['b', 1], ['c', 2], ['d', 1]]);
});

test('layout is left-to-right, non-overlapping and every edge goes forward', () => {
  const nodes = N('a', 'b', 'c', 'd', 'e', 'f'); const edges = E(['a', 'c'], ['b', 'c'], ['c', 'd'], ['c', 'e'], ['d', 'f'], ['e', 'f']);
  const lay = layoutDag(nodes, edges);
  const ids = Object.keys(lay.positions);
  assert.equal(ids.length, 6);
  for (const e of edges) assert.ok(lay.positions[e.source].x + NODE_W < lay.positions[e.target].x, `${e.source}->${e.target} must go right`);
  for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
    const a = lay.positions[ids[i]], b = lay.positions[ids[j]];
    const overlap = a.x < b.x + NODE_W && b.x < a.x + NODE_W && a.y < b.y + NODE_H && b.y < a.y + NODE_H;
    assert.ok(!overlap, `${ids[i]} overlaps ${ids[j]}`);
  }
  assert.equal(lay.edges.length, edges.length);
  assert.ok(lay.edges.every((e) => e.d.startsWith('M') && e.d.includes('C')));
  assert.ok(lay.width > 0 && lay.height > 0);
});

test('layout is deterministic', () => {
  const nodes = N('x', 'y', 'z'); const edges = E(['x', 'z'], ['y', 'z']);
  assert.deepEqual(layoutDag(nodes, edges), layoutDag(nodes, edges));
});

test('crossing reduction keeps a node near its parent', () => {
  const lay = layoutDag(N('p1', 'p2', 'c1', 'c2'), E(['p1', 'c1'], ['p2', 'c2']));
  assert.ok(Math.abs(lay.positions.p1.y - lay.positions.c1.y) < Math.abs(lay.positions.p1.y - lay.positions.c2.y));
});

test('empty graph, unknown deps and a cycle never throw', () => {
  assert.equal(layoutDag([], []).width, 0);
  assert.equal(layoutDag(N('a'), E(['ghost', 'a'])).edges.length, 0);
  const cyc = layoutDag(N('a', 'b'), E(['a', 'b'], ['b', 'a']));
  assert.equal(Object.keys(cyc.positions).length, 2);
});

test('edge states reflect progress', () => {
  assert.equal(edgeState('completed', 'running'), 'active');
  assert.equal(edgeState('completed', 'completed'), 'done');
  assert.equal(edgeState('running', 'pending'), 'active');
  assert.equal(edgeState('failed', 'skipped'), 'dead');
  assert.equal(edgeState('pending', 'pending'), 'idle');
});

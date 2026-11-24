// Regression: clicking a span in the trace tree (or a live refresh) rebuilt the pane and scrolled it to the top.
// Emptying a scroll container resets scrollTop to 0; preserveScroll must put it back.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { preserveScroll } from '../../../src/pai/api/admin/static/js/dom.js';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '../../../src/pai/api/admin/static/js');

/** A scroll container that behaves like the browser: emptying it resets scrollTop/Left to 0. */
function fakeScroller(top, left = 0) {
  return { scrollTop: top, scrollLeft: left, height: 1000, empty() { this.scrollTop = 0; this.scrollLeft = 0; this.height = 0; }, fill(n) { this.height = n; } };
}

test('scroll position survives a rebuild that empties the container', () => {
  const el = fakeScroller(640, 12);
  preserveScroll(el, () => { el.empty(); el.fill(1000); });
  assert.equal(el.scrollTop, 640);
  assert.equal(el.scrollLeft, 12);
});

test('without preserveScroll the same rebuild jumps to the top (the bug)', () => {
  const el = fakeScroller(640);
  el.empty(); el.fill(1000);
  assert.equal(el.scrollTop, 0);
});

test('returns what the rebuild returns and tolerates a rebuild that does nothing', () => {
  const el = fakeScroller(50);
  assert.equal(preserveScroll(el, () => 'x'), 'x');
  assert.equal(el.scrollTop, 50);
});

test('the trace view rebuilds its tree, list and refreshed detail through preserveScroll', () => {
  const src = readFileSync(join(STATIC, 'views/traces.js'), 'utf8');
  assert.match(src, /preserveScroll\(tree, buildTree\)/);          // click a span / expand / filter / live refresh
  assert.match(src, /preserveScroll\(list, buildList\)/);          // live list refresh
  assert.match(src, /preserveScroll\(detail, \(\) => renderDetail\(sel\)\)/);   // live refresh of the open node
  // selecting a span must not clear the tree outside that wrapper
  const selectNode = src.match(/function selectNode\(id\) \{[^}]*\}/)[0];
  assert.doesNotMatch(selectNode, /clear\(tree\)/);
});

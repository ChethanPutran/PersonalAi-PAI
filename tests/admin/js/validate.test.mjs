import test from 'node:test';
import assert from 'node:assert/strict';
import { validateDag, locateJsonError, findCycle, SAMPLE_DAG } from '../../../src/pai/api/admin/static/js/validate.js';

const known = new Set(['admin.test.echo', 'admin.test.sleep', 'admin.test.stream', 'admin.test.fail']);
const dag = (tasks) => JSON.stringify({ tasks });

test('the shipped sample is valid', () => assert.equal(validateDag(SAMPLE_DAG, known).ok, true));

test('JSON errors report line and column', () => {
  const text = '{\n  "tasks": [\n    { "id": "a", }\n  ]\n}';
  const v = validateDag(text);
  assert.equal(v.ok, false); assert.equal(v.parseError.line, 3);
  assert.equal(locateJsonError('{"a":', new Error('x')).line, 1);
});

test('structural problems are listed', () => {
  assert.match(validateDag('{"tasks": []}').issues[0], /non-empty/);
  assert.ok(validateDag(dag([{ id: 'bad id!', capability: 'admin.test.echo' }]), known).issues.some((i) => /"id" must be/.test(i)));
  assert.ok(validateDag(dag([{ id: 'a', capability: 'admin.test.echo' }, { id: 'a', capability: 'admin.test.echo' }]), known).issues.some((i) => /duplicate id/.test(i)));
  assert.ok(validateDag(dag([{ id: 'a', capability: 'nope.nope' }]), known).issues.some((i) => /unknown capability/.test(i)));
  assert.ok(validateDag(dag([{ id: 'a', capability: 'admin.test.echo', depends_on: ['ghost'] }]), known).issues.some((i) => /unknown task "ghost"/.test(i)));
  assert.ok(validateDag(dag([{ id: 'a', capability: 'admin.test.echo', depends_on: ['a'] }]), known).issues.some((i) => /depends on itself/.test(i)));
  assert.ok(validateDag(dag([{ id: 'a', capability: 'admin.test.echo', type: 'bogus' }]), known).issues.some((i) => /"type" must be/.test(i)));
  assert.ok(validateDag(dag([{ id: 'a', capability: 'admin.test.echo', parameters: [] }]), known).issues.some((i) => /"parameters"/.test(i)));
});

test('cycles are found and named', () => {
  const v = validateDag(dag([{ id: 'a', capability: 'admin.test.echo', depends_on: ['b'] }, { id: 'b', capability: 'admin.test.echo', depends_on: ['a'] }]), known);
  assert.equal(v.ok, false); assert.match(v.issues.join(' '), /Cyclic dependency: (a → b → a|b → a → b)/);
  assert.equal(findCycle([{ id: 'a', depends_on: [] }, { id: 'b', depends_on: ['a'] }]), null);
});

test('a bare array of tasks is accepted too', () => assert.equal(validateDag(JSON.stringify([{ id: 'a', capability: 'admin.test.echo' }]), known).ok, true));

// Settings page model against the REAL schema (dumped by tests/admin/test_settings_ui_model.py via PAI_SCHEMA_JSON).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fieldsOf, leaves, resolve, parseInput, toInput, effective, defaultFor, isOverridden, buildChange, matches, errorsByPath, showDefault, getAt, sameValue }
  from '../../../src/pai/api/admin/static/js/settings_model.js';

const FILE = process.env.PAI_SCHEMA_JSON;
const real = FILE ? JSON.parse(readFileSync(FILE, 'utf8')) : null;
const needsSchema = { skip: real ? false : 'PAI_SCHEMA_JSON not set (run through pytest)' };

// ---------- the real schema -------------------------------------------------
test('every section of the config appears and every leaf has a usable control', needsSchema, () => {
  const { schema, secret_paths: secrets } = real;
  const tree = fieldsOf(schema, secrets);
  assert.deepEqual(tree.map((s) => s.key), Object.keys(schema.properties));
  const all = tree.flatMap((s) => leaves(s.children));
  assert.ok(all.length > 150, `only ${all.length} settings`);
  for (const f of all) {
    assert.ok(['bool', 'int', 'number', 'string', 'enum', 'combo', 'multi', 'list', 'json', 'secret'].includes(f.kind), `${f.path}: ${f.kind}`);
    if (f.kind === 'enum' || f.kind === 'multi') assert.ok(f.options.length > 0, `${f.path} has no options`);
  }
});

test('enum settings are dropdowns with exactly the allowed values', needsSchema, () => {
  const tree = fieldsOf(real.schema, real.secret_paths);
  const byPath = Object.fromEntries(tree.flatMap((s) => leaves(s.children)).map((f) => [f.path, f]));
  assert.equal(byPath['llm_provider.provider'].kind, 'enum');
  assert.ok(byPath['llm_provider.provider'].options.includes('airouter'));
  assert.deepEqual(byPath['voice.stt'].options.sort(), ['airouter', 'auto', 'fake', 'openai_batch', 'openai_realtime', 'vosk']);
  assert.equal(byPath['prompts.guard.mode'] ? byPath['prompts.guard.mode'].kind : 'enum', 'enum');
  assert.equal(byPath['plugins.plugin_artifact_store'].kind, 'enum');
  assert.equal(byPath['devices.allowed_architectures']?.kind ?? 'multi', 'multi');
});

test('a setting that accepts any model id is a free-text combo with suggestions, not a closed dropdown', needsSchema, () => {
  const tree = fieldsOf(real.schema, real.secret_paths);
  const by = Object.fromEntries(tree.flatMap((s) => leaves(s.children)).map((f) => [f.path, f]));
  assert.equal(by['llm_provider.model_name'].kind, 'combo');
  assert.ok(by['llm_provider.model_name'].options.length >= 5);
  assert.equal(parseInput(by['llm_provider.model_name'], 'anthropic/claude-haiku-5.5'), 'anthropic/claude-haiku-5.5');   // not in the list: still valid
  assert.equal(parseInput(by['llm_provider.model_name'], ''), '');                                                       // the server validates the id
});

test('booleans, bounded numbers, lists and free-form maps get the right controls', needsSchema, () => {
  const tree = fieldsOf(real.schema, real.secret_paths);
  const by = Object.fromEntries(tree.flatMap((s) => leaves(s.children)).map((f) => [f.path, f]));
  assert.equal(by['admin.history_enabled'].kind, 'bool');
  assert.equal(by['admin.history_retention_days'].kind, 'int');
  assert.equal(by['admin.history_retention_days'].min, 1); assert.equal(by['admin.history_retention_days'].max, 365);
  assert.equal(by['admin.history_retention_days'].default, 7);
  assert.equal(by['llm_routes'].kind, 'json');
  assert.equal(by['llm_pricing'].kind, 'json');
  assert.equal(by['models_file'].kind, 'string');
});

test('secrets are locked, never editable', needsSchema, () => {
  const tree = fieldsOf(real.schema, real.secret_paths);
  const by = Object.fromEntries(tree.flatMap((s) => leaves(s.children)).map((f) => [f.path, f]));
  for (const p of ['redis.password', 'llm_provider.api_key', 'voice.airouter.api_key']) { assert.equal(by[p].kind, 'secret', p); assert.equal(by[p].secret, true); }
  assert.ok(!Object.keys(by).some((p) => p.startsWith('secrets')));
});

test('the page offers exactly the settings the server will accept', needsSchema, () => {
  const tree = fieldsOf(real.schema, real.secret_paths);
  const paths = tree.flatMap((s) => leaves(s.children)).filter((f) => !f.secret).map((f) => f.path);
  const known = new Set(real.known_paths);
  for (const p of paths) assert.ok(known.has(p), `${p} is shown but the server would refuse it`);
});

// ---------- parsing / values (no schema needed) -----------------------------
const F = (o) => ({ path: 'a.b', label: 'B', kind: 'string', nullable: false, ...o });

test('parseInput: numbers are range-checked, whole numbers enforced, blank handled by nullability', () => {
  const int = F({ kind: 'int', min: 1, max: 365 });
  assert.equal(parseInput(int, '14'), 14);
  assert.throws(() => parseInput(int, '0'), /at least 1/); assert.throws(() => parseInput(int, '366'), /at most 365/);
  assert.throws(() => parseInput(int, '1.5'), /whole number/); assert.throws(() => parseInput(int, 'x'), /not a number/); assert.throws(() => parseInput(int, ''), /required/);
  assert.equal(parseInput({ ...int, nullable: true }, ''), null);
  assert.throws(() => parseInput(F({ kind: 'number', min: 0, minExclusive: true }), '0'), /greater than 0/);
  assert.equal(parseInput(F({ kind: 'number' }), '0.25'), 0.25);
});

test('parseInput: enums only accept their options, lists split on lines or commas, json must parse', () => {
  const e = F({ kind: 'enum', options: ['a', 'b', 3] });
  assert.equal(parseInput(e, 'a'), 'a'); assert.equal(parseInput(e, '3'), 3); assert.throws(() => parseInput(e, 'z'), /one of: a, b, 3/);
  assert.equal(parseInput({ ...e, nullable: true }, ''), null); assert.throws(() => parseInput(e, ''), /choose/);
  assert.deepEqual(parseInput(F({ kind: 'list', itemKind: 'string' }), 'a, b\n c ,'), ['a', 'b', 'c']);
  assert.deepEqual(parseInput(F({ kind: 'list', itemKind: 'integer' }), '1\n2'), [1, 2]); assert.throws(() => parseInput(F({ kind: 'list', itemKind: 'integer' }), '1,x'), /not a number/);
  assert.deepEqual(parseInput(F({ kind: 'json' }), '{"a": 1}'), { a: 1 }); assert.throws(() => parseInput(F({ kind: 'json' }), '{nope'), /not valid JSON/);
  assert.deepEqual(parseInput(F({ kind: 'multi', options: ['x', 'y'] }), ['x']), ['x']); assert.throws(() => parseInput(F({ kind: 'multi', options: ['x'] }), ['q']), /not allowed/);
  assert.equal(parseInput(F({ kind: 'bool' }), true), true); assert.equal(parseInput(F({ kind: 'string', nullable: true }), ''), null);
});

test('toInput is the inverse of parseInput for what the form shows', () => {
  assert.equal(toInput(F({ kind: 'list' }), ['a', 'b']), 'a\nb'); assert.equal(toInput(F({ kind: 'json' }), { a: 1 }), '{\n  "a": 1\n}');
  assert.equal(toInput(F({ kind: 'string' }), null), ''); assert.equal(toInput(F({ kind: 'bool' }), null), false); assert.deepEqual(toInput(F({ kind: 'multi' }), ['x']), ['x']);
});

test('effective / default / overridden: override > config.yaml > built-in default', () => {
  const f = F({ path: 'voice.airouter.voice', default: 'builtin' });
  assert.equal(effective(f, { overrides: { voice: { airouter: { voice: 'o' } } }, base: { voice: { airouter: { voice: 'b' } } } }), 'o');
  assert.equal(effective(f, { overrides: {}, base: { voice: { airouter: { voice: 'b' } } } }), 'b');
  assert.equal(effective(f, { overrides: {}, base: {} }), 'builtin');
  assert.equal(defaultFor(f, { voice: { airouter: { voice: 'b' } } }), 'b'); assert.equal(defaultFor(f, {}), 'builtin');
  assert.ok(isOverridden(f, { voice: { airouter: { voice: 'o' } } })); assert.ok(!isOverridden(f, {}));
  assert.equal(getAt({ a: { b: 0 } }, 'a.b'), 0); assert.equal(getAt({ a: 1 }, 'a.b'), undefined);
});

test('buildChange sends only real changes and only resets of saved values', () => {
  const start = { 'a.x': 1, 'a.y': 'k' };
  const overrides = { a: { y: 'k' } };
  assert.deepEqual(buildChange({ 'a.x': 2, 'a.y': 'k' }, new Set(['a.y', 'a.z']), start, overrides), { set: { 'a.x': 2 }, reset: ['a.y'] });
  assert.deepEqual(buildChange({}, new Set(), start, overrides), { set: {}, reset: [] });
  assert.ok(sameValue([1, 2], [1, 2]) && !sameValue({ a: 1 }, { a: 2 }) && sameValue(undefined, null));
});

test('search, defaults text and server errors', () => {
  const f = F({ path: 'voice.airouter.stt_model', label: 'Stt model', description: 'Transcription model id' });
  assert.ok(matches(f, '') && matches(f, 'transcription') && matches(f, 'voice stt') && !matches(f, 'credits'));
  assert.equal(showDefault(F({ kind: 'list' }), ['a', 'b']), 'a, b'); assert.equal(showDefault(F({}), ''), '(empty)'); assert.equal(showDefault(F({}), undefined), 'none');
  assert.equal(showDefault(F({}), null), 'not set');
  assert.deepEqual(errorsByPath([{ path: 'a', message: 'x' }, { path: 'a', message: 'y' }, { path: 'b', message: 'z' }]), { a: ['x', 'y'], b: ['z'] });
});

test('resolve follows refs, nullable unions and keeps sibling defaults', () => {
  const schema = { $defs: { Mode: { enum: ['a', 'b'], type: 'string' } } };
  const r = resolve(schema, { anyOf: [{ $ref: '#/$defs/Mode' }, { type: 'null' }], default: 'a' });
  assert.equal(r.nullable, true); assert.deepEqual(r.node.enum, ['a', 'b']); assert.equal(r.node.default, 'a');
  assert.equal(resolve(schema, { anyOf: [{ type: 'string' }, { type: 'array' }] }).node.__union, true);
});

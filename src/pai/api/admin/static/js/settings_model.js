// Pure model behind the Settings page (no DOM): turn the server's JSON Schema into form fields and back.
//
//   fieldsOf(schema, secretPaths) -> [{ key, label, description, children: [group | field] }]   one entry per section
//   field:  { path, label, description, kind, options?, default?, nullable, min?, max?, secret }
//   kinds:  bool | int | number | string | enum (dropdown) | combo (free text with suggestions) | multi (several enum values) | list | json | secret
//
// Everything the server accepts is rendered: enums as dropdowns, booleans as switches, bounded numbers with their range.

const MAX_DEPTH = 8;

const titleCase = (s) => String(s).replace(/[_-]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

/** Follow $ref / allOf / anyOf(+null) to the real node. Siblings of a $ref (description, default) win. */
export function resolve(schema, node) {
  const defs = schema.$defs || {};
  let cur = node || {};
  let nullable = false;
  let guard = 0;
  let extra = {};
  while (guard++ < 12) {
    if (cur.$ref) {
      const { $ref, ...siblings } = cur;
      extra = { ...extra, ...siblings };
      cur = defs[$ref.split('/').pop()] || {};
      continue;
    }
    if (Array.isArray(cur.allOf) && cur.allOf.length === 1) {
      const { allOf, ...siblings } = cur;
      extra = { ...extra, ...siblings };
      cur = allOf[0];
      continue;
    }
    const alts = cur.anyOf || cur.oneOf;
    if (Array.isArray(alts)) {
      const nonNull = alts.filter((a) => a.type !== 'null');
      nullable = nullable || nonNull.length < alts.length;
      if (nonNull.length === 1) {
        const { anyOf, oneOf, ...siblings } = cur;
        extra = { ...extra, ...siblings };
        cur = nonNull[0];
        continue;
      }
      // a real union (e.g. string | list): edit it as JSON
      return { node: { ...cur, ...extra, __union: true }, nullable };
    }
    break;
  }
  if (Array.isArray(cur.type)) {
    nullable = nullable || cur.type.includes('null');
    cur = { ...cur, type: cur.type.find((t) => t !== 'null') };
  }
  return { node: { ...cur, ...extra }, nullable };
}

function kindOf(schema, node) {
  if (node.__union) return { kind: 'json' };
  if (Array.isArray(node.enum)) return { kind: node['x-open-enum'] ? 'combo' : 'enum', options: node.enum };   // open enum: any value is valid, the list is suggestions
  if (node.const !== undefined) return { kind: 'enum', options: [node.const] };
  switch (node.type) {
    case 'boolean': return { kind: 'bool' };
    case 'integer': return { kind: 'int' };
    case 'number': return { kind: 'number' };
    case 'string': return { kind: 'string' };
    case 'array': {
      const item = resolve(schema, node.items || {}).node;
      if (Array.isArray(item.enum)) return { kind: 'multi', options: item.enum };
      if (['string', 'integer', 'number'].includes(item.type)) return { kind: 'list', itemKind: item.type };
      return { kind: 'json' };
    }
    default: return { kind: 'json' };                    // maps with arbitrary keys, lists of objects, anything else
  }
}

function build(schema, node, path, secrets, depth) {
  const { node: n, nullable } = resolve(schema, node);
  const label = titleCase(path.split('.').pop());
  const description = n.description || '';
  if (secrets.has(path)) return { path, label, description, kind: 'secret', secret: true, nullable };
  if (n.properties && depth < MAX_DEPTH && !n.__union) {
    const children = Object.entries(n.properties).map(([k, v]) => build(schema, v, `${path}.${k}`, secrets, depth + 1));
    return { path, label, description, kind: 'group', children };
  }
  const { kind, options, itemKind } = kindOf(schema, n);
  const f = { path, label, description, kind, nullable, secret: false, default: n.default };
  if (options) f.options = options;
  if (itemKind) f.itemKind = itemKind;
  if (kind === 'int' || kind === 'number') {
    const lo = n.minimum ?? (n.exclusiveMinimum !== undefined ? n.exclusiveMinimum : undefined);
    const hi = n.maximum ?? (n.exclusiveMaximum !== undefined ? n.exclusiveMaximum : undefined);
    if (lo !== undefined) { f.min = lo; f.minExclusive = n.exclusiveMinimum !== undefined && n.minimum === undefined; }
    if (hi !== undefined) { f.max = hi; f.maxExclusive = n.exclusiveMaximum !== undefined && n.maximum === undefined; }
  }
  return f;
}

export function fieldsOf(schema, secretPaths = []) {
  const secrets = new Set(secretPaths);
  return Object.entries(schema.properties || {}).map(([key, v]) => {
    const node = build(schema, v, key, secrets, 0);
    const children = node.kind === 'group' ? node.children : [node];
    return { key, label: node.label, description: node.description || '', children };
  });
}

/** Every editable-or-locked leaf under a section/group list. */
export function leaves(children) {
  const out = [];
  for (const c of children) (c.kind === 'group' ? out.push(...leaves(c.children)) : out.push(c));
  return out;
}

export function getAt(obj, path) {
  let cur = obj;
  for (const p of String(path).split('.')) {
    if (cur === null || typeof cur !== 'object' || !(p in cur)) return undefined;
    cur = cur[p];
  }
  return cur;
}

/** The value a field starts from: the saved override, else config.yaml, else the model's built-in default. */
export function effective(field, { overrides = {}, base = {} } = {}) {
  const o = getAt(overrides, field.path);
  if (o !== undefined) return o;
  const b = getAt(base, field.path);
  return b !== undefined ? b : field.default;
}

/** What "reset" returns to: config.yaml's value when it sets one, else the model's built-in default. */
export function defaultFor(field, base = {}) {
  const b = getAt(base, field.path);
  return b !== undefined ? b : field.default;
}

export function isOverridden(field, overrides = {}) {
  return getAt(overrides, field.path) !== undefined;
}

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
export { same as sameValue };

/** Turn what the user typed (a string, a boolean, an array of strings) into the value to save. Throws {message}. */
export function parseInput(field, raw) {
  const fail = (message) => { throw new Error(message); };
  const blank = raw === '' || raw === null || raw === undefined;
  switch (field.kind) {
    case 'bool': return Boolean(raw);
    case 'int':
    case 'number': {
      if (blank) { if (field.nullable) return null; fail('a number is required'); }
      const n = Number(raw);
      if (!Number.isFinite(n)) fail(`"${raw}" is not a number`);
      if (field.kind === 'int' && !Number.isInteger(n)) fail('a whole number is required');
      if (field.min !== undefined && (field.minExclusive ? n <= field.min : n < field.min)) fail(`must be ${field.minExclusive ? 'greater than' : 'at least'} ${field.min}`);
      if (field.max !== undefined && (field.maxExclusive ? n >= field.max : n > field.max)) fail(`must be ${field.maxExclusive ? 'less than' : 'at most'} ${field.max}`);
      return n;
    }
    case 'enum': {
      if (blank) { if (field.nullable) return null; fail('choose a value'); }
      const hit = field.options.find((o) => String(o) === String(raw));
      if (hit === undefined) fail(`must be one of: ${field.options.join(', ')}`);
      return hit;
    }
    case 'multi': {
      const arr = Array.isArray(raw) ? raw : [];
      const bad = arr.filter((v) => !field.options.map(String).includes(String(v)));
      if (bad.length) fail(`not allowed: ${bad.join(', ')}`);
      return arr;
    }
    case 'list': {
      const items = String(raw ?? '').split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
      if (field.itemKind === 'string') return items;
      return items.map((s) => { const n = Number(s); if (!Number.isFinite(n)) fail(`"${s}" is not a number`); return n; });
    }
    case 'json': {
      if (blank) return field.nullable ? null : {};
      try { return typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (e) { fail(`not valid JSON: ${e.message}`); }
      return undefined;
    }
    case 'combo':
    case 'string':
    default:
      if (blank) return field.nullable ? null : '';
      return String(raw);
  }
}

/** How a value is shown in an input (the inverse of parseInput). */
export function toInput(field, value) {
  if (value === null || value === undefined) return field.kind === 'bool' ? false : field.kind === 'multi' ? [] : '';
  switch (field.kind) {
    case 'list': return (value || []).join('\n');
    case 'json': return JSON.stringify(value, null, 2);
    case 'multi': return Array.isArray(value) ? value.map(String) : [];
    case 'bool': return Boolean(value);
    default: return String(value);
  }
}

/** Short text for "default: …". */
export function showDefault(field, value) {
  if (value === undefined) return 'none';
  if (value === null) return 'not set';
  if (field.kind === 'list' || field.kind === 'multi') return value.length ? value.join(', ') : '(empty)';
  if (field.kind === 'json') { const s = JSON.stringify(value); return s.length > 60 ? `${s.slice(0, 57)}…` : s; }
  if (value === '') return '(empty)';
  return String(value);
}

/** The request to save: what differs from the starting value goes in `set`, fields reset to their default in `reset`. */
export function buildChange(edits, resets, startValues, overrides) {
  const set = {};
  for (const [path, value] of Object.entries(edits)) {
    if (!same(value, startValues[path])) set[path] = value;
  }
  const reset = [...resets].filter((p) => getAt(overrides, p) !== undefined && !(p in set));
  return { set, reset };
}

/** Filter a section's leaves by a search text (path, label or description). */
export function matches(field, q) {
  if (!q) return true;
  const hay = `${field.path} ${field.label} ${field.description}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
}

/** Server validation errors ({path, message}) grouped by field path, so each message sits under its input. */
export function errorsByPath(errors) {
  const out = {};
  for (const e of errors || []) (out[e.path] ||= []).push(e.message);
  return out;
}

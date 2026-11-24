// Pure client-side validation of a custom DAG (mirrors the server's checks so errors show instantly).
const ID_RE = /^[A-Za-z0-9_.:-]{1,64}$/;
const TYPES = new Set(['one_shot', 'long_running', 'stream']);

/** Convert a JSON.parse error message into {line, col}. */
export function locateJsonError(text, err) {
  const m = /position (\d+)/.exec(err.message || '');
  let pos = m ? Number(m[1]) : null;
  if (pos == null) { const lc = /line (\d+) column (\d+)/.exec(err.message || ''); if (lc) return { line: Number(lc[1]), col: Number(lc[2]) }; return { line: 1, col: 1 }; }
  pos = Math.min(pos, text.length);
  const before = text.slice(0, pos).split('\n');
  return { line: before.length, col: before[before.length - 1].length + 1 };
}

export function findCycle(tasks) {
  const deps = new Map(tasks.map((t) => [t.id, t.depends_on || []]));
  const state = new Map(); const stack = [];
  const visit = (id) => {
    if (state.get(id) === 2) return null;
    if (state.get(id) === 1) return [...stack.slice(stack.indexOf(id)), id];
    state.set(id, 1); stack.push(id);
    for (const d of deps.get(id) || []) { if (!deps.has(d)) continue; const c = visit(d); if (c) return c; }
    stack.pop(); state.set(id, 2); return null;
  };
  for (const id of deps.keys()) { const c = visit(id); if (c) return c; }
  return null;
}

export function validateDag(text, knownCapabilities = null) {
  let doc;
  try { doc = JSON.parse(text); }
  catch (e) { return { ok: false, parseError: { ...locateJsonError(text, e), message: e.message }, issues: [], tasks: null }; }
  const tasks = Array.isArray(doc) ? doc : doc && doc.tasks;
  const issues = [];
  if (!Array.isArray(tasks) || !tasks.length) return { ok: false, parseError: null, issues: ['Provide a non-empty "tasks" array.'], tasks: null };
  if (tasks.length > 50) issues.push('At most 50 tasks.');
  const ids = new Set();
  tasks.forEach((t, i) => {
    const at = `tasks[${i}]`;
    if (!t || typeof t !== 'object') { issues.push(`${at}: must be an object`); return; }
    if (typeof t.id !== 'string' || !ID_RE.test(t.id)) issues.push(`${at}: "id" must be 1-64 chars of letters, digits, _ . : -`);
    else if (ids.has(t.id)) issues.push(`${at}: duplicate id "${t.id}"`);
    else ids.add(t.id);
    if (typeof t.capability !== 'string' || !t.capability) issues.push(`${at}: "capability" is required`);
    else if (knownCapabilities && !knownCapabilities.has(t.capability)) issues.push(`${at}: unknown capability "${t.capability}"`);
    if (t.depends_on !== undefined && !Array.isArray(t.depends_on)) issues.push(`${at}: "depends_on" must be an array`);
    if (t.type !== undefined && !TYPES.has(t.type)) issues.push(`${at}: "type" must be one of ${[...TYPES].join(', ')}`);
    if (t.parameters !== undefined && (typeof t.parameters !== 'object' || Array.isArray(t.parameters) || t.parameters === null)) issues.push(`${at}: "parameters" must be an object`);
  });
  tasks.forEach((t, i) => (Array.isArray(t?.depends_on) ? t.depends_on : []).forEach((d) => {
    if (d === t.id) issues.push(`tasks[${i}]: "${t.id}" depends on itself`);
    else if (!ids.has(d)) issues.push(`tasks[${i}]: depends on unknown task "${d}"`);
  }));
  if (!issues.length) { const cyc = findCycle(tasks); if (cyc) issues.push(`Cyclic dependency: ${cyc.join(' → ')}`); }
  return { ok: issues.length === 0, parseError: null, issues, tasks };
}

export const SAMPLE_DAG = JSON.stringify({
  label: 'my first DAG',
  tasks: [
    { id: 'fetch_a', capability: 'admin.test.sleep', parameters: { seconds: 4 }, type: 'one_shot' },
    { id: 'fetch_b', capability: 'admin.test.sleep', parameters: { seconds: 6 } },
    { id: 'merge', capability: 'admin.test.echo', parameters: { message: 'merged!' }, depends_on: ['fetch_a', 'fetch_b'] },
    { id: 'report', capability: 'admin.test.stream', parameters: { frames: 8, interval_ms: 400 }, depends_on: ['merge'], type: 'stream' },
  ],
}, null, 2);

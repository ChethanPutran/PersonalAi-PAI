// Pure helpers for live event handling (testable under node).
export const MAX_EVENTS = 500;

export function classifyEvent(type) {
  if (/failed|error/.test(type)) return 'bad';
  if (/completed|finished|done/.test(type)) return 'ok';
  return '';
}

export function summarizeEvent(type, data) {
  const d = data || {};
  const id = d.task_id || d.id || '';
  const bits = [];
  if (type.startsWith('dag.')) {
    if (d.request_id) bits.push(`run ${String(d.request_id).slice(0, 12)}`);
    if (d.task_id) bits.push(`node ${d.task_id}`);
    if (d.status) bits.push(d.status);
    if (d.error) bits.push(`error: ${d.error}`);
    if (d.task_count != null) bits.push(`${d.task_count} nodes`);
  } else {
    bits.push(String(id).slice(0, 8));
    if (d.capability) bits.push(d.capability);
    if (d.status) bits.push(d.status);
    if (d.kind) bits.push(d.kind);
    if (d.sequence != null) bits.push(`#${d.sequence}`);
    if (d.error) bits.push(`error: ${d.error}`);
  }
  return bits.filter(Boolean).join(' · ');
}

/** Append to a bounded log (oldest dropped). Returns a NEW array; the input is never mutated. */
export function appendEvent(log, evt, max = MAX_EVENTS) {
  const next = log.length >= max ? log.slice(log.length - max + 1) : log.slice();
  next.push(evt);
  return next;
}

export function matchesFilter(evt, { text = '', kinds = new Set() } = {}) {
  if (kinds.size && !kinds.has(evt.event.split('.')[0])) return false;
  if (!text) return true;
  const needle = text.toLowerCase();
  return evt.event.toLowerCase().includes(needle) || JSON.stringify(evt.data ?? '').toLowerCase().includes(needle);
}

/** Does this event mean the list of tasks / runs probably changed? */
export function affectsLists(type) {
  return /^(task\.(created|queued|started|completed|failed|cancelled|paused|resumed|retrying|recovered)|dag\.)/.test(type);
}

/** Tiny pub/sub store. */
export function createStore(initial) {
  let state = initial;
  const subs = new Set();
  return {
    get: () => state,
    set(patch) { state = { ...state, ...patch }; subs.forEach((fn) => fn(state)); },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}

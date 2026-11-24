import { h, clear } from '../dom.js';
import { icon } from '../icons.js';
import { btn, emptyState, pageHead, toast } from '../ui.js';
import { fmtWhen } from '../format.js';
import { get } from '../api.js';
import { mergeHistory, RANGES } from '../history.js';
import { appendEvent, matchesFilter, classifyEvent, summarizeEvent, MAX_EVENTS } from '../state.js';

const SHOW = 250;                                    // rows painted at once (the log keeps MAX_EVENTS)

export async function mount(container, ctx) {
  let log = []; let paused = false; let dirty = false; let raf = 0; let seen = 0;
  let rangeIdx = 3; let retentionDays = null; let historyNote = '';
  const kinds = new Set(); const filter = { text: '' };
  const list = h('div', { class: 'events', role: 'log', 'aria-live': 'off', 'aria-label': 'Live event log' });
  const counter = h('span', { class: 'sub' });
  const text = h('input', { class: 'input', placeholder: 'Filter (type, task id, capability, error…)', 'aria-label': 'Filter events', oninput: (e) => { filter.text = e.target.value.trim(); paint(); } });
  const chip = (k, label) => { const b = h('button', { class: 'filter-chip', 'aria-pressed': 'false', onclick: () => { kinds.has(k) ? kinds.delete(k) : kinds.add(k); b.setAttribute('aria-pressed', String(kinds.has(k))); paint(); } }, label); return b; };
  const pause = btn('Pause', { ic: 'pause', onclick: () => { paused = !paused; pause.querySelector('span').textContent = paused ? 'Resume' : 'Pause'; if (!paused) paint(); } });
  const range = h('select', { class: 'select', id: 'events-range', 'aria-label': 'How far back to show',
    onchange: (e) => { rangeIdx = Number(e.target.value); loadHistory(); } });
  const fillRange = () => { clear(range); RANGES.forEach(([label, days], i) => { if (days == null || retentionDays == null || days <= retentionDays) range.append(h('option', { value: String(i), selected: i === rangeIdx }, label === 'All retained' && retentionDays ? `All · ${retentionDays} days` : label)); }); };
  fillRange();
  const clr = btn('Clear', { ic: 'trash', onclick: () => { log = []; seen = 0; paint(); } });

  container.append(h('div', { class: 'page' }, pageHead('Events', 'Every task and DAG event from every session: recent history, then live as it happens.', range, pause, clr),
    h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Stream'), counter, h('div', { class: 'right' }, h('span', { class: 'conn' }, h('span', { class: 'dot live' }), 'live'))),
      h('div', { class: 'toolbar' }, h('div', { class: 'search' }, icon('search'), text), chip('task', 'task.*'), chip('dag', 'dag.*')), list)));

  function row(e) {
    const type = e.event; const family = type.split('.')[0]; const body = summarizeEvent(type, e.data);
    const r = h('div', { class: 'ev', role: 'button', tabindex: '0', 'aria-expanded': 'false' },
      h('span', { class: 'time' }, fmtWhen(e.at)), h('span', { class: `type ${family} ${classifyEvent(type)}` }, type), h('span', { class: 'body' }, body));
    const toggle = () => { const open = r.classList.toggle('open'); r.setAttribute('aria-expanded', String(open)); r.querySelector('.body').textContent = open ? JSON.stringify(e.data, null, 2) : body; };
    r.addEventListener('click', toggle); r.addEventListener('keydown', (k) => { if (k.key === 'Enter' || k.key === ' ') { k.preventDefault(); toggle(); } });
    return r;
  }

  function paint() {
    dirty = false; clear(list);
    const shown = log.filter((e) => matchesFilter(e, { text: filter.text, kinds })).slice(-SHOW).reverse();
    counter.textContent = `${seen} live · showing ${shown.length} of ${log.length}${log.length >= MAX_EVENTS ? ` (newest ${MAX_EVENTS} kept)` : ''}${historyNote ? ` · ${historyNote}` : ''}`;
    if (!shown.length) { list.append(emptyState(log.length ? 'No events match this filter' : 'Waiting for events…', log.length ? 'Clear the filter to see everything.' : 'Nothing in this time range yet. Run a demo DAG in the Playground to see events flow in.', 'activity')); return; }
    shown.forEach((e) => list.append(row(e)));
  }
  function schedule() { if (!dirty && !paused) { dirty = true; raf = requestAnimationFrame(paint); } }

  // Live events start being collected immediately; history is merged in when it arrives (no duplicates).
  let live = [];
  const onEvent = (ev) => { seen += 1; const e = { ...ev.detail, at: new Date().toISOString() }; live.push(e); log = appendEvent(log, e); schedule(); };
  ctx.bus.addEventListener('event', onEvent);

  async function loadHistory() {
    const days = RANGES[rangeIdx][1];
    if (days === 0) { historyNote = ''; log = live.slice(-MAX_EVENTS); paint(); return; }      // "This session" = live only
    try {
      const res = await get('/admin/events/history', { params: { days: days ?? undefined, limit: MAX_EVENTS } });
      retentionDays = res.retention_days; fillRange();
      const past = (res.events || []).map((r) => ({ event: r.event, data: r.data, at: new Date(r.ts * 1000).toISOString() }));
      log = mergeHistory(past, live, MAX_EVENTS);
      historyNote = res.enabled === false ? 'history is off (admin.history_enabled)' : `${past.length} from history`;
    } catch (e) {
      historyNote = 'history unavailable'; toast(e.message || 'Could not load event history', 'error');
    }
    paint();
  }
  paint();
  loadHistory();
  return { unmount() { cancelAnimationFrame(raf); ctx.bus.removeEventListener('event', onEvent); } };
}

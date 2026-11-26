// Model picker for the open-enum (model id) settings: a filterable, sortable table of every model in config/models.yaml
// with input / output / total price and context size, so a model can be chosen by cost rather than by name alone.
import { h, clear, preserveScroll } from './dom.js';
import { fmtCost, fmtSize } from './format.js';

const num = (v) => (v == null || Number.isNaN(Number(v)) ? null : Number(v));
/** Input + output price per 1M tokens; null when the model has no price at all. */
export const totalPrice = (m) => (num(m.input_per_million) == null && num(m.output_per_million) == null ? null : (num(m.input_per_million) || 0) + (num(m.output_per_million) || 0));

export const COLUMNS = [
  { key: 'name', label: 'Model', get: (m) => m.name, text: true },
  { key: 'provider', label: 'Provider', get: (m) => m.provider || '', text: true },
  { key: 'input', label: 'Input $/1M', get: (m) => num(m.input_per_million), money: true },
  { key: 'output', label: 'Output $/1M', get: (m) => num(m.output_per_million), money: true },
  { key: 'total', label: 'Total $/1M', get: totalPrice, money: true },
  { key: 'context', label: 'Context', get: (m) => num(m.context_length), size: true },
];

/** Filter by text / capabilities / max total price, then sort by a column. Unknown values always sort last. */
export function filterSort(models, { query = '', tools = false, reasoning = false, vision = false, maxTotal = null, sortKey = 'total', dir = 1 } = {}) {
  const q = query.trim().toLowerCase();
  const col = COLUMNS.find((c) => c.key === sortKey) || COLUMNS[4];
  return models
    .filter((m) => (!q || `${m.name} ${m.provider || ''}`.toLowerCase().includes(q))
      && (!tools || m.tool_use) && (!reasoning || m.reasoning) && (!vision || m.vision)
      && (maxTotal == null || (totalPrice(m) != null && totalPrice(m) <= maxTotal)))
    .sort((a, b) => {
      const x = col.get(a); const y = col.get(b);
      if (x == null && y == null) return a.name.localeCompare(b.name);
      if (x == null) return 1;
      if (y == null) return -1;
      const c = col.text ? String(x).localeCompare(String(y)) : x - y;
      return (c || a.name.localeCompare(b.name)) * dir;
    });
}

/** One-line summary for datalist suggestions: "$0.10 in · $0.40 out · 128K ctx". */
export function describe(m) {
  const price = totalPrice(m) == null ? 'no price' : `${fmtCost(m.input_per_million ?? 0)} in · ${fmtCost(m.output_per_million ?? 0)} out`;
  return `${price} · ${fmtSize(m.context_length)} ctx`;
}

/** The picker panel. `onPick(name)` is called when a row is chosen; `getCurrent()` marks the selected model. */
export function modelPicker({ models, getCurrent, onPick }) {
  const st = { query: '', tools: false, reasoning: false, vision: false, maxTotal: null, sortKey: 'total', dir: 1 };
  const tbody = h('tbody');
  const scroller = h('div', { class: 'table-wrap picker-table' });
  const count = h('span', { class: 'faint' });
  const headRow = h('tr');

  function renderHead() {
    clear(headRow);
    for (const c of COLUMNS) {
      const active = st.sortKey === c.key;
      headRow.append(h('th', { class: c.text ? '' : 'num', 'aria-sort': active ? (st.dir === 1 ? 'ascending' : 'descending') : 'none' },
        h('button', { type: 'button', class: 'th-sort', onclick: () => { st.dir = active ? -st.dir : 1; st.sortKey = c.key; renderHead(); renderRows(); } },
          c.label, active ? (st.dir === 1 ? ' ▲' : ' ▼') : '')));
    }
    headRow.append(h('th', {}, 'Can do'));
  }
  function pick(name) {                                            // move the highlight only: rebuilding the rows would jump the list back to the top
    onPick(name);
    for (const r of tbody.querySelectorAll('.pick-row')) {
      const on = r.dataset.name === name; r.classList.toggle('selected', on); r.setAttribute('aria-selected', String(on));
    }
  }
  function renderRows() { preserveScroll(scroller, fillRows); }
  function fillRows() {
    clear(tbody);
    const list = filterSort(models, st); const cur = getCurrent();
    count.textContent = `${list.length} of ${models.length} models`;
    if (!list.length) { tbody.append(h('tr', {}, h('td', { colspan: String(COLUMNS.length + 1), class: 'faint' }, 'No model matches these filters'))); return; }
    for (const m of list) {
      const cap = (v, l) => (v ? h('span', { class: 'chip completed' }, l) : null);
      tbody.append(h('tr', { class: `pick-row${m.name === cur ? ' selected' : ''}`, tabindex: '0', 'data-name': m.name, title: `Use ${m.name}`, 'aria-selected': String(m.name === cur),
        onclick: () => pick(m.name),
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(m.name); } } },
        h('td', { class: 'mono' }, m.name), h('td', {}, m.provider || ''),
        ...COLUMNS.filter((c) => !c.text).map((c) => { const v = c.get(m); return h('td', { class: 'num' }, v == null ? '—' : c.size ? fmtSize(v) : fmtCost(v)); }),
        h('td', {}, cap(m.tool_use, 'tools'), ' ', cap(m.reasoning, 'reasoning'), ' ', cap(m.vision, 'vision'))));
    }
  }
  const check = (key, label) => h('label', { class: 'check' }, h('input', { type: 'checkbox', onchange: (e) => { st[key] = e.target.checked; renderRows(); } }), label);
  const filters = h('div', { class: 'picker-filters' },
    h('input', { class: 'input', type: 'search', placeholder: 'Filter by name or provider…', 'aria-label': 'Filter models', oninput: (e) => { st.query = e.target.value; renderRows(); } }),
    h('input', { class: 'input', type: 'number', min: '0', step: 'any', placeholder: 'Max total $/1M', 'aria-label': 'Maximum total price per million tokens',
      oninput: (e) => { st.maxTotal = e.target.value === '' ? null : Number(e.target.value); renderRows(); } }),
    check('tools', 'tools'), check('reasoning', 'reasoning'), check('vision', 'vision'), count);
  scroller.append(h('table', { class: 'data' }, h('thead', {}, headRow), tbody));
  renderHead(); fillRows();
  const root = h('div', { class: 'model-picker' }, filters, scroller);
  root.refresh = renderRows;
  return root;
}

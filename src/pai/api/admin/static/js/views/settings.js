// Settings: every configuration setting, rendered from the server's schema (enums as dropdowns, switches for booleans,
// ranges on numbers, defaults with a reset). Saved to config/overrides.yaml (merged over config.yaml); secrets stay in .env.
// "Reload system" applies the saved settings to the RUNNING server without a restart and without touching this page.
import { h, clear } from '../dom.js';
import { icon } from '../icons.js';
import { get, post, api } from '../api.js';
import { btn, busy, toast, confirmDialog, errorState, pageHead, callout, skeletonRows } from '../ui.js';
import { fieldsOf, leaves, effective, defaultFor, isOverridden, parseInput, toInput, showDefault, buildChange, matches, errorsByPath, sameValue, getAt } from '../settings_model.js';

const RISKY = ['security.', 'api.', 'admin.', 'database.', 'redis.'];

export async function mount(container, ctx) {
  let alive = true;
  let snap = null; let tree = []; let fields = new Map();          // path -> field
  let start = {};                                                  // path -> value the form started from
  let edits = {}; let resets = new Set(); let clientErrors = {}; let serverErrors = {};
  let section = null; let query = ''; let lastReload = null;
  let suggestions = [];                                            // model ids from models.yaml, offered by the open-enum (model) fields

  const page = h('div', { class: 'page settings-page' });
  container.append(page);
  const saveBtn = btn('Save changes', { variant: 'primary', ic: 'check', onclick: () => save() });
  const discardBtn = btn('Discard', { ic: 'close', onclick: () => { edits = {}; resets = new Set(); clientErrors = {}; serverErrors = {}; render(); } });
  const reloadBtn = btn('Reload system', { ic: 'refresh', title: 'Apply the saved settings to the running server (no restart; this page is not affected)', onclick: () => reload() });
  const search = h('input', { class: 'input', id: 'settings-search', type: 'search', placeholder: 'Search settings…', 'aria-label': 'Search settings',
    oninput: (e) => { query = e.target.value.trim(); renderBody(); } });
  const banner = h('div', { class: 'stack', id: 'settings-banner' });
  const body = h('div', { class: 'settings-layout' }, h('div', { class: 'card' }, skeletonRows(6, 3)));
  page.append(pageHead('Settings', 'Every setting, from the schema the server validates with. Saved values go to config/overrides.yaml and win over config.yaml; secrets stay in .env.',
    search, discardBtn, saveBtn, reloadBtn), banner, body);

  // ------------------------------------------------------------------ data
  async function load() {
    try {
      snap = await get('/system/settings');
      try { suggestions = ((await get('/system/models')).llm || []).map((m) => m.name); } catch { suggestions = []; }     // optional
      tree = fieldsOf(snap.schema, snap.secret_paths);
      fields = new Map(tree.flatMap((s) => leaves(s.children)).map((f) => [f.path, f]));
      start = {};
      for (const f of fields.values()) if (!f.secret) start[f.path] = effective(f, snap);
      if (!section || !tree.some((s) => s.key === section)) section = tree[0]?.key;
      render();
    } catch (e) { clear(body); body.append(h('div', { class: 'card' }, errorState(e, load))); }
  }

  const current = (f) => (f.path in edits ? edits[f.path] : resets.has(f.path) ? defaultFor(f, snap.base) : start[f.path]);
  const dirtyCount = () => Object.keys(edits).length + [...resets].filter((p) => isOverridden(fields.get(p) || { path: p }, snap.overrides)).length;
  const pendingPaths = () => new Set((snap?.pending || []).map((p) => p.path));

  function updateChrome() {
    const n = dirtyCount();
    saveBtn.disabled = n === 0 || Object.keys(clientErrors).length > 0;
    saveBtn.querySelector('span').textContent = n ? `Save ${n} change${n === 1 ? '' : 's'}` : 'Save changes';
    discardBtn.disabled = n === 0;
    reloadBtn.querySelector('span').textContent = (snap?.pending || []).length ? `Reload system (${snap.pending.length} saved)` : 'Reload system';
  }

  // ------------------------------------------------------------------ one field
  function control(f) {
    const id = `set-${f.path.replace(/[^a-z0-9]/gi, '-')}`;
    const cur = current(f);
    const onValue = (raw) => {
      try {
        const value = parseInput(f, raw);
        delete clientErrors[f.path];
        resets.delete(f.path);
        if (sameValue(value, start[f.path])) delete edits[f.path]; else edits[f.path] = value;
      } catch (e) { clientErrors[f.path] = e.message; }
      updateRow(f);
    };
    if (f.kind === 'secret') {
      return h('div', { class: 'secret-box' }, icon('lock'), h('input', { class: 'input', id, disabled: true, value: '••••••••', 'aria-label': `${f.label} (secret)` }),
        h('span', { class: 'faint' }, 'Secrets live in .env and are never shown or saved here'));
    }
    if (f.kind === 'enum') {
      const known = f.options.map(String);
      const orphan = cur !== undefined && cur !== null && cur !== '' && !known.includes(String(cur));      // never show a different value than the real one
      return h('select', { class: 'select', id, onchange: (e) => onValue(e.target.value) },
        f.nullable || cur === undefined || cur === null ? h('option', { value: '' }, '(not set)') : null,
        orphan ? h('option', { value: String(cur), selected: true }, `${cur} (current)`) : null,
        f.options.map((o) => h('option', { value: String(o), selected: String(o) === String(cur) }, String(o))));
    }
    if (f.kind === 'combo') {
      const listId = `${id}-list`;
      const names = [...new Set([...(f.options || []).map(String), ...suggestions])];
      return h('div', { class: 'combo' }, h('input', { class: 'input', id, list: listId, value: toInput(f, cur), autocomplete: 'off', spellcheck: 'false',
        placeholder: 'type or pick a model id', oninput: (e) => onValue(e.target.value) }),
        h('datalist', { id: listId }, names.map((n) => h('option', { value: n }))));
    }
    if (f.kind === 'bool') {
      return h('label', { class: 'switch', for: id }, h('input', { type: 'checkbox', id, checked: Boolean(cur), onchange: (e) => onValue(e.target.checked) }), h('span', { class: 'knob' }),
        h('span', { class: 'switch-label' }, Boolean(cur) ? 'on' : 'off'));
    }
    if (f.kind === 'multi') {
      const chosen = new Set((toInput(f, cur) || []).map(String));
      return h('div', { class: 'multi', id }, f.options.map((o) => h('label', { class: 'check' },
        h('input', { type: 'checkbox', checked: chosen.has(String(o)), onchange: (e) => { e.target.checked ? chosen.add(String(o)) : chosen.delete(String(o)); onValue([...f.options.map(String)].filter((x) => chosen.has(x))); } }), String(o))));
    }
    if (f.kind === 'list' || f.kind === 'json') {
      return h('textarea', { class: `textarea ${f.kind === 'json' ? 'mono' : ''}`, id, rows: f.kind === 'json' ? 5 : 3, spellcheck: 'false',
        placeholder: f.kind === 'list' ? 'one per line' : '{}', oninput: (e) => onValue(e.target.value) }, toInput(f, cur));
    }
    const attrs = { class: 'input', id, value: toInput(f, cur), autocomplete: 'off', spellcheck: 'false', oninput: (e) => onValue(e.target.value) };
    if (f.kind === 'int' || f.kind === 'number') {
      attrs.type = 'number'; attrs.step = f.kind === 'int' ? '1' : 'any';
      if (f.min !== undefined) attrs.min = String(f.min);
      if (f.max !== undefined) attrs.max = String(f.max);
    } else attrs.type = 'text';
    return h('input', attrs);
  }

  const rows = new Map();                                          // path -> { root, meta }
  function metaFor(f) {
    const cur = current(f); const def = defaultFor(f, snap.base);
    const pend = pendingPaths();
    const badges = [];
    if (f.path in edits || resets.has(f.path)) badges.push(h('span', { class: 'chip running', title: 'Changed here, not saved yet' }, 'modified'));
    if (isOverridden(f, snap.overrides)) badges.push(h('span', { class: 'chip completed', title: 'Saved in config/overrides.yaml' }, 'saved'));
    if (pend.has(f.path)) badges.push(h('span', { class: 'chip paused', title: 'Saved, but the server is still running the old value (Reload system, or restart if it needs one)' }, 'not running yet'));
    const errs = [...(clientErrors[f.path] ? [clientErrors[f.path]] : []), ...(serverErrors[f.path] || [])];
    const differs = !f.secret && !sameValue(cur, def) && def !== undefined;
    const actions = [];
    if (differs) actions.push(btn('Use default', { size: 'sm', title: `Set to ${showDefault(f, def)}`, onclick: () => useDefault(f) }));
    if (isOverridden(f, snap.overrides) && !resets.has(f.path)) actions.push(btn('Reset', { size: 'sm', ic: 'retry', title: 'Remove the saved value: fall back to config.yaml', onclick: () => stageReset(f) }));
    return h('div', { class: 'set-meta' },
      f.description ? h('div', { class: 'set-desc' }, f.description) : null,
      f.secret ? null : h('div', { class: 'set-default' }, `Default: `, h('code', {}, showDefault(f, def)),
        f.min !== undefined || f.max !== undefined ? h('span', { class: 'faint' }, ` · range ${f.min ?? '…'} to ${f.max ?? '…'}`) : null),
      actions.length ? h('div', { class: 'set-actions' }, actions) : null,
      errs.map((m) => h('div', { class: 'set-err', role: 'alert' }, m)),
      h('div', { class: 'set-badges' }, badges));
  }

  function buildRow(f) {
    const meta = metaFor(f);
    const root = h('div', { class: `set-field${f.path in edits ? ' modified' : ''}${f.secret ? ' secret' : ''}`, 'data-path': f.path },
      h('div', { class: 'set-head' }, h('label', { class: 'set-label', for: `set-${f.path.replace(/[^a-z0-9]/gi, '-')}` }, f.label), h('code', { class: 'set-path' }, f.path)),
      control(f), meta);
    rows.set(f.path, { root, meta });
    return root;
  }
  function updateRow(f) {                                          // refresh badges/errors/defaults without rebuilding the input (keeps focus)
    const r = rows.get(f.path);
    if (r) { const next = metaFor(f); r.meta.replaceWith(next); r.meta = next; r.root.classList.toggle('modified', f.path in edits || resets.has(f.path)); }
    updateChrome(); renderNav();
  }

  function useDefault(f) {
    const def = defaultFor(f, snap.base);
    resets.delete(f.path); delete clientErrors[f.path];
    if (sameValue(def, start[f.path])) delete edits[f.path]; else edits[f.path] = def;
    renderBody(); updateChrome(); renderNav();
  }
  function stageReset(f) { resets.add(f.path); delete edits[f.path]; delete clientErrors[f.path]; renderBody(); updateChrome(); renderNav(); }

  // ------------------------------------------------------------------ layout
  const nav = h('nav', { class: 'set-nav', 'aria-label': 'Settings sections' });
  const content = h('div', { class: 'card set-content', id: 'settings-content' });

  function sectionCounts(s) {
    const paths = leaves(s.children).map((f) => f.path);
    const mod = paths.filter((p) => p in edits || resets.has(p)).length;
    const saved = paths.filter((p) => isOverridden({ path: p }, snap.overrides)).length;
    return { mod, saved };
  }
  function renderNav() {
    clear(nav);
    for (const s of tree) {
      const { mod, saved } = sectionCounts(s);
      nav.append(h('button', { class: 'set-nav-item', type: 'button', 'aria-current': String(section === s.key), 'data-section': s.key, onclick: () => { section = s.key; renderBody(); renderNav(); } },
        h('span', {}, s.label), mod ? h('span', { class: 'badge hot' }, String(mod)) : saved ? h('span', { class: 'badge' }, String(saved)) : null));
    }
  }

  function renderGroup(children, depth) {
    const out = [];
    for (const c of children) {
      if (c.kind === 'group') {
        const inner = renderGroup(c.children, depth + 1);
        if (inner.length) out.push(h('div', { class: 'set-group' }, h('h4', {}, c.label, h('code', { class: 'set-path' }, c.path)), c.description ? h('p', { class: 'faint' }, c.description) : null, ...inner));
      } else if (matches(c, query)) out.push(buildRow(c));
    }
    return out;
  }
  function renderBody() {
    rows.clear(); clear(content);
    if (query) {                                                   // search spans every section
      const hits = tree.flatMap((s) => renderGroup(s.children, 0));
      content.append(h('h3', { class: 'card-title' }, `Search: ${hits.length} match${hits.length === 1 ? '' : 'es'}`), ...(hits.length ? hits : [h('div', { class: 'empty-inline' }, 'No setting matches')]));
      return;
    }
    const s = tree.find((x) => x.key === section);
    if (!s) return;
    content.append(h('h3', { class: 'card-title' }, s.label), s.description ? h('p', { class: 'faint' }, s.description) : null, ...renderGroup(s.children, 0));
  }

  function renderBanner() {
    clear(banner);
    const pend = snap?.pending || [];
    if (pend.length) banner.append(callout('info', h('div', {}, h('b', {}, `${pend.length} saved setting${pend.length === 1 ? '' : 's'} not running yet. `),
      'Use Reload system to apply the ones that can change live (models, prices, voice, credits, history). The rest apply on the next restart. ',
      h('span', { class: 'faint' }, pend.slice(0, 6).map((p) => p.path).join(', ') + (pend.length > 6 ? ' …' : '')))));
    if (lastReload) {
      const r = lastReload;
      banner.append(callout(r.ok ? 'info' : 'danger', h('div', {},
        h('b', {}, r.ok ? 'System reloaded. ' : 'Reload problem. '), r.error ? r.error : null,
        r.unchanged ? 'Nothing had changed. ' : null,
        ...(r.applied || []).map((a) => h('div', {}, `✓ applied live: ${a.group} — ${a.detail}`)),
        ...(r.failed || []).map((f) => h('div', {}, `✗ ${f.group} failed (left as it was): ${f.error}`)),
        (r.restart_required || []).length ? h('div', {}, `↻ needs a restart: ${r.restart_required.join(', ')}`) : null,
        h('div', { class: 'faint' }, 'This admin page and your session were not affected.'))));
    }
  }

  function render() { renderBanner(); clear(body); body.append(nav, content); renderNav(); renderBody(); updateChrome(); }

  // ------------------------------------------------------------------ actions
  async function save() {
    if (Object.keys(clientErrors).length) { toast('Fix the highlighted values first.', 'error'); return; }
    const { set, reset } = buildChange(edits, resets, start, snap.overrides);
    if (!Object.keys(set).length && !reset.length) { toast('Nothing to save.', 'info'); return; }
    const lines = [...Object.entries(set).map(([p, v]) => `${p}: ${showDefault(fields.get(p) || { kind: 'json' }, start[p])} → ${showDefault(fields.get(p) || { kind: 'json' }, v)}`), ...reset.map((p) => `${p}: reset to default`)];
    const risky = [...Object.keys(set), ...reset].some((p) => RISKY.some((r) => p.startsWith(r)));
    const ok = await confirmDialog({ title: `Save ${lines.length} setting${lines.length === 1 ? '' : 's'}?`, danger: risky, confirmLabel: 'Save',
      message: `${lines.slice(0, 8).join('\n')}${lines.length > 8 ? `\n… and ${lines.length - 8} more` : ''}\n\n${risky ? 'Security/API/admin/database settings are included: a wrong value can lock you out after the next restart. ' : ''}Saved to config/overrides.yaml; use Reload system to apply live settings.` });
    if (!ok) return;
    await busy(saveBtn, async () => {
      try {
        snap = await api('PUT', '/system/settings', { body: { set, reset } });
        edits = {}; resets = new Set(); clientErrors = {}; serverErrors = {};
        tree = fieldsOf(snap.schema, snap.secret_paths);
        for (const f of fields.values()) if (!f.secret) start[f.path] = effective(f, snap);
        toast(`Saved ${snap.changed.length} setting${snap.changed.length === 1 ? '' : 's'}.`, 'success');
        render();
      } catch (e) {
        const errs = e.body?.detail?.errors || [];
        serverErrors = errorsByPath(errs);
        toast(errs.length ? `${errs.length} value${errs.length === 1 ? ' was' : 's were'} refused: nothing was saved.` : (e.message || 'Could not save'), 'error');
        renderBody(); updateChrome();
        const first = errs[0]?.path && content.querySelector(`[data-path="${CSS.escape(errs[0].path)}"]`);
        first?.scrollIntoView({ block: 'center' });
      }
    });
  }

  async function reload() {
    const unsaved = dirtyCount();
    const ok = await confirmDialog({ title: 'Reload the system?', confirmLabel: 'Reload',
      message: `Applies the SAVED settings to the running server without a restart: models, prices, voice, credits and history retention change live. Devices, running tasks and this admin page are not interrupted. Settings that need a restart are listed afterwards.${unsaved ? `\n\n${unsaved} unsaved edit${unsaved === 1 ? ' is' : 's are'} not included.` : ''}` });
    if (!ok) return;
    await busy(reloadBtn, async () => {
      try {
        const r = await post('/system/reload');
        lastReload = r;
        if (r.settings) { snap = r.settings; tree = fieldsOf(snap.schema, snap.secret_paths); for (const f of fields.values()) if (!f.secret && !(f.path in edits)) start[f.path] = effective(f, snap); }
        toast(r.ok ? (r.unchanged ? 'Already up to date.' : `Reloaded: ${(r.applied || []).length} applied live, ${(r.restart_required || []).length} need a restart.`) : (r.error || 'Reload had problems.'), r.ok ? 'success' : 'error');
        render();
      } catch (e) { toast(e.message || 'Reload failed', 'error'); }
    });
  }

  await load();
  return { unmount() { alive = false; } };
}

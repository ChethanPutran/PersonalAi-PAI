import { h, s, clear } from './dom.js';
import { icon } from './icons.js';

// ---------------------------------------------------------------- toasts
let toastRoot = null;
let portal = null;                 // where toasts/dialogs mount (the tab's shadow root; falls back to <body>)
export function setPortal(node) { portal = node; toastRoot?.remove(); toastRoot = null; }
const portalNode = () => portal || document.body;
export function toast(message, type = 'info', { title = '', timeout = 5000 } = {}) {
  if (!toastRoot) {
    toastRoot = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'false' });
    portalNode().append(toastRoot);
  }
  if (type === 'error') console.error('[admin]', title || '', message);
  const el = h('div', { class: `toast ${type}` },
    icon(type === 'error' ? 'alert' : type === 'success' ? 'check' : 'info'),
    h('div', { class: 'body' }, title ? h('b', {}, title) : null, message),
    h('button', { class: 'btn ghost icon sm', 'aria-label': 'Dismiss', onclick: () => el.remove() }, icon('close')));
  toastRoot.append(el);
  while (toastRoot.children.length > 4) toastRoot.firstChild.remove();
  if (timeout) setTimeout(() => el.remove(), type === 'error' ? timeout * 2 : timeout);
  return el;
}

// ----------------------------------------------------------- confirm dialog
export function confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
  return new Promise((resolve) => {
    const previous = portalNode().activeElement || document.activeElement;
    const finish = (value) => { scrim.remove(); document.removeEventListener('keydown', onKey, true); previous?.focus?.(); resolve(value); };
    const cancel = h('button', { class: 'btn', onclick: () => finish(false) }, cancelLabel);
    const ok = h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => finish(true) }, confirmLabel);
    const dialog = h('div', { class: 'dialog', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': 'dlg-title', 'aria-describedby': 'dlg-msg' },
      h('h2', { id: 'dlg-title' }, title), h('p', { id: 'dlg-msg' }, message), h('div', { class: 'row' }, cancel, ok));
    const scrim = h('div', { class: 'scrim', onclick: (e) => { if (e.target === scrim) finish(false); } }, dialog);
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      if (e.key === 'Tab') {                                // focus trap
        const f = [cancel, ok]; const i = f.indexOf(portalNode().activeElement || document.activeElement);
        e.preventDefault(); f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    portalNode().append(scrim);
    (danger ? cancel : ok).focus();
  });
}

// -------------------------------------------------------------- primitives
export function statusChip(status, label) {
  const st = String(status || 'unknown').toLowerCase().replace(/[^a-z_]/g, '');
  return h('span', { class: `chip ${st}`, title: label && label.length > 24 ? label : null }, label ?? String(status || 'unknown'));
}

export function btn(label, { variant = '', ic = '', size = '', onclick, title, disabled = false, ariaLabel } = {}) {
  const el = h('button', { class: `btn ${variant} ${size}`.trim(), type: 'button', title, 'aria-label': ariaLabel || (label ? undefined : title),
                           disabled, onclick }, ic ? icon(ic) : null, label ? h('span', {}, label) : null);
  return el;
}

/** Disable a button and show a spinner while `fn` runs; always restores it. */
export async function busy(button, fn) {
  button.setAttribute('aria-busy', 'true'); button.disabled = true;
  try { return await fn(); } finally { button.removeAttribute('aria-busy'); button.disabled = false; }
}

export function skeletonRows(n = 6, cols = 5) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const cells = [];
    for (let c = 0; c < cols; c++) cells.push(h('div', { class: `skeleton skel-cell ${c === 0 ? 'lg' : c === cols - 1 ? 'sm' : ''}` }));
    rows.push(h('div', { class: 'skel-row', 'aria-hidden': 'true' }, cells));
  }
  return h('div', { role: 'status', 'aria-label': 'Loading' }, rows);
}

export function emptyState(title, text, ic = 'empty', action = null) {
  return h('div', { class: 'state' }, icon(ic), h('h3', {}, title), text ? h('p', {}, text) : null, action);
}

export function errorState(err, onRetry) {
  const msg = err?.message || String(err);
  return h('div', { class: 'state error', role: 'alert' }, icon('alert'), h('h3', {}, 'Something went wrong'), h('p', {}, msg),
    err?.issues?.length ? h('ul', {}, err.issues.map((i) => h('li', {}, i))) : null,
    onRetry ? btn('Try again', { ic: 'refresh', onclick: onRetry }) : null);
}

export function callout(kind, ...content) {
  return h('div', { class: `callout ${kind}`, role: kind === 'danger' ? 'alert' : 'note' }, icon(kind === 'info' ? 'info' : 'alert'), h('div', {}, content));
}

export function pageHead(title, subtitle, ...actions) {
  return h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, title), subtitle ? h('p', {}, subtitle) : null),
    actions.length ? h('div', { class: 'page-actions' }, actions) : null);
}

// ------------------------------------------------------------- sparkline
export function sparkline(values, { width = 96, height = 32 } = {}) {
  const svg = s('svg', { class: 'spark', viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
  if (values.length < 2) return svg;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [i / (values.length - 1) * width, height - 3 - ((v - min) / span) * (height - 6)]);
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  svg.append(s('path', { class: 'area', d: `${line} L${width},${height} L0,${height} Z` }), s('path', { class: 'line', d: line }));
  return svg;
}

export function copyButton(text) {
  return btn('', { variant: 'ghost', size: 'sm', ic: 'copy', title: 'Copy', onclick: async () => {
    try { await navigator.clipboard.writeText(text); toast('Copied', 'success', { timeout: 1500 }); } catch (e) { console.error(e); }
  } });
}

export { clear };


// ------------------------------------------------------------------ modal + form dialog
let modalSeq = 0;
/**
 * Generic modal (focus trapped, Esc / scrim closes). `body` is a Node; `actions` are buttons.
 * Returns { close, el }. Resolves nothing — callers own their flow.
 */
export function modal({ title, body, actions = [], wide = false, onClose = () => {} }) {
  const id = `dlg-${++modalSeq}`;
  const previous = portalNode().activeElement || document.activeElement;
  const dialog = h('div', { class: `dialog${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': `${id}-t` },
    h('h2', { id: `${id}-t` }, title), body, actions.length ? h('div', { class: 'row' }, actions) : null);
  const scrim = h('div', { class: 'scrim', onclick: (e) => { if (e.target === scrim) close(); } }, dialog);
  function close() { scrim.remove(); document.removeEventListener('keydown', onKey, true); previous?.focus?.(); onClose(); }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    const f = [...dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])')].filter((x) => !x.disabled);
    if (!f.length) return;
    const root = portalNode(); const i = f.indexOf(root.activeElement || document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
  }
  document.addEventListener('keydown', onKey, true);
  portalNode().append(scrim);
  (dialog.querySelector('input, select, textarea') || dialog.querySelector('button'))?.focus();
  return { close, el: dialog };
}

/**
 * Form dialog. fields: [{ name, label, type='text', value, placeholder, autocomplete, hint }]
 * (type 'checkbox' yields a boolean). onSubmit(values) may throw — its message is shown inline.
 */
export function formDialog({ title, fields, submitLabel = 'Save', onSubmit }) {
  const inputs = {};
  const err = h('div', { class: 'form-error', role: 'alert' });
  const rows = fields.map((f, i) => {
    const id = `ff-${modalSeq + 1}-${f.name}`;
    if (f.type === 'checkbox') {
      inputs[f.name] = h('input', { type: 'checkbox', id, checked: !!f.value });
      return h('label', { class: 'check', for: id }, inputs[f.name], f.label);
    }
    inputs[f.name] = h('input', { class: 'input', id, type: f.type || 'text', value: f.value ?? '', placeholder: f.placeholder, autocomplete: f.autocomplete || 'off', 'aria-describedby': f.hint ? `${id}-h` : null });
    return h('div', { class: 'field' }, h('label', { for: id }, f.label), inputs[f.name], f.hint ? h('div', { class: 'hint', id: `${id}-h` }, f.hint) : null);
  });
  const form = h('form', { class: 'stack', novalidate: true }, ...rows, err);
  const read = () => Object.fromEntries(fields.map((f) => [f.name, f.type === 'checkbox' ? inputs[f.name].checked : inputs[f.name].value]));
  const submit = btn(submitLabel, { variant: 'primary' });
  const cancel = btn('Cancel', { onclick: () => m.close() });
  const go = async () => {
    err.textContent = '';
    await busy(submit, async () => {
      try { await onSubmit(read()); m.close(); } catch (e) { err.textContent = e.message || String(e); }
    });
  };
  submit.addEventListener('click', go);
  form.addEventListener('submit', (e) => { e.preventDefault(); go(); });
  const m = modal({ title, body: form, actions: [cancel, submit] });
  return m;
}

/** Plain info modal with a Close button. */
export function infoDialog({ title, body, wide = true }) {
  const close = btn('Close', { onclick: () => m.close() });
  const m = modal({ title, body, actions: [close], wide });
  return m;
}

/** Collapsible JSON/text block with a labelled copy button. */
export function codeBlock(text, { cls = '', max = null } = {}) {
  const pre = h('pre', { class: `code ${cls}` }, text);
  if (max) pre.style.maxHeight = `${max}px`;
  return pre;
}

export function copyTextButton(getText, label = 'Copy') {
  const b = btn(label, { size: 'sm', variant: 'ghost', ic: 'copy' });
  b.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(getText()); b.lastChild.textContent = 'Copied'; }
    catch { b.lastChild.textContent = 'Failed'; }
    setTimeout(() => { b.lastChild.textContent = label; }, 1200);
  });
  return b;
}

export function fmtDuration(ms) {
  if (ms == null || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)} s`;
  const m = Math.floor(sec / 60), r = Math.round(sec % 60);
  if (m < 60) return `${m}m ${String(r).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString([], { hour12: false });
}

/** Time of day for today, "MM-DD HH:MM:SS" for earlier days (history can reach back several days). */
export function fmtWhen(iso, now = new Date()) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const t = d.toLocaleTimeString([], { hour12: false });
  if (d.toDateString() === now.toDateString()) return t;
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${t}`;
}

export function fmtAgo(iso, now = Date.now()) {
  if (!iso) return '—';
  const sec = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${Math.floor(sec)}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function shortId(id, n = 8) {
  if (!id) return '—';
  const s = String(id);
  return s.length <= n + 4 ? s : s.slice(0, n);
}

export function truncate(text, n = 80) {
  const s = String(text ?? '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function fmtNumber(n) {
  return n == null ? '—' : Number(n).toLocaleString();
}

export function initials(email) {
  return (email || '?').trim().slice(0, 1).toUpperCase();
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString([], { hour12: false, year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** Seconds -> "1h 05m" style (legacy "Uptime" card). */
export function humanDuration(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), r = sec % 60;
  if (m < 60) return `${m}m ${r}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Epoch-seconds timestamp -> HH:MM:SS.mmm (trace times). */
export function fmtEpoch(ts) {
  return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 }) : '';
}

/** Trace durations: 0.42ms / 83ms / 1.20s (legacy format). */
export function fmtMs(ms) {
  if (ms == null) return '';
  if (ms < 1) return `${ms.toFixed(2)}ms`;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

// ------------------------------------------------------------- tokens, cost, prompt breakdown

/** USD amount. null/undefined = unknown ("—"); tiny amounts keep enough digits to be readable. */
export function fmtCost(usd) {
  if (usd == null || Number.isNaN(Number(usd))) return '—';
  const v = Number(usd);
  const sign = v < 0 ? '-' : '';
  const a = Math.abs(v);
  const trim = (str, keep) => { const [i, f = ''] = str.split('.'); let t = f.replace(/0+$/, ''); while (t.length < keep) t += '0'; return t ? `${i}.${t}` : i; };
  if (a >= 1) return `${sign}$${a.toFixed(2)}`;
  if (a === 0) return '$0.00';
  if (a < 0.000001) return `${sign}<$0.000001`;
  if (a >= 0.01) return `${sign}$${trim(a.toFixed(4), 2)}`;
  return `${sign}$${trim(a.toFixed(6), 2)}`;
}

/** Token count, compact: 842, 1.2k, 3.4M. */
export function fmtTokens(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Math.abs(Number(n));
  if (v < 1000) return String(Math.round(Number(n)));
  if (v < 1e6) return `${(Number(n) / 1e3).toFixed(v < 1e4 ? 1 : 0).replace(/\.0$/, '')}k`;
  return `${(Number(n) / 1e6).toFixed(v < 1e7 ? 2 : 1).replace(/\.?0+$/, '')}M`;
}

export const SECTION_LABELS = {
  system_prompt: 'System prompt', capabilities: 'Capabilities / tools', history: 'Conversation history',
  memory: 'Memory', user_prompt: 'User prompt', tool_data: 'Tool data',
};
export const SECTION_ORDER = Object.keys(SECTION_LABELS);

export function sectionLabel(name) { return SECTION_LABELS[name] || String(name).replace(/_/g, ' '); }

/** {name: tokens} -> [{name, label, tokens, pct}] in a stable order (known sections first), pct of the total. */
export function sectionShares(breakdown) {
  const entries = Object.entries(breakdown || {}).filter(([, v]) => Number(v) > 0);
  const total = entries.reduce((a, [, v]) => a + Number(v), 0);
  if (!total) return [];
  const rank = (n) => { const i = SECTION_ORDER.indexOf(n); return i < 0 ? SECTION_ORDER.length : i; };
  return entries.sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([name, v]) => ({ name, label: sectionLabel(name), tokens: Number(v), pct: (100 * Number(v)) / total }));
}

/** Context / output sizes: 128000 -> "128K", 1100000 -> "1.1M"; unknown -> "—". */
export function fmtSize(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  if (v >= 1e6) return `${(v / 1e6).toFixed(v % 1e6 ? 1 : 0).replace(/\.0$/, '')}M`;
  if (v >= 1e3) return `${Math.round(v / 1e3)}K`;
  return String(v);
}

/** Price of a model for display: "$0.10 in · $0.50 out /1M", or the voice units; unknown prices say so. */
export function fmtModelPrice(m) {
  if (m == null) return '—';
  if (m.per_minute_cost !== undefined) return m.per_minute_cost == null ? 'no price' : `${fmtCost(m.per_minute_cost)} /min of audio`;
  if (m.per_1k_chars_cost !== undefined) return m.per_1k_chars_cost == null ? 'no price' : `${fmtCost(m.per_1k_chars_cost)} /1K chars`;
  if (m.input_per_million == null && m.output_per_million == null) return 'no price';
  return `${fmtCost(m.input_per_million ?? 0)} in · ${fmtCost(m.output_per_million ?? 0)} out /1M`;
}

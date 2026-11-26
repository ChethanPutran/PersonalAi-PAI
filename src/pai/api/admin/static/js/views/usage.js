// LLM usage & cost: credits remaining (settable here), tokens and cost by role / model / prompt part, recent calls.
// Data: GET /system/llm/usage; credits: PUT|DELETE /system/llm/credits. Prompt-part tokens are an estimated split
// (providers report one prompt total per call); each call's parts add up to its real prompt tokens.
import { h, clear } from '../dom.js';
import { icon } from '../icons.js';
import { get, api } from '../api.js';
import { skeletonRows, errorState, pageHead, btn, toast, busy, callout } from '../ui.js';
import { fmtCost, fmtTokens, fmtNumber, fmtEpoch, sectionShares, sectionLabel, fmtSize, fmtModelPrice } from '../format.js';
import { barChart } from '../charts.js';
import { seriesArrays, goalBars } from '../usage_data.js';

const REFRESH_MS = 10000;
const LIVE_REFRESH_MS = 3000;          // while a voice session is active the voice card updates every 3 s
// [label, bucket, how many buckets back]
const WINDOWS = [['Last 60 minutes', 'minute', 60], ['Last 24 hours', 'hour', 24], ['Last 30 days', 'day', 30]];
const legend = (items) => h('div', { class: 'ch-legend' }, items.map(([cls, text]) => h('span', {}, h('span', { class: `sw ${cls}` }), text)));

function statCard(label, ic, value, foot) {
  return h('div', { class: 'card stat' }, h('div', { class: 'stat-label' }, icon(ic), label),
    h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-foot' }, h('span', {}, foot || '')));
}

/** Horizontal share bar rows: [{label, value(text), pct, cls}] */
function barRows(rows) {
  return h('div', { class: 'share-rows' }, rows.map((r) => {
    const fill = h('div', { class: `share-fill ${r.cls || ''}` });
    fill.style.width = `${Math.max(0, Math.min(100, r.pct))}%`;
    return h('div', { class: 'share-row' }, h('span', { class: 'share-label', title: r.label }, r.label),
      h('div', { class: 'share-track' }, fill), h('span', { class: 'share-value' }, r.value));
  }));
}

function totalsTable(title, map, empty) {
  const keys = Object.keys(map || {}).sort((a, b) => (map[b].total_tokens || 0) - (map[a].total_tokens || 0));
  const head = h('thead', {}, h('tr', {}, ['', 'Calls', 'Tokens', 'Cost'].map((t, i) => h('th', { class: i ? 'num' : '' }, t))));
  const body = h('tbody', {}, keys.map((k) => {
    const t = map[k];
    return h('tr', {}, h('td', { class: 'mono', title: k }, k), h('td', { class: 'num' }, fmtNumber(t.calls)), h('td', { class: 'num' }, fmtTokens(t.total_tokens)),
      h('td', { class: 'num' }, fmtCost(t.cost_usd), t.unpriced_calls ? h('span', { class: 'faint', title: `${t.unpriced_calls} call(s) with no known price are not included` }, ` +${t.unpriced_calls}?`) : null));
  }));
  return h('div', { class: 'card' }, h('h3', { class: 'card-title' }, title),
    keys.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, head, body)) : h('div', { class: 'empty-inline' }, empty));
}

export async function mount(container) {
  let alive = true; let timer = null; let editing = false; let windowIdx = 1; let liveTimer = false;
  const page = h('div', { class: 'page usage-page' });
  container.append(page);
  const body = h('div', { class: 'stack' }, h('div', { class: 'card' }, skeletonRows(4, 4)));
  page.append(pageHead('LLM usage & cost', 'Credits remaining, tokens and cost by role, model and prompt part. Totals survive restarts (JSONL ledger).',
    btn('Refresh', { ic: 'refresh', onclick: () => load() })), body);

  async function load() {
    try {
      const [, bucket, count] = WINDOWS[windowIdx];
      const size = { minute: 60, hour: 3600, day: 86400 }[bucket];
      const since = Math.floor(Date.now() / 1000 / size) * size - (count - 1) * size;
      const [u, series, traces, models] = await Promise.all([
        get('/system/llm/usage', { params: { recent: 25 } }),
        get('/system/llm/usage/series', { params: { bucket, since } }),
        get('/system/traces', { params: { limit: 60 } }).catch(() => ({ traces: [] })),   // goals chart is optional
        get('/system/models').catch(() => null),                                         // models table is optional
      ]);
      if (alive && !editing) render(u, series, traces.traces || [], models);
      if (alive) setPace((u.voice?.active_sessions || 0) > 0);
    } catch (e) {
      if (!alive) return;
      clear(body);
      body.append(h('div', { class: 'card' }, e.status === 404 ? callout('info', 'LLM cost tracking is turned off (llm_cost.track: false in config).') : errorState(e, load)));
    }
  }

  /** The models each role uses now, what they cost and can do (from config/models.yaml), and every model to choose from. */
  function modelsCard(m) {
    if (!m) return null;
    const flag = (v, label) => (v == null ? null : h('span', { class: `chip ${v ? 'completed' : 'pending'}`, title: `${label}: ${v ? 'yes' : 'no'}` }, `${label} ${v ? '✓' : '✗'}`));
    const roleRows = m.roles.map((r) => h('tr', {},
      h('td', {}, r.role), h('td', { class: 'mono', title: r.model }, r.model),
      h('td', {}, r.known ? fmtModelPrice(r) : h('span', { class: 'err-text', title: 'Add it to config/models.yaml' }, 'not in models.yaml')),
      h('td', {}, r.kind === 'llm' && r.known ? [flag(r.tool_use, 'tools'), ' ', flag(r.reasoning, 'reasoning'), ' ', flag(r.vision, 'vision'), ` ctx ${fmtSize(r.context_length)}`] : '')));
    const allRows = m.llm.map((x) => h('tr', {}, h('td', { class: 'mono', title: x.name }, x.name), h('td', {}, x.provider || ''), h('td', {}, fmtModelPrice(x)),
      h('td', { class: 'num' }, fmtSize(x.context_length)), h('td', {}, [flag(x.tool_use, 'tools'), ' ', flag(x.reasoning, 'reasoning'), ' ', flag(x.vision, 'vision')])));
    const thead = (cols) => h('thead', {}, h('tr', {}, cols.map((c) => h('th', {}, c))));
    return h('div', { class: 'card models-card', id: 'models-table' }, h('h3', { class: 'card-title' }, 'Models & prices'),
      h('p', { class: 'faint' }, `From ${m.source || 'config/models.yaml'}. Prices are USD; edit that file to add or change a model.`),
      (m.warnings || []).length ? h('div', { class: 'callout warn', role: 'note' }, icon('alert'), h('div', {}, m.warnings.map((w) => h('div', {}, w)))) : null,
      h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, thead(['Role', 'Model', 'Price', 'Can do']), h('tbody', {}, roleRows))),
      h('details', { class: 'models-all' }, h('summary', {}, `All ${m.llm.length} language models you can choose (cheapest first)`),
        h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, thead(['Model', 'Provider', 'Price', 'Context', 'Can do']), h('tbody', {}, allRows)))));
  }

  /** Real-time voice cost: audio transcribed (billed per second) and text spoken (billed per character). */
  function voiceCard(v) {
    if (!v) return null;
    const active = v.active_sessions || 0;
    const mins = (v.stt.units || 0) / 60;
    const stat = (label, ic, value, foot) => h('div', { class: 'card stat voice-stat' }, h('div', { class: 'stat-label' }, icon(ic), label),
      h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-foot' }, h('span', {}, foot)));
    return h('div', { class: 'card voice-card', id: 'voice-cost' },
      h('div', { class: 'chart-controls' }, h('h3', { class: 'card-title' }, 'Voice cost (real time)'),
        h('span', { class: `chip ${active ? 'running' : 'pending'}` }, active ? `${active} live session${active === 1 ? '' : 's'} · updating every ${LIVE_REFRESH_MS / 1000}s` : 'no live session')),
      h('div', { class: 'grid stats' },
        stat('Voice cost, total', 'coins', fmtCost(v.total_cost_usd), `${fmtCost(v.last_hour_cost_usd)} in the last hour`),
        stat('Speech to text', 'mic', fmtCost(v.stt.cost_usd), `${mins.toFixed(mins < 10 ? 2 : 1)} min of audio · ${fmtNumber(v.stt.calls)} utterance${v.stt.calls === 1 ? '' : 's'}`),
        stat('Text to speech', 'speaker', fmtCost(v.tts.cost_usd), `${fmtNumber(Math.round(v.tts.units || 0))} characters · ${fmtNumber(v.tts.calls)} repl${v.tts.calls === 1 ? 'y' : 'ies'}`)),
      (v.stt.unpriced_calls || v.tts.unpriced_calls) ? h('div', { class: 'faint' }, `${v.stt.unpriced_calls + v.tts.unpriced_calls} voice call(s) have no price: set voice.airouter.stt_cost_per_minute_usd / tts_cost_per_1k_chars_usd in config.`) : null);
  }

  function creditsCard(c) {
    const input = h('input', { class: 'input', type: 'number', min: '0', step: '0.01', id: 'credits-input', 'aria-label': 'Credits you have now, in USD',
      placeholder: 'Credits you have now (USD)', value: c.configured ? String(c.remaining_usd > 0 ? Number(c.remaining_usd.toFixed(4)) : 0) : '' });
    input.addEventListener('focus', () => { editing = true; });
    input.addEventListener('blur', () => { editing = false; });
    const save = btn('Set balance', { variant: 'primary', ic: 'check', onclick: () => busy(save, async () => {
      const v = Number(input.value);
      if (input.value === '' || !Number.isFinite(v) || v < 0) { toast('Enter a credit amount in USD (0 or more).', 'error'); return; }
      try { await api('PUT', '/system/llm/credits', { body: { balance_usd: v } }); toast('Credits updated.', 'success'); editing = false; await load(); }
      catch (e) { toast(e.message || 'Could not update credits', 'error'); }
    }) });
    const reset = btn('Use config value', { ic: 'retry', title: 'Drop the balance set here and go back to llm_cost.initial_credits_usd', onclick: () => busy(reset, async () => {
      try { await api('DELETE', '/system/llm/credits'); toast('Back to the configured credits.', 'success'); editing = false; await load(); }
      catch (e) { toast(e.message || 'Could not reset credits', 'error'); }
    }) });
    const form = h('div', { class: 'credit-form' }, input, save, c.source === 'admin' ? reset : null);

    if (!c.configured) {
      return h('div', { class: 'card credits' }, h('div', { class: 'stat-label' }, icon('coins'), 'Credits remaining'),
        h('div', { class: 'faint' }, 'No balance set. Enter the credits you have on the gateway (USD) to track what is left — or set llm_cost.initial_credits_usd in config.'), form);
    }
    const left = c.remaining_usd;
    const pctLeft = c.initial_usd > 0 ? Math.max(0, Math.min(100, 100 - (c.used_pct ?? 0))) : 0;
    const level = c.exhausted ? 'danger' : pctLeft < 15 ? 'warn' : 'ok';
    const fill = h('div', { class: `credit-fill ${level}` }); fill.style.width = `${pctLeft}%`;
    return h('div', { class: 'card credits' },
      h('div', { class: 'stat-label' }, icon('coins'), 'Credits remaining'),
      h('div', { class: `stat-value credit-${level}` }, fmtCost(left), c.exhausted ? h('small', {}, ' exhausted') : null),
      h('div', { class: 'credit-track', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(Math.round(pctLeft)), 'aria-label': 'Credits remaining' }, fill),
      h('div', { class: 'stat-foot' }, h('span', {}, `${fmtCost(c.spent_usd)} spent of ${fmtCost(c.initial_usd)} · ${c.source === 'admin' ? `balance set ${c.as_of ? fmtEpoch(c.as_of) : ''}` : 'from config, all tracked spend'}`)),
      c.unpriced_calls ? h('div', { class: 'faint' }, `${c.unpriced_calls} call(s) have no known price and could not be deducted — set llm_pricing in config.`) : null,
      form);
  }

  function render(u, series, traces, models) {
    clear(body);
    const t = u.total;
    const stats = h('div', { class: 'grid stats' },
      statCard('Total cost', 'coins', fmtCost(t.cost_usd), t.unpriced_calls ? `${t.unpriced_calls} call(s) unpriced` : 'priced calls only'),
      statCard('Tokens', 'cpu', fmtTokens(t.total_tokens), `${fmtTokens(t.prompt_tokens)} prompt · ${fmtTokens(t.completion_tokens)} completion`),
      statCard('LLM calls', 'chat', fmtNumber(t.calls), `since ${fmtEpoch(u.since)}`));

    const shares = sectionShares(Object.fromEntries(Object.entries(u.by_section || {}).map(([k, v]) => [k, v.tokens])));
    const sectionCard = h('div', { class: 'card' }, h('h3', { class: 'card-title' }, 'Prompt tokens by part'),
      h('p', { class: 'faint' }, 'Estimated split of what was sent to the model; each call’s parts add up to its real prompt tokens.'),
      shares.length ? barRows(shares.map((s) => ({ label: s.label, value: `${fmtTokens(s.tokens)} · ${s.pct.toFixed(0)}%`, pct: s.pct, cls: `sec-${s.name}` })))
        : h('div', { class: 'empty-inline' }, 'No calls with a breakdown yet'));

    const recent = (u.recent || []).slice().reverse();
    const recentCard = h('div', { class: 'card' }, h('h3', { class: 'card-title' }, 'Recent calls'),
      recent.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ['Time', 'Role', 'Model', 'Prompt', 'Completion', 'Cost', 'Largest part'].map((x, i) => h('th', { class: i > 2 && i < 6 ? 'num' : '' }, x)))),
        h('tbody', {}, recent.map((r) => {
          const top = sectionShares(r.breakdown)[0];
          return h('tr', {}, h('td', {}, fmtEpoch(r.ts)), h('td', {}, r.route), h('td', { class: 'mono' }, r.model), r.unit ? h('td', { class: 'num', colspan: '2', title: 'Voice usage is billed per second of audio / per character, not per token' }, `${fmtNumber(Math.round(r.units * 100) / 100)} ${r.unit}`)
            : [h('td', { class: 'num' }, fmtTokens(r.prompt_tokens)), h('td', { class: 'num' }, fmtTokens(r.completion_tokens))], h('td', { class: 'num' }, fmtCost(r.cost_usd)),
            h('td', {}, top ? `${sectionLabel(top.name)} (${top.pct.toFixed(0)}%)` : '—'));
        })))) : h('div', { class: 'empty-inline' }, 'No calls yet'));

    const sa = seriesArrays(series);
    const windowSel = h('select', { class: 'select', id: 'usage-window', 'aria-label': 'Time window',
      onchange: (e) => { windowIdx = Number(e.target.value); load(); } },
      WINDOWS.map(([label], i) => h('option', { value: String(i), selected: i === windowIdx }, label)));
    const overTime = h('div', { class: 'card' }, h('div', { class: 'chart-controls' }, h('h3', { class: 'card-title' }, 'Usage over time'), windowSel),
      h('div', { class: 'grid two' },
        h('div', {}, h('div', { class: 'faint' }, 'Tokens per period'),
          barChart({ labels: sa.labels, titles: sa.titles, ariaLabel: 'Tokens over time', fmt: fmtTokens,
            series: [{ name: 'Prompt', values: sa.prompt, cls: 'ch-s0' }, { name: 'Completion', values: sa.completion, cls: 'ch-s1' }] }),
          legend([['ch-s0', 'Prompt'], ['ch-s1', 'Completion']])),
        h('div', {}, h('div', { class: 'faint' }, 'Cost per period'),
          barChart({ labels: sa.labels, titles: sa.titles, ariaLabel: 'Cost over time', fmt: fmtCost,
            series: [{ name: 'Cost', values: sa.cost, cls: 'ch-s3' }] }),
          legend([['ch-s3', 'Cost (priced calls only)']]))));
    const gb = goalBars(traces);
    const perGoal = h('div', { class: 'card' }, h('h3', { class: 'card-title' }, 'Cost and tokens per goal'),
      h('p', { class: 'faint' }, 'Each bar is one goal (trace) with every LLM call made for it — planner, chat, agents and tool-triggered calls. Hover for the goal; open Traces for the call-by-call view.'),
      gb.ids.length ? h('div', { class: 'grid side' },
        h('div', {}, h('div', { class: 'faint' }, 'Cost'), barChart({ labels: gb.labels, titles: gb.titles, ariaLabel: 'Cost per goal', fmt: fmtCost, series: [{ name: 'Cost', values: gb.cost, cls: 'ch-s3' }] })),
        h('div', {}, h('div', { class: 'faint' }, 'Tokens'), barChart({ labels: gb.labels, titles: gb.titles, ariaLabel: 'Tokens per goal', fmt: fmtTokens, series: [{ name: 'Tokens', values: gb.tokens, cls: 'ch-s0' }] })))
        : h('div', { class: 'empty-inline' }, 'No goals with LLM usage yet'));

    body.append(creditsCard(u.credits), ...[voiceCard(u.voice)].filter(Boolean), stats, ...[modelsCard(models)].filter(Boolean), overTime, perGoal,
      h('div', { class: 'grid two' }, totalsTable('By role', u.by_route, 'No calls yet'), totalsTable('By model', u.by_model, 'No calls yet')),
      sectionCard, recentCard);
  }

  // 10 s normally; 3 s while a voice session is live so the voice cost moves in real time
  function setPace(live) {
    if (!alive || live === liveTimer) return;
    liveTimer = live; clearInterval(timer);
    timer = setInterval(load, live ? LIVE_REFRESH_MS : REFRESH_MS);
  }

  await load();
  if (!liveTimer) { clearInterval(timer); timer = setInterval(load, REFRESH_MS); }
  return { unmount() { alive = false; clearInterval(timer); } };
}

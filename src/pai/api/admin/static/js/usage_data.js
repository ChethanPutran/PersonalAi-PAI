// Pure data prep for the cost/token charts (no DOM): trace events -> LLM calls, API series -> chart arrays.

/** Axis ticks for 0..max with a "nice" step (1, 2, 2.5, 5 x 10^n). Always includes 0 and covers max. */
export function niceTicks(max, count = 4) {
  const m = Number(max);
  if (!Number.isFinite(m) || m <= 0) return [0, 1];
  const rough = m / Math.max(1, count);
  const pow = 10 ** Math.floor(Math.log10(rough));
  const f = rough / pow;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * pow;
  const ticks = [];
  for (let v = 0; v < m + step * 0.999; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

/** Running total: [1, 2, 3] -> [1, 3, 6]. Null/NaN count as 0. */
export function cumulative(values) {
  let sum = 0;
  return values.map((v) => { sum += Number.isFinite(Number(v)) ? Number(v) : 0; return sum; });
}

/**
 * Every LLM call in a trace (one goal), in order, with the span it ran under, so tool-triggered and
 * planner/chat calls all show up. `events` are the raw trace events from GET /system/traces/{id}.
 */
export function llmCallsFromEvents(events) {
  const spanNames = new Map();
  for (const e of events || []) if (e.kind === 'span.start') spanNames.set(e.span_id, e.name);
  const calls = [];
  for (const e of events || []) {
    if (e.kind !== 'llm.response' || e.error) continue;
    const a = e.attributes || {};
    const usage = a.usage || {};
    const prompt = Number(a.prompt_tokens ?? usage.prompt_tokens ?? 0);
    const completion = Number(a.completion_tokens ?? usage.completion_tokens ?? 0);
    const total = Number(a.total_tokens ?? usage.total_tokens ?? prompt + completion);
    if (!total && !prompt && !completion) continue;           // a call that reported no usage
    calls.push({
      ts: e.timestamp, span: spanNames.get(e.span_id) || null, model: a.model || '', route: a.route || '',
      prompt_tokens: prompt, completion_tokens: completion, total_tokens: total,
      cost_usd: a.cost_usd == null ? null : Number(a.cost_usd), duration_ms: e.duration_ms ?? null,
    });
  }
  return calls.sort((x, y) => x.ts - y.ts);
}

/** Speech steps of a voice trace (voice.stt / voice.tts spans): billed per second of audio / per character, not per token. */
export function speechStepsFromEvents(events) {
  const steps = [];
  for (const e of events || []) {
    if (e.kind !== 'span.end' || (e.name !== 'voice.stt' && e.name !== 'voice.tts')) continue;
    const a = e.attributes || {};
    steps.push({ ts: e.timestamp, kind: e.name === 'voice.stt' ? 'STT' : 'TTS', model: a.model || a.provider || '', units: a.units == null ? null : Number(a.units),
      unit: a.unit || (e.name === 'voice.stt' ? 'seconds' : 'characters'), cost_usd: a.cost_usd == null ? null : Number(a.cost_usd), duration_ms: e.duration_ms ?? null, error: e.error || null });
  }
  return steps.sort((x, y) => x.ts - y.ts);
}

/** Label for one call on a chart axis: "1 · planner" (position + role, else span, else model). */
export function callLabel(call, index) {
  const what = call.route || (call.span ? String(call.span).split('.').pop() : '') || call.model || 'call';
  return `${index + 1} · ${what}`;
}

/** Totals over a list of calls; cost counts known costs only and `unpriced` counts the rest. */
export function totalsOf(calls) {
  const t = { calls: calls.length, prompt: 0, completion: 0, total: 0, cost: 0, unpriced: 0 };
  for (const c of calls) {
    t.prompt += c.prompt_tokens; t.completion += c.completion_tokens; t.total += c.total_tokens;
    if (c.cost_usd == null) t.unpriced += 1; else t.cost += c.cost_usd;
  }
  return t;
}

/** Short x-axis label for a bucket start (epoch s): time for minute/hour, date for day. */
export function bucketLabel(t, bucketSeconds) {
  const d = new Date(t * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  if (bucketSeconds >= 86400) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** API series -> arrays ready for the charts. */
export function seriesArrays(series) {
  const pts = series?.points || [];
  return {
    labels: pts.map((p) => bucketLabel(p.t, series.bucket_seconds)),
    titles: pts.map((p) => new Date(p.t * 1000).toLocaleString()),
    prompt: pts.map((p) => p.prompt_tokens), completion: pts.map((p) => p.completion_tokens),
    total: pts.map((p) => p.total_tokens), cost: pts.map((p) => p.cost_usd), calls: pts.map((p) => p.calls),
  };
}

/** Recent traces (goals) -> bars of cost and tokens per goal, oldest first. */
export function goalBars(traces, limit = 30) {
  const rows = (traces || []).filter((t) => (t.total_tokens || 0) > 0 || (t.cost_usd || 0) > 0)
    .sort((a, b) => a.started_at - b.started_at).slice(-limit);
  return {
    ids: rows.map((t) => t.trace_id),
    labels: rows.map((t) => String(t.goal || t.name || t.trace_id).slice(0, 18)),
    titles: rows.map((t) => String(t.goal || t.name || t.trace_id).slice(0, 120)),
    tokens: rows.map((t) => t.total_tokens || 0), cost: rows.map((t) => t.cost_usd || 0),
  };
}

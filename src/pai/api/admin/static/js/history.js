// Pure helpers for the history-aware Events and DAG views.

/** [label, days] — days: 0 = live only (this session), null = everything retained, else a number of days. */
export const RANGES = [['This session', 0], ['Last hour', 1 / 24], ['Last 24 hours', 1], ['All retained', null]];

const keyOf = (e) => `${e.event}|${JSON.stringify(e.data)}`;

/**
 * Past events (oldest first, from the server) + events received live since the page opened -> one log, oldest
 * first, capped to the newest `max`. A live event that history already has (it was recorded while the history
 * request was in flight) appears once.
 */
export function mergeHistory(past, live, max = 500) {
  const seen = new Map();
  for (const e of past) seen.set(keyOf(e), (seen.get(keyOf(e)) || 0) + 1);
  const fresh = [];
  for (const e of live) {
    const k = keyOf(e);
    const n = seen.get(k) || 0;
    if (n > 0) seen.set(k, n - 1); else fresh.push(e);
  }
  const all = [...past, ...fresh];
  return all.length > max ? all.slice(all.length - max) : all;
}

/** "What we are showing" for the DAG list header: the day window as words. */
export function windowLabel(days, retentionDays) {
  if (days == null) return retentionDays ? `all, ${retentionDays} days` : 'all retained';
  if (days === 0) return 'active only';
  if (days < 1) return `last ${Math.round(days * 24)} hour${Math.round(days * 24) === 1 ? '' : 's'}`;
  return days === 1 ? 'last 24 hours' : `last ${days} days`;
}

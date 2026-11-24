// Shared poller for /system/admin/data (the legacy page's single aggregate: summary, devices, plugins, users).
import { get } from './api.js';

const INTERVAL_KEY = 'pai_admin_refresh_ms';
export const DEFAULT_REFRESH_MS = 5000;

export function loadInterval() {
  try {
    const raw = localStorage.getItem(INTERVAL_KEY);
    if (raw === null) return DEFAULT_REFRESH_MS;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) && n >= 0 ? n : DEFAULT_REFRESH_MS;
  } catch { return DEFAULT_REFRESH_MS; }
}
export function saveInterval(ms) { try { localStorage.setItem(INTERVAL_KEY, String(ms)); } catch { /* ignore */ } }
export function fmtInterval(ms) { return !ms ? 'manual' : ms < 1000 ? `${ms}ms` : `${ms / 1000}s`; }

export function createAdminData() {
  let state = { data: null, error: null, at: null, loading: false };
  let timer = null; let ms = loadInterval();
  const subs = new Set();
  const emit = () => subs.forEach((fn) => fn(state));

  async function refresh() {
    state = { ...state, loading: true }; emit();
    try {
      const data = await get('/system/admin/data', { headers: { 'Cache-Control': 'no-store' } });
      state = { data, error: null, at: new Date(), loading: false };
    } catch (e) {
      state = { ...state, error: e, loading: false };
    }
    emit();
    return state;
  }
  return {
    get: () => state,
    refresh,
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    interval: () => ms,
    setInterval(next) { ms = next; saveInterval(next); this.start(); },
    start() { this.stop(); refresh(); if (ms > 0) timer = setInterval(refresh, ms); },
    stop() { clearInterval(timer); timer = null; },
    reset() { this.stop(); state = { data: null, error: null, at: null, loading: false }; emit(); },
  };
}

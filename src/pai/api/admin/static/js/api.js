// API client: base URL, bearer token (memory + sessionStorage only), typed errors, visible failures.
import { openStream } from './sse.js';

const BASE = '/api/v1';
// Shared with the existing admin.html login (it owns sign-in / sign-out).
const TOKEN_KEY = 'pai_admin_token';
const EMAIL_KEY = 'pai_admin_email';

export class ApiError extends Error {
  constructor(status, message, { issues = [], body = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.issues = issues;
    this.body = body;
  }
}

export const AUTH_EVENT = 'admin:unauthorized';

export const session = {
  get token() { try { return localStorage.getItem(TOKEN_KEY) || null; } catch { return null; } },
  get user() { try { return { email: localStorage.getItem(EMAIL_KEY) || '' }; } catch { return { email: '' }; } },
  load() { return !!this.token; },
  save(token, email) {
    try { localStorage.setItem(TOKEN_KEY, token); localStorage.setItem(EMAIL_KEY, email || ''); } catch (e) { console.error('[admin] cannot persist session', e); }
  },
  clear() {
    try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EMAIL_KEY); } catch { /* ignore */ }
  },
};

export function describeError(status, body) {
  const d = body && body.detail !== undefined ? body.detail : body;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return { message: d.message, issues: d.issues || [] };
  if (Array.isArray(d)) {                                 // FastAPI validation errors
    const issues = d.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`);
    return { message: 'Invalid request', issues };
  }
  if (typeof d === 'string') return { message: d, issues: [] };
  return { message: `HTTP ${status}`, issues: [] };
}

export async function api(method, path, { body, params, signal, headers = {}, raw = false } = {}) {
  let url = BASE + path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
    const qs = q.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }
  const init = { method, signal, headers: { Accept: 'application/json', ...headers } };
  if (session.token) init.headers.Authorization = `Bearer ${session.token}`;
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) init.body = body;
    else { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json'; }
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    console.error(`[admin] ${method} ${path} network error`, e);
    throw new ApiError(0, 'Cannot reach the server', { body: String(e) });
  }
  if (raw && res.ok) return res;
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (!res.ok) {
    const { message, issues } = describeError(res.status, data);
    console.error(`[admin] ${method} ${path} -> ${res.status}`, data);
    if (res.status === 401 || res.status === 403) { window.dispatchEvent(new CustomEvent(AUTH_EVENT)); }
    throw new ApiError(res.status, message, { issues, body: data });
  }
  return data;
}

export const get = (path, opts) => api('GET', path, opts);
export const post = (path, body, opts) => api('POST', path, { body: body ?? {}, ...opts });

// A stream that is refused with 401/403 means the session is gone: tell the shell once.
const guard = (onStatus = () => {}) => (st, info = {}) => {
  if (st === 'closed' && [401, 403].includes(info.error?.status)) window.dispatchEvent(new CustomEvent(AUTH_EVENT));
  onStatus(st, info);
};

/** Admin SSE (task.* / dag.*), authenticated with the Authorization header. */
export function openAdminEvents(onEvent, onStatus) {
  onStatus = guard(onStatus);
  return openStream(`${BASE}/admin/events`, { headers: { Authorization: `Bearer ${session.token}` }, onEvent, onStatus });
}

/** One-shot streamed POST (LLM test). Resolves when the stream ends. */
export function streamPost(path, body, onEvent, signal) {
  return new Promise((resolve, reject) => {
    const stream = openStream(BASE + path, {
      method: 'POST', body: JSON.stringify(body), reconnect: false,
      headers: { Authorization: `Bearer ${session.token}`, 'Content-Type': 'application/json' },
      onEvent, onStatus: (st, info) => { if (st === 'closed') (info.error ? reject(info.error) : resolve()); },
    });
    signal?.addEventListener('abort', () => stream.close());
  });
}

/** Sign in through the same endpoint the legacy page used; only administrators get a session. */
export async function login(email, password) {
  const res = await fetch(`${BASE}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, describeError(res.status, body).message);
  if (!body.is_admin) throw new ApiError(403, 'Account does not have admin access.');
  session.save(body.access_token, body.email || email);
  return body;
}

/** Validate a stored token (same as the legacy boot()). Resolves to the user or throws. */
export async function whoAmI() {
  const me = await get('/auth/me');
  if (!me.is_admin) throw new ApiError(403, 'not admin');
  return me;
}

/** Live trace stream (SSE over fetch). Calls onEvent(parsedEvent) for each `data:` frame. Returns {close}. */
export function openTraceStream(onEvent, onStatus = () => {}) {
  onStatus = guard(onStatus);
  return openStream(`${BASE}/system/traces/stream`, { headers: { Authorization: `Bearer ${session.token}` },
    onEvent: (evt) => { if (evt && evt.data) onEvent(evt.data); }, onStatus });
}

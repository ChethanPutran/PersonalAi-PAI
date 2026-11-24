// Server-Sent Events over fetch (so the Authorization header can be used — no token in URLs).

/** Incremental SSE parser: feed text chunks, get complete events back. */
export class SseParser {
  constructor() { this.buffer = ''; this.event = null; this.data = []; this.id = null; }

  push(chunk) {
    this.buffer += chunk;
    const out = [];
    let idx;
    while ((idx = this.buffer.search(/\r\n|\n|\r/)) !== -1) {
      const line = this.buffer.slice(0, idx);
      const m = this.buffer.slice(idx).match(/^(\r\n|\n|\r)/);
      this.buffer = this.buffer.slice(idx + m[0].length);
      const ev = this._line(line);
      if (ev) out.push(ev);
    }
    return out;
  }

  _line(line) {
    if (line === '') {                       // blank line = dispatch
      if (!this.data.length && this.event == null) return null;
      const ev = { event: this.event || 'message', data: this.data.join('\n'), id: this.id };
      this.event = null; this.data = [];
      return ev;
    }
    if (line.startsWith(':')) return null;   // comment / keep-alive
    const i = line.indexOf(':');
    const field = i === -1 ? line : line.slice(0, i);
    let value = i === -1 ? '' : line.slice(i + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.event = value;
    else if (field === 'data') this.data.push(value);
    else if (field === 'id') this.id = value;
    return null;
  }
}

export function backoffDelay(attempt, base = 1000, max = 15000) {
  return Math.min(max, base * 2 ** Math.max(0, attempt));
}

/**
 * Open an SSE stream and keep it open. Reconnects with exponential backoff.
 *   onEvent({event, data(parsed JSON or text), id})   onStatus('connecting'|'live'|'retrying'|'closed', info)
 * Returns {close()}.
 */
export function openStream(url, { headers = {}, method = 'GET', body = null, onEvent, onStatus = () => {},
                                  reconnect = true, fetchImpl = (...a) => fetch(...a) } = {}) {
  const ctrl = new AbortController();
  let closed = false;

  async function run() {
    let attempt = 0;
    while (!closed) {
      try {
        onStatus('connecting', { attempt });
        const res = await fetchImpl(url, { method, headers: { Accept: 'text/event-stream', ...headers }, body, signal: ctrl.signal });
        if (!res.ok) {
          const err = new Error(`stream ${url} -> HTTP ${res.status}`);
          err.status = res.status;
          throw err;
        }
        attempt = 0;
        onStatus('live', {});
        const parser = new SseParser();
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const ev of parser.push(decoder.decode(value, { stream: true }))) {
            let data = ev.data;
            try { data = JSON.parse(ev.data); } catch { /* keep text */ }
            onEvent({ event: ev.event, data, id: ev.id });
          }
        }
        if (!reconnect) { onStatus('closed', {}); return; }
      } catch (err) {
        if (closed || err.name === 'AbortError') break;
        console.error('[admin] stream error:', err);
        if (err.status === 401 || err.status === 403 || !reconnect) { onStatus('closed', { error: err }); return; }
      }
      if (closed || !reconnect) break;
      const delay = backoffDelay(attempt++);
      onStatus('retrying', { attempt, delay });
      await new Promise((r) => setTimeout(r, delay));
    }
    onStatus('closed', {});
  }
  run();
  return { close() { closed = true; ctrl.abort(); } };
}

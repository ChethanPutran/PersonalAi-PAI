// Minimal Chrome DevTools Protocol driver (no dependencies; Node >= 22 has a global WebSocket).
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export async function launchChrome({ width = 1440, height = 900, chrome = process.env.CHROME || 'google-chrome' } = {}) {
  const port = 9300 + Math.floor(Math.random() * 500);
  const dir = mkdtempSync(join(tmpdir(), 'pai-chrome-'));
  const proc = spawn(chrome, [`--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, '--headless=new', '--no-sandbox', '--disable-gpu',
    '--hide-scrollbars', '--force-color-profile=srgb', `--window-size=${width},${height}`, 'about:blank'], { stdio: 'ignore' });
  let tabs;
  for (let i = 0; i < 60; i++) {
    try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (tabs.length) break; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 150));
  }
  if (!tabs?.length) { proc.kill(); throw new Error('Chrome did not start'); }
  const page = tabs.find((t) => t.type === 'page') || tabs[0];
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const listeners = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); msg.error ? rej(new Error(msg.error.message)) : res(msg.result); }
    else listeners.forEach((fn) => fn(msg));
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  const logs = [];
  listeners.push((m) => {
    if (m.method === 'Runtime.consoleAPICalled') logs.push(`${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description ?? '').join(' ')}`);
    if (m.method === 'Runtime.exceptionThrown') logs.push(`EXCEPTION: ${m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text}`);
    if (m.method === 'Log.entryAdded') logs.push(`${m.params.entry.level}: ${m.params.entry.text} ${m.params.entry.url || ''}`);
  });
  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  const api = {
    logs, send,
    async setViewport(w, h) { await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false }); },
    async goto(url) { await send('Page.navigate', { url }); await new Promise((r) => setTimeout(r, 700)); },
    async eval(expression) { const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; },
    async sleep(ms) { await new Promise((r) => setTimeout(r, ms)); },
    async waitFor(expr, timeout = 8000) { const t0 = Date.now(); while (Date.now() - t0 < timeout) { if (await api.eval(expr)) return true; await api.sleep(120); } throw new Error('timeout waiting for: ' + expr); },
    async screenshot(path) { const r = await send('Page.captureScreenshot', { format: 'png' }); writeFileSync(path, Buffer.from(r.data, 'base64')); return path; },
    async close() { try { ws.close(); } catch { /* */ } proc.kill('SIGKILL'); },
  };
  return api;
}

import { h, clear } from '../dom.js';
import { icon } from '../icons.js';
import { get, post, api, streamPost, ApiError } from '../api.js';
import { btn, busy, toast, callout, pageHead, statusChip } from '../ui.js';
import { validateDag, SAMPLE_DAG } from '../validate.js';

export async function mount(container, ctx) {
  let alive = true; let caps = null; let voice = null;
  const page = h('div', { class: 'page' });
  container.append(page);

  try { [caps, voice] = await Promise.all([get('/admin/capabilities'), get('/admin/test/voice-status')]); }
  catch (e) { page.append(pageHead('Playground', ''), callout('danger', `Could not load capabilities: ${e.message}`)); return { unmount() { alive = false; } }; }
  if (!alive) return { unmount() {} };

  const known = new Set(caps.capabilities.map((c) => c.id));
  const openRun = (rid) => h('a', { href: `#/dags/${rid}` }, 'Watch it live →');

  // ------------------------------------------------------------- 1. goal
  const goal = h('textarea', { class: 'textarea', id: 'goal', rows: 3, placeholder: 'e.g. “Take a photo and save it to my downloads”', maxlength: 2000 });
  const goalOut = h('div', {});
  const runGoal = btn('Run goal', { variant: 'primary', ic: 'play', onclick: () => busy(runGoal, async () => {
    if (!goal.value.trim()) { toast('Type a goal first.', 'error'); return; }
    try { const r = await post('/admin/run/goal', { goal: goal.value.trim() }); clear(goalOut); goalOut.append(callout('info', 'Started — the plan appears as a DAG once planning finishes. ', openRun(r.request_id))); toast('Goal started', 'success'); }
    catch (e) { toast(e.message, 'error', { title: 'Could not start goal' }); }
  }) });
  const goalCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Run a goal'), h('span', { class: 'sub' }, 'natural language → plan → DAG')),
    h('div', { class: 'card-body stack' }, h('div', { class: 'field' }, h('label', { for: 'goal' }, 'What should the assistant do?'), goal),
      h('div', { class: 'row' }, runGoal, h('span', { class: 'hint' }, 'Runs through the real planner, as you (the admin). Tools run on your connected devices.')), goalOut));

  // ------------------------------------------------------------- 2. demos
  const fast = h('input', { type: 'checkbox', id: 'fast', checked: true });
  const demoOut = h('div', {});
  const demos = h('div', { class: 'demo-grid' }, caps.demos.map((d) => {
    const run = btn('Run demo', { ic: 'play', size: 'sm', variant: '', disabled: !caps.test_capabilities_enabled, onclick: () => busy(run, async () => {
      try { const r = await post(`/admin/run/demo/${d.kind}`, {}, { params: { scale: fast.checked ? 0.2 : undefined } }); clear(demoOut); demoOut.append(callout('info', `“${d.title}” started. `, openRun(r.request_id))); }
      catch (e) { toast(e.message, 'error', { title: 'Demo failed to start' }); }
    }) });
    return h('div', { class: 'demo' }, h('h3', {}, d.title), h('p', {}, d.description), run);
  }));
  const demoCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Demo DAGs'), h('span', { class: 'sub' }, 'no device needed'),
    h('div', { class: 'right' }, h('label', { class: 'row', for: 'fast' }, fast, h('span', { class: 'hint' }, 'Fast mode (5× quicker)')))),
    h('div', { class: 'card-body stack' }, caps.test_capabilities_enabled ? null : callout('warn', 'The admin.test.* capabilities are disabled on this server. Set ', h('code', {}, 'admin.test_capabilities: true'), ' (or PAI_ADMIN_TEST_CAPABILITIES=1) and restart to enable demos.'),
      demos, demoOut));

  // ----------------------------------------------------------- 3. editor
  const area = h('textarea', { id: 'dag-json', spellcheck: 'false', 'aria-label': 'DAG JSON', autocapitalize: 'off', value: SAMPLE_DAG });
  const gutter = h('pre', { class: 'gutter', 'aria-hidden': 'true' });
  const status = h('div', { class: 'editor-status', role: 'status' });
  const serverIssues = h('div', {});
  const runDag = btn('Run DAG', { variant: 'primary', ic: 'play' });
  const insert = h('select', { class: 'select', 'aria-label': 'Insert a capability node', onchange: () => {
    if (!insert.value) return;
    try {
      const doc = JSON.parse(area.value); const list = Array.isArray(doc) ? doc : doc.tasks;
      list.push({ id: `node_${list.length + 1}`, capability: insert.value, parameters: {}, depends_on: [] });
      area.value = JSON.stringify(doc, null, 2); recheck();
    } catch { toast('Fix the JSON first, then insert a node.', 'error'); }
    insert.value = '';
  } }, h('option', { value: '' }, 'Insert node…'), caps.capabilities.map((c) => h('option', { value: c.id }, c.id)));
  let lastBadLine = 0;
  function recheck() {
    const v = validateDag(area.value, known);
    lastBadLine = v.parseError ? v.parseError.line : 0;
    const lines = area.value.split('\n').length;
    clear(gutter);
    for (let i = 1; i <= lines; i++) gutter.append(h('span', { class: i === lastBadLine ? 'bad' : '' }, i === lastBadLine ? `${i} ●` : String(i)), '\n');
    status.className = `editor-status ${v.ok ? 'ok' : 'bad'}`; clear(status);
    if (v.ok) status.append(icon('check'), `Valid — ${v.tasks.length} task${v.tasks.length === 1 ? '' : 's'}`);
    else if (v.parseError) status.append(icon('alert'), `JSON error at line ${v.parseError.line}, column ${v.parseError.col}: ${v.parseError.message}`);
    else status.append(icon('alert'), `${v.issues.length} problem${v.issues.length === 1 ? '' : 's'}: ${v.issues[0]}${v.issues.length > 1 ? ` (+${v.issues.length - 1} more)` : ''}`);
    runDag.disabled = !v.ok; return v;
  }
  area.addEventListener('input', () => { recheck(); clear(serverIssues); });
  area.addEventListener('scroll', () => { gutter.scrollTop = area.scrollTop; });
  area.addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); const p = area.selectionStart; area.setRangeText('  ', p, area.selectionEnd, 'end'); recheck(); } });
  runDag.addEventListener('click', () => busy(runDag, async () => {
    const v = recheck(); if (!v.ok) return;
    const doc = JSON.parse(area.value);
    clear(serverIssues);
    try { const r = await post('/admin/run/dag', { tasks: v.tasks, label: (!Array.isArray(doc) && doc.label) || 'custom dag' }); serverIssues.append(callout('info', `Started ${r.task_count} tasks. `, openRun(r.request_id))); toast('DAG started', 'success'); }
    catch (e) { serverIssues.append(callout('danger', h('b', {}, 'The server rejected this DAG'), h('ul', {}, (e.issues?.length ? e.issues : [e.message]).map((i) => h('li', {}, i))))); }
  }));
  const editorCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Custom DAG'), h('span', { class: 'sub' }, 'compose nodes and dependencies'), h('div', { class: 'right' }, insert)),
    h('div', { class: 'card-body stack' }, h('div', { class: 'editor' }, gutter, area), status, serverIssues,
      h('div', { class: 'row' }, runDag, btn('Reset', { ic: 'refresh', onclick: () => { area.value = SAMPLE_DAG; recheck(); clear(serverIssues); } }),
        h('span', { class: 'hint' }, 'Each node: id, capability, parameters, depends_on, type (one_shot | long_running | stream).'))));

  // --------------------------------------------------------- 4. tools
  const llmPrompt = h('input', { class: 'input', id: 'llm-prompt', value: 'In one sentence, what is a DAG?', maxlength: 2000, 'aria-label': 'LLM test prompt' });
  const llmOut = h('div', { class: 'stream-out', 'aria-live': 'polite' });
  let llmCtl = null;
  const llmBtn = btn('Stream', { ic: 'bolt', variant: 'primary', onclick: async () => {
    if (llmCtl) { llmCtl.abort(); return; }
    llmCtl = new AbortController(); clear(llmOut); llmOut.classList.add('live'); llmBtn.querySelector('span').textContent = 'Stop';
    try {
      await streamPost('/admin/test/llm', { prompt: llmPrompt.value }, (ev) => {
        if (ev.event === 'delta') llmOut.append(document.createTextNode(ev.data.delta));
        if (ev.event === 'error') toast(ev.data.message || 'LLM error', 'error', { title: 'LLM test failed' });
      }, llmCtl.signal);
    } catch (e) { if (e.name !== 'AbortError') toast(e.message || String(e), 'error', { title: 'LLM test failed' }); }
    llmCtl = null; llmOut.classList.remove('live'); llmBtn.querySelector('span').textContent = 'Stream';
  } });
  const llmCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'LLM streaming'), h('span', { class: 'sub' }, 'tokens arrive as they are generated')),
    h('div', { class: 'card-body stack' }, h('div', { class: 'row' }, llmPrompt, llmBtn), llmOut));

  const ttsText = h('input', { class: 'input', id: 'tts-text', value: 'Hello! The admin console is working.', maxlength: 1000, 'aria-label': 'Text to speak' });
  const audio = h('audio', { controls: true, class: 'hidden' });
  const ttsBtn = btn('Speak', { ic: 'speaker', variant: 'primary', disabled: !voice.available, onclick: () => busy(ttsBtn, async () => {
    try {
      const res = await api('POST', '/admin/test/tts', { body: { text: ttsText.value }, raw: true });
      const blob = await res.blob(); audio.src = URL.createObjectURL(blob); audio.classList.remove('hidden'); await audio.play().catch(() => {});
    } catch (e) { toast(e.message, 'error', { title: 'TTS failed' }); }
  }) });
  const file = h('input', { type: 'file', accept: '.wav,audio/wav', class: 'input', 'aria-label': 'Audio file to transcribe', disabled: !voice.available });
  const sttOut = h('div', { class: 'stream-out' }, h('span', { class: 'faint' }, 'Transcript appears here.'));
  const sttBtn = btn('Transcribe', { ic: 'mic', disabled: !voice.available, onclick: () => busy(sttBtn, async () => {
    if (!file.files[0]) { toast('Choose a .wav file first.', 'error'); return; }
    try {
      const r = await api('POST', '/admin/test/stt', { body: file.files[0], headers: { 'Content-Type': 'audio/wav' } });
      clear(sttOut); sttOut.append(h('div', {}, r.text || h('span', { class: 'faint' }, '(no speech recognised)')), h('div', { class: 'hint' }, `${r.provider} · ${r.audio_seconds}s of audio · ${r.elapsed_ms} ms`));
    } catch (e) { toast(e.message, 'error', { title: 'STT failed' }); }
  }) });
  const voiceCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Voice'),
    h('div', { class: 'right' }, statusChip(voice.available ? 'completed' : 'failed'), h('span', { class: 'hint' }, voice.available ? `STT ${voice.stt} · TTS ${voice.tts}` : 'unavailable'))),
    h('div', { class: 'card-body stack' }, voice.available ? null : callout('warn', voice.reason || 'Voice is not configured.'),
      h('div', { class: 'row' }, ttsText, ttsBtn), audio, h('div', { class: 'row' }, file, sttBtn), sttOut));

  page.append(pageHead('Playground', 'Run things on purpose: goals, demo DAGs, your own DAGs — and check that streaming and voice work.'),
    goalCard, demoCard, editorCard, h('div', { class: 'two-col' }, llmCard, voiceCard));
  recheck();
  return { unmount() { alive = false; llmCtl?.abort(); } };
}

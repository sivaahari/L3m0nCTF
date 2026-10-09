// A small headless-Chrome driver over the DevTools protocol, with Node built-ins only (no packages). Shared by the browser checks of
// the crew's pages (release_page_check.mjs, scoring_page_check.mjs).
//   CHROME  the browser, if it is not in a usual place
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CANDIDATES = [process.env.CHROME, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);

export async function launch() {
  const exe = CANDIDATES.find((p) => existsSync(p));
  if (!exe) throw new Error('Chrome not found (set CHROME)');
  const profile = mkdtempSync(join(tmpdir(), 'l3mon-chrome-'));
  const proc = spawn(exe, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
  let info;
  for (let i = 0; i < 100 && !info; i++) {
    await sleep(100);
    try { info = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n'); } catch { /* not yet */ }
  }
  if (!info) { proc.kill(); throw new Error('Chrome did not expose a DevTools port'); }
  const ws = new WebSocket(`ws://127.0.0.1:${info[0]}${info[1]}`);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('DevTools socket failed')); });
  const pending = new Map();
  const listeners = [];
  let id = 0;
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
    else if (m.method) listeners.forEach((l) => l(m));
  };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
    const i = ++id;
    const timer = setTimeout(() => { pending.delete(i); rej(new Error(`DevTools call timed out: ${method}`)); }, 45000);
    pending.set(i, { res: (v) => { clearTimeout(timer); res(v); }, rej: (e) => { clearTimeout(timer); rej(e); } });
    ws.send(JSON.stringify({ id: i, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  return {
    send, on: (fn) => listeners.push(fn),
    async close() { try { await send('Browser.close'); } catch { /* gone */ } try { ws.close(); } catch { /* ignore */ } proc.kill(); await sleep(300); try { rmSync(profile, { recursive: true, force: true, maxRetries: 3 }); } catch { /* best effort */ } },
  };
}

// One tab. Dialogs (alert, confirm) are accepted and counted in log.dialogs; script errors, failed requests and every URL asked for are logged.
//   opts: base (the site), cookie (the session cookie to set), shots (a folder for screenshots, optional), width, height, mobile
export async function tab(chrome, { base, cookie, shots, width = 1280, height = 1100, mobile = false } = {}) {
  const { targetId } = await chrome.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await chrome.send('Target.attachToTarget', { targetId, flatten: true });
  const s = (m, p) => chrome.send(m, p, sessionId);
  const log = { errors: [], failed: [], urls: [], dialogs: 0, messages: [] };
  chrome.on((m) => {
    if (m.sessionId !== sessionId) return;
    const p = m.params;
    if (m.method === 'Runtime.exceptionThrown') log.errors.push(p.exceptionDetails.exception?.description || p.exceptionDetails.text);
    else if (m.method === 'Runtime.consoleAPICalled' && p.type === 'error') log.errors.push(p.args.map((a) => a.value ?? a.description ?? '').join(' '));
    else if (m.method === 'Network.requestWillBeSent') log.urls.push(p.request.url);
    else if (m.method === 'Network.loadingFailed' && !p.canceled) log.failed.push(`${p.requestId}: ${p.errorText}`);
    else if (m.method === 'Page.javascriptDialogOpening') { log.dialogs += 1; log.messages.push(p.message); s('Page.handleJavaScriptDialog', { accept: true }); }
  });
  for (const d of ['Page', 'Runtime', 'Network']) await s(`${d}.enable`);
  await s('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
  if (cookie) await s('Network.setCookie', { name: 'session', value: cookie, domain: new URL(base).hostname, path: '/', httpOnly: true });
  return {
    log, send: s,
    async goto(url) { const loaded = new Promise((res) => chrome.on((m) => { if (m.sessionId === sessionId && m.method === 'Page.loadEventFired') res(); })); await s('Page.navigate', { url }); await loaded; },
    async run(expression) {
      const r = await s('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(`page script failed: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
      return r.result.value;
    },
    async shot(name) { if (!shots) return; mkdirSync(shots, { recursive: true }); const r = await s('Page.captureScreenshot', { format: 'png' }); writeFileSync(join(shots, name), Buffer.from(r.data, 'base64')); },
  };
}

// A tally: check(name, ok, detail) prints one line; finish() prints the total and exits 1 when anything failed.
export function checker() {
  const results = [];
  return {
    check(name, ok, detail = '') { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${!ok && detail ? ` -- ${detail}` : ''}`); },
    finish() {
      const failed = results.filter((r) => !r.ok);
      console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
      process.exit(failed.length ? 1 : 0);
    },
  };
}

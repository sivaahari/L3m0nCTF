// The crew's release page in a real browser (headless Chrome over the DevTools protocol, Node built-ins only).
// Run by test_release_page_browser.py, which seeds the plan and passes the administrator's session cookie in L3MON_COOKIE.
//   env: L3MON_BASE (default http://localhost:8080), L3MON_COOKIE, L3MON_SHOTS (a folder for two screenshots, optional),
//        L3MON_TOMORROW_IST_EPOCH (the epoch second of tomorrow 09:00 India time, worked out by the test in Python),
//        CHROME (the browser, if it is not in a usual place)
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const COOKIE = process.env.L3MON_COOKIE;
const SHOTS = process.env.L3MON_SHOTS;
const TOMORROW = Number(process.env.L3MON_TOMORROW_IST_EPOCH);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CANDIDATES = [process.env.CHROME, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);

async function launch() {
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

async function tab(chrome, { width = 1280, height = 1100, mobile = false } = {}) {
  const { targetId } = await chrome.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await chrome.send('Target.attachToTarget', { targetId, flatten: true });
  const s = (m, p) => chrome.send(m, p, sessionId);
  const log = { errors: [], failed: [], urls: [] };
  chrome.on((m) => {
    if (m.sessionId !== sessionId) return;
    const p = m.params;
    if (m.method === 'Runtime.exceptionThrown') log.errors.push(p.exceptionDetails.exception?.description || p.exceptionDetails.text);
    else if (m.method === 'Runtime.consoleAPICalled' && p.type === 'error') log.errors.push(p.args.map((a) => a.value ?? a.description ?? '').join(' '));
    else if (m.method === 'Network.requestWillBeSent') log.urls.push(p.request.url);
    else if (m.method === 'Network.loadingFailed' && !p.canceled) log.failed.push(`${p.requestId}: ${p.errorText}`);
    else if (m.method === 'Page.javascriptDialogOpening') { log.dialogs = (log.dialogs || 0) + 1; s('Page.handleJavaScriptDialog', { accept: true }); }
  });
  for (const d of ['Page', 'Runtime', 'Network']) await s(`${d}.enable`);
  await s('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
  await s('Network.setCookie', { name: 'session', value: COOKIE, domain: new URL(BASE).hostname, path: '/', httpOnly: true });
  return {
    log, send: s,
    async goto(url) { const loaded = new Promise((res) => chrome.on((m) => { if (m.sessionId === sessionId && m.method === 'Page.loadEventFired') res(); })); await s('Page.navigate', { url }); await loaded; },
    async run(expression) {
      const r = await s('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(`page script failed: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
      return r.result.value;
    },
    async shot(name) { if (!SHOTS) return; mkdirSync(SHOTS, { recursive: true }); const r = await s('Page.captureScreenshot', { format: 'png' }); writeFileSync(join(SHOTS, name), Buffer.from(r.data, 'base64')); },
  };
}

const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${!ok && detail ? ` -- ${detail}` : ''}`); };

const chrome = await launch();
try {
  const page = await tab(chrome);
  await page.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2000);
  const text = () => page.run('document.getElementById("release-app").innerText');
  const row = (slug) => `Array.from(document.querySelectorAll("tr")).find((r) => r.children[1] && r.children[1].textContent === ${JSON.stringify(slug)})`;
  const rowText = (slug) => page.run(`(${row(slug)} || {}).innerText || ""`);
  const press = (slug, label) => page.run(`(() => { const b = Array.from(${row(slug)}.querySelectorAll("button")).find((x) => x.textContent.startsWith(${JSON.stringify(label)})); b.click(); return true; })()`);
  const dialogs = () => page.log.dialogs || 0;

  check('the page is called Release control', (await page.run('document.querySelector("h1").textContent')).includes('Release control'));
  check('three channels are drawn', (await page.run('document.querySelectorAll(".rc-channel").length')) === 3);
  const first = await text();
  check('the summary counts what is on air, in the singular for one', /1 programme on air, 4 coming up/.test(first), first.slice(0, 160));
  check('a name that contains markup is plain text and no element was made from it', first.includes('<img src=x onerror=alert(1)>') && (await page.run('document.querySelector("#release-app img") === null')));
  check('the released programme says players see it', (await rowText('padding')).includes('players see it'));
  check('the other says off air and withheld', /off air/.test(await rowText('mango')) && /withheld/.test(await rowText('mango')));
  await page.shot('release-desktop.png');

  let before = dialogs();
  await press('mango', 'Release now');
  await sleep(1500);
  check('Release now asks first, then releases', dialogs() === before + 1 && /released/.test(await rowText('mango')) && /changed\./.test(await text()), (await rowText('mango')));

  // the reason box keeps what is typed across a refresh of the plan, and is cleared once the change it was for is made
  await page.run('document.querySelector("input[aria-label=\\"Reason for the change\\"]").focus()');
  await page.send('Input.insertText', { text: 'typing a reason' });
  await sleep(16500); // the page re-reads the plan every 15 seconds
  check('a refresh of the plan does not take the reason box away or empty it',
    (await page.run('document.activeElement && document.activeElement.getAttribute("aria-label") === "Reason for the change" && document.activeElement.value')) === 'typing a reason');
  await press('logger', 'Release now');
  await sleep(1500);
  const audit = await text();
  check('the reason reaches the audit trail', audit.includes('typing a reason') && audit.includes('release.set') && audit.includes('organiser'), audit.slice(-300));
  check('and is cleared afterwards, so the next change does not reuse it', (await page.run('document.querySelector("input[aria-label=\\"Reason for the change\\"]").value')) === '');

  // a time typed in India becomes the right second
  await press('sauce', 'Schedule');
  await sleep(300);
  const setInput = (v) => page.run(`(() => { const i = document.querySelector("input[type=datetime-local]"); i.value = ${JSON.stringify(v)}; return i.value; })()`);
  const clickSet = () => page.run('(() => { Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Set").click(); return true; })()');
  const day = (offsetDays) => new Date(Date.now() + 5.5 * 3600e3 + offsetDays * 86400e3).toISOString().slice(0, 10);
  await setInput(`${day(-1)}T09:00`); await clickSet(); await sleep(300);
  check('a time in the past is refused plainly', /still to come/.test(await text()), (await text()).slice(0, 120));
  await setInput(`${day(1)}T09:00`); await clickSet(); await sleep(1500);
  const view = await page.run(`fetch(${JSON.stringify(`${BASE}/api/v1/l3mon/admin/release`)}, { credentials: "same-origin" }).then((r) => r.json())`);
  const sauce = view.data.channels.flatMap((c) => c.programmes).find((p) => p.slug === 'sauce');
  check('09:00 typed in India is stored as exactly that second (09:00 IST is 03:30 UTC)', sauce.state === 'scheduled' && sauce.at === TOMORROW && /09:00 IST$/.test(sauce.at_ist), JSON.stringify(sauce));

  before = dialogs();
  await page.run('(() => { Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Withhold every channel").click(); return true; })()');
  await sleep(1800);
  check('Withhold every channel asks first and takes everything off air', dialogs() === before + 1 && /0 programmes on air/.test(await text()), (await text()).slice(0, 160));
  check('no script error, no failed request', page.log.errors.length === 0 && page.log.failed.length === 0, JSON.stringify({ errors: page.log.errors, failed: page.log.failed }));
  check('nothing was asked of any other host', page.log.urls.every((u) => u.startsWith(BASE) || u.startsWith('data:')), JSON.stringify(page.log.urls.filter((u) => !u.startsWith(BASE) && !u.startsWith('data:')).slice(0, 5)));

  const phone = await tab(chrome, { width: 390, height: 900, mobile: true });
  await phone.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2000);
  check('at phone width nothing spills sideways and each cell carries its column name',
    (await phone.run('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')) === true && (await phone.run('document.querySelector("#release-app td").getAttribute("data-label")')) === 'No.');
  await phone.shot('release-phone.png');
} finally {
  await chrome.close();
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);

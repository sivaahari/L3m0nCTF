// The cold-open comic's player in a real browser (headless Chrome over the DevTools protocol, Node built-ins only).
// It serves the player's own files and a made-up story from a small local server, so it needs neither the platform nor a network.
// Run by test_story_player_browser.py (or: node tests/browser/story_player_check.mjs). env: CHROME, L3MON_SHOTS (a folder for screenshots).
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const ASSETS = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'plugins', 'l3mon_story', 'assets');
const SHOTS = process.env.L3MON_SHOTS;
const { check, finish } = checker();

const svg = (fill, extra = '') => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="${fill}"/>${extra}<circle cx="80" cy="45" r="18" fill="#ffd23f"/></svg>`;
const panel = (n, text, extra = {}) => ({
  id: `p${n}`, ms: n === 1 ? 3600 : 1800, enter: ['static', 'slide', 'pop', 'cut'][n % 4], alt: `Panel ${n}: a round sun on a coloured field.`,
  layers: [{ art: 'field', x: 0, y: 0, w: 100, h: 100, z: 0, from: { x: 0, y: 0, s: 1 }, to: { x: -3, y: 0, s: 1.1 } }, { art: 'sun', x: 40, y: 30, w: 20, h: 40, z: 1, anim: 'bob' }],
  bubbles: [{ kind: 'say', who: 'Tara', text, x: 5, y: 6, w: 44, tail: 'bl', at: 100 }, { kind: 'sfx', text: 'POW!', x: 60, y: 60, w: 30, at: 200 }],
  sfx: [{ cue: 'pop', at: 0 }],
  ...extra,
});
const story = (slug = 'street', title = 'Moth Hour') => ({
  v: 1, slug, title, kicker: 'CH 01 · Mighty Street', lang: 'en',
  panels: [panel(1, 'Good evening, crew.'), panel(2, 'The bars hum a message.'), panel(3, 'Master control, we need you.')],
  art: { field: svg('#223a6b'), sun: svg('#00000000') },
});
const hostile = () => {
  const s = story('evil', '<img src=x onerror="window.__pwn=1">');
  s.kicker = '<b>bold</b>';
  s.panels[0].alt = '<script>window.__pwn=2</script>';
  s.panels[0].bubbles[0] = { kind: 'say', who: '<i>who</i>', text: '<img src=x onerror="window.__pwn=3">', x: 5, y: 5, w: 40, tail: 'bl', at: 0 };
  s.art.field = svg('red', '<script>window.__pwn=4</script><rect onload="window.__pwn=5" width="1" height="1"/><foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml"><img src=x onerror="window.__pwn=6"></div></foreignObject>');
  return s;
};

// ---- a small server -----------------------------------------------------------------------------------------------------------
const state = { mode: 'ok', slug: null, requests: [] };
const TYPES = { '.js': 'text/javascript', '.css': 'text/css' };
// the platform's approved policy: the player must work under it with no inline script or style of its own
const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'";
const server = http.createServer((req, res) => {
  state.requests.push(`${req.method} ${req.url}`);
  const url = new URL(req.url, 'http://x');
  res.setHeader('Content-Security-Policy', CSP);
  if (url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<!doctype html><meta charset=utf-8><title>host</title><link rel=stylesheet href=/assets/comic.css><link rel=stylesheet href=/host.css><script src=/guard.js></script>'
      + '<body><main id=page><h1>Board</h1><button id=open>▶ Cold open (3 panels)</button><button id=other>Another button</button></main><script type=module src=/host.js></script>');
    return;
  }
  if (url.pathname === '/host.css') { res.writeHead(200, { 'Content-Type': 'text/css' }); res.end('body{margin:0;background:#101018;color:#eee;font-family:system-ui}'); return; }
  if (url.pathname === '/guard.js') {
    // a classic script that runs before anything else: it records every refusal by the policy
    res.writeHead(200, { 'Content-Type': 'text/javascript' });
    res.end("window.__csp = []; document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));");
    return;
  }
  if (url.pathname === '/host.js') {
    res.writeHead(200, { 'Content-Type': 'text/javascript' });
    res.end("import { open, mount } from '/assets/comic.js'; window.__open = open; window.__mount = mount; document.getElementById('open').addEventListener('click', (e) => open('street', { opener: e.currentTarget }));");
    return;
  }
  if (url.pathname.startsWith('/assets/')) {
    const name = url.pathname.slice('/assets/'.length);
    if (!/^[a-z]+\.(js|css)$/.test(name)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': TYPES[name.slice(name.lastIndexOf('.'))] });
    res.end(readFileSync(join(ASSETS, name)));
    return;
  }
  if (url.pathname.startsWith('/api/v1/l3mon/story/')) {
    const slug = url.pathname.split('/').pop();
    const send = (code, body) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (state.mode === '401') return send(401, { success: false, error: 'auth_required', message: 'Sign in.' });
    if (state.mode === '404') return send(404, { success: false, error: 'not_found', message: 'No.' });
    if (state.mode === '403v') return send(403, { success: false, error: 'unverified', message: 'Verify.' });
    if (state.mode === '500') return send(500, { success: false, error: 'server_error', message: 'Broke.' });
    if (state.mode === 'hostile') return send(200, { success: true, data: hostile() });
    return send(200, { success: true, data: story(slug) });
  }
  res.writeHead(404); res.end();
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}`;

const chrome = await launch();
const until = async (fn, ms = 4000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await fn()) return true; await sleep(60); } return false; };
const tab = (opts = {}) => openTab(chrome, { base: BASE, shots: SHOTS, ...opts });

try {
  const page = await tab();
  // count the audio contexts that get made: none may exist before the person asks for sound
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__contexts = 0; const A = window.AudioContext; window.AudioContext = function(...a) { window.__contexts++; return new A(...a); }; window.AudioContext.prototype = A.prototype;' });
  await page.goto(`${BASE}/`);
  const q = (css) => page.run(`document.querySelector(${JSON.stringify(css)})`) ;
  const text = (css) => page.run(`(document.querySelector(${JSON.stringify(css)}) || {}).textContent || ''`);
  const count = (css) => page.run(`document.querySelectorAll(${JSON.stringify(css)}).length`);
  const attr = (css, a) => page.run(`(document.querySelector(${JSON.stringify(css)}) || {getAttribute(){return null}}).getAttribute(${JSON.stringify(a)})`);

  // ---- opening ---------------------------------------------------------------------------------------------------------------
  await page.run('document.getElementById("open").focus(); document.getElementById("open").click(); true');
  check('the dialog opens over the page', await until(async () => (await count('.l3m-backdrop [role=dialog]')) === 1));
  check('it is a modal dialog with a name', (await attr('.l3m-backdrop [role=dialog]', 'aria-modal')) === 'true' && /Cold open: Moth Hour/.test(await attr('.l3m-backdrop [role=dialog]', 'aria-label')));
  check('the page behind it is inert and the page cannot scroll', (await attr('#page', 'inert')) !== null && await page.run('document.documentElement.classList.contains("l3m-lock")'));
  check('focus moved into the dialog', await page.run('document.activeElement.closest(".l3m") !== null'));
  check('panel one draws its two pictures from data addresses, as images', (await count('.l3m-layer')) === 2 && await page.run('[...document.querySelectorAll(".l3m-layer")].every((i) => i.tagName === "IMG" && i.src.startsWith("data:image/svg+xml") && i.alt === "")'));
  check('no sound context exists yet', (await page.run('window.__contexts')) === 0);
  check('the sound switch says off and is not pressed', /off/i.test(await text('.l3m-sound')) && (await attr('.l3m-sound', 'aria-pressed')) === 'false');

  // ---- the words type out ---------------------------------------------------------------------------------------------------------
  const words = () => text('.l3m-bubble.l3m-say .l3m-words');
  const early = await until(async () => { const w = await words(); return w.length > 0 && w.length < 'Good evening, crew.'.length; }, 1500);
  check('a speech bubble types its words a few at a time', early, await words());
  check('and has all of them in the end', await until(async () => (await words()) === 'Good evening, crew.', 3000), await words());
  check('the speaker is a label inside the bubble', (await text('.l3m-bubble.l3m-say .l3m-who')) === 'Tara');
  check('the live region says what the panel shows and who says what', /Panel 1 of 3\. Panel 1: a round sun on a coloured field\. Tara: Good evening, crew\./.test(await text('.l3m-sr')), await text('.l3m-sr'));
  check('the pictures are hidden from assistive technology while the words type', (await attr('.l3m-bubbles', 'aria-hidden')) === 'true');

  // ---- moving on by itself and by hand ---------------------------------------------------------------------------------------
  check('the story moves to panel two by itself', await until(async () => (await attr('.l3m-dot:nth-child(2)', 'aria-current')) === 'step', 5000));
  await page.run('document.querySelector(".l3m-play").click(); true');
  check('pause stops it and says Play', await until(async () => (await attr('.l3m-play', 'aria-label')) === 'Play'));
  const frozen = await words(); await sleep(700);
  check('a paused panel does not change', (await words()) === frozen);
  await page.run('document.querySelector(".l3m-dialog, .l3m").focus(); true');
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37 });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37 });
  check('the left arrow goes back a panel', await until(async () => (await attr('.l3m-dot:nth-child(1)', 'aria-current')) === 'step'));
  await page.run('document.querySelector(".l3m-dot:nth-child(3)").click(); true');
  check('a dot goes straight to its panel', await until(async () => (await attr('.l3m-dot:nth-child(3)', 'aria-current')) === 'step') && /Master control|Panel 3/.test(await text('.l3m-sr')));
  check('the Previous button is on again after the first panel', await page.run('!document.querySelector("[aria-label=\\"Previous panel\\"]").disabled'));
  await page.shot('story-desktop.png');

  // ---- the end and watching again ---------------------------------------------------------------------------------------------
  await page.run('document.querySelector("[aria-label=\\"Next panel\\"]").click(); true');
  check('after the last panel an end card appears with a way to watch again', await until(async () => (await page.run('!document.querySelector(".l3m-end").hidden')) === true) && /Watch again/.test(await text('.l3m-replay')));
  await page.run('document.querySelector(".l3m-replay").click(); true');
  check('watching again starts at panel one', await until(async () => (await attr('.l3m-dot:nth-child(1)', 'aria-current')) === 'step') && await page.run('document.querySelector(".l3m-end").hidden'));

  // ---- sound is the person's choice ---------------------------------------------------------------------------------------------
  await page.run('document.querySelector(".l3m-sound").click(); true');
  check('turning sound on makes one audio context and the switch says on', (await page.run('window.__contexts')) === 1 && (await attr('.l3m-sound', 'aria-pressed')) === 'true' && /on/i.test(await text('.l3m-sound')));
  await page.run('document.querySelector(".l3m-sound").click(); true');
  check('and off again', (await attr('.l3m-sound', 'aria-pressed')) === 'false');

  // ---- text and strip -----------------------------------------------------------------------------------------------------------
  await page.run('document.querySelector(".l3m-text").click(); true');
  check('the transcript lists every panel as text', !(await page.run('document.querySelector(".l3m-transcript").hidden')) && (await count('.l3m-transcript li')) === 3 && /Tara: The bars hum a message/.test(await text('.l3m-transcript')));
  await page.run('document.querySelector(".l3m-stripbtn").click(); true');
  check('the strip shows every panel at once with its words complete and no motion', (await count('.l3m-figure')) === 3 && await page.run('[...document.querySelectorAll(".l3m-strip .l3m-say .l3m-words")].every((w) => w.textContent.length > 10)') && await page.run('document.querySelector(".l3m-tv").hidden'));
  check('every strip picture has a caption in words', await page.run('[...document.querySelectorAll(".l3m-figure figcaption")].every((c) => /Panel \\d: a round sun/.test(c.textContent))'));
  await page.shot('story-strip.png');
  await page.run('document.querySelector(".l3m-stripbtn").click(); true');
  check('and the motion comic comes back', await until(async () => (await page.run('!document.querySelector(".l3m-tv").hidden'))));

  // ---- closing --------------------------------------------------------------------------------------------------------------------
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  check('Escape closes it, frees the page and puts focus back on the button that opened it', await until(async () => (await count('.l3m-backdrop')) === 0) && (await attr('#page', 'inert')) === null && await page.run('document.activeElement.id === "open"') && !(await page.run('document.documentElement.classList.contains("l3m-lock")')));
  const kids = await page.run('document.body.children.length');
  for (let i = 0; i < 4; i++) { await page.run('document.getElementById("open").click(); true'); await until(async () => (await count('.l3m-backdrop .l3m')) === 1); await page.run('document.querySelector(".l3m-close").click(); true'); await sleep(50); }
  check('opening and closing it again and again leaves nothing behind', (await count('.l3m-backdrop')) === 0 && (await page.run('document.body.children.length')) === kids && (await attr('#page', 'inert')) === null);

  // ---- when it cannot be watched ----------------------------------------------------------------------------------------------------
  for (const [mode, title, extra] of [['401', 'Sign up to watch', null], ['404', 'Not on air yet', null], ['403v', 'Verify your email first', null], ['500', 'Technical difficulty', null]]) {
    state.mode = mode;
    await page.run('document.getElementById("open").click(); true');
    check(`a ${mode} answer is explained plainly (${title})`, await until(async () => (await text('.l3m-notice h2')) === title), await text('.l3m-notice h2'));
    if (mode === '401') {
      const href = await attr('.l3m-cta', 'href');
      check('and a visitor is offered the registration page, coming back to this story afterwards', href === '/register?next=%2Fstory%2Fstreet', href);
    }
    await page.run('document.querySelector(".l3m-notice .l3m-close").click(); true');
    check(`the notice closes (${mode})`, await until(async () => (await count('.l3m-backdrop')) === 0));
  }
  state.mode = 'ok';

  // ---- hostile content is only ever text ------------------------------------------------------------------------------------------
  state.mode = 'hostile';
  await page.run('document.getElementById("open").click(); true');
  await until(async () => (await count('.l3m-backdrop .l3m')) === 1);
  await sleep(1500);
  check('markup in a title, a kicker, a name, a bubble and a description is shown as text, never built into elements', (await text('.l3m-title')) === '<img src=x onerror="window.__pwn=1">' && (await count('.l3m-bar img, .l3m-bubbles img, .l3m-bar b, .l3m-bubble i')) === 0);
  check('a picture that carries a script, an event handler and a foreignObject runs nothing', (await page.run('window.__pwn === undefined')) && page.log.dialogs === 0);
  check('no script error was raised', page.log.errors.length === 0, page.log.errors.join(' | ').slice(0, 300));
  check('the approved content security policy refused nothing the player did', (await page.run('window.__csp.length')) === 0, await page.run('window.__csp.join(" | ")'));
  // the recorder itself works: an inline script (which the policy forbids) must be seen
  await page.run('const s = document.createElement("script"); s.textContent = "window.__inline = 1"; document.body.append(s); true');
  await sleep(200);
  check('control: the recorder sees a refusal, and the refused script did not run', (await page.run('window.__csp.length')) === 1 && (await page.run('window.__inline === undefined')));
  await page.run('window.__csp.length = 0; true');
  await page.run('document.querySelector(".l3m-close").click(); true');
  state.mode = 'ok';

  // ---- the person asked for less motion ----------------------------------------------------------------------------------------------
  await page.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await page.run('document.getElementById("open").click(); true');
  check('with reduced motion the comic opens as a still strip, with no moving pictures and no static', await until(async () => (await count('.l3m-figure')) === 3) && await page.run('document.querySelector(".l3m-tv").hidden') && (await page.run('window.__contexts')) === 1);
  await page.run('document.querySelector(".l3m-close").click(); true');
  await page.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });

  check('the reduced-motion strip was not refused by the policy either', (await page.run('window.__csp.length')) === 0);

  // ---- nothing else is asked for ----------------------------------------------------------------------------------------------------
  const outside = page.log.urls.filter((u) => !u.startsWith(BASE) && !u.startsWith('data:') && !u.startsWith('about:'));
  check('the player never asked for anything outside its own site', outside.length === 0, outside.join(', '));
  check('apart from the page and its three files the only request was for the story', state.requests.every((r) => /^GET (\/|\/(host|guard)\.(js|css)|\/assets\/(comic|timeline|sounds)\.js|\/assets\/comic\.css|\/api\/v1\/l3mon\/story\/[a-z]+|\/favicon\.ico)$/.test(r)), state.requests.join(' | '));

  // ---- a phone ----------------------------------------------------------------------------------------------------------------------------
  const phone = await tab({ width: 375, height: 812, mobile: true });
  await phone.goto(`${BASE}/`);
  await phone.run('document.getElementById("open").click(); true');
  await until(async () => (await phone.run('document.querySelectorAll(".l3m-backdrop .l3m").length')) === 1);
  await sleep(500);
  check('on a phone the comic fits the width, with no sideways scroll', await phone.run('document.documentElement.scrollWidth <= window.innerWidth + 1 && document.querySelector(".l3m-backdrop").scrollWidth <= window.innerWidth + 1'));
  check('and the controls can all be reached (each is at least 36 pixels high)', await phone.run('[...document.querySelectorAll(".l3m-controls button")].every((b) => b.getBoundingClientRect().height >= 36)'));
  check('the phone page was not refused by the policy', (await phone.run('window.__csp.length')) === 0);
  await phone.shot('story-phone.png');
} finally {
  await chrome.close();
  server.close();
}
finish();

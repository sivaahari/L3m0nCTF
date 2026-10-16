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
  bubbles: [{ kind: 'say', who: 'Host', text, x: 5, y: 6, w: 44, tail: 'bl', at: 100 }, { kind: 'sfx', text: 'POW!', x: 60, y: 60, w: 30, at: 200 }],
  sfx: [{ cue: 'pop', at: 0 }],
  ...extra,
});
const story = (slug = 'street', title = 'Moth Hour') => ({
  v: 1, slug, title, kicker: 'CH 01 · Mighty Street', lang: 'en',
  panels: [panel(1, 'Good evening, crew.'), panel(2, 'The bars hum a message.'), panel(3, 'Master control, we need you.')],
  art: { field: svg('#223a6b'), sun: svg('#00000000') },
});
// a story of painted pictures (format version 2): the pages are turned by the person, the words are in the pictures and again as `lines`
const WEBP = Buffer.from('UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==', 'base64'); // a real 1 x 1 WebP
const picStory = (slug = 'street', title = 'Picture Hour') => ({
  v: 2, slug, title, kicker: slug === 'finale' ? 'LAST CALL' : 'CH 01 · Mighty Street', lang: 'en', flip: true,
  panels: [1, 2, 3].map((n) => ({
    id: `p${n}`, ms: 1200, enter: n === 1 ? 'static' : 'slide', alt: `Picture ${n}: a street at sunset.`,
    layers: [{ pic: `p${n}`, x: 0, y: 0, w: 100, h: 100, z: 0, from: { x: 0, y: 0, s: 1 }, to: { x: -1, y: 0, s: 1.05 } }],
    lines: [{ kind: 'caption', text: `Scene ${n} sets the street.` }, { kind: 'say', who: 'Anchor', text: `Line ${n}!` }],
    sfx: [{ cue: 'pop', at: 0 }],
  })),
  pics: Object.fromEntries([1, 2, 3].map((n) => [`p${n}`, { w: 1600, h: 893, bytes: 100 + n, sha256: String(n).repeat(64) }])),
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
  const picture = url.pathname.match(/^\/api\/v1\/l3mon\/story\/([a-z-]+)\/pic\/([a-z0-9_]+)$/);
  if (picture) {
    if (state.mode !== 'pics' || !/^p[1-3]$/.test(picture[2])) { res.writeHead(404, { 'Content-Type': 'application/json' }); res.end('{"success":false}'); return; }
    res.writeHead(200, { 'Content-Type': 'image/webp', 'Cache-Control': 'private, max-age=300' });
    res.end(WEBP);
    return;
  }
  if (url.pathname.startsWith('/api/v1/l3mon/story/')) {
    const slug = url.pathname.split('/').pop();
    const send = (code, body) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (state.mode === '401') return send(401, { success: false, error: 'auth_required', message: 'Sign in.' });
    if (state.mode === '404') return send(404, { success: false, error: 'not_found', message: 'No.' });
    if (state.mode === '403v') return send(403, { success: false, error: 'unverified', message: 'Verify.' });
    if (state.mode === '500') return send(500, { success: false, error: 'server_error', message: 'Broke.' });
    if (state.mode === 'hang') return;  // never answers
    if (state.mode === 'hostile') return send(200, { success: true, data: hostile() });
    if (state.mode === 'pics') return send(200, { success: true, data: picStory(slug) });
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
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__contexts = 0; const A = window.AudioContext; window.AudioContext = function(...a) { window.__contexts++; return new A(...a); }; window.AudioContext.prototype = A.prototype; window.__closed = 0; const C = A.prototype.close; A.prototype.close = function() { window.__closed++; return C.call(this); };' });
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
  check('the speaker is a label inside the bubble', (await text('.l3m-bubble.l3m-say .l3m-who')) === 'Host');
  check('the live region says what the panel shows and who says what', /Panel 1 of 3\. Panel 1: a round sun on a coloured field\. Host: Good evening, crew\./.test(await text('.l3m-sr')), await text('.l3m-sr'));
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
  check('the transcript lists every panel as text', !(await page.run('document.querySelector(".l3m-transcript").hidden')) && (await count('.l3m-transcript li')) === 3 && /Host: The bars hum a message/.test(await text('.l3m-transcript')));
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
  check('closing the comic lets the audio context go (a page that opens it again and again must not collect them)', (await page.run('window.__closed')) === 1, await page.run('window.__closed'));
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

  // ---- a request that never answers --------------------------------------------------------------------------------------------------
  state.mode = 'hang';
  await page.run('document.getElementById("open").focus(); document.getElementById("open").click(); true');
  check('while the story loads the person sees it and has a way out', await until(async () => (await text('.l3m-loading')).includes('Tuning in')) && (await count('.l3m-backdrop .l3m-close')) === 1);
  await page.run('document.querySelector(".l3m-backdrop .l3m-close").click(); true');
  check('the close button frees the page and gives focus back', await until(async () => (await count('.l3m-backdrop')) === 0) && (await attr('#page', 'inert')) === null && await page.run('document.activeElement.id === "open"'));
  await page.run('document.getElementById("open").focus(); document.getElementById("open").click(); true');
  await until(async () => (await count('.l3m-loading')) === 1);
  await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  check('and so does Escape', await until(async () => (await count('.l3m-backdrop')) === 0) && (await attr('#page', 'inert')) === null);
  await page.run('window.__open("street", { opener: document.getElementById("open"), timeoutMs: 700 }); true');
  check('a request that never answers ends in the "No connection" notice and not in a page that stays locked', await until(async () => (await text('.l3m-notice h2')) === 'No connection', 4000), await text('.l3m-notice h2'));
  await page.run('document.querySelector(".l3m-notice .l3m-close").click(); true');
  check('and the notice closes', await until(async () => (await count('.l3m-backdrop')) === 0) && (await attr('#page', 'inert')) === null);
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

  // ---- a story of painted pictures: the person turns the pages ----------------------------------------------------------------------------
  state.requests.length = 0;
  state.mode = 'pics';
  await page.run('document.getElementById("open").focus(); document.getElementById("open").click(); true');
  check('a picture story opens as a dialog', await until(async () => (await count('.l3m-backdrop [role=dialog]')) === 1));
  const photo = () => page.run('(() => { const i = document.querySelector(".l3m-layer"); return i ? [i.tagName, i.getAttribute("src"), i.complete, i.naturalWidth, i.alt] : null; })()');
  check('panel one is one picture, fetched from its gated address (not a data address), drawn, with no alt text of its own',
    await until(async () => { const p = await photo(); return !!p && p[2] && p[3] > 0; }) && (await count('.l3m-layer')) === 1
      && JSON.stringify(await photo()) === JSON.stringify(['IMG', '/api/v1/l3mon/story/street/pic/p1', true, 1, '']), JSON.stringify(await photo()));
  check('the next picture is fetched ahead of time, so a turned page is already there',
    await until(async () => state.requests.includes('GET /api/v1/l3mon/story/street/pic/p2')), state.requests.join(' | '));
  check('no play button is offered, because the person turns the pages', await page.run('document.querySelector(".l3m-play").hidden'));
  check('the tag says cold open and the window is named for it', /^COLD OPEN$/.test(await text('.l3m-tag')) && /Cold open: Picture Hour/.test(await attr('.l3m-backdrop [role=dialog]', 'aria-label')));
  check('the live region gives the picture and its painted words', /Panel 1 of 3\. Picture 1: a street at sunset\. Scene 1 sets the street\. Anchor: Line 1!/.test(await text('.l3m-sr')) || await until(async () => /Anchor: Line 1!/.test(await text('.l3m-sr'))), await text('.l3m-sr'));
  await sleep(2800);
  check('the page does not turn by itself, however long it stays', (await attr('.l3m-dot:nth-child(1)', 'aria-current')) === 'step' && (await count('.l3m-end:not([hidden])')) === 0);
  const key = async (k, code, vk) => { await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, windowsVirtualKeyCode: vk }); await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk }); };
  const here = (n) => until(async () => (await attr(`.l3m-dot:nth-child(${n})`, 'aria-current')) === 'step');
  await page.run('document.querySelector("[aria-label=\\"Next panel\\"]").click(); true');
  check('the Next button turns to page two', await here(2) && /\/pic\/p2$/.test((await photo())[1]));
  await page.run('document.querySelector(".l3m").focus(); true');
  await key(' ', 'Space', 32);
  check('the space bar turns the page (there is nothing to play)', await here(3));
  await key('ArrowLeft', 'ArrowLeft', 37);
  check('the left arrow turns back', await here(2));
  await key('Home', 'Home', 36);
  check('Home goes to the first page', await here(1));
  await key('End', 'End', 35);
  check('End goes to the last page', await here(3));
  await key('ArrowRight', 'ArrowRight', 39);
  check('the right arrow on the last page ends it with a card that offers it again, in the right words', await until(async () => (await page.run('!document.querySelector(".l3m-end").hidden')) === true) && /End of the cold open\./.test(await text('.l3m-end')));
  await page.run('document.querySelector(".l3m-replay").click(); true');
  check('and watching again starts at the first page', await here(1));
  await page.run('document.querySelector(".l3m-screen").click(); true');
  check('a click on the picture turns the page', await here(2));
  const box = JSON.parse(await page.run('JSON.stringify(document.querySelector(".l3m-screen").getBoundingClientRect())'));
  const drag = async (fromX, toX) => {
    const y = Math.round(box.y + box.height / 2);
    await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: fromX, y, button: 'left', clickCount: 1 });
    await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round((fromX + toX) / 2), y, button: 'left' });
    await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: toX, y, button: 'left' });
    await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: toX, y, button: 'left', clickCount: 1 });
  };
  const mid = Math.round(box.x + box.width / 2);
  await drag(mid + 120, mid - 120);
  check('a swipe to the left turns to the next page, once (the click that ends it does not turn it twice)', await here(3));
  await sleep(150);
  check('and it stayed there', (await attr('.l3m-dot:nth-child(3)', 'aria-current')) === 'step');
  await drag(mid - 120, mid + 120);
  check('a swipe to the right turns back', await here(2));
  await drag(mid - 10, mid + 10);
  await sleep(150);
  check('a small slip of the finger is not a swipe (it counts as a click: the next page)', await here(3));
  await page.run('document.querySelector(".l3m-text").click(); true');
  check('the transcript lists each picture with its painted words as text', (await count('.l3m-transcript li')) === 3 && /Scene 2 sets the street\. Anchor: Line 2!/.test(await text('.l3m-transcript')), await text('.l3m-transcript'));
  await page.run('document.querySelector(".l3m-stripbtn").click(); true');
  check('the strip shows every picture at once, each drawn', await until(async () => (await count('.l3m-figure img')) === 3) && await page.run('[...document.querySelectorAll(".l3m-figure img")].every((i) => /\\/pic\\/p[1-3]$/.test(i.getAttribute("src")))'));
  check('and the pictures in the strip load', await until(async () => await page.run('[...document.querySelectorAll(".l3m-figure img")].every((i) => i.complete && i.naturalWidth > 0)')));
  await page.run('document.querySelector(".l3m-close").click(); true');
  check('closing it frees the page', await until(async () => (await count('.l3m-backdrop')) === 0) && (await attr('#page', 'inert')) === null);
  const odd = await page.run(`(() => { const host = document.createElement('div'); document.body.append(host); const b = ${JSON.stringify({ ...picStory('x?y#z/w'), slug: 'x?y#z/w' })}; b.pics['p 1'] = b.pics.p1; b.panels[0].layers[0].pic = 'p 1'; window.__mount(host, b, { mode: 'page' }); const src = host.querySelector('.l3m-layer').getAttribute('src'); host.remove(); return src; })()`);
  check('the address of a picture is built with every part encoded, whatever the data says', odd === '/api/v1/l3mon/story/x%3Fy%23z%2Fw/pic/p%201', odd);
  state.requests.length = 0;
  check('the pictures were not refused by the policy, and nothing raised an error', (await page.run('window.__csp.length')) === 0 && page.log.errors.length === 0, await page.run('window.__csp.join(" | ")'));
  check('the only requests were for the story and its own pictures', state.requests.every((r) => /^GET \/api\/v1\/l3mon\/story\/(street|x%3Fy%23z%2Fw)(\/pic\/(p[1-3]|p%201))?$/.test(r)), state.requests.join(' | '));
  // the finale is a picture story too, and says so
  await page.run('window.__open("finale", { opener: document.getElementById("open") }); true');
  check('the finale is tagged as the finale, in its window name and its end card', await until(async () => (await count('.l3m-backdrop [role=dialog]')) === 1)
    && /^FINALE$/.test(await text('.l3m-tag')) && /Finale: Picture Hour/.test(await attr('.l3m-backdrop [role=dialog]', 'aria-label')));
  await page.run('document.querySelector(".l3m-dot:nth-child(3)").click(); true');
  await page.run('document.querySelector("[aria-label=\\"Next panel\\"]").click(); true');
  state.mode = '404';
  await page.run('document.querySelector(".l3m-close").click(); true');
  await page.run('window.__open("finale", { opener: document.getElementById("open") }); true');
  check('a finale that is not open yet says so in its own words, not those of a cold open', await until(async () => (await text('.l3m-notice h2')) === 'Not on air yet') && /finale/i.test(await text('.l3m-notice p')) && !/cold open/i.test(await text('.l3m-notice p')), await text('.l3m-notice p'));
  await page.run('document.querySelector(".l3m-notice .l3m-close").click(); true');
  state.mode = 'pics';
  await page.run('window.__open("finale", { opener: document.getElementById("open") }); true');
  await until(async () => (await count('.l3m-backdrop [role=dialog]')) === 1);
  await page.run('document.querySelector(".l3m-dot:nth-child(3)").click(); true');
  await page.run('document.querySelector("[aria-label=\\"Next panel\\"]").click(); true');
  check('and the end card of the finale does not say master control is on', await until(async () => (await page.run('!document.querySelector(".l3m-end").hidden')) === true) && /End of the finale\./.test(await text('.l3m-end')) && !/Master control/.test(await text('.l3m-end')), await text('.l3m-end'));
  await page.run('document.querySelector(".l3m-close").click(); true');
  state.mode = 'ok';

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
  // a real finger: the touch events a phone sends, not a mouse drag (a drag with the finger was cancelled by the browser's own panning before the review of 2026-10-16)
  await phone.run('document.querySelector(".l3m-close").click(); true');
  await phone.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
  state.mode = 'pics';
  await phone.run('document.getElementById("open").click(); true');
  await until(async () => (await phone.run('document.querySelectorAll(".l3m-backdrop .l3m").length')) === 1);
  await sleep(300);
  check('the screen tells a phone that a sideways drag is ours and an up-and-down one is the page\'s', (await phone.run('getComputedStyle(document.querySelector(".l3m-screen")).touchAction')) === 'pan-y pinch-zoom');
  const phoneAt = (n) => until(async () => (await phone.run(`document.querySelector('.l3m-dot:nth-child(${n})').getAttribute('aria-current')`)) === 'step');
  const area = JSON.parse(await phone.run('JSON.stringify(document.querySelector(".l3m-screen").getBoundingClientRect())'));
  const finger = async (points) => {
    const [first, ...rest] = points;
    await phone.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: first[0], y: first[1] }] });
    await sleep(40);  // a finger rests a moment: a touch that starts and ends in the same instant is not read as a tap
    for (const point of rest) { await phone.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: point[0], y: point[1] }] }); await sleep(16); }
    await phone.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await sleep(700);  // a tap right after a drag is taken by the browser as the end of a fling, not as a tap
  };
  const cy = Math.round(area.y + area.height / 2);
  const cx = Math.round(area.x + area.width / 2);
  await finger([[cx + 120, cy], [cx + 60, cy], [cx, cy], [cx - 60, cy], [cx - 120, cy]]);
  check('a finger swiped to the left turns to the next page', await phoneAt(2));
  await finger([[cx - 120, cy], [cx - 60, cy], [cx, cy], [cx + 60, cy], [cx + 120, cy]]);
  check('and to the right turns back', await phoneAt(1));
  await phone.run('window.__ev = []; for (const t of ["pointerdown", "pointerup", "pointercancel", "click", "touchstart", "touchend"]) document.querySelector(".l3m-screen").addEventListener(t, (e) => window.__ev.push(t + ":" + (e.isPrimary === undefined ? "" : e.isPrimary)), true); true');
  await finger([[cx, cy]]);
  check('a tap turns the page, as a click does', await phoneAt(2), await phone.run('JSON.stringify(window.__ev)'));
  const scrolled = await phone.run('window.scrollY');
  await finger([[cx, cy], [cx, cy - 40], [cx, cy - 80]]);
  check('a drag up the screen does not turn a page', (await phone.run('document.querySelector(".l3m-dot:nth-child(2)").getAttribute("aria-current")')) === 'step' && scrolled >= 0);
  const which = () => phone.run('[...document.querySelectorAll(".l3m-dot")].findIndex((d) => d.getAttribute("aria-current") === "step")');
  const was = await which();
  await phone.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: cx - 40, y: cy, id: 1 }, { x: cx + 40, y: cy, id: 2 }] });
  await phone.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: cx - 100, y: cy, id: 1 }, { x: cx + 100, y: cy, id: 2 }] });
  await phone.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await sleep(250);
  check('two fingers (a pinch) do not turn the page', (await which()) === was && was === 1, String(was));
  await phone.run('document.querySelector(".l3m-close").click(); true');
  state.mode = 'ok';
} finally {
  await chrome.close();
  server.closeAllConnections();
  server.close();
}
finish();

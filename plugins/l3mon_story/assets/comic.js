// The cold-open comic's player. Plain JavaScript, no framework, no outside request: it asks the platform for one story (a checked bundle),
// draws each panel from layered SVG pictures shown as images (an image cannot run code or reach the page), and puts the words in as real text.
//
//   open(slug)        play a story in a dialog over the current page (what the board's "Cold open" button calls)
//   mount(host, bundle, options)   play a bundle you already have inside an element (the story page, the tests, the preview)
//
// The same story is playable four ways and none is required: as a motion comic (the default), as a still strip of panels (when the person
// asked their system for less motion, or chose it), as plain text (the transcript), or as the page's own text when no script runs.
// Sound is off until the person turns it on. Keyboard: arrows, space, home, end, escape, S (sound), T (text).

import { Sound } from './sounds.js';
import { bubbleState, dueCues, enterState, firstChars, layerState, spoken } from './timeline.js';

const SVG_URL = (svg) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const FOCUSABLE = 'button, a[href], [tabindex]:not([tabindex="-1"])';

/** Where a layer turns and grows from: its own middle, or the middle of the panel so a backdrop pushes in like a camera. */
function pivotOf(layer) {
  if (layer.pivot !== 'panel') return '';
  return `;transform-origin:${((50 - layer.x) / layer.w) * 100}% ${((50 - layer.y) / layer.h) * 100}%`;
}

function el(tag, cls, attrs, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private window or blocked storage: the comic works without it */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

/** The player for one bundle inside `host`. */
export class Player {
  constructor(host, bundle, options = {}) {
    this.host = host;
    this.bundle = bundle;
    this.mode = options.mode || 'page';
    this.onClose = options.onClose || null;
    this.index = 0;
    this.t = 0;
    this.prev = -1;
    this.playing = false;
    this.ended = false;
    this.closed = false;
    this.strip = options.strip !== undefined ? options.strip : reducedMotion();
    this.sound = new Sound();
    this.urls = new Map();
    this.raf = 0;
    this.last = 0;
    this.W = 640;
    this.H = 360;
    this.build();
    this.bind();
    this.render();
  }

  // ---- building ----------------------------------------------------------------------------------------------------------

  art(id) {
    if (!this.urls.has(id)) this.urls.set(id, SVG_URL(this.bundle.art[id]));
    return this.urls.get(id);
  }

  build() {
    const b = this.bundle;
    this.root = el('div', `l3m l3m-${this.mode}`, { role: this.mode === 'overlay' ? 'dialog' : 'group', 'aria-label': `Cold open: ${b.title}`, tabindex: '-1', lang: b.lang });
    if (this.mode === 'overlay') this.root.setAttribute('aria-modal', 'true');

    const bar = el('div', 'l3m-bar');
    this.kicker = el('span', 'l3m-kicker', null, b.kicker);
    this.bar = bar;
    bar.append(el('span', 'l3m-tag', null, 'COLD OPEN'), this.kicker, el('strong', 'l3m-title', null, b.title));
    if (this.mode === 'overlay' || this.onClose) {
      this.closeBtn = el('button', 'l3m-btn l3m-close', { type: 'button', 'aria-label': 'Close the cold open', title: 'Close (Esc)' }, '✕');
      bar.append(this.closeBtn);
    }

    this.tv = el('div', 'l3m-tv');
    this.screen = el('div', 'l3m-screen');
    this.panelEl = el('div', 'l3m-panel');
    this.layersEl = el('div', 'l3m-layers');
    this.bubblesEl = el('div', 'l3m-bubbles', { 'aria-hidden': 'true' });
    this.noise = el('canvas', 'l3m-noise', { width: '96', height: '54', 'aria-hidden': 'true' });
    this.panelEl.append(this.layersEl, this.bubblesEl);
    this.screen.append(this.panelEl, this.noise, el('div', 'l3m-scan', { 'aria-hidden': 'true' }), el('div', 'l3m-bug', { 'aria-hidden': 'true' }, b.kicker));
    this.endCard = el('div', 'l3m-end', { hidden: '' });
    this.endCard.append(el('p', 'l3m-end-line', null, 'End of the cold open.'), el('p', null, null, 'Master control: you are on.'));
    this.replayBtn = el('button', 'l3m-btn l3m-replay', { type: 'button' }, '↻ Watch again');
    this.endCard.append(this.replayBtn);
    this.screen.append(this.endCard);
    this.tv.append(this.screen);

    this.sr = el('p', 'l3m-sr', { 'aria-live': 'polite', 'aria-atomic': 'true' });
    this.stripEl = el('div', 'l3m-strip', { hidden: '' });

    const controls = el('div', 'l3m-controls', { role: 'toolbar', 'aria-label': 'Cold open controls' });
    this.prevBtn = el('button', 'l3m-btn', { type: 'button', 'aria-label': 'Previous panel', title: 'Previous (←)' }, '◀');
    this.playBtn = el('button', 'l3m-btn l3m-play', { type: 'button', 'aria-label': 'Pause', title: 'Play or pause (Space)' }, '❚❚');
    this.nextBtn = el('button', 'l3m-btn', { type: 'button', 'aria-label': 'Next panel', title: 'Next (→)' }, '▶');
    this.dots = el('div', 'l3m-dots', { role: 'group', 'aria-label': 'Panels' });
    this.dotBtns = b.panels.map((_, i) => {
      const d = el('button', 'l3m-dot', { type: 'button', 'aria-label': `Panel ${i + 1} of ${b.panels.length}` });
      d.addEventListener('click', () => this.go(i, true));
      this.dots.append(d);
      return d;
    });
    this.soundBtn = el('button', 'l3m-btn l3m-sound', { type: 'button', 'aria-pressed': 'false', title: 'Sound on or off (S)' }, '🔇 Sound off');
    this.textBtn = el('button', 'l3m-btn l3m-text', { type: 'button', 'aria-pressed': 'false', title: 'Read it as text (T)' }, 'Aa Text');
    this.stripBtn = el('button', 'l3m-btn l3m-stripbtn', { type: 'button', 'aria-pressed': 'false', title: 'Show every panel at once, without motion' }, '▤ Strip');
    controls.append(this.prevBtn, this.playBtn, this.nextBtn, this.dots, this.soundBtn, this.textBtn, this.stripBtn);

    this.transcript = el('div', 'l3m-transcript', { hidden: '' });
    const list = el('ol');
    for (const panel of b.panels) list.append(el('li', null, null, spoken(panel)));
    this.transcript.append(el('h3', null, null, 'As text'), list);

    this.root.append(bar, this.tv, this.stripEl, this.sr, controls, this.transcript);
    this.controls = controls;
    this.host.append(this.root);
    this.measure();
  }

  measure() {
    const box = this.screen.getBoundingClientRect();
    if (box.width > 0) { this.W = box.width; this.H = box.height || (box.width * 9) / 16; }
  }

  bind() {
    this.on = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); (this.off ||= []).push(() => target.removeEventListener(type, fn, opts)); };
    this.on(this.prevBtn, 'click', () => this.go(this.index - 1, this.playing || this.ended === false));
    this.on(this.nextBtn, 'click', () => this.next());
    this.on(this.playBtn, 'click', () => (this.playing ? this.pause() : this.play()));
    this.on(this.replayBtn, 'click', (e) => { e.stopPropagation(); this.go(0, true); }); // the button sits on the screen, which advances on a click
    this.on(this.soundBtn, 'click', () => this.setSound(!this.sound.on));
    this.on(this.textBtn, 'click', () => this.setTranscript(this.transcript.hidden));
    this.on(this.stripBtn, 'click', () => this.setStrip(!this.strip));
    this.on(this.screen, 'click', () => (this.ended ? this.go(0, true) : this.next()));
    if (this.closeBtn) this.on(this.closeBtn, 'click', () => this.close());
    this.on(this.root, 'keydown', (e) => this.key(e));
    // Escape works wherever focus has gone (a button that was hidden took it with it)
    this.on(document, 'keydown', (e) => { if (e.key === 'Escape' && !this.root.contains(e.target) && (this.mode === 'overlay' || this.onClose)) this.close(); });
    this.on(document, 'visibilitychange', () => { if (document.hidden) this.pause(); });
    if (typeof ResizeObserver === 'function') {
      this.resizer = new ResizeObserver(() => { this.measure(); if (!this.strip) this.draw(); });
      this.resizer.observe(this.screen);
    } else {
      this.on(window, 'resize', () => { this.measure(); this.draw(); });
    }
  }

  key(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = (e.target && e.target.tagName) || '';
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); this.next(); break;
      case 'ArrowLeft': e.preventDefault(); this.go(this.index - 1, this.playing); break;
      case 'Home': e.preventDefault(); this.go(0, this.playing); break;
      case 'End': e.preventDefault(); this.go(this.bundle.panels.length - 1, false); break;
      case ' ': if (tag !== 'BUTTON' && tag !== 'A') { e.preventDefault(); this.playing ? this.pause() : this.play(); } break;
      case 's': case 'S': this.setSound(!this.sound.on); break;
      case 't': case 'T': this.setTranscript(this.transcript.hidden); break;
      case 'Escape': if (this.mode === 'overlay' || this.onClose) { e.preventDefault(); this.close(); } break;
      case 'Tab': this.trap(e); break;
      default: break;
    }
  }

  trap(e) {
    if (this.mode !== 'overlay') return;
    const nodes = [...this.root.querySelectorAll(FOCUSABLE)].filter((n) => !n.closest('[hidden]'));
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === this.root)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  // ---- showing -----------------------------------------------------------------------------------------------------------

  render() {
    this.stripBtn.setAttribute('aria-pressed', String(this.strip));
    this.tv.hidden = this.strip;
    this.stripEl.hidden = !this.strip;
    this.controls.classList.toggle('l3m-stripmode', this.strip);
    for (const node of [this.prevBtn, this.playBtn, this.nextBtn, this.dots, this.soundBtn]) node.hidden = this.strip;
    if (this.strip) { this.pause(); this.buildStrip(); this.keepFocus(); return; }
    this.go(this.index, false, true);
    this.keepFocus();
  }

  /** A button that is hidden takes focus with it; keep the person inside the comic. */
  keepFocus() {
    const a = document.activeElement;
    if (!a || a === document.body || !this.root.contains(a) || a.closest('[hidden]')) this.root.focus({ preventScroll: true });
  }

  buildStrip() {
    this.stripEl.replaceChildren();
    for (const [i, panel] of this.bundle.panels.entries()) {
      const fig = el('figure', 'l3m-figure');
      const screen = el('div', 'l3m-screen l3m-still');
      const box = el('div', 'l3m-panel');
      const layers = el('div', 'l3m-layers');
      const bubbles = el('div', 'l3m-bubbles');
      box.append(layers, bubbles);
      screen.append(box);
      fig.append(screen, el('figcaption', null, null, `${i + 1}. ${panel.alt}`));
      this.stripEl.append(fig);
      const w = 640;
      const h = 360;
      this.paintLayers(layers, panel, panel.ms, w, h);
      this.paintBubbles(bubbles, panel, panel.ms, true);
    }
  }

  paintLayers(into, panel, t, w, h) {
    into.replaceChildren();
    const shown = enterState('cut', t);
    for (const layer of panel.layers) {
      const img = el('img', 'l3m-layer', { alt: '', draggable: 'false', decoding: 'async', src: this.art(layer.art) });
      img.style.cssText = `left:${layer.x}%;top:${layer.y}%;width:${layer.w}%;height:${layer.h}%;z-index:${layer.z || 0}${pivotOf(layer)}`;
      const s = layerState(layer, t, panel.ms, w, h);
      img.style.transform = `translate(${s.tx}px,${s.ty}px) rotate(${s.rot}deg) scale(${s.scale * (layer.flip ? -1 : 1)}, ${s.scale})`;
      img.style.opacity = String(s.opacity * shown.opacity);
      into.append(img);
    }
  }

  paintBubbles(into, panel, t, full) {
    into.replaceChildren();
    for (const bubble of panel.bubbles || []) {
      const node = el('div', `l3m-bubble l3m-${bubble.kind} l3m-tail-${bubble.tail || 'none'} l3m-size-${bubble.size || 'm'}`);
      node.style.cssText = `left:${bubble.x}%;top:${bubble.y}%;width:${bubble.w}%`;
      if (bubble.who && bubble.kind !== 'caption' && bubble.kind !== 'sfx') node.append(el('span', 'l3m-who', null, bubble.who));
      const text = el('span', 'l3m-words', null, full ? bubble.text : '');
      node.append(text);
      node.dataset.full = bubble.text;
      into.append(node);
    }
  }

  /** Go to a panel. `play` starts it moving; `quiet` skips the announcement (the first draw). */
  go(i, play, quiet) {
    const last = this.bundle.panels.length - 1;
    this.index = Math.max(0, Math.min(last, i));
    this.t = 0;
    this.prev = -1;
    this.ended = false;
    this.endCard.hidden = true;
    const panel = this.bundle.panels[this.index];
    this.paintLayers(this.layersEl, panel, 0, this.W, this.H);
    this.paintBubbles(this.bubblesEl, panel, 0, false);
    this.layerEls = [...this.layersEl.children];
    this.bubbleEls = [...this.bubblesEl.children];
    this.dotBtns.forEach((d, n) => { d.setAttribute('aria-current', n === this.index ? 'step' : 'false'); d.classList.toggle('l3m-done', n < this.index); });
    this.keepFocus();
    this.prevBtn.disabled = this.index === 0;
    this.nextBtn.disabled = false;
    if (!quiet) this.sr.textContent = `Panel ${this.index + 1} of ${last + 1}. ${spoken(panel)}`;
    this.draw();
    if (play) this.play(); else this.pause();
  }

  next() {
    if (this.index >= this.bundle.panels.length - 1) { this.finish(); return; }
    this.go(this.index + 1, true);
  }

  finish() {
    this.ended = true;
    this.pause();
    this.endCard.hidden = false;
    remember(`l3m.seen.${this.bundle.slug}`, '1');
    this.sr.textContent = 'End of the cold open. Master control: you are on.';
    if (this.mode === 'overlay') this.replayBtn.focus();
  }

  play() {
    if (this.strip) return;
    if (this.ended) { this.go(0, true); return; }
    this.playing = true;
    this.playBtn.textContent = '❚❚';
    this.playBtn.setAttribute('aria-label', 'Pause');
    this.last = performance.now();
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame((now) => this.loop(now));
  }

  pause() {
    this.playing = false;
    cancelAnimationFrame(this.raf);
    this.playBtn.textContent = '▶';
    this.playBtn.setAttribute('aria-label', 'Play');
  }

  loop(now) {
    if (!this.playing) return;
    const dt = Math.min(100, now - this.last); // a hidden tab or a slow frame never skips a whole panel
    this.last = now;
    const panel = this.bundle.panels[this.index];
    this.t += dt;
    if (this.t >= panel.ms) { this.t = panel.ms; this.draw(); this.next(); return; }
    this.draw();
    this.raf = requestAnimationFrame((n) => this.loop(n));
  }

  draw() {
    if (this.strip) return;
    const panel = this.bundle.panels[this.index];
    const enter = enterState(panel.enter || 'cut', this.t);
    this.panelEl.style.opacity = String(enter.opacity);
    this.panelEl.style.transform = `translateX(${enter.shift}%) scale(${enter.scale})`;
    panel.layers.forEach((layer, n) => {
      const s = layerState(layer, this.t, panel.ms, this.W, this.H);
      const img = this.layerEls[n];
      if (img) {
        img.style.transform = `translate(${s.tx}px,${s.ty}px) rotate(${s.rot}deg) scale(${s.scale * (layer.flip ? -1 : 1)}, ${s.scale})`;
        img.style.opacity = String(s.opacity);
      }
    });
    (panel.bubbles || []).forEach((bubble, n) => {
      const node = this.bubbleEls[n];
      if (!node) return;
      const s = bubbleState(bubble, this.t);
      node.style.visibility = s.visible ? 'visible' : 'hidden';
      node.style.transform = `scale(${0.7 + 0.3 * s.pop}) rotate(${bubble.kind === 'sfx' ? -6 : 0}deg)`;
      node.style.opacity = String(s.pop);
      const words = node.querySelector('.l3m-words');
      const shown = firstChars(bubble.text, s.chars);
      if (words.textContent !== shown) words.textContent = shown;
    });
    this.paintNoise(enter.noise);
    if (this.playing || this.t === 0) {
      for (const cue of dueCues(panel, this.prev, this.t)) this.sound.play(cue);
    }
    this.prev = this.t;
  }

  paintNoise(strength) {
    if (strength <= 0.01) { if (this.noise.style.opacity !== '0') this.noise.style.opacity = '0'; return; }
    const ctx = this.noise.getContext('2d');
    if (!ctx) return;
    const img = ctx.createImageData(96, 54);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = Math.random() * 255;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      img.data[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    this.noise.style.opacity = String(strength);
  }

  // ---- the person's choices ----------------------------------------------------------------------------------------------

  setSound(on) {
    if (on && !this.sound.enable()) {
      this.soundBtn.textContent = '🔇 No sound here';
      return;
    }
    if (!on) this.sound.disable();
    this.soundBtn.setAttribute('aria-pressed', String(on));
    this.soundBtn.textContent = on ? '🔊 Sound on' : '🔇 Sound off';
    if (on) this.sound.play('pop');
  }

  setTranscript(show) {
    this.transcript.hidden = !show;
    this.textBtn.setAttribute('aria-pressed', String(show));
  }

  setStrip(on) {
    this.strip = on;
    this.render();
    if (!on) this.go(0, true);
  }

  close() {
    if (this.closed) return;
    this.destroy();
    if (this.onClose) this.onClose();
  }

  destroy() {
    this.closed = true;
    this.pause();
    this.sound.disable();
    if (this.resizer) this.resizer.disconnect();
    (this.off || []).forEach((undo) => undo());
    this.root.remove();
  }
}

export function mount(host, bundle, options) {
  return new Player(host, bundle, options);
}

// ---- getting a story ---------------------------------------------------------------------------------------------------------

/** Fetch a story. Resolves {ok: true, bundle} or {ok: false, reason: 'signin'|'verify'|'banned'|'closed'|'offline'|'broken'}. */
export async function fetchStory(url) {
  let response;
  try {
    response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  } catch {
    return { ok: false, reason: 'offline' };
  }
  if (response.status === 401) return { ok: false, reason: 'signin' };
  if (response.status === 404) return { ok: false, reason: 'closed' };
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (response.status === 403) return { ok: false, reason: (body && body.error === 'unverified') ? 'verify' : 'banned' };
  if (!response.ok || !body || !body.data || !Array.isArray(body.data.panels)) return { ok: false, reason: 'broken' };
  return { ok: true, bundle: body.data };
}

const MESSAGES = {
  signin: ['Sign up to watch', 'Cold opens are for registered crews. It takes a minute, and you come straight back.'],
  verify: ['Verify your email first', 'Confirm your email address to watch.'],
  banned: ['Off air for this account', 'This account cannot watch right now.'],
  closed: ['Not on air yet', 'This cold open starts when its channel does.'],
  offline: ['No connection', 'Could not reach the studio. Check your connection and try again.'],
  broken: ['Technical difficulty', 'Something broke on our side. Try again in a moment.'],
};

function notice(host, reason, slug, onClose) {
  const [title, text] = MESSAGES[reason] || MESSAGES.broken;
  const box = el('div', 'l3m l3m-overlay l3m-notice', { role: 'dialog', 'aria-modal': 'true', 'aria-label': title, tabindex: '-1' });
  box.append(el('h2', null, null, title), el('p', null, null, text));
  if (reason === 'signin') {
    const next = encodeURIComponent(`/story/${slug}`);
    box.append(el('a', 'l3m-btn l3m-cta', { href: `/register?next=${next}` }, 'Create your account'), el('a', 'l3m-btn', { href: `/login?next=${next}` }, 'I already have one'));
  }
  const close = el('button', 'l3m-btn l3m-close', { type: 'button', 'aria-label': 'Close' }, '✕');
  close.addEventListener('click', onClose);
  box.addEventListener('keydown', (e) => { if (e.key === 'Escape') onClose(); });
  box.append(close);
  host.append(box);
  box.focus();
  return box;
}

let active = null;

/** Open a story over the page. Safe to call twice: the second call replaces the first. */
export async function open(slug, { opener, url } = {}) {
  if (active) active.close();
  const backdrop = el('div', 'l3m-backdrop');
  const returnTo = opener || document.activeElement;
  const hidden = [];
  for (const child of [...document.body.children]) {
    if (!child.hasAttribute('inert')) { child.setAttribute('inert', ''); hidden.push(child); }
  }
  document.body.append(backdrop);
  document.documentElement.classList.add('l3m-lock');
  const done = () => {
    backdrop.remove();
    hidden.forEach((n) => n.removeAttribute('inert'));
    document.documentElement.classList.remove('l3m-lock');
    active = null;
    if (returnTo && typeof returnTo.focus === 'function') returnTo.focus();
  };
  const handle = { close: done };
  active = handle;
  const loading = el('p', 'l3m-loading', { role: 'status' }, 'Tuning in…');
  backdrop.append(loading);
  const found = await fetchStory(url || `/api/v1/l3mon/story/${encodeURIComponent(slug)}`);
  if (active !== handle) return null;
  loading.remove();
  if (!found.ok) { notice(backdrop, found.reason, slug, done); return null; }
  const player = mount(backdrop, found.bundle, { mode: 'overlay', onClose: done, strip: reducedMotion() });
  handle.close = () => { player.destroy(); done(); };
  player.root.focus();
  if (!player.strip) player.go(0, true);
  return player;
}

// ---- the story page ----------------------------------------------------------------------------------------------------------

async function mountFromPage() {
  const stage = document.getElementById('l3m-stage');
  if (!stage || !stage.dataset.storyUrl) return;
  const found = await fetchStory(stage.dataset.storyUrl);
  stage.replaceChildren();
  if (!found.ok) { notice(stage, found.reason, stage.dataset.slug || '', () => { location.href = '/'; }); return; }
  const player = mount(stage, found.bundle, { mode: 'page' });
  if (!player.strip) player.go(0, true);
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountFromPage);
  else mountFromPage();
}

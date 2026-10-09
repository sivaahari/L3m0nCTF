// The cold open's clock, as pure functions: given a panel and a time, what is on the screen. No page, no DOM, no random numbers, so the same
// time always gives the same picture (the tests rely on it, and so will a renderer that turns the comic into a video clip).

export const clamp = (v, low, high) => Math.min(high, Math.max(low, v));

/** Ease in and out: 0 to 1. */
export const ease = (u) => (u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2);

const chars = (text) => Array.from(text); // by character, never by half of an emoji

/** How many characters of a bubble are showing at time t (ms in the panel). The whole text always takes at most `maxMs` to type. */
export function typedCount(text, t, at, perChar = 26, maxMs = 1500) {
  const n = chars(text).length;
  if (t < at) return 0;
  const per = Math.min(perChar, maxMs / Math.max(1, n));
  return Math.min(n, Math.floor((t - at) / per) + 1);
}

export const firstChars = (text, n) => chars(text).slice(0, n).join('');

/** Where a layer is at time t: pixels of movement for a panel of W by H, a scale, a turn in degrees, an opacity. */
export function layerState(layer, t, ms, W, H) {
  const from = layer.from || {};
  const to = layer.to || from;
  const u = ease(clamp(t / ms, 0, 1));
  const mix = (a, b, d) => (a === undefined ? d : a) + ((b === undefined ? (a === undefined ? d : a) : b) - (a === undefined ? d : a)) * u;
  let tx = (mix(from.x, to.x, 0) / 100) * W;
  let ty = (mix(from.y, to.y, 0) / 100) * H;
  let scale = mix(from.s, to.s, 1);
  let rot = 0;
  switch (layer.anim) {
    case 'bob': ty += Math.sin(t / 450) * H * 0.012; break;
    case 'sway': rot += Math.sin(t / 700) * 1.6; break;
    case 'pulse': scale *= 1 + Math.sin(t / 400) * 0.03; break;
    case 'shake': tx += Math.sin(t * 0.09) * W * 0.006; ty += Math.cos(t * 0.13) * H * 0.006; break;
    default: break;
  }
  return { tx, ty, scale, rot, opacity: layer.opacity === undefined ? 1 : layer.opacity };
}

/** How a panel arrives: opacity, a sideways shift (percent of the width), a scale, and how strong the TV static is (0 to 1). */
export function enterState(kind, t) {
  switch (kind) {
    case 'static': return { opacity: t < 90 ? 0 : 1, shift: 0, scale: 1, noise: clamp(1 - t / 460, 0, 1) };
    case 'slide': { const u = ease(clamp(t / 380, 0, 1)); return { opacity: u, shift: (1 - u) * 9, scale: 1, noise: 0 }; }
    case 'pop': { const u = ease(clamp(t / 260, 0, 1)); return { opacity: u, shift: 0, scale: 0.9 + 0.1 * u, noise: 0 }; }
    default: return { opacity: 1, shift: 0, scale: 1, noise: 0 };
  }
}

/** How a bubble looks at time t: not there yet, popping in, typing, or complete. */
export function bubbleState(bubble, t) {
  const at = bubble.at || 0;
  const typed = bubble.kind === 'sfx' ? chars(bubble.text).length : typedCount(bubble.text, t, at);
  return { visible: t >= at, chars: t >= at ? typed : 0, pop: clamp((t - at) / 170, 0, 1), done: t >= at && typed >= chars(bubble.text).length };
}

/** The sound cues whose time falls in (from, to]. The very first frame passes from = -1 so a cue at 0 is heard. */
export function dueCues(panel, from, to) {
  return (panel.sfx || []).filter((c) => (c.at || 0) > from && (c.at || 0) <= to).map((c) => c.cue);
}

export const totalMs = (bundle) => bundle.panels.reduce((sum, p) => sum + p.ms, 0);

/** The text of a panel for a screen reader or a transcript: what the picture shows, then who says what. */
export function spoken(panel) {
  const lines = (panel.bubbles || []).map((b) => (b.who ? `${b.who}: ${b.text}` : b.text));
  return [panel.alt, ...lines].join(' ');
}

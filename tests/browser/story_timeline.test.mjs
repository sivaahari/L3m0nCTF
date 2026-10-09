// The cold open's clock, as pure functions (plugins/l3mon_story/assets/timeline.js). Run: node --test tests/browser/story_timeline.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { bubbleState, clamp, dueCues, ease, enterState, firstChars, layerState, spoken, totalMs, typedCount } from '../../plugins/l3mon_story/assets/timeline.js';

test('clamp and ease stay in range and meet at the ends', () => {
  assert.equal(clamp(5, 0, 1), 1);
  assert.equal(clamp(-5, 0, 1), 0);
  assert.equal(ease(0), 0);
  assert.equal(ease(1), 1);
  assert.equal(ease(0.5), 0.5);
  for (let u = 0; u <= 1; u += 0.05) assert.ok(ease(u) >= 0 && ease(u) <= 1);
  assert.ok(ease(0.25) < 0.25 && ease(0.75) > 0.75, 'slow at the ends, fast in the middle');
});

test('typing: nothing before the bubble, a few characters at a time, all of it in the end, never more than a second and a half', () => {
  assert.equal(typedCount('Hello there', 100, 500), 0);
  assert.equal(typedCount('Hello there', 500, 500), 1);
  assert.ok(typedCount('Hello there', 650, 500) > 1);
  assert.equal(typedCount('Hello there', 5000, 500), 11);
  const long = 'x'.repeat(160);
  assert.equal(typedCount(long, 500 + 1500, 500), 160, 'a long text still takes at most 1.5 seconds');
  assert.equal(typedCount('', 1000, 0), 0);
});

test('typing counts characters, not halves of an emoji', () => {
  const text = 'a😀b';
  assert.equal(typedCount(text, 10000, 0), 3);
  assert.equal(firstChars(text, 2), 'a😀');
  assert.equal(firstChars(text, 0), '');
  assert.equal(firstChars(text, 99), text);
});

test('a layer moves from its start to its end over the panel, and stands still without a move', () => {
  const layer = { from: { x: 0, y: 0, s: 1 }, to: { x: -10, y: 5, s: 1.2 } };
  const start = layerState(layer, 0, 4000, 1000, 500);
  const end = layerState(layer, 4000, 4000, 1000, 500);
  assert.deepEqual([start.tx, start.ty, start.scale], [0, 0, 1]);
  assert.deepEqual([end.tx, end.ty, +end.scale.toFixed(6)], [-100, 25, 1.2]);
  const mid = layerState(layer, 2000, 4000, 1000, 500);
  assert.ok(Math.abs(mid.tx + 50) < 1e-9, 'half way at half the time');
  const still = layerState({}, 1234, 4000, 1000, 500);
  assert.deepEqual([still.tx, still.ty, still.scale, still.rot, still.opacity], [0, 0, 1, 0, 1]);
  assert.equal(layerState({ opacity: 0.4 }, 0, 4000, 1, 1).opacity, 0.4);
  const past = layerState(layer, 99999, 4000, 1000, 500);
  assert.equal(past.tx, -100, 'after the end it stays at the end');
});

test('a layer with only an end position starts where it is', () => {
  const s = layerState({ to: { x: 10 } }, 0, 1000, 1000, 500);
  assert.equal(s.tx, 0);
  assert.equal(layerState({ to: { x: 10 } }, 1000, 1000, 1000, 500).tx, 100);
});

test('the ongoing animations are small, repeat, and never depend on chance', () => {
  for (const anim of ['bob', 'sway', 'pulse', 'shake']) {
    const a = layerState({ anim }, 1234, 5000, 1000, 500);
    const b = layerState({ anim }, 1234, 5000, 1000, 500);
    assert.deepEqual(a, b, `${anim} gives the same answer for the same time`);
    assert.ok(Math.abs(a.tx) < 20 && Math.abs(a.ty) < 20 && Math.abs(a.rot) < 3 && Math.abs(a.scale - 1) < 0.05, anim);
  }
  assert.notDeepEqual(layerState({ anim: 'bob' }, 0, 5000, 1000, 500), layerState({ anim: 'bob' }, 700, 5000, 1000, 500));
});

test('how a panel arrives', () => {
  assert.deepEqual(enterState('cut', 0), { opacity: 1, shift: 0, scale: 1, noise: 0 });
  const early = enterState('static', 10);
  assert.equal(early.opacity, 0);
  assert.equal(early.noise > 0.9, true);
  const late = enterState('static', 600);
  assert.deepEqual([late.opacity, late.noise], [1, 0]);
  const slide = enterState('slide', 0);
  assert.equal(slide.shift, 9);
  assert.equal(enterState('slide', 400).shift, 0);
  assert.equal(enterState('pop', 0).scale, 0.9);
  assert.equal(enterState('pop', 300).scale, 1);
  assert.deepEqual(enterState('something-else', 5), enterState('cut', 5), 'an unknown entrance is a cut');
});

test('a bubble is hidden, then pops in and types, then is complete', () => {
  const b = { kind: 'say', text: 'Sixty seconds!', at: 800 };
  assert.deepEqual(bubbleState(b, 0), { visible: false, chars: 0, pop: 0, done: false });
  const mid = bubbleState(b, 900);
  assert.equal(mid.visible, true);
  assert.ok(mid.chars > 0 && mid.chars < 14 && mid.done === false);
  const end = bubbleState(b, 4000);
  assert.deepEqual([end.chars, end.pop, end.done], [14, 1, true]);
  const sfx = bubbleState({ kind: 'sfx', text: 'POW!', at: 100 }, 100);
  assert.equal(sfx.chars, 4, 'a sound-word is not typed');
});

test('sound cues fire once, in the window they belong to', () => {
  const panel = { sfx: [{ cue: 'whoosh', at: 0 }, { cue: 'pop', at: 500 }, { cue: 'tada', at: 500 }] };
  assert.deepEqual(dueCues(panel, -1, 0), ['whoosh']);
  assert.deepEqual(dueCues(panel, 0, 499), []);
  assert.deepEqual(dueCues(panel, 499, 500), ['pop', 'tada']);
  assert.deepEqual(dueCues(panel, 500, 9999), []);
  assert.deepEqual(dueCues({}, -1, 9999), []);
});

test('the text of a panel and the length of a story', () => {
  const panel = { alt: 'A street at dusk.', bubbles: [{ who: 'Tara', text: 'Good evening.' }, { text: 'The bars hum.' }] };
  assert.equal(spoken(panel), 'A street at dusk. Tara: Good evening. The bars hum.');
  assert.equal(spoken({ alt: 'Only a picture.' }), 'Only a picture.');
  assert.equal(totalMs({ panels: [{ ms: 4000 }, { ms: 5000 }] }), 9000);
});

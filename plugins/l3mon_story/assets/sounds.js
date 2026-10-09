// The cartoon stings of the cold open, made in the browser with the Web Audio interface: no audio files, nothing to host, nothing to load.
// Off until the player turns it on; the audio context is only created after that click (browsers refuse it before a gesture, and so do we).
// Every sting is a few oscillators or a burst of noise with a short envelope, kept quiet (the master gain is low).

const MASTER = 0.18;

/** The names the story format allows (format.py CUES). A name that is not one of these is ignored. */
export const CUES = ['whoosh', 'pop', 'bonk', 'tada', 'sparkle', 'static', 'boing', 'zap', 'thud', 'jingle', 'sting', 'tick'];

export class Sound {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.on = false;
  }

  /** Turn the sound on (call from a click). Returns false when the browser has no Web Audio. */
  enable() {
    const Ctx = typeof window !== 'undefined' && (window.AudioContext || window.webkitAudioContext);
    if (!Ctx) return false;
    if (!this.ctx) {
      this.ctx = new Ctx();
      this.master = this.ctx.createGain();
      this.master.gain.value = MASTER;
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
    this.on = true;
    return true;
  }

  disable() {
    this.on = false;
  }

  /** Play a sting by name. Does nothing when the sound is off or the name is not one of CUES. */
  play(cue) {
    if (!this.on || !this.ctx || !CUES.includes(cue)) return;
    const t = this.ctx.currentTime + 0.01;
    try {
      RECIPES[cue](this, t);
    } catch {
      /* a sting that cannot play is not worth an error */
    }
  }

  tone(type, from, to, start, length, gain = 1) {
    const osc = this.ctx.createOscillator();
    const amp = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(from, start);
    if (to !== from) osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), start + length);
    amp.gain.setValueAtTime(0.0001, start);
    amp.gain.exponentialRampToValueAtTime(gain, start + 0.012);
    amp.gain.exponentialRampToValueAtTime(0.0001, start + length);
    osc.connect(amp).connect(this.master);
    osc.start(start);
    osc.stop(start + length + 0.03);
  }

  noise(start, length, from, to, gain = 1) {
    const frames = Math.max(1, Math.floor(this.ctx.sampleRate * length));
    const buffer = this.ctx.createBuffer(1, frames, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < frames; i++) data[i] = Math.random() * 2 - 1;
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    const filter = this.ctx.createBiquadFilter();
    filter.type = 'bandpass';
    filter.Q.value = 0.9;
    filter.frequency.setValueAtTime(from, start);
    filter.frequency.exponentialRampToValueAtTime(Math.max(20, to), start + length);
    const amp = this.ctx.createGain();
    amp.gain.setValueAtTime(0.0001, start);
    amp.gain.exponentialRampToValueAtTime(gain, start + length * 0.2);
    amp.gain.exponentialRampToValueAtTime(0.0001, start + length);
    src.connect(filter).connect(amp).connect(this.master);
    src.start(start);
  }
}

const NOTE = (n) => 440 * Math.pow(2, (n - 69) / 12);

const RECIPES = {
  whoosh: (s, t) => s.noise(t, 0.42, 300, 3200, 0.9),
  pop: (s, t) => s.tone('sine', 900, 380, t, 0.09, 0.9),
  bonk: (s, t) => { s.tone('sine', 240, 110, t, 0.18, 1); s.tone('triangle', 520, 260, t, 0.05, 0.4); },
  tada: (s, t) => [60, 64, 67, 72].forEach((n, i) => s.tone('triangle', NOTE(n), NOTE(n), t + i * 0.09, i === 3 ? 0.5 : 0.14, 0.7)),
  sparkle: (s, t) => [84, 88, 91, 96, 93].forEach((n, i) => s.tone('sine', NOTE(n), NOTE(n), t + i * 0.06, 0.18, 0.4)),
  static: (s, t) => s.noise(t, 0.28, 1800, 5200, 0.7),
  boing: (s, t) => { s.tone('sine', 180, 640, t, 0.11, 0.9); s.tone('sine', 640, 150, t + 0.11, 0.3, 0.9); },
  zap: (s, t) => s.tone('sawtooth', 1800, 90, t, 0.22, 0.45),
  thud: (s, t) => { s.tone('sine', 90, 45, t, 0.22, 1); s.noise(t, 0.08, 400, 120, 0.4); },
  jingle: (s, t) => [67, 71, 74, 79].forEach((n, i) => s.tone('square', NOTE(n), NOTE(n), t + i * 0.11, 0.16, 0.25)),
  sting: (s, t) => { s.tone('sawtooth', NOTE(52), NOTE(52), t, 0.5, 0.35); s.tone('sawtooth', NOTE(55), NOTE(55), t + 0.12, 0.5, 0.3); },
  tick: (s, t) => s.tone('square', 1600, 1200, t, 0.02, 0.4),
};

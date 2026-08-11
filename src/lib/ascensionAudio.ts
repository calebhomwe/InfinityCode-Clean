// Original WebAudio ascension chimes -- no external assets, no copyright.
// Created on first user gesture; safe no-op when audio is unavailable.

let ctx: AudioContext | null = null;

function ensureCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (ctx) return ctx;
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
  } catch {
    return null;
  }
  return ctx;
}

/** One short note with an exponential decay envelope. */
function note(c: AudioContext, freq: number, at: number, gain: number): void {
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = "triangle";
  osc.frequency.setValueAtTime(freq, at);
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(gain, at + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, at + 0.5);
  osc.connect(g);
  g.connect(c.destination);
  osc.start(at);
  osc.stop(at + 0.55);
}

// Per-form root frequencies: an ascending arpeggio in A.
const ROOTS = [440, 523.25, 587.33, 659.25, 783.99, 1046.5];

/** Play the ascension cue for a form level (0-5). Decorative only. */
export function playAscension(level: number): void {
  if (typeof window === "undefined") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const c = ensureCtx();
  if (!c) return;
  if (c.state === "suspended") void c.resume().catch(() => undefined);
  const base = ROOTS[Math.max(0, Math.min(level, 5))];
  const t = c.currentTime + 0.03;
  const steps = Math.min(level + 2, 4);
  for (let i = 0; i < steps; i += 1) {
    note(c, base * (1 + 0.25 * i), t + i * 0.09, 0.12);
  }
  if (level >= 4) {
    // Low pad for Blue / Mr X Final -- calm under the arpeggio.
    note(c, base / 4, t, 0.06);
  }
}

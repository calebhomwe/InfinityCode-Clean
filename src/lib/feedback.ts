// Feedback: synthesized completion sounds + desktop notifications.
//
// Deliberately self-contained — no audio files, no new deps, no Tauri allowlist
// changes. Sounds are generated live with the Web Audio API; notifications use
// the standard Notification API (works in WebView2). Both respect user prefs
// persisted in localStorage and dispatched via the "infinity:feedback-prefs"
// event so Settings can toggle them live.

export type SoundKind =
  | "success"
  | "error"
  | "start"
  | "tick"
  | "send"    // message leaves the composer — quick, tactile lift
  | "turn"    // assistant finished a chat turn — subtle, low-energy
  | "tool"    // a tool call resolved — mechanical click
  | "approve"; // user approved a gated action — warm confirming double-blip

interface FeedbackPrefs {
  sound: boolean;
  notifications: boolean;
  volume: number; // 0..1
}

const PREFS_KEY = "infinity-feedback";

const DEFAULTS: FeedbackPrefs = { sound: true, notifications: true, volume: 0.35 };

export function getFeedbackPrefs(): FeedbackPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<FeedbackPrefs>;
    return {
      sound: parsed.sound ?? DEFAULTS.sound,
      notifications: parsed.notifications ?? DEFAULTS.notifications,
      volume:
        typeof parsed.volume === "number"
          ? Math.min(1, Math.max(0, parsed.volume))
          : DEFAULTS.volume,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function setFeedbackPrefs(patch: Partial<FeedbackPrefs>): FeedbackPrefs {
  const next = { ...getFeedbackPrefs(), ...patch };
  localStorage.setItem(PREFS_KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent("infinity:feedback-prefs"));
  return next;
}

// --- Web Audio: one shared context, lazily created on first sound -------- //

let audioCtx: AudioContext | null = null;

function ctx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  try {
    if (!audioCtx) {
      const Ctor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;
      if (!Ctor) return null;
      audioCtx = new Ctor();
    }
    // Autoplay policy can leave the context suspended until a gesture.
    if (audioCtx.state === "suspended") void audioCtx.resume();
    return audioCtx;
  } catch {
    return null;
  }
}

// A single enveloped tone. Short attack, exponential release — reads as a
// clean "UI blip" rather than a raw beep.
function tone(
  ac: AudioContext,
  freq: number,
  startAt: number,
  durationMs: number,
  peak: number,
  type: OscillatorType = "sine",
  pan = 0,
): void {
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  const filter = ac.createBiquadFilter();
  const panner = typeof ac.createStereoPanner === "function" ? ac.createStereoPanner() : null;
  osc.type = type;
  osc.frequency.setValueAtTime(freq, startAt);
  filter.type = "lowpass";
  filter.frequency.setValueAtTime(Math.max(1500, freq * 3.2), startAt);
  filter.Q.setValueAtTime(0.65, startAt);
  if (panner) panner.pan.setValueAtTime(Math.max(-1, Math.min(1, pan)), startAt);
  const end = startAt + durationMs / 1000;
  gain.gain.setValueAtTime(0, startAt);
  gain.gain.linearRampToValueAtTime(peak, startAt + 0.008);
  gain.gain.exponentialRampToValueAtTime(0.0001, end);
  osc.connect(filter).connect(gain);
  if (panner) gain.connect(panner).connect(ac.destination);
  else gain.connect(ac.destination);
  osc.start(startAt);
  osc.stop(end + 0.02);
}

// Each kind is a tiny motif. Success is a bright major arpeggio; error a low
// minor two-note; start a quick upward "whoosh"; tick a single soft blip.
const MOTIFS: Record<SoundKind, Array<[freq: number, atMs: number, durMs: number, type?: OscillatorType]>> = {
  success: [
    [587.33, 0, 110],   // D5
    [739.99, 90, 110],  // F#5
    [987.77, 180, 220], // B5
  ],
  error: [
    [220.0, 0, 160, "triangle"], // A3
    [174.61, 130, 240, "triangle"], // F3
  ],
  start: [
    [392.0, 0, 70, "sawtooth"], // G4
    [587.33, 55, 90, "sawtooth"], // D5
  ],
  tick: [[880.0, 0, 45]],
  send: [
    [392.0, 0, 54, "triangle"],
    [659.25, 42, 92, "sine"],
  ],
  // Turn: soft two-note fall — "done thinking, over to you." Quieter than success.
  turn: [
    [659.25, 0, 90],   // E5
    [523.25, 70, 130], // C5
  ],
  // Tool: quick sine blip pair — reads as a mechanical latch.
  tool: [
    [1046.5, 0, 40],   // C6
    [1318.5, 35, 55],  // E6
  ],
  // Approve: warm triangle major-third double — confirming, not celebratory.
  approve: [
    [523.25, 0, 100, "triangle"],  // C5
    [659.25, 80, 160, "triangle"], // E5
  ],
};

export function playSound(kind: SoundKind): void {
  const prefs = getFeedbackPrefs();
  if (!prefs.sound) return;
  const ac = ctx();
  if (!ac) return;
  const now = ac.currentTime + 0.01;
  // Soft kinds live under the flow; loud kinds punctuate it.
  const softness =
    kind === "tick" ? 0.35 :
    kind === "turn" || kind === "tool" ? 0.55 :
    kind === "approve" ? 0.75 :
    1;
  // Keep the master level deliberately quiet. Multiple notes overlap and a
  // raw 0..1 gain sounds harsh in WebView2 on headphones.
  const peak = 0.0001 + prefs.volume * softness * 0.065;
  const motif = MOTIFS[kind];
  motif.forEach(([freq, atMs, durMs, type], index) => {
    const spread = motif.length <= 1 ? 0 : (index / (motif.length - 1) - 0.5) * 0.22;
    tone(ac, freq, now + atMs / 1000, durMs, peak, type, spread);
  });
}

// --- Desktop notifications ---------------------------------------------- //

let permissionAsked = false;

export function ensureNotificationPermission(): void {
  if (permissionAsked) return;
  permissionAsked = true;
  try {
    if ("Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  } catch {
    /* not supported — silently skip */
  }
}

export function notify(title: string, body: string): void {
  const prefs = getFeedbackPrefs();
  if (!prefs.notifications) return;
  try {
    if (!("Notification" in window)) return;
    if (Notification.permission === "granted") {
      const n = new Notification(title, { body, silent: true });
      window.setTimeout(() => n.close(), 6000);
    } else if (Notification.permission === "default") {
      // Ask now; the next completion will fire once granted.
      void Notification.requestPermission();
    }
  } catch {
    /* ignore */
  }
}

// Convenience: the two things that happen together when work finishes.
export function celebrate(
  outcome: "success" | "error",
  title: string,
  body: string,
): void {
  playSound(outcome);
  notify(title, body);
}

// Personality settings store. Mirrors the appearance.ts pattern: a typed
// localStorage-backed store, validated on read, broadcast via an event so any
// component can react without prop drilling.
import type { PersonalitySettings, PersonalityTone } from "./types";

const KEY = "infinity-personality";

const TONES: PersonalityTone[] = ["warm", "sharp", "minimal", "energetic"];

const DEFAULTS: PersonalitySettings = {
  enabled: true,
  name: "Infinity",
  userName: "",
  tone: "warm",
  emoji: false,
};

function sanitize(raw: Partial<PersonalitySettings>): PersonalitySettings {
  const tone =
    raw.tone && TONES.includes(raw.tone) ? raw.tone : DEFAULTS.tone;
  return { ...DEFAULTS, ...raw, tone };
}

export function getPersonality(): PersonalitySettings {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<PersonalitySettings>;
    return sanitize(raw);
  } catch {
    return { ...DEFAULTS };
  }
}

export function setPersonality(
  patch: Partial<PersonalitySettings>,
): PersonalitySettings {
  const next = sanitize({ ...getPersonality(), ...patch });
  localStorage.setItem(KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent("infinity:personality", { detail: next }));
  return next;
}
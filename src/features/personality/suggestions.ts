// Actionable suggestion chips for the chat empty state. Tone-aware: sharper
// tones push efficiency prompts, warmer tones invite guided walkthroughs.
import type { PersonalityTone } from "./types";

const BASE = [
  "Explain this error",
  "Refactor a function",
  "Draft a plan",
  "Summarize this",
];

const EXTRA: Record<PersonalityTone, string[]> = {
  warm: ["Walk me through this code"],
  sharp: ["Find the bottleneck", "Optimize this"],
  minimal: ["Tighten this up"],
  energetic: ["Ship it faster", "Break this down"],
};

/** Base chips always shown, plus tone-specific ones, capped for visual balance. */
export function getSuggestions(tone: PersonalityTone): string[] {
  const extras = EXTRA[tone] ?? [];
  return [...BASE, ...extras].slice(0, 6);
}
// Pure greeting builders. No DOM, no localStorage — fully unit-testable.
// The clock is injected so tests can pin a specific daypart.
import type { Daypart, PersonalitySettings, PersonalityTone } from "./types";

/** Map an hour-of-day (0-23) to a Daypart. */
export function getDaypart(hour: number): Daypart {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 18) return "afternoon";
  if (hour >= 18 && hour < 22) return "evening";
  return "night";
}

const OPENERS: Record<Daypart, Record<PersonalityTone, string>> = {
  morning: {
    warm: "Good morning",
    sharp: "Morning",
    minimal: "Morning",
    energetic: "Good morning!",
  },
  afternoon: {
    warm: "Good afternoon",
    sharp: "Afternoon",
    minimal: "Afternoon",
    energetic: "Good afternoon!",
  },
  evening: {
    warm: "Good evening",
    sharp: "Evening",
    minimal: "Evening",
    energetic: "Good evening!",
  },
  night: {
    warm: "Evening",
    sharp: "Late hours",
    minimal: "Hi",
    energetic: "Night-owl shift",
  },
};

function salute(daypart: Daypart, tone: PersonalityTone): string {
  return OPENERS[daypart][tone] ?? OPENERS[daypart].warm;
}

/** Include the user's name as a plain-text callout when one is set. */
function who(settings: PersonalitySettings): string {
  return settings && settings.userName ? `, ${settings.userName}` : "";
}

/** Chat empty-state greeting, e.g. "Good morning, Caleb — what can I help you with?" */
export function buildGreeting(
  daypart: Daypart,
  settings: PersonalitySettings,
): string {
  const head = `${salute(daypart, settings.tone)}${who(settings)}`;
  const emoji = "";
  return `${head}${emoji} — what can I help you with?`;
}

/** Mission-composer greeting, e.g. "Good morning — what should we build?" */
export function buildMissionGreeting(
  daypart: Daypart,
  settings: PersonalitySettings,
): string {
  const head = `${salute(daypart, settings.tone)}${who(settings)}`;
  const emoji = "";
  return `${head}${emoji} — what should we build?`;
}
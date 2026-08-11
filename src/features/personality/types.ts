// Personality feature types. The personality system gives Infinity Code a
// stable identity (name, greeting tone, optional user callout) that survives
// reloads and is shared across every surface that greets the user (chat hero,
// mission composer). It is intentionally pure/ui-only — no model routing.

export type Daypart = "morning" | "afternoon" | "evening" | "night";

/** Voice of the empty-state greeting + suggestion chips. */
export type PersonalityTone = "warm" | "sharp" | "minimal" | "energetic";

export interface PersonalitySettings {
  /** Master switch — off restores the original static greeting. */
  enabled: boolean;
  /** Display name of the assistant (used in the composer placeholder). */
  name: string;
  /** Optional user first name; rendered as text, never as HTML. "" = none. */
  userName: string;
  /** Which voice the greeting + suggestions use. */
  tone: PersonalityTone;
  /** Emoji in the empty-state greeting (off by default). */
  emoji: boolean;
}
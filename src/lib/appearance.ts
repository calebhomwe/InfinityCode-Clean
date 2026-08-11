// Central appearance store: theme, background tone, contrast, font size, and
// accent. Persisted to localStorage (per-device), applied to :root as data-*
// attributes + CSS vars, and broadcast via an "infinity:appearance" event so
// any component (App, Settings) can react. Consumed by src/index.css.

export type ThemeMode = "dark" | "light" | "system";
export type Tone = "charcoal" | "slate" | "black";
export type Contrast = "normal" | "high" | "soft";
export type FontSize = "xs" | "s" | "m" | "l" | "xl";
export type FontFamily =
  | "geist"
  | "inter"
  | "space"
  | "jetbrains"
  | "sfpro"
  | "roboto"
  | "nunito"
  | "fira"
  | "source"
  | "ibmplex"
  | "ibmplexmono"
  | "dmsans";
export type Preset =
  | "default"
  | "kimi"
  | "qwen"
  | "chatgpt"
  | "claude"
  | "vscode"
  | "neon"
  | "paper"
  | "oled"
  | "pastel";
/** Top-level shell layout. "chat" is the current single-column chat feel;
 *  "assistant" is the assistant-forward view; "vscode" is dense editor chrome
 *  (thin sidebar, tab-like rows, status bar). Stamped on :root as data-layout. */
export type Layout = "chat" | "assistant" | "vscode";

export interface Appearance {
  theme: ThemeMode;
  tone: Tone;
  contrast: Contrast;
  fontSize: FontSize;
  fontFamily: FontFamily;
  accent: string; // key into ACCENTS
  preset: Preset; // whole-app style pack; overrides tokens on :root
  layout: Layout; // top-level shell shape
  /** Per-section font scales — independently tunable from the global base.
   *  Each value multiplies the 16px root so html { font-size: calc(16px * uiScale); }
   *  Chat / code / sidebar then multiply their own sections relative to that base. */
  uiScale: FontSize;
  chatScale: FontSize;
  codeScale: FontSize;
  sidebarScale: FontSize;
}

export const LAYOUTS: { id: Layout; label: string; hint: string }[] = [
  { id: "chat",      label: "Chat",       hint: "Current single-column chat feel" },
  { id: "assistant", label: "Assistant",  hint: "Executive-assistant forward view" },
  { id: "vscode",    label: "VS Code",    hint: "Dense editor chrome, thin sidebar, status bar" },
];

/** Preset packs — visible labels + a one-liner shown in Settings. The colors
 *  for the original six live in src/index.css under [data-preset="…"] blocks;
 *  the runtime packs below ship their tokens from this file instead. */
export const PRESETS: { id: Preset; label: string; hint: string }[] = [
  { id: "kimi",    label: "Kimi",    hint: "Near-monochrome, cool blue accent" },
  { id: "qwen",    label: "Qwen",    hint: "Indigo-violet, cool slate" },
  { id: "chatgpt", label: "ChatGPT", hint: "Near-black + spearmint green" },
  { id: "claude",  label: "Claude",  hint: "Warm paper + soft coral" },
  { id: "vscode",  label: "VS Code", hint: "Dark+ palette, cyan-blue" },
  { id: "neon",    label: "Neon",    hint: "Cyberpunk night, high-chroma cyan" },
  { id: "paper",   label: "Paper",   hint: "Warm cream pages, sepia ink, ochre" },
  { id: "oled",    label: "OLED",    hint: "Pure #000 black, ice-blue accent" },
  { id: "pastel",  label: "Pastel",  hint: "Soft lavender-mint, gentle contrast" },
  { id: "default", label: "Default", hint: "Original Infinity tokens" },
];

/** Curated from professional developer tools: each remains legible in dense UI. */
export const FONTS: Record<FontFamily, { label: string; stack: string }> = {
  geist: {
    label: "Geist",
    stack: '"Geist Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  },
  inter: {
    label: "Inter",
    stack: '"Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
  },
  space: {
    label: "Space Grotesk",
    stack: '"Space Grotesk", "Inter", ui-sans-serif, system-ui, sans-serif',
  },
  jetbrains: {
    label: "JetBrains Mono",
    stack: '"JetBrains Mono", ui-monospace, "Cascadia Code", Consolas, monospace',
  },
  sfpro: {
    label: "SF Pro",
    stack: '"SF Pro Display", "SF Pro Text", -apple-system, "Segoe UI", sans-serif',
  },
  roboto: {
    label: "Roboto",
    stack: '"Roboto", "Helvetica Neue", Arial, sans-serif',
  },
  nunito: {
    label: "Nunito",
    stack: '"Nunito", ui-sans-serif, system-ui, sans-serif',
  },
  fira: {
    label: "Fira Code",
    stack: '"Fira Code", ui-monospace, "Cascadia Code", Consolas, monospace',
  },
  source: {
    label: "Source Sans 3",
    stack: '"Source Sans 3", "Source Sans Pro", ui-sans-serif, sans-serif',
  },
  ibmplex: {
    label: "IBM Plex Sans",
    stack: '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
  },
  ibmplexmono: {
    label: "IBM Plex Mono",
    stack: '"IBM Plex Mono", ui-monospace, "Cascadia Code", Consolas, monospace',
  },
  dmsans: {
    label: "DM Sans",
    stack: '"DM Sans", ui-sans-serif, system-ui, sans-serif',
  },
};

export const ACCENTS: Record<
  string,
  { label: string; hex: string; hover: string; rgb: string; hoverRgb: string }
> = {
  kimi: { label: "Kimi", hex: "#4a9de8", hover: "#63aeef", rgb: "74 157 232", hoverRgb: "99 174 239" },
  amber: { label: "Amber", hex: "#f0b347", hover: "#f7bd55", rgb: "240 179 71", hoverRgb: "247 189 85" },
  blue: { label: "Blue", hex: "#5b9dff", hover: "#74acff", rgb: "91 157 255", hoverRgb: "116 172 255" },
  green: { label: "Green", hex: "#4ec98a", hover: "#63d59b", rgb: "78 201 138", hoverRgb: "99 213 155" },
  violet: { label: "Violet", hex: "#a78bfa", hover: "#b6a0fb", rgb: "167 139 250", hoverRgb: "182 160 251" },
  rose: { label: "Rose", hex: "#f2789b", hover: "#f58cab", rgb: "242 120 155", hoverRgb: "245 140 171" },
};

const FONT_SCALE: Record<FontSize, string> = {
  xs: "0.88",
  s: "0.94",
  m: "1",
  l: "1.08",
  xl: "1.16",
};
const KEY = "infinity-appearance";

const DEFAULTS: Appearance = {
  theme: "dark",
  tone: "charcoal",
  contrast: "normal",
  fontSize: "m",
  fontFamily: "inter",
  accent: "kimi",
  preset: "kimi",
  layout: "chat",
  uiScale: "m",
  chatScale: "m",
  codeScale: "s",
  sidebarScale: "xs",
};

export function getAppearance(): Appearance {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<Appearance>;
    // 0.1.60: the legacy default Geist renders heavy at 400 ("everything
    // looks bold"); migrate saved legacy values to the cleaner Inter.
    const fontFamily = raw.fontFamily && raw.fontFamily in FONTS && raw.fontFamily !== "geist"
      ? raw.fontFamily as FontFamily
      : DEFAULTS.fontFamily;
    return { ...DEFAULTS, ...raw, fontFamily };
  } catch {
    return { ...DEFAULTS };
  }
}

export function resolveTheme(theme: ThemeMode): "dark" | "light" {
  if (theme === "system") {
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  }
  return theme;
}

export function applyAppearance(a: Appearance = getAppearance()): void {
  const root = document.documentElement;
  const resolved = resolveTheme(a.theme);
  root.dataset.theme = resolved;
  // Tone + contrast presets only apply to dark; clear them in light so the
  // light palette stays clean.
  root.dataset.tone = resolved === "dark" ? a.tone : "";
  root.dataset.contrast = resolved === "dark" ? a.contrast : "";
  // Preset packs remake the whole app (bg/surface/text/accent/radius/font).
  // "default" clears the attribute so the base :root tokens win.
  root.dataset.preset = a.preset && a.preset !== "default" ? a.preset : "";
  root.dataset.layout = a.layout || "chat";
  // Global fallback scale (retained for backwards compat).
  root.style.setProperty("--font-scale", FONT_SCALE[a.fontSize] ?? "1");
  // Per-section font scales — each multiplies the 16px root independently.
  root.style.setProperty("--font-ui", FONT_SCALE[a.uiScale] ?? "1");
  root.style.setProperty("--font-chat", FONT_SCALE[a.chatScale] ?? "1");
  root.style.setProperty("--font-code", FONT_SCALE[a.codeScale] ?? "1");
  root.style.setProperty("--font-sidebar", FONT_SCALE[a.sidebarScale] ?? "1");
  const font = FONTS[a.fontFamily] ?? FONTS.geist;
  root.style.setProperty("--font-ui-family", font.stack);
  // Presets own their accent as part of the pack; only apply the standalone
  // ACCENTS palette when no preset is active, so per-preset accents stick.
  if (!a.preset || a.preset === "default") {
    const ac = ACCENTS[a.accent] ?? ACCENTS.kimi;
    root.style.setProperty("--accent", ac.hex);
    root.style.setProperty("--accent-hover", ac.hover);
    root.style.setProperty("--accent-rgb", ac.rgb);
    root.style.setProperty("--accent-hover-rgb", ac.hoverRgb);
  } else {
    // Let the [data-preset] CSS block own --accent-* — clear any inline
    // overrides from a prior "default" mount.
    root.style.removeProperty("--accent");
    root.style.removeProperty("--accent-hover");
    root.style.removeProperty("--accent-rgb");
    root.style.removeProperty("--accent-hover-rgb");
  }
}

export function setAppearance(patch: Partial<Appearance>): Appearance {
  const candidate = { ...getAppearance(), ...patch };
  const next: Appearance = {
    ...candidate,
    fontFamily: candidate.fontFamily in FONTS ? candidate.fontFamily : "inter",
  };
  localStorage.setItem(KEY, JSON.stringify(next));
  applyAppearance(next);
  window.dispatchEvent(new CustomEvent("infinity:appearance", { detail: next }));
  return next;
}

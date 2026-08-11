/** @type {import('tailwindcss').Config} */
export default {
  content: {
    // Resolve globs against this config file, not the process cwd, so the
    // dev server works no matter where it is launched from.
    relative: true,
    files: ["./index.html", "./src/**/*.{ts,tsx}"],
  },
  theme: {
    extend: {
      fontFamily: {
        // One typeface across the whole app — including code. `mono` is kept as
        // an alias so existing font-mono classes still use the universal face.
        sans: ["var(--font-ui)"],
        mono: ["var(--font-ui)"],
      },
      colors: {
        // Accent follows the user's choice via CSS vars (see appearance.ts +
        // index.css). rgb-var form keeps opacity modifiers (accent/40) working.
        accent: {
          DEFAULT: "rgb(var(--accent-rgb) / <alpha-value>)",
          hover: "rgb(var(--accent-hover-rgb) / <alpha-value>)",
        },
        // Semantic tokens backed by CSS vars (see index.css :root +
        // [data-theme="light"]). All support Tailwind alpha: bg-bd/[0.07].
        bg: "rgb(var(--c-bg) / <alpha-value>)",
        surface: {
          DEFAULT: "rgb(var(--c-surface) / <alpha-value>)",
          2: "rgb(var(--c-surface2) / <alpha-value>)",
        },
        tx: {
          DEFAULT: "rgb(var(--c-tx) / <alpha-value>)",
          dim: "rgb(var(--c-tx-dim) / <alpha-value>)",
          mut: "rgb(var(--c-tx-mut) / <alpha-value>)",
        },
        // Hairlines + hover overlays: white in dark mode, near-black in light.
        bd: "rgb(var(--c-bd) / <alpha-value>)",
        // Semantic status colors. Backed by --status-*-rgb triplets so alpha
        // modifiers (bg-error/[0.06], border-error/20) work. Presets can
        // override these to retint their status palette.
        error: "rgb(var(--status-error-rgb) / <alpha-value>)",
        success: "rgb(var(--status-success-rgb) / <alpha-value>)",
        warning: "rgb(var(--status-warning-rgb) / <alpha-value>)",
        info: "rgb(var(--status-info-rgb) / <alpha-value>)",
      },
    },
  },
  plugins: [],
};

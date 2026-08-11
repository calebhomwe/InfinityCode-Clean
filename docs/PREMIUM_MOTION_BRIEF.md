# Premium Motion & Finish Brief — Infinity Code X

This is the standard the premium-polish swarm judges against. Reference feel:
**Qoder** (agent status pulses, diff-gutter glow, command-palette spring),
**Qwen Code** (streaming token shimmer, tool cards collapsing with spring
easing, calm status color system), **Kimi** (gradient auras, breathing idle
states, soft focus transitions). The app already has ascension auras + chimes;
this brief defines what "refined and premium" means for everything around them.

## Hard constraints (non-negotiable)

1. **60 fps or it doesn't ship.** Animate `transform`, `opacity`, `filter` only.
   Never animate `top/left/width/height/margin/box-shadow` directly (shadow
   fades go through a pseudo-element opacity).
2. **Easing vocabulary.** Entrances: `cubic-bezier(0.22, 1, 0.36, 1)`
   (easeOutQuint feel). Exits: `cubic-bezier(0.4, 0, 1, 1)` short. Emphasis:
   `cubic-bezier(0.34, 1.56, 0.64, 1)` used sparingly (max 1.06 overshoot).
   No `linear`, no default `ease` on anything user-visible.
3. **Durations.** Micro-interactions 120–200 ms, panel reveals 240–400 ms,
   ambient loops 4–9 s. Nothing snaps; nothing lingers past 500 ms on a click
   response.
4. **Stagger discipline.** Lists and card grids reveal with 40–60 ms steps,
   capped at ~360 ms total spread.
5. **`prefers-reduced-motion`** must disable all non-essential motion
   (existing auras already do; new work must too).
6. **No new dependencies.** CSS keyframes / transitions + existing React only.
7. **TypeScript strict stays green** (`tsc --noEmit`), `vite build` stays green.
8. **Design tokens only.** Colors/radii/shadows come from the CSS variables in
   `src/index.css`; no new hardcoded hex outside new token definitions.

## Anti-slop rules

- No bounce on everything; overshoot is for ONE hero moment per view, max.
- No rainbow gradients outside the ascension auras.
- No animation that delays content: text and controls appear instantly, motion
  decorates — it never gates.
- No pulsing/breathing on more than two elements per view at once.
- Hover states answer within 100 ms or they read as broken.

## Where premium shows up (priority order)

1. **First paint** — app shell entrance: sidebar, tab bar, and chat surface
   arrive as one choreographed stagger, ≤ 400 ms total.
2. **Micro-interactions** — buttons (press scale 0.97 + glow settle), sidebar
   items (indicator slide, not color swap), tabs (active pill glides between
   tabs), toggles/switches (spring thumb).
3. **Panel & modal transitions** — transform+opacity with the entrance curve;
   backdrop fade 200 ms; no `display:none` snaps (use visibility + transition).
4. **Streaming & status** — token shimmer while streaming; agent status dots
   with soft breathing; progress as a thin gradient sweep, never a thick bar.
5. **Council & Ascension surfaces** — model cards lift on hover (translateY
   -2px + token shadow), ACTIVE badge has a gentle glow pulse, dial segment
   changes flash once then settle; auras stay as-is (already good).
6. **Focus & keyboard** — focus rings fade in 120 ms; command-palette-style
   overlays scale from 0.98 with the entrance curve.

## Verification

Premium is proven by: (a) before/after screenshots judged by the vision oracle
rubric (motion, depth/glow, typography hierarchy, spacing rhythm,
micro-interaction affordance, cohesion — each 0–100), (b) `tsc`/`vite` gates,
(c) the constraint reviewer veto list. A change that raises the rubric total
but breaks a hard constraint is reverted regardless of score.

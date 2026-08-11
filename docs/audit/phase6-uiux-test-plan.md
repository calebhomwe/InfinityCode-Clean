# Phase 6 — UI/UX Test Plan
Target: v0.1.59 | DESIGN.md compliance verification

## Automated (now in CI)
- TypeScript typecheck: npx tsc --noEmit
- Unit tests: npm test (vitest, 8 tests baseline, grow per-feature)
- Build gate: npm run build (bundle budget: index.js < 500KB raw / 120KB gzip — current 389KB/113KB)

## Manual Exploratory Checklist
### Modes & Navigation
- [ ] Ctrl+Shift+B cycles chat -> assistant -> build -> ide cleanly, state preserved per mode
- [ ] Splash screen: video plays; fallback breathing logo triggers if autoplay blocked (>2.5s)
- [ ] Settings modal: all sections reachable, Esc closes, focus trapped while open
- [ ] Panels (lazy-loaded): open each once — no blank flash > 300ms

### Accessibility (DESIGN.md WCAG 2.2 AA targets)
- [ ] Contrast: amber #f0b347 on dark bg passes 4.5:1 for body text, 3:1 for large/UI
- [ ] Keyboard-only pass: Tab order logical, visible focus rings, no keyboard traps
- [ ] prefers-reduced-motion: splash animation + transitions respect OS setting
- [ ] Screen reader: chat messages announced, streaming updates not announced per-token

### Chat UX
- [ ] Streaming response renders progressively; stop button halts token stream
- [ ] Code blocks: copy button, syntax highlight, horizontal scroll (no page layout break)
- [ ] Empty state greeting matches personality settings (tone/name) — covered by unit tests
- [ ] Long conversation (200+ messages): scroll stays pinned to bottom unless user scrolls up

### Error & Edge States
- [ ] Backend down: UI shows reconnect banner, not white screen
- [ ] 401 (stale token): UI refetches /api/v1/auth/token once and retries
- [ ] LLM quota exhausted: failover message visible, not silent hang

## Cloud/Browser Automation (optional)
- Playwright E2E can run against dev build (tauri dev + vite 1420)
- axe-core scan for automated a11y violations

# UI_HANDOFF — Authoritative UI Implementation Ledger

> This file is the single ledger for UI decisions and surface moves in Infinity
> Code. Update it whenever a component moves, a visible surface is added or
> removed, or a design rule changes. Companion docs: `DESIGN.md` (design
> system + palette), `AUDIT_MAP.md`, `docs/RESEARCH_WAVE0.md` (UI inventory),
> `docs/IMPLEMENTATION_REPORT_2026-08-10.md` (overhaul evidence).

## Design rules in force

1. No emoji in UI text; no decorative unicode glyphs (`✓ ✕ ⌘ ⏰ →`). Close
   glyphs use the typographic `×`; icons are inline SVG.
2. Main view stays minimal: prompt + mode pill + model pill + send. Everything
   else lives behind `+`, Ctrl+K, or the Pro drawer.
3. Developer surfaces (Long Tasks, Knowledge, Health, Council, Vision Verify,
   Training, Beast Arena, Scheduled Tasks, Vault, Models, Credits, Providers)
   are NOT primary nav; they live in the Pro drawer and the command palette.
4. First launch asks zero questions: onboarding is skippable with one click,
   and one dismissible tips card (Ctrl+K hint) replaces any nagging.
5. Zero-deletion: dead components move to `src/_legacy/` (git history keeps
   them); never delete from git.

## Surface moves (2026-08-10 overhaul)

| Component / surface | From | To | Reason |
|---|---|---|---|
| VoiceDock.tsx | src/components | src/_legacy | DESIGN.md bans persistent voice dock; zero importers |
| AIChatHistory.tsx | src/components | src/_legacy | Dead (superseded by chat tabs); zero importers |
| ChatHistoryBrowser.tsx | src/components | src/_legacy | Dead; zero importers |
| PersonaPicker.tsx | src/components | src/_legacy | Superseded by AgentPicker; import fixed to ../components |
| Agent picker (composer) | opens 200+ persona modal | static "Auto team: X (roles)" chip | Dispatcher 2b; /agent command + palette remain as advanced path |
| TabBar "other kinds" tabs | visible no-op | open real overlays (settings/vault/scheduled) | Dead affordance fixed |
| Onboarding close | icon-only Close | explicit "Skip" text button | Zero-question first launch |
| Developer surfaces | command-palette-only | Pro drawer (sidebar "Pro" button) | Collected in one collapsible place |
| Tips card | none | one dismissible card (localStorage `infinity-tip-dismissed`) | First-launch guidance |
| Emoji/glyph fixes | 10 occurrences across 6 files | removed / SVG / `×` | Rule 1 |

## Backend surfaces

- `GET /api/v1/diagnostics?path=` — LSP-style diagnostics (ruff/tsc syntax).
- `POST /api/v1/chats/{id}/compact` — manual DB-trimming compaction; auto-
  compact also runs at ~75% of the model context window in the stream path.
- `file_path` on chat stream requests — attaches editor diagnostics to the
  system prompt.

## Verification

- `npm run build` (tsc && vite build) must stay green after UI changes.
- Browser checks: onboarding Skip, Auto team chip label, Pro drawer, tips
  card dismissal (evidence in the overhaul report + `evidence/`).

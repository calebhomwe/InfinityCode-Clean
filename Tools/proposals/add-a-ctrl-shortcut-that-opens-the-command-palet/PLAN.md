## Plan

**One file changes: `src/App.tsx`** — extend the existing Ctrl+K branch in the global `onKey` handler (inside the "Global keyboard shortcuts" `useEffect`).

### Change

In `src/App.tsx`, find:

```ts
if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
  e.preventDefault();
  setPaletteOpen((o) => !o);
}
```

Replace with:

```ts
if (
  (e.ctrlKey || e.metaKey) &&
  (e.key.toLowerCase() === "k" || e.key === "/")
) {
  e.preventDefault();
  setPaletteOpen((o) => !o);
}
```

Notes for the implementer:
- Use `e.key === "/"` (not `e.code`); matches the existing style of key checks in this handler. Do **not** lowercase-compare — `"/"` has no case.
- Keep the toggle semantics (`(o) => !o`) so Ctrl+/ is a true alias of Ctrl+K, per the feature spec ("alias").
- No new deps in the `useEffect` dependency array — the change is inside the existing handler.

### Secondary: shortcut hint text (verify, update only if present)

Grep for existing shortcut labels and update any user-visible hint to mention both bindings:

```sh
grep -rn "Ctrl+K\|⌘K\|Cmd+K\|ctrl-k" src/
```

Likely spots: `src/components/CommandPalette.tsx` (footer hints), `src/components/SettingsModal.tsx` (shortcuts tab, if any). Render as `Ctrl+K / Ctrl+/` following whatever kbd-hint convention the component already uses. If no hint exists, skip — don't add new UI.

### Risks

- **Editor conflict:** Ctrl+/ is "toggle comment" in many code editors. If any focused component (composer, code viewer) handles Ctrl+/ itself and calls `stopPropagation()`, the global handler won't fire there — same limitation Ctrl+K already has. Acceptable.
- **Layout edge case:** on layouts where `/` requires Shift (e.g., German: Shift+7), the shortcut fires as Ctrl+Shift+7 producing `e.key === "/"` — still works. US Ctrl+Shift+/ produces `"?"` and won't fire; out of scope.
- **Toggle while typing in palette:** pressing Ctrl+/ with the palette open closes it — identical to Ctrl+K today, so consistent.

No tests exist for this handler in the shown code; manual verification: Ctrl+/ opens, again closes, and doesn't hijack plain `/` typing in inputs.
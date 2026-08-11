FILE: src/App.tsx
```diff
--- a/src/App.tsx
+++ b/src/App.tsx
@@ -263,7 +263,10 @@ export default function App(): JSX.Element {
   useEffect(() => {
     const onKey = (e: KeyboardEvent): void => {
-      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
+      if (
+        (e.ctrlKey || e.metaKey) &&
+        (e.key.toLowerCase() === "k" || e.key === "/")
+      ) {
         e.preventDefault();
         setPaletteOpen((o) => !o);
       } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
```

No other source files were provided, so I can’t verify existing `Ctrl+K` hint labels in `CommandPalette.tsx` or `SettingsModal.tsx`. If any are present, update them to read `Ctrl+K / Ctrl+/` (or `⌘K / ⌘/` on macOS) using the same kbd styling; otherwise leave the UI unchanged as specified.
/**
 * Native-app feel for the web shell. With OS window decorations kept ON
 * (Tauri v1 snap-layouts stay intact), the browser-y tells to kill are:
 *   1. the default right-click context menu appearing everywhere, and
 *   2. text being selectable across all chrome (buttons, labels, sidebar).
 *
 * We suppress the browser context menu except where a real one helps
 * (text inputs + selected text in message content), and let CSS restrict
 * text selection to content regions (see index.css: user-select rules).
 */

const SELECTABLE = "input, textarea, [contenteditable=\"true\"]";
const CONTENT = ".chat-markdown, .selectable";

function isEditable(el: Element | null): boolean {
  return !!el && !!el.closest(SELECTABLE);
}

function inContent(el: Element | null): boolean {
  return !!el && !!el.closest(CONTENT);
}

export function initNativeFeel(): void {
  // Suppress the browser context menu on chrome; keep it on inputs and on
  // actual selected text inside message content (so "Copy" still works there).
  window.addEventListener(
    "contextmenu",
    (e) => {
      const target = e.target as Element | null;
      const hasSelection = (window.getSelection()?.toString() ?? "").length > 0;
      if (isEditable(target)) return; // native menu (paste/select) in fields
      if (inContent(target) && hasSelection) return; // native copy on selected text
      e.preventDefault();
    },
    { capture: true },
  );

  // Kill browser-only accelerators that make it feel like a web page:
  // Ctrl+R / F5 reload, Ctrl+ +/-/0 zoom, Ctrl+P print. Keep DevTools in dev.
  window.addEventListener(
    "keydown",
    (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === "r" || k === "p" || k === "=" || k === "-" || k === "+" || k === "0") {
        e.preventDefault();
      }
    },
    { capture: true },
  );

  // Prevent the whole window from being a drop target (files dropped outside
  // the composer would otherwise navigate the webview to the file).
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    const target = e.target as Element | null;
    if (!target || !target.closest(".composer, textarea")) e.preventDefault();
  });
}

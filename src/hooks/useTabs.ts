// Multi-tab shell state — persistent tab set for chats + missions + panels.
//
// This layer is deliberately additive: it stores an ordered list of open
// "things" (chats, missions, agent picker, vault, settings) plus the active
// index. The existing App.tsx state (activeChatId etc.) is derived from the
// active tab, so introducing tabs does not require ripping out the current
// modal/panel flow — modals can still overlay a tab, and closing all tabs
// falls back to the existing empty state.
//
// Persistence: the tab set is written to localStorage under a single key so
// it survives dev-server restarts. Missions/chats that no longer exist on
// the server become stale entries — the App layer prunes them on first paint.

import { useCallback, useEffect, useState } from "react";

export type TabKind =
  | "chat"
  | "mission"
  | "agent-lib"
  | "vault"
  | "settings"
  | "scheduled";

export interface Tab {
  id: string;      // stable local id (uuid or `<kind>:<refId>`)
  kind: TabKind;
  refId?: string;  // chat id, mission id, etc. Absent for singleton panels.
  title: string;   // shown on the tab
}

interface TabsState {
  tabs: Tab[];
  activeId: string | null;
}

const KEY = "infinity-tabs";
const KNOWN_KINDS: ReadonlySet<TabKind> = new Set<TabKind>([
  "chat",
  "mission",
  "agent-lib",
  "vault",
  "settings",
  "scheduled",
]);

function readInitial(): TabsState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { tabs: [], activeId: null };
    const parsed = JSON.parse(raw) as Partial<TabsState>;
    // Validate `kind` against the known set so a corrupted localStorage entry
    // (from an older schema) can't render as an unknown TabKind.
    const tabs = Array.isArray(parsed.tabs)
      ? parsed.tabs.filter(
          (t): t is Tab =>
            !!t &&
            typeof t === "object" &&
            typeof t.id === "string" &&
            typeof t.kind === "string" &&
            KNOWN_KINDS.has(t.kind as TabKind) &&
            typeof t.title === "string",
        )
      : [];
    const activeId = typeof parsed.activeId === "string" ? parsed.activeId : null;
    return { tabs, activeId: tabs.some((t) => t.id === activeId) ? activeId : (tabs[0]?.id ?? null) };
  } catch {
    return { tabs: [], activeId: null };
  }
}

function persist(state: TabsState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* private mode / quota: fall through — tabs still work in-memory */
  }
}

/** Deterministic id per (kind, refId) so re-opening a chat/mission focuses
 *  the existing tab instead of duplicating it. Singleton panels get a fixed id. */
export function tabIdFor(kind: TabKind, refId?: string): string {
  if (kind === "chat" || kind === "mission") {
    return `${kind}:${refId ?? "new"}`;
  }
  return kind; // singleton
}

export function useTabs(): {
  tabs: Tab[];
  activeId: string | null;
  activeTab: Tab | null;
  open: (tab: Tab) => void;
  close: (id: string) => void;
  /** Replace tab `id` in place with `next` (keeps position + active state). */
  promote: (id: string, next: Tab) => void;
  activate: (id: string) => void;
  rename: (id: string, title: string) => void;
  prune: (predicate: (tab: Tab) => boolean) => void;
  reset: () => void;
} {
  const [state, setState] = useState<TabsState>(() => readInitial());

  useEffect(() => {
    persist(state);
  }, [state]);

  const open = useCallback((tab: Tab): void => {
    setState((prev) => {
      let existing = prev.tabs.findIndex((t) => t.id === tab.id);
      // A chat/mission may already have a tab under a placeholder id
      // (chat:new:N) that was promoted to a real refId. Re-focus that tab
      // instead of opening a duplicate.
      if (
        existing < 0 &&
        (tab.kind === "chat" || tab.kind === "mission") &&
        tab.refId
      ) {
        existing = prev.tabs.findIndex(
          (t) => t.kind === tab.kind && t.refId === tab.refId,
        );
      }
      if (existing >= 0) {
        // Refocus the existing tab AND refresh its title in case the caller
        // now has better data (e.g. a chat placeholder that just loaded its
        // real title). No-op if the title is unchanged. Always activate by
        // the EXISTING tab's id — the caller's id may be the canonical one
        // (chat:<id>) while the tab lives under a promoted placeholder id.
        const current = prev.tabs[existing];
        if (current.title === tab.title) {
          return prev.activeId === current.id ? prev : { ...prev, activeId: current.id };
        }
        const nextTabs = prev.tabs.slice();
        nextTabs[existing] = { ...current, title: tab.title };
        return { tabs: nextTabs, activeId: current.id };
      }
      return { tabs: [...prev.tabs, tab], activeId: tab.id };
    });
  }, []);

  const close = useCallback((id: string): void => {
    setState((prev) => {
      const idx = prev.tabs.findIndex((t) => t.id === id);
      if (idx < 0) return prev;
      const next = prev.tabs.filter((t) => t.id !== id);
      let nextActive = prev.activeId;
      if (prev.activeId === id) {
        // Prefer the neighbour to the left, then to the right, else nothing.
        nextActive = next[idx - 1]?.id ?? next[idx]?.id ?? null;
      }
      return { tabs: next, activeId: nextActive };
    });
  }, []);

  const activate = useCallback((id: string): void => {
    setState((prev) => (prev.tabs.some((t) => t.id === id) ? { ...prev, activeId: id } : prev));
  }, []);

  const rename = useCallback((id: string, title: string): void => {
    setState((prev) => ({
      ...prev,
      tabs: prev.tabs.map((t) => (t.id === id ? { ...t, title } : t)),
    }));
  }, []);

  const prune = useCallback((predicate: (tab: Tab) => boolean): void => {
    setState((prev) => {
      const kept = prev.tabs.filter(predicate);
      if (kept.length === prev.tabs.length) return prev;
      const nextActive = kept.some((t) => t.id === prev.activeId)
        ? prev.activeId
        : (kept[0]?.id ?? null);
      return { tabs: kept, activeId: nextActive };
    });
  }, []);

  const reset = useCallback((): void => {
    setState({ tabs: [], activeId: null });
  }, []);

  const promote = useCallback((id: string, next: Tab): void => {
    setState((prev) => {
      const idx = prev.tabs.findIndex((t) => t.id === id);
      if (idx < 0) return prev;
      const tabs = prev.tabs.slice();
      tabs[idx] = { ...next, id: next.id };
      return { tabs, activeId: prev.activeId === id ? next.id : prev.activeId };
    });
  }, []);

  const activeTab: Tab | null =
    state.tabs.find((t) => t.id === state.activeId) ?? null;

  // Fresh reference each render is fine — consumers that need referential
  // stability (e.g. keyboard-shortcut effects) ref-mirror the value on their
  // side, and the individual methods are already useCallback-stable.
  return {
    tabs: state.tabs,
    activeId: state.activeId,
    activeTab,
    open,
    close,
    promote,
    activate,
    rename,
    prune,
    reset,
  };
}

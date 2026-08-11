// Sidebar visibility prefs — one boolean per toggleable item, persisted to
// localStorage (per-device) and broadcast via "infinity:sidebar-prefs" so App
// re-renders on change. Non-essential items only: the brand, mode toggle,
// chat list, and Settings button are always visible.

export type SidebarItem =
  | "scheduled"
  | "themeToggle"
  | "cost"
  | "pinned";

interface SidebarPrefs {
  visible: Record<SidebarItem, boolean>;
}

export const SIDEBAR_ITEMS: { id: SidebarItem; label: string; hint: string }[] = [
  { id: "pinned", label: "Pinned chats", hint: "Pinned section above Recents." },
  { id: "scheduled", label: "Scheduled Tasks", hint: "Cron-style scheduled routines." },
  { id: "themeToggle", label: "Theme toggle", hint: "Quick dark/light switch button." },
  { id: "cost", label: "Cost meter", hint: "Daily spend tracker at the bottom." },
];

const KEY = "infinity-sidebar-prefs";

// Kimi-style defaults: only the essentials show at rest. Everything else
// stays reachable via the Customize dialog (right-click sidebar) but is
// off until the user asks for it.
const DEFAULTS: SidebarPrefs = {
  visible: {
    pinned: true,
    scheduled: false,
    themeToggle: false,
    cost: false,
  },
};

export function getSidebarPrefs(): SidebarPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<SidebarPrefs>;
    return {
      visible: {
        ...DEFAULTS.visible,
        ...(raw.visible ?? {}),
      },
    };
  } catch {
    return { visible: { ...DEFAULTS.visible } };
  }
}

export function setSidebarPrefs(patch: Partial<Record<SidebarItem, boolean>>): SidebarPrefs {
  const current = getSidebarPrefs();
  const next: SidebarPrefs = {
    visible: { ...current.visible, ...patch },
  };
  localStorage.setItem(KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent("infinity:sidebar-prefs"));
  return next;
}

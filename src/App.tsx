import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import ChatView from "./components/ChatView";
import { buildMissionGreeting, getDaypart, usePersonality } from "./features/personality";
import OnboardingModal, { isOnboarded } from "./features/onboarding";
import CommandPalette from "./components/CommandPalette";
import CostTracker from "./components/CostTracker";
import CreditMeter from "./components/CreditMeter";
import WorkspacePicker from "./components/WorkspacePicker";
import CustomizeSidebar from "./components/CustomizeSidebar";
import TabBar from "./components/TabBar";
import ContextRail from "./components/ContextRail";
import EvidencePanel from "./components/EvidencePanel";
import InfinityMark from "./components/InfinityMark";
import MissionCard from "./components/MissionCard";
import MissionBrief from "./components/MissionBrief";
import OneBox from "./components/OneBox";
import SettingsModal from "./components/SettingsModal";
import type { SettingsTab } from "./components/SettingsModal";
import SplashScreen from "./components/SplashScreen";
import SwarmPanel from "./components/SwarmPanel";
import TurboActivity from "./components/TurboActivity";
import WindowTitleBar from "./components/WindowTitleBar";
import WindowResizeHandles from "./components/WindowResizeHandles";
import ModelsView from "./components/ModelsView";
import ReplMode from "./components/ReplMode";
// Overlays that are never needed at first paint — split into async chunks.
const DesignPanel = lazy(() => import("./components/DesignPanel"));
const VaultBrowser = lazy(() => import("./components/VaultBrowser"));
const ScheduledTasks = lazy(() => import("./components/ScheduledTasks"));
const LongTaskPanel = lazy(() => import("./components/LongTaskPanel"));
const BeastPanel = lazy(() => import("./components/BeastPanel"));
const KnowledgeView = lazy(() => import("./components/KnowledgeView"));
const AIChatHistory = lazy(() => import("./components/AIChatHistory"));
const HealthReportModal = lazy(() => import("./components/HealthReportModal"));
const VisionPanel = lazy(() => import("./components/VisionPanel"));
const TrainingPanel = lazy(() => import("./components/TrainingPanel"));
const BrowserView = lazy(() => import("./components/BrowserView"));
const SwarmCouncilPanel = lazy(() => import("./components/SwarmCouncilPanel"));
const CreditsView = lazy(() => import("./components/CreditsView"));
import { deleteChat, patchChat, useChats } from "./hooks/useChats";
import { useCost } from "./hooks/useCost";
import { useMissions } from "./hooks/useMissions";
import { useWebSocket } from "./hooks/useWebSocket";
import { DEFAULT_TOOLS, TOOLS } from "./lib/composer";
import type { ComposerSubmission, Effort, Mode, ToolId } from "./lib/composer";
import type { Artifact } from "./lib/artifact";
import { applyAppearance, getAppearance, resolveTheme, setAppearance } from "./lib/appearance";
import { getSidebarPrefs } from "./lib/sidebar";
import { useTabs, tabIdFor } from "./hooks/useTabs";
import { Toaster } from "sonner";
import { celebrate, ensureNotificationPermission, playSound } from "./lib/feedback";
import {
  apiFetch,
  API_BASE,
  fetchProvidersInfo,
  hasConfiguredLlmProvider,
} from "./lib/api";
import type { ProvidersInfo } from "./lib/api";
import { missionModeLabel, missionStatusLabel } from "./lib/missionStatus";

type View = "chat" | "assistant" | "build" | "ide" | "browser";

interface ComposerDefaults {
  mode: Mode;
  effort: Effort;
  fast: boolean;
  vision_loop: boolean;
  speculative: boolean;
}

export default function App(): JSX.Element {
  const [backendReady, setBackendReady] = useState<boolean>(false);
  const { missions, loading, error, refetch } = useMissions();
  const { cost } = useCost();
  const [activeMissionId, setActiveMissionId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [vaultOpen, setVaultOpen] = useState<boolean>(false);
  const [schedOpen, setSchedOpen] = useState<boolean>(false);
  const [longtaskOpen, setLongtaskOpen] = useState<boolean>(false);
  const [beastOpen, setBeastOpen] = useState<boolean>(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState<boolean>(false);
const [chatHistoryOpen, setChatHistoryOpen] = useState<boolean>(false);
  const [healthOpen, setHealthOpen] = useState<boolean>(false);
  const [visionOpen, setVisionOpen] = useState<boolean>(false);
  const [trainingOpen, setTrainingOpen] = useState<boolean>(false);
  const [councilOpen, setCouncilOpen] = useState<boolean>(false);
  const [listCollapsed, setListCollapsed] = useState<boolean>(false);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");
  // First-run keyless UX: provider status drives the onboarding banner and the
  // composer hint. null = not fetched yet (no banner, no composer block).
  const [providersInfo, setProvidersInfo] = useState<ProvidersInfo | null>(null);
  const [providerBannerDismissed, setProviderBannerDismissed] = useState<boolean>(
    () => localStorage.getItem("infinity-provider-banner-dismissed") === "1",
  );
  const [defaults, setDefaults] = useState<ComposerDefaults | null>(null);
  const [turbo, setTurbo] = useState<boolean>(false);
  const [view, setView] = useState<View>("chat");
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [designArtifact, setDesignArtifact] = useState<Artifact | null>(null);
  const [suggestions, setSuggestions] = useState<
    { title: string; goal: string; mode: Mode; reason: string }[]
  >([]);
  const { chats, refetch: refetchChats } = useChats();
  const live = useWebSocket(activeMissionId);
  const [paletteOpen, setPaletteOpen] = useState<boolean>(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState<string>("");
  const [theme, setThemeState] = useState<"dark" | "light">(() =>
    resolveTheme(getAppearance().theme),
  );
  const [customizeOpen, setCustomizeOpen] = useState<boolean>(false);
const [proOpen, setProOpen] = useState<boolean>(false);
// One dismissible tips card after first launch (Ctrl+K hint).
const [tipDismissed, setTipDismissed] = useState<boolean>(
  () => localStorage.getItem("infinity-tip-dismissed") === "1",
);
  const [modelsOpen, setModelsOpen] = useState<boolean>(false);
const [creditsOpen, setCreditsOpen] = useState<boolean>(false);
  const [newMenuOpen, setNewMenuOpen] = useState<boolean>(false);
  const [sidebarPrefs, setSidebarPrefs] = useState(() => getSidebarPrefs());
  // Live-update the sidebar when the Customize dialog toggles a checkbox.
  useEffect(() => {
    const onPrefs = (): void => setSidebarPrefs(getSidebarPrefs());
    window.addEventListener("infinity:sidebar-prefs", onPrefs);
    return () => window.removeEventListener("infinity:sidebar-prefs", onPrefs);
  }, []);
  const showItem = sidebarPrefs.visible;

  // Multi-tab shell. Tabs mirror activeChatId/activeMissionId so introducing
  // tabs is additive — the existing single-selection flow keeps working. When
  // a chat/mission is opened by any path (sidebar click, palette, deep link),
  // a tab is created or refocused; when a tab is activated, the corresponding
  // active-* state is set.
  const tabsApi = useTabs();
  // Global keyboard shortcuts must see the LATEST tab state without re-registering
  // the listener on every render. Ref-mirror the values we read from inside onKey.
  const tabsApiRef = useRef(tabsApi);
  useEffect(() => {
    tabsApiRef.current = tabsApi;
  }, [tabsApi]);
  // Every "New chat" needs its own placeholder id: a shared id would make a
  // second click just refocus the first (stale) empty tab.
  const newChatSeq = useRef(0);
  const openChatTab = useCallback(
    (chatId: string | null, title = "New chat"): void => {
      const id =
        chatId === null
          ? `chat:new:${++newChatSeq.current}`
          : tabIdFor("chat", chatId);
      tabsApi.open({ id, kind: "chat", refId: chatId ?? undefined, title });
    },
    [tabsApi],
  );

  // Apply all appearance prefs (theme/tone/contrast/font/accent) on mount and
  // keep the local theme flag in sync with Settings-driven changes + the OS
  // theme when "system" is selected.
  useEffect(() => {
    applyAppearance();
    const sync = (): void => setThemeState(resolveTheme(getAppearance().theme));
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onOS = (): void => {
      applyAppearance();
      sync();
    };
    window.addEventListener("infinity:appearance", sync);
    mq.addEventListener("change", onOS);
    return () => {
      window.removeEventListener("infinity:appearance", sync);
      mq.removeEventListener("change", onOS);
    };
  }, []);

  // Sound + desktop notification when a mission reaches a terminal state.
  // We compare each poll's statuses against the previous snapshot so we fire
  // exactly once on the transition — never on initial load or re-renders.
  const prevStatusRef = useRef<Map<string, string>>(new Map());
  const feedbackPrimedRef = useRef<boolean>(false);
  useEffect(() => {
    const prev = prevStatusRef.current;
    const TERMINAL_OK = new Set(["completed", "approved"]);
    const TERMINAL_BAD = new Set(["failed", "rejected"]);
    // First pass just seeds the map (avoids a chime for every existing mission
    // on startup); subsequent passes detect real transitions.
    if (!feedbackPrimedRef.current) {
      for (const m of missions) prev.set(m.id, m.status);
      feedbackPrimedRef.current = true;
      return;
    }
    for (const m of missions) {
      const before = prev.get(m.id);
      if (before && before !== m.status) {
        if (TERMINAL_OK.has(m.status) && !TERMINAL_OK.has(before)) {
          celebrate("success", "Mission complete", `${m.title} finished successfully.`);
        } else if (TERMINAL_BAD.has(m.status) && !TERMINAL_BAD.has(before)) {
          celebrate("error", "Mission failed", `${m.title} needs your attention.`);
        }
      }
      prev.set(m.id, m.status);
    }
  }, [missions]);

  const toggleTheme = useCallback((): void => {
    const next = theme === "dark" ? "light" : "dark";
    setAppearance({ theme: next });
    setThemeState(next);
  }, [theme]);

  const openSettings = useCallback((tab: SettingsTab = "general"): void => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  }, []);

  // Resizable sidebar width (persisted). widthRef mirrors state so the mouseup
  // handler always saves the latest value (listeners attach once).
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => {
    const saved = Number(localStorage.getItem("infinity-sidebar-w"));
    return saved >= 200 && saved <= 420 ? saved : 264;
  });
  const draggingRef = useRef<boolean>(false);
  const widthRef = useRef<number>(sidebarWidth);
  useEffect(() => {
    const onMove = (e: MouseEvent): void => {
      if (!draggingRef.current) return;
      const w = Math.min(420, Math.max(200, e.clientX));
      widthRef.current = w;
      setSidebarWidth(w);
    };
    const onUp = (): void => {
      if (draggingRef.current) {
        draggingRef.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        localStorage.setItem("infinity-sidebar-w", String(widthRef.current));
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // Multiple ChatView instances stay mounted (one per tab), so focus the
  // composer that is actually visible rather than the first in the DOM.
  const focusVisibleComposer = useCallback((): void => {
    window.setTimeout(() => {
      const els = Array.from(
        document.querySelectorAll<HTMLElement>('[id^="chat-input-"], #chat-input'),
      );
      els.find((el) => el.offsetParent !== null)?.focus();
    }, 0);
  }, []);

  const startNewChat = useCallback((): void => {
    setView("chat");
    setActiveChatId(null);
    openChatTab(null, "New chat");
    focusVisibleComposer();
  }, [openChatTab, focusVisibleComposer]);

  // When the sidebar (or palette) sets activeChatId, ensure a tab exists and
  // reflects it. Use the chat's title if we have it.
  useEffect(() => {
    if (!activeChatId) return;
    const chat = chats.find((c) => c.id === activeChatId);
    openChatTab(activeChatId, chat?.title || "Chat");
  }, [activeChatId, chats, openChatTab]);

  // When the user clicks a tab, mirror it back into activeChatId etc.
  useEffect(() => {
    const t = tabsApi.activeTab;
    if (!t) return;
    if (t.kind === "chat") {
      setView("chat");
      if (t.refId && t.refId !== activeChatId) setActiveChatId(t.refId);
      if (!t.refId && activeChatId !== null) setActiveChatId(null);
    }
    // Non-chat tab kinds open their overlay on activation so the tab is a
    // real affordance, not a visible no-op.
    if (t.kind === "settings") setSettingsOpen(true);
    else if (t.kind === "vault") setVaultOpen(true);
    else if (t.kind === "scheduled") setSchedOpen(true);
  }, [tabsApi.activeTab, activeChatId]);

  // Prune tabs whose backing chat/mission no longer exists on the server.
  // Depends on `chats` only — tabsApi is read via ref so this doesn't re-run
  // on every parent render just because the hook return is a fresh object.
  useEffect(() => {
    const chatIds = new Set(chats.map((c) => c.id));
    tabsApiRef.current.prune(
      (t) =>
        t.kind !== "chat" ||
        // Placeholders stay, except the legacy shared `chat:new` id from
        // builds before unique placeholder ids — it never backs a real chat.
        (t.refId ? chatIds.has(t.refId) : t.id !== "chat:new"),
    );
  }, [chats]);

  // Global keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        startNewChat();
      } else if (
        (e.ctrlKey || e.metaKey) &&
        e.shiftKey &&
        e.key.toLowerCase() === "l"
      ) {
        e.preventDefault();
        toggleTheme();
      } else if (
        (e.ctrlKey || e.metaKey) &&
        e.shiftKey &&
        e.key.toLowerCase() === "b"
      ) {
        // Cycle all five modes (chat -> assistant -> build -> ide -> browser).
        e.preventDefault();
        setView((v) =>
          v === "chat" ? "assistant" : v === "assistant" ? "build" : v === "build" ? "ide" : v === "ide" ? "browser" : "chat"
        );
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "t") {
        // New chat tab. Cmd/Ctrl+T is universal for "new tab".
        e.preventDefault();
        startNewChat();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "w") {
        // Close the active tab. Cmd/Ctrl+W is universal for "close tab".
        e.preventDefault();
        const api = tabsApiRef.current;
        if (api.activeId) api.close(api.activeId);
      } else if (e.ctrlKey && e.key === "Tab") {
        // Ctrl+Tab: cycle forward. Ctrl+Shift+Tab: cycle backward.
        const api = tabsApiRef.current;
        if (api.tabs.length > 1) {
          e.preventDefault();
          const idx = api.tabs.findIndex((t) => t.id === api.activeId);
          const step = e.shiftKey ? -1 : 1;
          const next = api.tabs[(idx + step + api.tabs.length) % api.tabs.length];
          if (next) api.activate(next.id);
        }
      } else if (
        (e.ctrlKey || e.metaKey) &&
        !e.shiftKey &&
        /^[1-9]$/.test(e.key)
      ) {
        // Cmd/Ctrl+1..9: jump to the Nth tab.
        const api = tabsApiRef.current;
        const n = Number(e.key) - 1;
        const target = api.tabs[n];
        if (target) {
          e.preventDefault();
          api.activate(target.id);
        }
      } else if (e.key === "Escape") {
        // Escape closes the topmost overlay — every modal, not just some.
        // (CommandPalette and AgentPicker handle their own before this fires.)
        if (designArtifact) setDesignArtifact(null);
        else if (vaultOpen) setVaultOpen(false);
        else if (settingsOpen) setSettingsOpen(false);
        else if (schedOpen) setSchedOpen(false);
        else if (longtaskOpen) setLongtaskOpen(false);
        else if (beastOpen) setBeastOpen(false);
else if (knowledgeOpen) setKnowledgeOpen(false);
else if (chatHistoryOpen) setChatHistoryOpen(false);
else if (healthOpen) setHealthOpen(false);
        else if (knowledgeOpen) setKnowledgeOpen(false);
        else if (healthOpen) setHealthOpen(false);
        else if (visionOpen) setVisionOpen(false);
        else if (trainingOpen) setTrainingOpen(false);
        else if (councilOpen) setCouncilOpen(false);
        else if (modelsOpen) setModelsOpen(false);
else if (creditsOpen) setCreditsOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [startNewChat, toggleTheme, designArtifact, vaultOpen, settingsOpen, schedOpen, longtaskOpen, beastOpen, knowledgeOpen, healthOpen, visionOpen, trainingOpen, councilOpen, modelsOpen]);

  // Slash-command "/skill" (and future callers) opens the Skill Vault.
  useEffect(() => {
    const open = (): void => setVaultOpen(true);
    window.addEventListener("infinity:open-vault", open);
    return () => window.removeEventListener("infinity:open-vault", open);
  }, []);

  // "Send to agent" bridge: BrowserView extracts the page text and dispatches
  // infinity:compose. Fill the VISIBLE composer (native setter + input event
  // so React state picks it up) and focus it — the user reviews before
  // sending, nothing is auto-submitted.
  useEffect(() => {
    const onCompose = (e: Event): void => {
      const detail = (e as CustomEvent<{ text?: unknown }>).detail;
      if (typeof detail?.text !== "string" || !detail.text) return;
      setView("chat");
      window.setTimeout(() => {
        const els = Array.from(
          document.querySelectorAll<HTMLTextAreaElement>('[id^="chat-input-"], #chat-input'),
        );
        const el = els.find((node) => node.offsetParent !== null);
        if (!el) return;
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype,
          "value",
        )?.set;
        setter?.call(el, detail.text);
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.focus();
      }, 0);
    };
    window.addEventListener("infinity:compose", onCompose);
    return () => window.removeEventListener("infinity:compose", onCompose);
  }, []);

  // BrowserView status chips deep-link into Settings -> MCP servers.
  useEffect(() => {
    const onOpenSettings = (e: Event): void => {
      const detail = (e as CustomEvent<{ tab?: unknown }>).detail;
      openSettings((detail?.tab as SettingsTab) ?? "general");
    };
    window.addEventListener("infinity:open-settings", onOpenSettings);
    return () => window.removeEventListener("infinity:open-settings", onOpenSettings);
  }, [openSettings]);

  // Turbo burst: the composer fires "infinity:turbo" when Fast is enabled.
  // Flash the logo (body class) and show a toast for a moment.
  useEffect(() => {
    const onTurbo = (): void => {
      document.body.classList.add("turbo-burst");
      window.setTimeout(() => document.body.classList.remove("turbo-burst"), 900);
      setTurbo(true);
      window.setTimeout(() => setTurbo(false), 2600);
    };
    window.addEventListener("infinity:turbo", onTurbo);
    return () => window.removeEventListener("infinity:turbo", onTurbo);
  }, []);

  // Load saved composer defaults so the settings actually pre-select the box.
  // Re-fetched when the settings modal closes, so changes take effect at once.
  useEffect(() => {
    if (settingsOpen) {
      return;
    }
    let mounted = true;
    (async () => {
      try {
        const response = await apiFetch(`${API_BASE}/settings`);
        if (!response.ok) {
          return;
        }
        const data = (await response.json()) as {
          default_mode: Mode;
          default_effort: Effort;
          default_fast: boolean;
          default_vision_loop?: boolean;
          default_speculative?: boolean;
        };
        if (mounted) {
          setDefaults({
            mode: data.default_mode,
            effort: data.default_effort,
            fast: data.default_fast,
            vision_loop: data.default_vision_loop ?? false,
            speculative: data.default_speculative ?? false,
          });
        }
      } catch {
        // Composer falls back to its own defaults if settings are unreachable.
      }
    })();
    return () => {
      mounted = false;
    };
  }, [settingsOpen]);

  // Learn whether any LLM provider key exists once the backend is up. The
  // banner + composer hint only speak when we KNOW there are no keys - a
  // failed probe (backend 503, still booting) stays silent.
  useEffect(() => {
    if (!backendReady) {
      return;
    }
    let mounted = true;
    void fetchProvidersInfo().then((info) => {
      if (mounted) setProvidersInfo(info);
    });
    // The chat list hook fetches once on mount, which can race the
    // backend boot; re-fetch now that readiness is confirmed so a
    // returning user never sees a permanently empty sidebar.
    void refetchChats();
    return () => {
      mounted = false;
    };
  }, [backendReady]);

  // When Settings closes, re-probe provider state - the user may have just
  // saved a key, and the banner/composer must react immediately.
  const settingsWasOpenRef = useRef<boolean>(false);
  useEffect(() => {
    if (settingsOpen) {
      settingsWasOpenRef.current = true;
      return;
    }
    if (!settingsWasOpenRef.current) {
      return;
    }
    settingsWasOpenRef.current = false;
    let mounted = true;
    void fetchProvidersInfo().then((info) => {
      if (mounted) setProvidersInfo(info);
    });
    return () => {
      mounted = false;
    };
  }, [settingsOpen]);

  const selectedMission =
    missions.find((mission) => mission.id === activeMissionId) ?? null;

  const hour = new Date().getHours();
  const { settings: persona } = usePersonality();
  const [onboardingOpen, setOnboardingOpen] = useState<boolean>(
    () => !isOnboarded(),
  );

  const createMission = useCallback(
    async (submission: ComposerSubmission): Promise<void> => {
      setCreateError(null);
      try {
        const response = await apiFetch(`${API_BASE}/missions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: submission.title,
            goal: submission.goal,
            reference_image_path:
              submission.referenceImagePath === ""
                ? null
                : submission.referenceImagePath,
            end_reference_image_path:
              submission.endReferenceImagePath === ""
                ? null
                : submission.endReferenceImagePath ?? null,
            mode: submission.mode,
            effort: submission.effort,
            fast: submission.fast,
            vision_loop: submission.vision_loop,
            speculative: submission.speculative,
            attachments: submission.attachments.map((a) => a.path),
            tools: submission.tools,
            agents: submission.agents,
            combine_with_default_swarm: submission.combine_with_default_swarm,
          }),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status} creating mission`);
        }
        const mission = (await response.json()) as { id: string };
        ensureNotificationPermission();
        playSound("start");
        setActiveMissionId(mission.id);
        await refetch();
      } catch (err) {
        setCreateError(
          err instanceof Error ? err.message : "Failed to create mission"
        );
      }
    },
    [refetch]
  );

  const startNewMission = useCallback((): void => {
    setActiveMissionId(null);
    setCreateError(null);
    window.setTimeout(
      () => document.getElementById("onebox-input")?.focus(),
      0
    );
  }, []);

  // Stop a running/queued mission so it can never spin forever.
  const cancelMission = useCallback(
    async (id: string): Promise<void> => {
      try {
        await apiFetch(`${API_BASE}/missions/${id}/cancel`, { method: "POST" });
      } catch {
        /* best-effort; the poll will reflect the real state */
      }
      await refetch();
    },
    [refetch]
  );

  // Re-run a finished/failed mission with the same goal + mode.
  const retryMission = useCallback(
    async (m: {
      title: string;
      goal: string;
      mode?: string | null;
      params?: {
        mode?: string | null;
        effort?: string | null;
        fast?: boolean;
        vision_loop?: boolean;
        speculative?: boolean;
        attachments?: string[];
        tools?: string[];
        agents?: string[];
        combine_with_default_swarm?: boolean;
      } | null;
    }): Promise<void> => {
      await createMission({
        title: m.title,
        goal: m.goal,
        mode: (m.params?.mode ?? m.mode ?? "auto") as Mode,
        effort: (m.params?.effort ?? "med") as Effort,
        fast: Boolean(m.params?.fast),
        vision_loop: Boolean(m.params?.vision_loop),
        speculative: Boolean(m.params?.speculative),
        referenceImagePath: "",
        // Mission history stores paths, not the original file byte count. The
        // composer only uses size for display, so restored attachments are
        // intentionally marked as archived (0 B) rather than guessed.
        attachments: (m.params?.attachments ?? []).map((path) => ({
          path,
          name: path.split(/[\\/]/).pop() ?? path,
          size: 0,
        })),
        tools: (m.params?.tools ?? []).filter((tool): tool is ToolId =>
          TOOLS.some((available) => available.id === tool),
        ),
        agents: m.params?.agents ?? [],
        combine_with_default_swarm: m.params?.combine_with_default_swarm ?? true,
      });
    },
    [createMission]
  );

  // Predicted next-mission suggestions for the Build empty state.
  useEffect(() => {
    if (view !== "build" || activeMissionId) {
      return;
    }
    let mounted = true;
    void apiFetch(`${API_BASE}/suggestions?n=3`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (mounted && d?.suggestions) {
          setSuggestions(d.suggestions);
        }
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, [view, activeMissionId, missions.length]);

  if (!backendReady) {
    return (
      <>
        <WindowResizeHandles />
        <SplashScreen onReady={() => setBackendReady(true)} />
      </>
    );
  }

  // null while the provider probe is in flight - only act on a real answer.
  const noProviderConfigured =
    providersInfo !== null && !hasConfiguredLlmProvider(providersInfo);
  const showProviderBanner = noProviderConfigured && !providerBannerDismissed;

  const running = live.agents_active.length > 0;
  const startFresh = (): void => {
    if (view !== "build") {
      // Must open a real tab: nulling activeChatId alone lets the tab-mirror
      // effect re-assert the previous tab's refId, so nothing visibly happens.
      startNewChat();
    } else {
      startNewMission();
    }
  };

  return (
    <div className="app-root bg-bg text-tx">
      <WindowTitleBar title={modelsOpen ? "Models" : "Infinity Code"} />
      <WindowResizeHandles />
      {modelsOpen ? (
        <ModelsView onClose={() => setModelsOpen(false)} />
      ) : creditsOpen ? (
        <CreditsView onClose={() => setCreditsOpen(false)} />
      ) : (
      <div className="app-shell shell-enter flex bg-bg text-tx overflow-hidden">
      {/* Sidebar */}
      <aside
        className="app-sidebar shell-enter stagger-1 relative flex-none border-r border-bd/[0.07] flex flex-col"
        style={{ width: sidebarWidth }}
        onContextMenu={(e) => {
          // Kimi restraint: instead of parking Customize on the sidebar as a
          // permanent row, expose it via right-click. Only fire on the aside
          // itself (not on child interactive elements) so per-chat right-clicks
          // remain free for other future menus.
          if (e.target === e.currentTarget) {
            e.preventDefault();
            setCustomizeOpen(true);
          }
        }}
      >
        <div className="px-4 py-3.5 flex items-center gap-2.5">
          <div className="brand-mark-light h-6 w-6 rounded-md border border-bd/[0.12] bg-surface-2 flex items-center justify-center shadow-sm" aria-hidden="true">
            <InfinityMark lit className="w-4 h-auto accent-text" />
          </div>
          <span className="brand-wordmark font-display text-[14px] font-normal tracking-normal text-tx">
            Infinity Code
          </span>
        </div>

        {/* New chat split-button. Click the left half to start something new
            in the current mode; click the chevron to switch modes explicitly.
            Replaces the always-visible Chat/Assistant/Build 3-toggle so the
            sidebar reads as pure content, not chrome. */}
        <div className="px-3 pb-2 relative">
          <div className="flex rounded-lg border border-bd/[0.08] overflow-hidden">
            <button
              type="button"
              onClick={() => {
                setNewMenuOpen(false);
                startFresh();
              }}
              className="light-sweep-control flex-1 flex items-center gap-2 px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors border-r border-bd/[0.08]"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              <span className="light-sweep-text">{view === "browser" ? "Browse web" : view === "build" ? "New mission" : view === "assistant" ? "New session" : view === "ide" ? "Open IDE" : "New chat"}</span>
            </button>
            <button
              type="button"
              aria-label="Choose new item kind"
              onClick={() => setNewMenuOpen((v) => !v)}
              className="px-2 text-tx-mut hover:bg-bd/[0.05] hover:text-tx transition-colors"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
          </div>
          {newMenuOpen && (
            <>
              {/* click-away catcher */}
              <div
                className="fixed inset-0 z-40"
                onClick={() => setNewMenuOpen(false)}
              />
              <div className="absolute left-3 right-3 top-full mt-1 z-50 rounded-lg border border-bd/[0.1] bg-surface-2 shadow-xl overflow-hidden">
                {[
                  { id: "chat" as View, label: "New chat", hint: "Standard conversation" },
                  { id: "assistant" as View, label: "New assistant session", hint: "Executive assistant mode" },
                  { id: "build" as View, label: "New mission", hint: "Multi-agent build" },
                  { id: "ide" as View, label: "Open IDE", hint: "Browser IDE workspace" },
                ].map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => {
                      setView(opt.id);
                      setNewMenuOpen(false);
                      // Defer so setView commits before startFresh reads it.
                      window.setTimeout(() => {
                        if (opt.id === "build") startNewMission();
                        else {
                          setActiveChatId(null);
                          openChatTab(
                            null,
                            opt.id === "assistant" ? "New session" : "New chat",
                          );
                          focusVisibleComposer();
                        }
                      }, 0);
                    }}
                    className={`w-full text-left px-3 py-2 hover:bg-bd/[0.06] transition-colors ${
                      view === opt.id ? "bg-bd/[0.03]" : ""
                    }`}
                  >
                    <div className="text-[13px] text-tx">{opt.label}</div>
                    <div className="text-[11px] text-tx-mut">{opt.hint}</div>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        <WorkspacePicker />

        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="light-sweep-control sidebar-search mx-3 mb-1 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="6.5" />
            <path d="m16 16 4 4" />
          </svg>
          <span className="light-sweep-text">Search chats</span>
          <kbd className="ml-auto rounded border border-bd/[0.1] px-1.5 py-0.5 font-mono text-[10px] text-tx-mut">Ctrl K</kbd>
        </button>

        <button
          type="button"
          onClick={() => setListCollapsed((c) => !c)}
          className="light-sweep-control sidebar-section-label w-full flex items-center gap-1.5 px-4 pt-1 pb-1 text-[11px] font-medium uppercase tracking-widest text-tx-mut hover:text-tx-dim transition-colors"
        >
          <svg
            className={`w-3 h-3 transition-transform ${listCollapsed ? "-rotate-90" : ""}`}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
          <span className="light-sweep-text">{view === "build" ? "Missions" : view === "ide" ? "IDE" : view === "browser" ? "Browser" : "Chats"}</span>
        </button>

        {listCollapsed ? null : view !== "build" ? (
          <div className="sidebar-chat-list flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
            {chats.length === 0 && (
              <p className="text-xs text-tx-mut px-4 py-2">No chats yet</p>
            )}
            {(showItem.pinned ? chats : chats.filter((c) => !c.pinned)).map((chat) =>
              renamingId === chat.id ? (
                <input
                  key={chat.id}
                  autoFocus
                  value={renameText}
                  onChange={(e) => setRenameText(e.target.value)}
                  onBlur={() => setRenamingId(null)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && renameText.trim()) {
                      void patchChat(chat.id, { title: renameText.trim() }).then(
                        () => refetchChats(),
                      );
                      setRenamingId(null);
                    } else if (e.key === "Escape") {
                      setRenamingId(null);
                    }
                  }}
                  className="sidebar-chat-rename w-full rounded-lg px-3 py-2 text-sm bg-surface border border-accent/50 text-tx focus:outline-none"
                />
              ) : (
                <div
                  key={chat.id}
                  className={`chat-row group relative w-full rounded-lg border-l-2 transition-colors hover:bg-bd/[0.04] ${
                    chat.id === activeChatId
                      ? "bg-bd/[0.06] border-accent"
                      : "border-transparent"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setActiveChatId(chat.id)}
                    onDoubleClick={() => {
                      setRenamingId(chat.id);
                      setRenameText(chat.title);
                    }}
                    className={`light-sweep-control sidebar-chat-button w-full text-left px-3 py-2.5 pr-16 text-sm truncate ${
                      chat.id === activeChatId ? "text-tx" : "text-tx-dim"
                    }`}
                  >
                    {chat.pinned ? (
                      <span className="accent-text mr-1.5" title="Pinned">
                        ●
                      </span>
                    ) : null}
                    <span className="light-sweep-text">{chat.title}</span>
                  </button>
                  <span className="absolute right-1.5 top-1/2 -translate-y-1/2 hidden group-hover:flex items-center gap-0.5">
                    <button
                      type="button"
                      title={chat.pinned ? "Unpin" : "Pin to top"}
                      onClick={() =>
                        void patchChat(chat.id, { pinned: !chat.pinned }).then(
                          () => refetchChats(),
                        )
                      }
                      className="h-6 w-6 rounded flex items-center justify-center text-tx-mut hover:text-accent hover:bg-bd/[0.08] transition-colors"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill={chat.pinned ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 17v5" />
                        <path d="M9 10.76a2 2 0 01-1.11 1.79l-1.78.9A2 2 0 005 15.24V16a1 1 0 001 1h12a1 1 0 001-1v-.76a2 2 0 00-1.11-1.79l-1.78-.9A2 2 0 0115 10.76V6h1a2 2 0 000-4H8a2 2 0 000 4h1z" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      title="Delete chat"
                      onClick={() => {
                        void deleteChat(chat.id).then(() => {
                          if (chat.id === activeChatId) {
                            setActiveChatId(null);
                          }
                          void refetchChats();
                        });
                      }}
                      className="h-6 w-6 rounded flex items-center justify-center text-tx-mut hover:text-error hover:bg-bd/[0.08] transition-colors"
                    >
                      <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 6h18" />
                        <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                      </svg>
                    </button>
                  </span>
                </div>
              ),
            )}
          </div>
        ) : (
        <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
          {error && (
            <div className="mx-2 rounded-lg border border-error/20 bg-error/[0.06] text-error text-xs p-3">
              <p>Couldn't reach the backend.</p>
              <button
                type="button"
                onClick={() => void refetch()}
                className="mt-1.5 rounded-md border border-error/30 px-2 py-0.5 text-[11px] font-medium text-error hover:bg-error/10 transition-colors"
              >
                Retry
              </button>
            </div>
          )}

          {loading && (
            <div className="space-y-1.5 px-2">
              <div className="rounded-lg h-11 bg-bd/[0.04] animate-pulse" />
              <div className="rounded-lg h-11 bg-bd/[0.04] animate-pulse" />
              <div className="rounded-lg h-11 bg-bd/[0.04] animate-pulse" />
            </div>
          )}

          {!loading && !error && missions.length === 0 && (
            <p className="text-xs text-tx-mut px-4 py-2">No missions yet</p>
          )}

          {missions.map((mission) => (
            <MissionCard
              key={mission.id}
              mission={mission}
              selected={mission.id === activeMissionId}
              onSelect={setActiveMissionId}
              onChanged={() => {
                void refetch();
              }}
              // The live socket only follows the selected mission, so only that
              // card can narrate its current phase.
              livePhase={
                mission.id === activeMissionId
                  ? (live.agents_active[0] ?? null)
                  : null
              }
            />
          ))}
        </div>
        )}

        {/* Skill Vault removed from the sidebar (still reachable via "/skill"
            slash-command and the command palette). */}

        <nav className="sidebar-utility-nav flex-none border-t border-bd/[0.06] pt-1.5 pb-1" aria-label="Workspace tools">
        {showItem.themeToggle && (
          <button
            type="button"
            onClick={toggleTheme}
            className="light-sweep-control mx-3 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
          >
            {theme === "dark" ? (
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
              </svg>
            )}
            <span className="light-sweep-text">{theme === "dark" ? "Light theme" : "Dark theme"}</span>
          </button>
        )}

        {/* Customize sidebar lives behind a right-click on the sidebar itself
            (see <aside onContextMenu>) instead of a permanent row. */}
        <button
          type="button"
          onClick={() => openSettings()}
          className="light-sweep-control mx-3 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
        >
          <svg
            className="w-3.5 h-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
          </svg>
          <span className="light-sweep-text">Settings</span>
        </button>

        <button
          type="button"
          onClick={() => setModelsOpen(true)}
          className="light-sweep-control mx-3 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          <span className="light-sweep-text">Models</span>
        </button>

        <button
          type="button"
          onClick={() => setCreditsOpen(true)}
          className="light-sweep-control mx-3 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="8" />
            <path d="M12 7v10M8.5 9.5h5a2 2 0 100-4h-3a2 2 0 100 4h3a2 2 0 110 4h-5a2 2 0 100 4h3a2 2 0 100-4" />
          </svg>
          <span className="light-sweep-text">Credits</span>
        </button>

        <button
          type="button"
          onClick={() => setView("browser")}
          className="light-sweep-control mx-3 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M3 12h18M12 3a15 15 0 010 18M12 3a15 15 0 000 18" />
          </svg>
          <span className="light-sweep-text">Browser</span>
        </button>

        <button
          type="button"
          onClick={() => setProOpen(true)}
          className="light-sweep-control mx-3 mb-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
          title="All advanced tools in one drawer"
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 2l2.4 4.8L19.5 8l-3.4 3.7.7 5.1L12 14.5l-4.8 2.3.7-5.1L4.5 8l5.1-1.2z" />
          </svg>
          <span className="light-sweep-text">Pro</span>
        </button>
        </nav>

        {showItem.cost && (
          <div className="px-4 py-3 border-t border-bd/[0.07]">
            <CostTracker cost={cost} />
            <CreditMeter />
          </div>
        )}

        {/* Drag handle: resize the sidebar (double-click resets). */}
        <div
          onMouseDown={() => {
            draggingRef.current = true;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
          }}
          onDoubleClick={() => {
            widthRef.current = 264;
            setSidebarWidth(264);
            localStorage.setItem("infinity-sidebar-w", "264");
          }}
          title="Drag to resize · double-click to reset"
          className="group absolute top-0 right-0 h-full w-1.5 translate-x-1/2 cursor-col-resize z-10"
        >
          <div className="h-full w-px mx-auto bg-transparent group-hover:bg-accent/50 transition-colors" />
        </div>
      </aside>

      {/* One dismissible tips card after first launch. */}
      {!tipDismissed && (
        <div className="fixed top-12 left-1/2 -translate-x-1/2 z-30 flex items-center gap-3 rounded-xl border border-bd/[0.08] bg-surface px-4 py-2 text-xs text-tx-dim shadow-lg">
          <span>Tip: Ctrl+K opens every tool — models, vault, council, scheduled tasks and more.</span>
          <button
            type="button"
            onClick={() => {
              localStorage.setItem("infinity-tip-dismissed", "1");
              setTipDismissed(true);
            }}
            className="rounded-md px-2 py-0.5 text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
            aria-label="Dismiss tip"
          >
            ×
          </button>
        </div>
      )}

      {/* Pro drawer: every advanced surface in one collapsible place. */}
      {proOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/30"
          onClick={() => setProOpen(false)}
          role="presentation"
        >
          <div
            className="absolute right-0 top-0 h-full w-72 flex flex-col border-l border-bd/[0.07] bg-surface shadow-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="Pro drawer"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-bd/[0.06]">
              <span className="text-sm font-semibold text-tx">Pro tools</span>
              <button
                type="button"
                onClick={() => setProOpen(false)}
                className="h-8 w-8 rounded-lg text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                aria-label="Close pro drawer"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-2 py-2 flex flex-col gap-0.5">
              {[
                { label: "Models", run: () => setModelsOpen(true) },
                { label: "Credits", run: () => setCreditsOpen(true) },
                { label: "Providers", run: () => openSettings() },
                { label: "Skill vault", run: () => setVaultOpen(true) },
                { label: "Scheduled tasks", run: () => setSchedOpen(true) },
                { label: "Long tasks", run: () => setLongtaskOpen(true) },
                { label: "Knowledge", run: () => setKnowledgeOpen(true) },
{ label: "AI Chat History", run: () => setChatHistoryOpen(true) },
                { label: "Health report", run: () => setHealthOpen(true) },
                { label: "Vision verify", run: () => setVisionOpen(true) },
                { label: "Training", run: () => setTrainingOpen(true) },
                { label: "Beast arena", run: () => setBeastOpen(true) },
                { label: "Council", run: () => setCouncilOpen(true) },
              ].map((item) => (
                <button
                  key={item.label}
                  type="button"
                  onClick={() => {
                    setProOpen(false);
                    item.run();
                  }}
                  className="mx-1 flex items-center justify-between rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.05] hover:text-tx transition-colors press"
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="px-4 py-3 border-t border-bd/[0.06] text-[11px] text-tx-mut">
              Everything here is also reachable with Ctrl+K.
            </div>
          </div>
        </div>
      )}

      {/* Main */}
      <main className="shell-enter stagger-2 flex-1 flex flex-col min-w-0">
        <TabBar
          tabs={tabsApi.tabs}
          activeId={tabsApi.activeId}
          onActivate={tabsApi.activate}
          onClose={tabsApi.close}
          onNewChat={startNewChat}
        />
        <header className="app-mobile-header" aria-label="Mobile navigation">
          <div className="brand-mark-light h-6 w-6 rounded-md border border-bd/[0.12] bg-surface-2 flex items-center justify-center" aria-hidden="true">
            <InfinityMark lit className="w-4 h-auto accent-text" />
          </div>
          {/* Kimi restraint on mobile too: the mode is chosen via New chat menu
              in the sidebar (or via /command). No always-visible mode chrome. */}
          <div className="flex-1" />
          <button type="button" onClick={startFresh} className="app-mobile-action" aria-label={view === "build" ? "New mission" : "New chat"}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <button type="button" onClick={() => openSettings()} className="app-mobile-action" aria-label="Settings">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
            </svg>
          </button>
        </header>
        {showProviderBanner && (
          <div className="flex items-center gap-3 px-5 py-2.5 border-b border-bd/[0.08] bg-surface/50">
            <div className="flex-1 min-w-0 text-[13px] leading-snug text-tx-dim">
              <span className="text-tx font-medium">
                No API key is set up yet.
              </span>{" "}
              Add a free DashScope key so the engine can respond - it takes under
              a minute.
            </div>
            <button
              type="button"
              onClick={() => openSettings("providers")}
              className="flex-none rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-black hover:bg-accent-hover transition-colors btn-press"
            >
              Set up keys
            </button>
            <button
              type="button"
              onClick={() => {
                setProviderBannerDismissed(true);
                localStorage.setItem("infinity-provider-banner-dismissed", "1");
              }}
              aria-label="Dismiss"
              className="flex-none h-6 w-6 rounded-md flex items-center justify-center text-tx-mut hover:text-tx hover:bg-bd/[0.06] transition-colors"
            >
              <svg
                className="w-3.5 h-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}
        {/* Chat / Assistant: always mounted so streams and drafts survive
            mode switches. Hidden when the user is in Build or IDE. */}
        <div
          style={{ display: view === "chat" || view === "assistant" ? "flex" : "none" }}
          className="flex-1 min-h-0 flex flex-col"
        >
          {/* One ChatView per open chat tab, kept MOUNTED (hidden when
              inactive) so a stream or draft in one tab survives switching
              to another — tabs are concurrent conversations, not pointers. */}
          <>
            {tabsApi.tabs
              .filter((t) => t.kind === "chat")
              .map((t) => (
                <div
                  key={t.id}
                  className="flex-1 min-h-0 flex flex-col"
                  style={{ display: t.id === tabsApi.activeId ? "flex" : "none" }}
                >
                  <ChatView
                    tabKey={t.id}
                    chatId={t.refId ?? null}
                    assistant={view === "assistant"}
                    onChatCreated={(id) => {
                      if (t.id === tabsApi.activeId) setActiveChatId(id);
                      // Promote the placeholder tab to the real chat so the
                      // tab bar doesn't keep a dead empty tab behind the
                      // conversation. Keep the placeholder's own id — it
                      // keys this mounted instance (remount kills streams).
                      if (t.id.startsWith("chat:new:")) {
                        tabsApi.promote(t.id, {
                          id: t.id,
                          kind: "chat",
                          refId: id,
                          title: "Chat",
                        });
                      }
                    }}
                    onChanged={() => {
                      void refetchChats();
                    }}
                    onOpenArtifact={(a) => setDesignArtifact(a)}
                    onOpenToolsSettings={() => openSettings("tools")}
                  />
                </div>
              ))}
            <div
              className="flex-1 min-h-0 flex flex-col"
              style={{ display: tabsApi.activeTab?.kind === "chat" ? "none" : "flex" }}
            >
              <ChatView
                tabKey={null}
                chatId={activeChatId}
                assistant={view === "assistant"}
                onChatCreated={(id) => {
                  setActiveChatId(id);
                  const placeholderId = tabsApi.activeId;
                  if (placeholderId && placeholderId.startsWith("chat:new:")) {
                    tabsApi.promote(placeholderId, {
                      id: placeholderId,
                      kind: "chat",
                      refId: id,
                      title: "Chat",
                    });
                  }
                }}
                onChanged={() => {
                  void refetchChats();
                }}
                onOpenArtifact={(a) => setDesignArtifact(a)}
                onOpenToolsSettings={() => openSettings("tools")}
              />
            </div>
          </>
        </div>
        {/* Build: always mounted, hidden when not in build mode. */}
        <div
          style={{ display: view === "build" ? "flex" : "none" }}
          className="flex-1 min-h-0 flex flex-col"
        >
          {!activeMissionId ? (
          // New mission: centered greeting + composer.
          <div className="flex-1 overflow-y-auto flex items-center justify-center px-6">
            <div className="w-full max-w-2xl -mt-10 fade-in-up">
              <div className="text-center">
                <h1 className="font-display text-[32px] font-normal tracking-[-0.018em] text-tx">
                  {buildMissionGreeting(getDaypart(hour), persona)}
                </h1>
                <p className="mt-3 text-tx-dim text-sm max-w-md mx-auto leading-relaxed">
                  Describe a goal. The swarm plans, builds, tests, and shows you
                  the evidence.
                </p>
              </div>
              <div className="mt-8">
                <OneBox
                  key={defaults ? `${defaults.mode}-${defaults.effort}-${defaults.fast}-${defaults.vision_loop}-${defaults.speculative}` : "loading"}
                  onSubmit={createMission}
                  initialMode={defaults?.mode}
                  initialEffort={defaults?.effort}
                  initialFast={defaults?.fast}
                  initialVisionLoop={defaults?.vision_loop}
                  initialSpeculative={defaults?.speculative}
                  noProviderConfigured={noProviderConfigured}
                  onOpenProviderSettings={() => openSettings("providers")}
                />
              </div>
              {createError && (
                <div className="mt-4 rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3">
                  {createError}
                </div>
              )}

              {suggestions.length > 0 && (
                <div className="mt-6">
                  <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-2 text-center">
                    Suggested for you
                  </p>
                  <div className="flex flex-col gap-2">
                    {suggestions.map((s, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => {
                          void createMission({
                            title: s.title,
                            goal: s.goal,
                            referenceImagePath: "",
                            mode: s.mode,
                            effort: defaults?.effort ?? "med",
                            fast: defaults?.fast ?? false,
                            vision_loop: defaults?.vision_loop ?? false,
                            speculative: defaults?.speculative ?? false,
                            attachments: [],
                            tools: DEFAULT_TOOLS,
                            agents: [],
                            combine_with_default_swarm: true,
                          });
                        }}
                        className="text-left rounded-lg border border-bd/[0.07] bg-surface hover:border-accent/40 px-3.5 py-2.5 transition-colors group"
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] rounded px-1.5 py-0.5 bg-bd/[0.06] text-tx-dim font-mono">
                            {missionModeLabel(s.mode)}
                          </span>
                          <span className="text-sm text-tx truncate">
                            {s.title}
                          </span>
                          <svg
                            className="ml-auto w-3.5 h-3.5 text-tx-mut group-hover:text-accent transition-colors flex-none"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <path d="M5 12h14M13 6l6 6-6 6" />
                          </svg>
                        </div>
                        <p className="text-xs text-tx-mut mt-1 truncate">
                          {s.reason}
                        </p>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          // Active mission: scrollable transcript, composer pinned at the bottom.
          <>
            <div className="flex-1 overflow-y-auto">
              <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
                <div className="flex items-center justify-between gap-3">
                  <h1 className="font-display text-2xl font-normal tracking-tight text-tx truncate">
                    {selectedMission?.title ?? "Mission"}
                  </h1>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className="text-xs font-mono text-tx-mut">
                      {selectedMission
                        ? `${missionStatusLabel(selectedMission.status)} · $${selectedMission.total_cost_aud.toFixed(3)} AUD`
                        : ""}
                    </span>
                    {selectedMission &&
                      (selectedMission.status === "running" ||
                        selectedMission.status === "queued") && (
                        <button
                          type="button"
                          onClick={() => void cancelMission(selectedMission.id)}
                          className="rounded-lg border border-bd/[0.12] px-2.5 py-1 text-xs text-tx-dim hover:text-error hover:border-error/40 press transition-colors"
                        >
                          Cancel
                        </button>
                      )}
                    {selectedMission &&
                      (selectedMission.status === "failed" ||
                        selectedMission.status === "rejected") && (
                        <button
                          type="button"
                          onClick={() => void retryMission(selectedMission)}
                          className="rounded-lg border border-accent/40 px-2.5 py-1 text-xs text-accent hover:bg-accent/[0.08] press transition-colors"
                        >
                          Retry
                        </button>
                      )}
                  </div>
                </div>

                <TurboActivity
                  live={live}
                  running={running}
                  fallbackCost={selectedMission?.total_cost_aud ?? 0}
                />

                {selectedMission && <MissionBrief mission={selectedMission} />}

                {/* The run's timeline, built from real events. Hidden until a
                    mission is selected — there is no swarm without one. */}
                {selectedMission && (
                  <SwarmPanel
                    events={live.events}
                    running={running}
                    missedEvents={live.missedEvents}
                    roleModels={live.role_models}
                    agentsActive={live.agents_active}
                  />
                )}

                {selectedMission ? (
                  <EvidencePanel
                    mission={selectedMission}
                    liveScreenshot={running ? live.screenshot : null}
                  />
                ) : (
                  <div className="rounded-xl border border-bd/[0.06] bg-surface p-5 text-sm text-tx-mut">
                    Loading mission...
                  </div>
                )}
              </div>
            </div>

            {/* Pinned composer */}
            <div className="shrink-0 border-t border-bd/[0.07] px-6 py-4">
              <div className="max-w-3xl mx-auto space-y-3">
                {running && (
                  <div className="flex items-center gap-2 px-1" aria-label="Working">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                  </div>
                )}
                {createError && (
                  <div className="rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3">
                    {createError}
                  </div>
                )}
                <OneBox
                  key={defaults ? `${defaults.mode}-${defaults.effort}-${defaults.fast}-${defaults.vision_loop}-${defaults.speculative}` : "loading"}
                  onSubmit={createMission}
                  initialMode={defaults?.mode}
                  initialEffort={defaults?.effort}
                  initialFast={defaults?.fast}
                  initialVisionLoop={defaults?.vision_loop}
                  initialSpeculative={defaults?.speculative}
                  noProviderConfigured={noProviderConfigured}
                  onOpenProviderSettings={() => openSettings("providers")}
                />
              </div>
            </div>
          </>
          )}
        </div>
        {/* IDE: always mounted, hidden when not in IDE mode. */}
        <div
          style={{ display: view === "ide" ? "flex" : "none" }}
          className="flex-1 min-h-0"
        >
          <ReplMode />
        </div>
        {/* Browser: always mounted so the page survives mode switches. */}
        <div
          style={{ display: view === "browser" ? "flex" : "none" }}
          className="flex-1 min-h-0"
        >
          <Suspense fallback={null}>
            <BrowserView />
          </Suspense>
        </div>
      </main>
      <ContextRail />
      </div>
      )}

      {schedOpen && (
        <Suspense fallback={null}>
          <ScheduledTasks onClose={() => setSchedOpen(false)} />
        </Suspense>
      )}

      {longtaskOpen && (
        <Suspense fallback={null}>
          <LongTaskPanel onClose={() => setLongtaskOpen(false)} />
        </Suspense>
      )}

      {knowledgeOpen && (
        <Suspense fallback={null}>
          <KnowledgeView onClose={() => setKnowledgeOpen(false)} />
        </Suspense>
      )}
      {chatHistoryOpen && (
        <Suspense fallback={null}>
          <AIChatHistory onClose={() => setChatHistoryOpen(false)} />
        </Suspense>
      )}

      {healthOpen && (
        <Suspense fallback={null}>
          <HealthReportModal onClose={() => setHealthOpen(false)} />
        </Suspense>
      )}

      {visionOpen && (
        <Suspense fallback={null}>
          <VisionPanel onClose={() => setVisionOpen(false)} />
        </Suspense>
      )}

{councilOpen && (
        <Suspense fallback={null}>
          <SwarmCouncilPanel onClose={() => setCouncilOpen(false)} />
        </Suspense>
      )}

      {trainingOpen && (
        <Suspense fallback={null}>
          <TrainingPanel onClose={() => setTrainingOpen(false)} />
        </Suspense>
      )}

      {beastOpen && (
        <Suspense fallback={null}>
          <BeastPanel onClose={() => setBeastOpen(false)} />
        </Suspense>
      )}

      {/* Skill Vault modal */}
      {vaultOpen && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm"
          onClick={() => setVaultOpen(false)}
        >
          <div
            className="w-full max-w-2xl max-h-[80vh] overflow-y-auto rounded-xl border border-bd/[0.09] bg-surface shadow-2xl shadow-black/50 p-5 fade-in-up"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-display text-lg font-normal text-tx">
                Skill Vault
              </h2>
              <button
                type="button"
                onClick={() => setVaultOpen(false)}
                className="h-7 w-7 rounded-lg flex items-center justify-center text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
                aria-label="Close vault"
              >
                <svg
                  className="w-4 h-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                >
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
            <Suspense fallback={<div className="text-sm text-tx-mut">Loading…</div>}>
              <VaultBrowser />
            </Suspense>
          </div>
        </div>
      )}

      {designArtifact && (
        <Suspense fallback={null}>
          <DesignPanel
            artifact={designArtifact}
            onClose={() => setDesignArtifact(null)}
          />
        </Suspense>
      )}

      <SettingsModal
        open={settingsOpen}
        initialTab={settingsTab}
        onClose={() => setSettingsOpen(false)}
      />

      <OnboardingModal
        open={onboardingOpen}
        onFinish={() => setOnboardingOpen(false)}
      />

      <CustomizeSidebar
        open={customizeOpen}
        onClose={() => setCustomizeOpen(false)}
      />

      {/* Sonner toasts — used for non-modal notifications (saves, errors,
          transient status). Positioned bottom-right so they don't clash with
          the composer or the modal stack. Theme is driven by prefers-color. */}
      <Toaster
        position="bottom-right"
        theme={theme}
        richColors
        closeButton
      />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onOpenChat={(id) => {
          setView("chat");
          setActiveChatId(id);
        }}
        commands={[
          { id: "new-chat", label: "New chat", hint: "Ctrl+N", run: startNewChat },
          {
            id: "toggle-theme",
            label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
            hint: "Ctrl+Shift+L",
            run: toggleTheme,
          },
          {
            id: "toggle-view",
            label:
              view === "chat"
                ? "Go to Assistant mode"
                : view === "assistant"
                  ? "Go to Build mode"
                  : view === "build"
                    ? "Go to IDE mode"
                    : view === "ide"
                      ? "Go to Browser mode"
                      : "Go to Chat mode",
            hint: "Ctrl+Shift+B",
            run: () =>
              setView((v) =>
                v === "chat" ? "assistant" : v === "assistant" ? "build" : v === "build" ? "ide" : v === "ide" ? "browser" : "chat",
              ),
          },
          {
            id: "open-ide",
            label: "Open IDE",
            hint: "Browser IDE",
            run: () => setView("ide"),
          },
          {
            id: "open-browser",
            label: "Open Browser",
            hint: "In-app web browser",
            run: () => setView("browser"),
          },
          {
            id: "settings",
            label: "Open Settings",
            run: () => openSettings(),
          },
          {
            id: "models",
            label: "Open Models",
            run: () => setModelsOpen(true),
          },
          {
            id: "long-tasks",
            label: "Developer: Long Tasks",
            run: () => setLongtaskOpen(true),
          },
          {
            id: "knowledge",
            label: "Developer: Knowledge",
            run: () => setKnowledgeOpen(true),
          },
          {
            id: "health",
            label: "Developer: Health",
            run: () => setHealthOpen(true),
          },
          {
            id: "council",
            label: "Developer: Council",
            run: () => setCouncilOpen(true),
          },
          {
            id: "vision",
            label: "Developer: Vision Verify",
            run: () => setVisionOpen(true),
          },
          {
            id: "training",
            label: "Developer: Training",
            run: () => setTrainingOpen(true),
          },
          {
            id: "beast",
            label: "Developer: Beast Arena",
            run: () => setBeastOpen(true),
          },
          {
            id: "scheduled",
            label: "Developer: Scheduled Tasks",
            run: () => setSchedOpen(true),
          },
          {
            id: "vault",
            label: "Open Skill Vault",
            run: () => setVaultOpen(true),
          },
        ]}
      />

      {turbo && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 fade-in-up">
          <div className="flex items-center gap-2 rounded-full border border-accent/40 bg-surface-2 px-4 py-2 shadow-2xl shadow-black/50">
            <svg
              className="w-4 h-4 accent-text"
              viewBox="0 0 24 24"
              fill="currentColor"
            >
              <path d="M13 2L3 14h7l-1 8 11-14h-7z" />
            </svg>
            <span className="text-sm text-tx">
              Turbo mode:{" "}
              <span className="text-tx-dim">
                faster, cheaper, less planning
              </span>
            </span>
          </div>
        </div>
      )}

    </div>
  );
}

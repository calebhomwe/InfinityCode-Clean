import { useEffect, useRef, useState } from "react";
import { EFFORTS, MODES } from "../lib/composer";
import { fetchChatModels, type ChatModel } from "../hooks/useChats";
import { toast } from "sonner";
import {
  ACCENTS,
  FONTS,
  PRESETS,
  LAYOUTS,
  type Preset,
  type Layout,
  getAppearance,
  setAppearance,
  type Appearance,
  type Contrast,
  type FontSize,
  type FontFamily,
  type ThemeMode,
  type Tone,
} from "../lib/appearance";
import {
  ensureNotificationPermission,
  getFeedbackPrefs,
  playSound,
  setFeedbackPrefs,
} from "../lib/feedback";
import { apiFetch, API_BASE  } from "../lib/api";

function relTime(epochSeconds: number): string {
  const secs = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

export interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  initialTab?: SettingsTab;
}

interface SettingsState {
  daily_budget_aud: number;
  default_mode: string;
  default_effort: string;
  default_fast: boolean;
  pass_threshold: number;
  tournament_candidates: number;
  user_notes: string;
  default_chat_model: string;
  temperature: number;
  max_response_tokens: number;
  system_prompt: string;
  enabled_tools: string[];
  auto_memory: boolean;
  free_mode: boolean;
  api_key_connected: boolean;
  models: Record<string, string>;
}

interface MarketRow {
  role: string;
  model_id: string;
  strength: number;
  blended_per_million: number;
  capability_per_dollar: number;
  note?: string;
}
interface MarketReport {
  rows: MarketRow[];
  cheapest_capable: string | null;
  local_available: boolean;
}
interface Proposal {
  area: string;
  finding: string;
  proposal: string;
  severity: "low" | "med" | "high";
}

const ALL_TOOLS: { id: string; label: string; cat: string; description: string }[] = [
  { id: "run_python", label: "Run Python", cat: "Compute", description: "Sandboxed code for data, calculations, and repeatable tasks." },
  { id: "calculator", label: "Calculator", cat: "Compute", description: "Exact arithmetic without model estimation." },
  { id: "web_search", label: "Web search", cat: "Web", description: "Find current facts and sources on the live web." },
  { id: "fetch_url", label: "Fetch URL", cat: "Web", description: "Open and read a specific webpage." },
  { id: "research", label: "Research", cat: "Web", description: "Build a deeper briefing across multiple sources." },
  { id: "review_screen", label: "Review screen", cat: "Vision", description: "Capture and inspect your primary display." },
  { id: "see_image", label: "See image", cat: "Vision", description: "Open an image and inspect its visual details." },
  { id: "critique", label: "Critique", cat: "Reasoning", description: "Score an answer or artifact and identify improvements." },
  { id: "list_skills", label: "List skills", cat: "Knowledge", description: "See the specialist skills Infinity Code can load." },
  { id: "read_skill", label: "Read skill", cat: "Knowledge", description: "Load specialist instructions before doing the work." },
  { id: "find_workspace_files", label: "Find workspace files", cat: "Workspace", description: "Find project files with a bounded workspace-relative glob." },
  { id: "search_workspace_text", label: "Search workspace text", cat: "Workspace", description: "Search project text with bounded path and line results." },
  { id: "read_workspace_files", label: "Read workspace files", cat: "Workspace", description: "Read several project files together before making a change." },
  { id: "launch_app", label: "Launch installed apps", cat: "Workspace", description: "Start a desktop app through the approval gate without a command shell." },
];
const ALL_TOOL_IDS = ALL_TOOLS.map((t) => t.id);

const EMPTY: SettingsState = {
  daily_budget_aud: 100,
  default_mode: "auto",
  default_effort: "med",
  default_fast: false,
  pass_threshold: 0.8,
  tournament_candidates: 5,
  user_notes: "",
  default_chat_model: "",
  temperature: 0.7,
  max_response_tokens: 4000,
  system_prompt: "",
  enabled_tools: [],
  auto_memory: true,
  free_mode: false,
  api_key_connected: false,
  models: {},
};

export type SettingsTab =
  | "general"
  | "chat"
  | "tools"
  | "knowledge"
  | "mcp"
  | "persona"
  | "providers"
  | "build"
  | "improve"
  | "shortcuts"
  | "about";
const TABS: { id: SettingsTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "chat", label: "Chat" },
  { id: "tools", label: "Tools" },
  { id: "knowledge", label: "Knowledge" },
  { id: "mcp", label: "MCP servers" },
  { id: "persona", label: "Persona" },
  { id: "providers", label: "Providers & API keys" },
  { id: "build", label: "Build & Swarm" },
  { id: "improve", label: "Self-improve" },
  { id: "shortcuts", label: "Shortcuts" },
  { id: "about", label: "About" },
];

// One-click game/DCC engine MCP presets (community-standard stdio servers;
// the Unreal + Blender entries are proven on this machine's live config).
const QUICK_MCP: { id: string; label: string; command: string; args: string[] }[] = [
  { id: "unreal", label: "Unreal Engine MCP", command: "npx", args: ["-y", "@runreal/unreal-mcp"] },
  { id: "unity", label: "Unity Game MCP", command: "npx", args: ["-y", "@akiojin/unity-mcp-server"] },
  { id: "godot", label: "Godot MCP", command: "npx", args: ["-y", "@coding-solo/godot-mcp"] },
  { id: "blender", label: "Blender MCP", command: "uvx", args: ["blender-mcp"] },
];

interface McpServerStatus {
  name: string;
  enabled: boolean;
  connected: boolean;
  tool_count: number;
  tools: string[];
  error: string | null;
}

interface ProvidersInfo {
  comfyui_url: string;
  fal_key: string;
  novita_key: string;
  elevenlabs_key: string;
  deepseek_key: string;
  dashscope_key: string;
  nvidia_key: string;
  configured: Record<string, boolean>;
  other: { name: string; kind: string; note: string }[];
}

type ProvField =
  | "comfyui_url"
  | "fal_key"
  | "novita_key"
  | "elevenlabs_key"
  | "deepseek_key"
  | "dashscope_key"
  | "nvidia_key";

/** One row in the Providers tab. `editable: false` rows (Moonshot) are
 *  status-only: their key comes from the environment, not this dialog. */
type ProvEntry =
  | { field: "dashscope_key"; testName: "dashscope"; label: string; note: string; editable: true }
  | { field: "deepseek_key"; testName: "deepseek"; label: string; note: string; editable: true }
  | { field: "nvidia_key"; testName: "nvidia"; label: string; note: string; editable: true }
  | { field: null; testName: "moonshot"; label: string; note: string; editable: false }
  | { field: "fal_key"; testName: "fal"; label: string; note: string; editable: true }
  | { field: "novita_key"; testName: "novita"; label: string; note: string; editable: true }
  | { field: "elevenlabs_key"; testName: "elevenlabs"; label: string; note: string; editable: true };

const CLOUD_PROVIDERS: ProvEntry[] = [
  { field: "dashscope_key", testName: "dashscope", label: "Qwen · DashScope", note: "Free-tier primary — Qwen chat and coding models.", editable: true },
  { field: "deepseek_key", testName: "deepseek", label: "DeepSeek", note: "Direct chat, coder and reasoner models.", editable: true },
  { field: "nvidia_key", testName: "nvidia", label: "NVIDIA NIM", note: "Free-tier Llama, Kimi, GLM and Nemotron models.", editable: true },
  { field: null, testName: "moonshot", label: "Kimi · Moonshot", note: "Direct Kimi API for K3 and coding models.", editable: false },
  { field: "fal_key", testName: "fal", label: "FAL.AI", note: "Image and video generation.", editable: true },
  { field: "novita_key", testName: "novita", label: "Novita", note: "Hosted image and language models.", editable: true },
  { field: "elevenlabs_key", testName: "elevenlabs", label: "ElevenLabs", note: "Text-to-speech voices.", editable: true },
];

interface SelfImproveStatus {
  enabled: boolean;
  auto_approve: boolean;
  constitution_loaded: boolean;
  daily_patch_limit: number | null;
  daily_budget_aud: number | null;
  auto_fix_ready: boolean;
  evolve_ready: boolean;
}

interface SelfImprovePatch {
  filename: string;
  size_bytes: number;
  modified_at: string;
  path: string;
}

interface EvolutionEntry {
  date?: string;
  event?: string;
  scores?: Record<string, number>;
  proposals_count?: number;
  queued_drills?: number;
  cost_aud?: number;
  promotions?: unknown[];
  weaknesses?: Record<string, unknown[]>;
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-1.5">
      <label className="block text-xs font-medium text-tx-dim">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-tx-mut">{hint}</p>}
    </div>
  );
}

/** Segmented control for small enumerated choices. */
function Segmented<T extends string>({
  value,
  options,
  onChange,
  wrap = false,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (v: T) => void;
  wrap?: boolean;
}): JSX.Element {
  return (
    <div className={wrap ? "grid grid-cols-2 gap-1 rounded-lg bg-bd/[0.05] p-1" : "flex gap-1 rounded-lg bg-bd/[0.05] p-1"}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => onChange(o.id)}
          className={`${wrap ? "min-w-0" : "flex-1"} rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
            value === o.id
              ? "bg-surface text-tx shadow-sm"
              : "text-tx-mut hover:text-tx-dim"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A row with a label + a toggle switch. */
function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <div>
        <p className="text-sm text-tx">{label}</p>
        {hint && <p className="text-[11px] text-tx-mut">{hint}</p>}
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 flex-none rounded-full transition-colors ${
          checked ? "bg-accent" : "bg-bd/[0.14]"
        }`}
        aria-pressed={checked}
      >
        <span
          className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>
    </div>
  );
}

/** Configured / testing status chip for a provider row (Settings > Providers). */
function ProvStatusChip({ status }: { status: "ok" | "off" | "err" | "busy" }): JSX.Element {
  const label =
    status === "busy"
      ? "Testing"
      : status === "ok"
        ? "Configured"
        : status === "err"
          ? "Error"
          : "Not configured";
  const cls =
    status === "busy"
      ? "prov-chip prov-chip--busy"
      : status === "ok"
        ? "prov-chip prov-chip--ok"
        : status === "err"
          ? "prov-chip prov-chip--err"
          : "prov-chip prov-chip--off";
  return <span className={cls}>{label}</span>;
}

const inputClass =
  "w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx focus:outline-none focus:border-accent/50";

// Small localStorage-backed pref helper (device-local defaults).
function lsBool(key: string, def: boolean): boolean {
  const v = localStorage.getItem(key);
  return v === null ? def : v === "1";
}
function setLsBool(key: string, v: boolean): void {
  localStorage.setItem(key, v ? "1" : "0");
  window.dispatchEvent(new CustomEvent("infinity:appearance"));
}

/** Personal learning topics — the app self-trains on these on the nightly
 *  cycle. One label per line; the labels become live-web queries and the
 *  resulting knowledge cards get folded into RAG for the next chat. */
function PersonalTopics(): JSX.Element {
  const [text, setText] = useState<string>("");
  const [loaded, setLoaded] = useState<boolean>(false);
  const [saving, setSaving] = useState<boolean>(false);
  const [saved, setSaved] = useState<boolean>(false);
  // Track pending timers so we clear them if the Settings modal closes mid-toast.
  const savedTimerRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (savedTimerRef.current != null) window.clearTimeout(savedTimerRef.current);
    };
  }, []);
  useEffect(() => {
    let ok = true;
    void apiFetch(`${API_BASE}/training/personal-topics`)
      .then((r) => (r.ok ? r.json() : { topics: [] }))
      .then((d) => {
        if (!ok) return;
        const lines = (d.topics as { label: string }[] | undefined)?.map((t) => t.label) ?? [];
        setText(lines.join("\n"));
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    return () => {
      ok = false;
    };
  }, []);
  const save = async (): Promise<void> => {
    const labels = text
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((label) => ({ label }));
    setSaving(true);
    setSaved(false);
    try {
      const r = await apiFetch(`${API_BASE}/training/personal-topics`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topics: labels }),
      });
      if (r.ok) {
        setSaved(true);
        if (savedTimerRef.current != null) window.clearTimeout(savedTimerRef.current);
        savedTimerRef.current = window.setTimeout(() => {
          setSaved(false);
          savedTimerRef.current = null;
        }, 2500);
        toast.success("Personal topics saved", {
          description: `${labels.length} topic${labels.length === 1 ? "" : "s"} — used on the next self-training cycle.`,
        });
      } else {
        toast.error("Could not save personal topics");
      }
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="border-t border-bd/[0.06] pt-3">
      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-1">
        Learn my style
      </p>
      <Field
        label="Personal topics"
        hint="One per line. The app pulls live-web research on each nightly cycle, distils it into a knowledge card, and surfaces it via RAG in future chats. Edits apply on the next cycle."
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          placeholder={loaded ? "Roblox monetization\nUnreal 5.8 Lumen\nQwen Model Studio LoRA" : "Loading…"}
          className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50 resize-y font-mono"
        />
      </Field>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving || !loaded}
          className="rounded-lg bg-accent hover:bg-accent-hover text-black px-3 py-1.5 text-xs font-medium disabled:opacity-40 transition-colors"
        >
          {saving ? "Saving…" : "Save topics"}
        </button>
        {saved && <span className="text-xs accent-text">Saved</span>}
      </div>
    </div>
  );
}

export default function SettingsModal({
  open,
  onClose,
  initialTab = "general",
}: SettingsModalProps): JSX.Element | null {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [state, setState] = useState<SettingsState>(EMPTY);
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<boolean>(false);
  const [market, setMarket] = useState<MarketReport | null>(null);
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [chatModels, setChatModels] = useState<ChatModel[]>([]);
  const [prov, setProv] = useState<ProvidersInfo | null>(null);
  // Knowledge + self-test + about data (loaded lazily per tab).
  const [know, setKnow] = useState<{
    sources: { id: string; path: string; kind: string; enabled: number; file_count: number; chunk_count: number }[];
    counts: { files: number; chunks: number };
  } | null>(null);
  const [reindexing, setReindexing] = useState<boolean>(false);
  const [reindexMsg, setReindexMsg] = useState<string>("");
  const [selftest, setSelftest] = useState<{ count: number; local_model: string | null; gaps: { question: string; score: number; relpath: string }[] } | null>(null);
  const [testing, setTesting] = useState<boolean>(false);
  const [testMsg, setTestMsg] = useState<string>("");
  const [sysInfo, setSysInfo] = useState<{ version: string; data_dir: string; db_sizes: Record<string, number>; knowledge: { files: number; chunks: number }; gaps: number; local_model: string | null } | null>(null);

  const [siStatus, setSiStatus] = useState<SelfImproveStatus | null>(null);
  const [siPatches, setSiPatches] = useState<SelfImprovePatch[]>([]);
  const [siEvolution, setSiEvolution] = useState<EvolutionEntry[]>([]);

  const loadKnowledge = (): void => {
    void apiFetch(`${API_BASE}/knowledge/sources`).then((r) => r.json()).then(setKnow).catch(() => undefined);
    void apiFetch(`${API_BASE}/selftest/gaps`).then((r) => r.json()).then(setSelftest).catch(() => undefined);
  };

  const loadSelfImprove = (): void => {
    void apiFetch(`${API_BASE}/self-improve/status`).then((r) => r.json()).then(setSiStatus).catch(() => undefined);
    void apiFetch(`${API_BASE}/self-improve/patches`).then((r) => r.json()).then(setSiPatches).catch(() => undefined);
    void apiFetch(`${API_BASE}/self-improve/evolution?limit=20`).then((r) => r.json()).then(setSiEvolution).catch(() => undefined);
  };
  const [provEdit, setProvEdit] = useState<Record<string, string>>({});
  const [provTest, setProvTest] = useState<Record<string, string>>({});
  const [provBusy, setProvBusy] = useState<string>("");
  const [provVisible, setProvVisible] = useState<Record<string, boolean>>({});
const dashRef = useRef<HTMLInputElement | null>(null);
const dashAutoFocusRef = useRef<boolean>(false);
  const [catalog, setCatalog] = useState<{
    built_in: { id: string; label: string; cat: string }[];
    mcp: { server: string; tool: string }[];
    counts: { built_in: number; mcp: number; total: number };
  } | null>(null);
  const [mcp, setMcp] = useState<McpServerStatus[]>([]);
  const [mcpConfig, setMcpConfig] = useState<
    Record<string, { command: string; args: string[]; url: string; enabled: boolean }>
  >({});
  const [mcpBusy, setMcpBusy] = useState<string>("");
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [memoryList, setMemoryList] = useState<
    { id: string; text: string; created_at: number }[]
  >([]);
  const [newMemory, setNewMemory] = useState<string>("");
  const [newServer, setNewServer] = useState<{
    name: string;
    command: string;
    args: string;
    env: string;
  }>({ name: "", command: "", args: "", env: "" });

  // Appearance (device-local, applied immediately).
  const [appear, setAppear] = useState<Appearance>(() => getAppearance());
  const patchAppear = (p: Partial<Appearance>): void =>
    setAppear(setAppearance(p));

  // Device-local behavior prefs.
  const [showCost, setShowCost] = useState<boolean>(false);
  const [fb, setFb] = useState(() => getFeedbackPrefs());
  const [defTools, setDefTools] = useState<boolean>(false);
  const [defWeb, setDefWeb] = useState<boolean>(false);
  const [sendOnEnter, setSendOnEnter] = useState<boolean>(true);

  useEffect(() => {
    if (!open) return;
    setTab(initialTab);
    setAppear(getAppearance());
    setShowCost(lsBool("infinity-show-message-cost", false));
    setDefTools(lsBool("infinity-default-tools", false));
    setDefWeb(lsBool("infinity-default-web", false));
    setSendOnEnter(lsBool("infinity-send-on-enter", true));
  }, [initialTab, open]);

  // Lazy-load per-tab data (Knowledge / Self-improve / About).
  useEffect(() => {
    if (!open) return;
    if (tab === "knowledge") loadKnowledge();
    if (tab === "improve") loadSelfImprove();
    if (tab === "about") {
      void apiFetch(`${API_BASE}/system/info`).then((r) => r.json()).then(setSysInfo).catch(() => undefined);
    }
  }, [tab, open]);

  // Fresh installs open the Providers tab pre-focused on the DashScope key
// field so the first key has an obvious home. A ref (not state) guards the
// one-shot: setState would re-run this effect and its cleanup would cancel
// the pending focus.
useEffect(() => {
    if (!open || tab !== "providers") return;
    if (!prov || dashAutoFocusRef.current) return;
    const keyCount = Object.entries(prov.configured ?? {}).filter(
      ([n, on]) => on && n !== "comfyui",
    ).length;
    if (keyCount > 0) return;
    dashAutoFocusRef.current = true;
    const t = window.setTimeout(() => dashRef.current?.focus(), 60);
    return () => window.clearTimeout(t);
}, [open, tab, prov]);


  useEffect(() => {
    if (!open) return;
    let mounted = true;
    setLoading(true);
    setError(null);
    setSaved(false);
    setMarket(null);
    setProposals(null);
    void fetchChatModels()
      .then((d) => mounted && setChatModels(d.models))
      .catch(() => undefined);
    setProvEdit({});
    setProvTest({});
    setProvBusy("");
    setProvVisible({});
    void apiFetch(`${API_BASE}/providers`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => mounted && d && setProv(d as ProvidersInfo))
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/tools`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => mounted && d && setCatalog(d))
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/memories`)
      .then((r) => (r.ok ? r.json() : { count: 0, memories: [] }))
      .then((d) => {
        if (!mounted) return;
        setMemoryCount(d.count ?? 0);
        setMemoryList(d.memories ?? []);
      })
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/mcp/status`)
      .then((r) => (r.ok ? r.json() : { servers: [] }))
      .then((d) => mounted && setMcp((d.servers ?? []) as McpServerStatus[]))
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/mcp/servers`)
      .then((r) => (r.ok ? r.json() : { servers: {} }))
      .then((d) => mounted && setMcpConfig(d.servers ?? {}))
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/market`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => mounted && d && setMarket(d as MarketReport))
      .catch(() => undefined);
    void apiFetch(`${API_BASE}/self-review`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => mounted && d && setProposals((d.proposals ?? []) as Proposal[]))
      .catch(() => undefined);
    (async () => {
      try {
        const response = await apiFetch(`${API_BASE}/settings`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as SettingsState;
        if (mounted) {
          // Materialize "all tools" so per-tool toggles work.
          const enabled =
            data.enabled_tools && data.enabled_tools.length > 0
              ? data.enabled_tools
              : ALL_TOOL_IDS;
          setState({ ...EMPTY, ...data, enabled_tools: enabled });
        }
      } catch (err) {
        if (mounted)
          setError(err instanceof Error ? err.message : "Failed to load settings");
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [open]);

  if (!open) return null;

  const save = async (): Promise<void> => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const response = await apiFetch(`${API_BASE}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          daily_budget_aud: state.daily_budget_aud,
          default_mode: state.default_mode,
          default_effort: state.default_effort,
          default_fast: state.default_fast,
          pass_threshold: state.pass_threshold,
          tournament_candidates: state.tournament_candidates,
          user_notes: state.user_notes,
          default_chat_model: state.default_chat_model,
          temperature: state.temperature,
          max_response_tokens: state.max_response_tokens,
          system_prompt: state.system_prompt,
          enabled_tools: state.enabled_tools,
          auto_memory: state.auto_memory,
          free_mode: state.free_mode,
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as SettingsState;
      const enabled =
        data.enabled_tools && data.enabled_tools.length > 0
          ? data.enabled_tools
          : ALL_TOOL_IDS;
      setState({ ...EMPTY, ...data, enabled_tools: enabled });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const saveProviderKey = async (field: ProvField, label: string): Promise<void> => {
    const draft = provEdit[field]?.trim();
    if (!draft) return;
    const saveResponse = await apiFetch(`${API_BASE}/providers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: draft }),
    });
    if (!saveResponse.ok) throw new Error(`save failed (HTTP ${saveResponse.status})`);
    setProv((await saveResponse.json()) as ProvidersInfo);
    setProvEdit((current) => {
      const next = { ...current };
      delete next[field];
      return next;
    });
    toast.success(`${label} key saved`);
  };

  const testProvider = async (name: string, label: string): Promise<void> => {
    const res = await apiFetch(`${API_BASE}/providers/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const d = (await res.json()) as { ok: boolean; detail: string };
    const text = d.ok ? `Connected · ${d.detail}` : `Error · ${d.detail}`;
    setProvTest((t) => ({ ...t, [name]: text }));
    if (d.ok) toast.success(`${label} connected`, { description: d.detail });
    else toast.error(`${label} connection failed`, { description: d.detail });
  };

  const runProviderSave = async (field: ProvField, name: string, label: string): Promise<void> => {
    setProvBusy(name);
    try {
      await saveProviderKey(field, label);
    } catch (e) {
      toast.error(`Could not save ${label} key`, {
        description: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setProvBusy("");
    }
  };

  const runProviderTest = async (name: string, label: string): Promise<void> => {
    setProvBusy(name);
    setProvTest((t) => ({ ...t, [name]: "" }));
    try {
      await testProvider(name, label);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setProvTest((t) => ({ ...t, [name]: `Error · ${msg}` }));
      toast.error(`${label} connection failed`, { description: msg });
    } finally {
      setProvBusy("");
    }
  };

  const saveAndTestProvider = async (field: ProvField, name: string, label: string): Promise<void> => {
    setProvBusy(name);
    setProvTest((t) => ({ ...t, [name]: "" }));
    try {
      await saveProviderKey(field, label);
      await testProvider(name, label);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setProvTest((t) => ({ ...t, [name]: `Error · ${msg}` }));
      toast.error(`Could not save ${label}`, { description: msg });
    } finally {
      setProvBusy("");
    }
  };

  const providerKeyCount = prov
    ? Object.entries(prov.configured ?? {}).filter(([n, on]) => on && n !== "comfyui").length
    : 0;
  const noProvidersConfigured = prov !== null && providerKeyCount === 0;

  const chipStatus = (testName: string): "ok" | "off" | "err" | "busy" => {
    if (provBusy === testName) return "busy";
    const t = provTest[testName];
    if (t?.startsWith("Error")) return "err";
    if (t?.startsWith("Connected") || prov?.configured?.[testName]) return "ok";
    return "off";
  };

  const mcpRefresh = async (): Promise<void> => {
    try {
      const [s, c] = await Promise.all([
        apiFetch(`${API_BASE}/mcp/status`),
        apiFetch(`${API_BASE}/mcp/servers`),
      ]);
      if (s.ok) setMcp(((await s.json()).servers ?? []) as McpServerStatus[]);
      if (c.ok) setMcpConfig((await c.json()).servers ?? {});
    } catch {
      /* ignore */
    }
  };

  const mcpAction = async (
    path: string,
    body?: unknown,
    busy = "working",
  ): Promise<void> => {
    setMcpBusy(busy);
    try {
      const r = await apiFetch(`${API_BASE}${path}`, {
        method: body ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (r.ok) {
        const d = await r.json();
        if (d.servers && Array.isArray(d.servers)) setMcp(d.servers as McpServerStatus[]);
      } else {
        setError(`MCP action failed: HTTP ${r.status}`);
      }
    } catch (e) {
      setError(`MCP error: ${e instanceof Error ? e.message : "unknown"}`);
    } finally {
      setMcpBusy("");
      void mcpRefresh();
    }
  };

  // Full servers map from the saved config, so command/args are preserved.
  const mcpBaseMap = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const [name, c] of Object.entries(mcpConfig)) {
      out[name] = c.url
        ? { url: c.url, enabled: c.enabled }
        : { command: c.command, args: c.args, enabled: c.enabled };
    }
    return out;
  };

  const addServer = async (): Promise<void> => {
    if (!newServer.name.trim() || !newServer.command.trim()) return;
    // Parse "KEY=VALUE" lines into an env map (for GitHub/Postgres/etc. keys).
    const env: Record<string, string> = {};
    for (const line of newServer.env.split("\n")) {
      const i = line.indexOf("=");
      if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    const map = mcpBaseMap();
    map[newServer.name.trim()] = {
      command: newServer.command.trim(),
      args: newServer.args.trim() ? newServer.args.trim().split(/\s+/) : [],
      ...(Object.keys(env).length ? { env } : {}),
      enabled: true,
    };
    await mcpAction("/mcp/servers", { servers: map }, "adding");
    setNewServer({ name: "", command: "", args: "", env: "" });
  };

  const toggleServer = async (name: string, enabled: boolean): Promise<void> => {
    const map = mcpBaseMap();
    if (map[name]) (map[name] as { enabled: boolean }).enabled = enabled;
    await mcpAction("/mcp/servers", { servers: map }, "saving");
  };

  const removeServer = async (name: string): Promise<void> => {
    const map = mcpBaseMap();
    delete map[name];
    await mcpAction("/mcp/servers", { servers: map }, "removing");
  };

  // Quick connect: add (or re-enable) a known engine MCP in one click.
  const quickConnect = async (q: (typeof QUICK_MCP)[number]): Promise<void> => {
    const map = mcpBaseMap();
    map[q.id] = { command: q.command, args: q.args, enabled: true };
    await mcpAction("/mcp/servers", { servers: map }, "connecting");
  };

  const toolEnabled = (id: string): boolean => state.enabled_tools.includes(id);
  const toggleTool = (id: string): void =>
    setState((s) => ({
      ...s,
      enabled_tools: s.enabled_tools.includes(id)
        ? s.enabled_tools.filter((t) => t !== id)
        : [...s.enabled_tools, id],
    }));

  return (
    <div
      className="settings-overlay fixed inset-0 z-40 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="settings-panel w-full max-w-3xl h-[78vh] flex overflow-hidden rounded-[22px] border border-bd/[0.09] material-overlay shadow-2xl shadow-black/50 fade-in-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Left nav */}
        <div className="settings-nav w-44 flex-none border-r border-bd/[0.07] p-3 flex flex-col">
          <h2 className="font-display text-base text-tx px-2 pt-1 pb-3">Settings</h2>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`light-sweep-control text-left rounded-lg px-2.5 py-2 text-sm transition-colors ${
                tab === t.id
                  ? "bg-bd/[0.07] text-tx"
                  : "text-tx-dim hover:bg-bd/[0.04] hover:text-tx"
              }`}
            >
              <span className="light-sweep-text">{t.label}</span>
            </button>
          ))}
          <div className="mt-auto flex items-center gap-1.5 px-2 pt-3 text-[11px]">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                state.api_key_connected ? "bg-success" : "bg-error"
              }`}
            />
            <span className="text-tx-mut">
              {state.api_key_connected ? "API connected" : "API not set"}
            </span>
          </div>
        </div>

        {/* Content */}
        <div className="settings-content flex-1 flex flex-col min-w-0">
          <div className="flex items-center justify-between px-5 py-3 border-b border-bd/[0.07]">
            <span className="text-sm font-medium text-tx">
              {TABS.find((t) => t.id === tab)?.label}
            </span>
            <button
              type="button"
              onClick={onClose}
              className="h-7 w-7 rounded-lg flex items-center justify-center text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
              aria-label="Close settings"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
            {loading ? (
              <div className="space-y-3">
                <div className="h-10 rounded-lg bg-bd/[0.04] animate-pulse" />
                <div className="h-10 rounded-lg bg-bd/[0.04] animate-pulse" />
                <div className="h-10 rounded-lg bg-bd/[0.04] animate-pulse" />
              </div>
            ) : (
              <>
                {/* ---------------- GENERAL ---------------- */}
                {tab === "general" && (
                  <>
                    <Field
                      label="Theme preset"
                      hint="Remakes the whole app to match another AI's look. Overrides accent + surfaces."
                    >
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {PRESETS.map((p) => {
                          const active = appear.preset === p.id;
                          return (
                            <button
                              key={p.id}
                              type="button"
                              onClick={() => patchAppear({ preset: p.id as Preset })}
                              className={`text-left rounded-lg border px-3 py-2 transition-colors ${
                                active
                                  ? "border-accent bg-accent/[0.08]"
                                  : "border-bd/[0.09] hover:border-bd/[0.18] bg-surface"
                              }`}
                            >
                              <div className="flex items-center gap-2 text-[13px] font-medium text-tx">
                                <span className="preset-swatch" aria-hidden="true" />
                                {p.label}
                                {active && (
                                  <span className="ml-2 text-[10px] uppercase tracking-wide accent-text">
                                    Active
                                  </span>
                                )}
                              </div>
                              <div className="text-[11.5px] text-tx-mut mt-0.5">
                                {p.hint}
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </Field>
                    <Field
                      label="Shell layout"
                      hint="Chat = current single-column feel. Assistant = executive-assistant forward. VS Code = dense editor chrome."
                    >
                      <Segmented<Layout>
                        value={appear.layout}
                        options={LAYOUTS.map((l) => ({ id: l.id, label: l.label }))}
                        onChange={(v) => patchAppear({ layout: v })}
                      />
                    </Field>
                    <Field label="Theme">
                      <Segmented<ThemeMode>
                        value={appear.theme}
                        options={[
                          { id: "dark", label: "Dark" },
                          { id: "light", label: "Light" },
                          { id: "system", label: "System" },
                        ]}
                        onChange={(v) => patchAppear({ theme: v })}
                      />
                    </Field>
                    <Field label="Background tone" hint="Applies to dark mode.">
                      <Segmented<Tone>
                        value={appear.tone}
                        options={[
                          { id: "charcoal", label: "Charcoal" },
                          { id: "slate", label: "Slate" },
                          { id: "black", label: "Black" },
                        ]}
                        onChange={(v) => patchAppear({ tone: v })}
                      />
                    </Field>
                    {appear.preset === "default" && (
                      <Field
                        label="Accent color"
                        hint="Only used with the Default preset — other presets own their accent."
                      >
                        <div className="flex gap-2">
                          {Object.entries(ACCENTS).map(([id, a]) => (
                            <button
                              key={id}
                              type="button"
                              onClick={() => patchAppear({ accent: id })}
                              title={a.label}
                              className={`h-7 w-7 rounded-full border-2 transition-transform ${
                                appear.accent === id
                                  ? "border-tx scale-110"
                                  : "border-transparent hover:scale-105"
                              }`}
                              style={{ backgroundColor: a.hex }}
                            />
                          ))}
                        </div>
                      </Field>
                    )}
                    <Field label="Font size">
                      <Segmented<FontSize>
                        value={appear.fontSize}
                        options={[
                          { id: "xs", label: "XS" },
                          { id: "s", label: "S" },
                          { id: "m", label: "M" },
                          { id: "l", label: "L" },
                          { id: "xl", label: "XL" },
                        ]}
                        onChange={(v) => patchAppear({ fontSize: v })}
                      />
                    </Field>
                    <div className="border-t border-bd/[0.06] pt-3 space-y-3">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">
                        Per-section font size
                      </p>
                      <div className="grid grid-cols-2 gap-3">
                        <Field label="UI chrome">
                          <Segmented<FontSize>
                            value={appear.uiScale ?? "m"}
                            options={[
                              { id: "xs", label: "XS" },
                              { id: "s", label: "S" },
                              { id: "m", label: "M" },
                              { id: "l", label: "L" },
                              { id: "xl", label: "XL" },
                            ]}
                            onChange={(v) => patchAppear({ uiScale: v })}
                          />
                        </Field>
                        <Field label="Chat messages">
                          <Segmented<FontSize>
                            value={appear.chatScale ?? "m"}
                            options={[
                              { id: "xs", label: "XS" },
                              { id: "s", label: "S" },
                              { id: "m", label: "M" },
                              { id: "l", label: "L" },
                              { id: "xl", label: "XL" },
                            ]}
                            onChange={(v) => patchAppear({ chatScale: v })}
                          />
                        </Field>
                        <Field label="Code blocks">
                          <Segmented<FontSize>
                            value={appear.codeScale ?? "s"}
                            options={[
                              { id: "xs", label: "XS" },
                              { id: "s", label: "S" },
                              { id: "m", label: "M" },
                              { id: "l", label: "L" },
                              { id: "xl", label: "XL" },
                            ]}
                            onChange={(v) => patchAppear({ codeScale: v })}
                          />
                        </Field>
                        <Field label="Sidebar">
                          <Segmented<FontSize>
                            value={appear.sidebarScale ?? "xs"}
                            options={[
                              { id: "xs", label: "XS" },
                              { id: "s", label: "S" },
                              { id: "m", label: "M" },
                              { id: "l", label: "L" },
                              { id: "xl", label: "XL" },
                            ]}
                            onChange={(v) => patchAppear({ sidebarScale: v })}
                          />
                        </Field>
                      </div>
                    </div>
                    <Field label="Typeface" hint="Curated for dense professional coding and agent workspaces.">
                      <Segmented<FontFamily>
                        value={appear.fontFamily}
                        options={Object.entries(FONTS).map(([id, font]) => ({
                          id: id as FontFamily,
                          label: font.label,
                        }))}
                        onChange={(v) => patchAppear({ fontFamily: v })}
                        wrap
                      />
                    </Field>
                    <Field label="Text contrast" hint="Applies to dark mode.">
                      <Segmented<Contrast>
                        value={appear.contrast}
                        options={[
                          { id: "soft", label: "Soft" },
                          { id: "normal", label: "Normal" },
                          { id: "high", label: "High" },
                        ]}
                        onChange={(v) => patchAppear({ contrast: v })}
                      />
                    </Field>
                    <div className="border-t border-bd/[0.06] pt-3">
                      <Toggle
                        label="Show per-message cost"
                        hint="Token + dollar cost under each reply."
                        checked={showCost}
                        onChange={(v) => {
                          setShowCost(v);
                          setLsBool("infinity-show-message-cost", v);
                        }}
                      />
                    </div>
                    <div className="border-t border-bd/[0.06] pt-3 space-y-1">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-1">
                        Feedback
                      </p>
                      <Toggle
                        label="Completion sounds"
                        hint="Local low-latency cues for send, tools, approvals, and completion."
                        checked={fb.sound}
                        onChange={(v) => {
                          setFb(setFeedbackPrefs({ sound: v }));
                          if (v) playSound("success");
                        }}
                      />
                      {fb.sound && (
                        <div className="flex items-center justify-between gap-3 py-1">
                          <p className="text-sm text-tx-dim">Volume</p>
                          <input
                            type="range"
                            min={0}
                            max={100}
                            value={Math.round(fb.volume * 100)}
                            onChange={(e) =>
                              setFb(
                                setFeedbackPrefs({
                                  volume: Number(e.target.value) / 100,
                                }),
                              )
                            }
                            onMouseUp={() => playSound("tick")}
                            className="w-40"
                            style={{ accentColor: "rgb(var(--c-accent))" }}
                          />
                        </div>
                      )}
                      {fb.sound && (
                        <div className="flex flex-wrap items-center gap-1.5 pt-1">
                          <span className="text-[11px] text-tx-mut mr-1">Preview:</span>
                          {(["send", "turn", "tool", "approve", "success", "error", "start", "tick"] as const).map((k) => (
                            <button
                              key={k}
                              type="button"
                              onClick={() => playSound(k)}
                              className="rounded-full border border-bd/[0.09] hover:border-accent/50 px-2.5 py-0.5 text-[11px] text-tx-dim hover:text-tx transition-colors"
                            >
                              {k}
                            </button>
                          ))}
                        </div>
                      )}
                      <Toggle
                        label="Desktop notifications"
                        hint="A system notification when a mission completes, even in the background."
                        checked={fb.notifications}
                        onChange={(v) => {
                          setFb(setFeedbackPrefs({ notifications: v }));
                          if (v) ensureNotificationPermission();
                        }}
                      />
                    </div>
                  </>
                )}

                {/* ---------------- CHAT ---------------- */}
                {tab === "chat" && (
                  <>
                    <Field
                      label="Default chat model"
                      hint="Used for new chats when you haven't picked one."
                    >
                      <select
                        value={state.default_chat_model}
                        onChange={(e) =>
                          setState((s) => ({ ...s, default_chat_model: e.target.value }))
                        }
                        className={inputClass}
                      >
                        <option value="">Strongest available (auto)</option>
                        {chatModels.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.label}
                            {m.free ? " · free" : ""}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Toggle
                      label="Free mode"
                      hint="Prefer zero-cost OpenRouter models. Great for testing; paid models are still used as a fallback if all free ones fail."
                      checked={state.free_mode}
                      onChange={(v) => setState((s) => ({ ...s, free_mode: v }))}
                    />
                    <Field
                      label={`Temperature: ${state.temperature.toFixed(2)}`}
                      hint="Lower = focused and deterministic, higher = creative."
                    >
                      <input
                        type="range"
                        min={0}
                        max={2}
                        step={0.05}
                        value={state.temperature}
                        onChange={(e) =>
                          setState((s) => ({ ...s, temperature: Number(e.target.value) }))
                        }
                        className="w-full accent-[color:var(--accent)]"
                      />
                    </Field>
                    <Field
                      label="Max response length (tokens)"
                      hint="Caps how long a single reply can be."
                    >
                      <input
                        type="number"
                        min={256}
                        max={32000}
                        step={256}
                        value={state.max_response_tokens}
                        onChange={(e) =>
                          setState((s) => ({
                            ...s,
                            max_response_tokens: Number(e.target.value),
                          }))
                        }
                        className={inputClass}
                      />
                    </Field>
                    <div className="border-t border-bd/[0.06] pt-3 space-y-1">
                      <Toggle
                        label="Send on Enter"
                        hint="Off = Enter makes a newline; Ctrl+Enter sends."
                        checked={sendOnEnter}
                        onChange={(v) => {
                          setSendOnEnter(v);
                          setLsBool("infinity-send-on-enter", v);
                        }}
                      />
                    </div>
                  </>
                )}

                {/* ---------------- TOOLS ---------------- */}
                {tab === "tools" && (
                  <>
                    <div className="space-y-1">
                      <Toggle
                        label="Tools on by default"
                        hint="New chats start with the toolbox enabled."
                        checked={defTools}
                        onChange={(v) => {
                          setDefTools(v);
                          setLsBool("infinity-default-tools", v);
                        }}
                      />
                      <Toggle
                        label="Web search on by default"
                        checked={defWeb}
                        onChange={(v) => {
                          setDefWeb(v);
                          setLsBool("infinity-default-web", v);
                        }}
                      />
                    </div>
                    <div className="border-t border-bd/[0.06] pt-3">
                      <div className="flex items-baseline justify-between mb-2">
                        <p className="text-xs font-medium text-tx-dim">
                          Tools the model may call
                        </p>
                        {catalog && (
                          <span className="text-[11px] text-tx-mut tabular-nums">
                            {catalog.counts.built_in} built-in
                            {catalog.counts.mcp > 0
                              ? ` + ${catalog.counts.mcp} MCP`
                              : ""}
                          </span>
                        )}
                      </div>
                      {/* Toggleable chat tools (enabled_tools controls these). */}
                      {["Compute", "Web", "Vision", "Reasoning", "Knowledge", "Workspace"].map(
                        (cat) => (
                          <div key={cat} className="mb-2">
                            <p className="text-[10px] uppercase tracking-widest text-tx-mut mb-1">
                              {cat}
                            </p>
                            {ALL_TOOLS.filter((t) => t.cat === cat).map((t) => (
                              <Toggle
                                key={t.id}
                                label={t.label}
                                hint={t.description}
                                checked={toolEnabled(t.id)}
                                onChange={() => toggleTool(t.id)}
                              />
                            ))}
                          </div>
                        ),
                      )}
                      {/* Assistant-only tools (always available in Assistant
                          mode; the action ones need the Actions toggle). */}
                      {catalog && (
                        <div className="mb-2">
                          <p className="text-[10px] uppercase tracking-widest text-tx-mut mb-1">
                            Assistant mode
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {catalog.built_in
                              .filter((t) => t.cat.startsWith("Files") || t.cat.startsWith("Workspace") || t.cat.startsWith("Desktop") || t.cat.startsWith("Create"))
                              .map((t) => (
                                <span
                                  key={t.id}
                                  title={t.cat}
                                  className="rounded-md bg-bd/[0.05] px-2 py-1 text-[11px] text-tx-dim"
                                >
                                  {t.label}
                                  {t.cat.includes("action") ? " ·" : ""}
                                </span>
                              ))}
                          </div>
                          <p className="text-[10px] text-tx-mut mt-1">
                            Available in Assistant mode; "· action" tools run only with Actions enabled.
                          </p>
                        </div>
                      )}
                      {/* Live MCP tools. */}
                      <div className="mb-1">
                        <p className="text-[10px] uppercase tracking-widest text-tx-mut mb-1">
                          MCP tools{catalog && catalog.mcp.length > 0 ? ` (${catalog.mcp.length})` : ""}
                        </p>
                        {catalog && catalog.mcp.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5">
                            {catalog.mcp.slice(0, 40).map((m, i) => (
                              <span
                                key={m.server + m.tool + i}
                                title={m.server}
                                className="rounded-md bg-accent/[0.08] px-2 py-1 text-[11px] accent-text"
                              >
                                {m.tool}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="text-[11px] text-tx-mut">
                            None connected. Add servers in the MCP tab to expand the toolbelt.
                          </p>
                        )}
                      </div>
                    </div>
                  </>
                )}

                {/* ---------------- MCP SERVERS ---------------- */}
                {tab === "mcp" && (
                  <>
                    <div className="flex items-start justify-between gap-3 -mt-1">
                      <p className="text-xs text-tx-mut">
                        Connect MCP servers (the same ones Claude Desktop uses).
                        Their tools appear to the chat + Assistant automatically.
                      </p>
                      <div className="flex gap-2 flex-none">
                        <button
                          type="button"
                          onClick={() => void mcpAction("/mcp/import", undefined, "importing")}
                          className="rounded-lg border border-bd/[0.1] px-2.5 py-1 text-xs text-tx-dim hover:text-tx hover:bg-bd/[0.05] transition-colors"
                        >
                          Import ~/.mcp.json
                        </button>
                        <button
                          type="button"
                          onClick={() => void mcpAction("/mcp/reconnect", undefined, "reconnecting")}
                          className="rounded-lg border border-bd/[0.1] px-2.5 py-1 text-xs text-tx-dim hover:text-tx hover:bg-bd/[0.05] transition-colors"
                        >
                          Reconnect
                        </button>
                      </div>
                    </div>
                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3">
                      <p className="text-[11px] uppercase tracking-wide text-tx-mut mb-2">
                        Quick connect
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {QUICK_MCP.map((q) => (
                          <button
                            key={q.id}
                            type="button"
                            disabled={!!mcpBusy}
                            onClick={() => void quickConnect(q)}
                            className="rounded-lg border border-bd/[0.1] px-2.5 py-1 text-xs text-tx-dim hover:text-tx hover:bg-bd/[0.05] transition-colors"
                          >
                            {mcpConfig[q.id] ? `✓ ${q.label}` : `Connect to ${q.label}`}
                          </button>
                        ))}
                      </div>
                    </div>
                    {mcpBusy && (
                      <p className="text-[11px] accent-text">{mcpBusy}…</p>
                    )}

                    {mcp.length === 0 && !mcpBusy && (
                      <p className="text-sm text-tx-mut">
                        No MCP servers yet. Import your existing ones or add one below.
                      </p>
                    )}

                    <div className="space-y-2">
                      {mcp.map((s) => (
                        <div
                          key={s.name}
                          className="rounded-lg border border-bd/[0.06] bg-bg p-3"
                        >
                          <div className="flex items-center gap-2">
                            <span
                              className={`w-1.5 h-1.5 rounded-full flex-none ${
                                s.connected
                                  ? "bg-success"
                                  : s.error
                                    ? "bg-error"
                                    : "bg-tx-mut"
                              }`}
                            />
                            <span className="text-sm text-tx truncate">{s.name}</span>
                            <span className="text-[11px] text-tx-mut">
                              {s.connected
                                ? `${s.tool_count} tools`
                                : s.error
                                  ? "error"
                                  : s.enabled
                                    ? "connecting…"
                                    : "disabled"}
                            </span>
                            <div className="ml-auto flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => void toggleServer(s.name, !s.enabled)}
                                className={`relative h-5 w-9 flex-none rounded-full transition-colors ${
                                  s.enabled ? "bg-accent" : "bg-bd/[0.14]"
                                }`}
                                aria-pressed={s.enabled}
                              >
                                <span
                                  className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                                    s.enabled ? "translate-x-4" : "translate-x-0"
                                  }`}
                                />
                              </button>
                              <button
                                type="button"
                                onClick={() => void removeServer(s.name)}
                                title="Remove server"
                                className="text-tx-mut hover:text-error transition-colors"
                              >
                                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M18 6L6 18M6 6l12 12" />
                                </svg>
                              </button>
                            </div>
                          </div>
                          {s.connected && s.tools.length > 0 && (
                            <p className="text-[11px] text-tx-mut mt-1.5 truncate">
                              {s.tools.slice(0, 8).join(", ")}
                              {s.tools.length > 8 ? " …" : ""}
                            </p>
                          )}
                          {s.error && (
                            <p className="text-[11px] text-error mt-1.5 truncate">
                              {s.error}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>

                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3 space-y-2">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">
                        Add a server
                      </p>
                      <input
                        value={newServer.name}
                        onChange={(e) =>
                          setNewServer((n) => ({ ...n, name: e.target.value }))
                        }
                        placeholder="name (e.g. filesystem)"
                        className={inputClass}
                      />
                      <input
                        value={newServer.command}
                        onChange={(e) =>
                          setNewServer((n) => ({ ...n, command: e.target.value }))
                        }
                        placeholder="command (e.g. npx or full path to uvx)"
                        className={inputClass}
                      />
                      <input
                        value={newServer.args}
                        onChange={(e) =>
                          setNewServer((n) => ({ ...n, args: e.target.value }))
                        }
                        placeholder="args, space-separated (e.g. -y @modelcontextprotocol/server-filesystem C:/Users/caleb)"
                        className={inputClass}
                      />
                      <textarea
                        value={newServer.env}
                        onChange={(e) =>
                          setNewServer((n) => ({ ...n, env: e.target.value }))
                        }
                        rows={2}
                        placeholder="env vars, one per line (e.g. GITHUB_TOKEN=ghp_… )"
                        className={inputClass + " resize-y font-mono text-[12px]"}
                      />
                      <button
                        type="button"
                        onClick={() => void addServer()}
                        disabled={!newServer.name.trim() || !newServer.command.trim()}
                        className="rounded-lg bg-accent hover:bg-accent-hover text-black px-3 py-1.5 text-xs font-medium disabled:opacity-40 transition-colors"
                      >
                        Add & connect
                      </button>
                    </div>
                  </>
                )}

                {/* ---------------- PERSONA ---------------- */}
                {tab === "persona" && (
                  <>
                    <Field
                      label="System prompt"
                      hint="The assistant's core instructions. Leave blank for the default."
                    >
                      <textarea
                        value={state.system_prompt}
                        onChange={(e) =>
                          setState((s) => ({ ...s, system_prompt: e.target.value }))
                        }
                        rows={5}
                        maxLength={8000}
                        placeholder="You are Infinity Code, a sharp, concise coding and creative assistant…"
                        className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50 resize-y"
                      />
                    </Field>
                    <Field
                      label="Memory"
                      hint="Facts to always remember (stack, style, projects). Prepended to every chat."
                    >
                      <textarea
                        value={state.user_notes}
                        onChange={(e) =>
                          setState((s) => ({ ...s, user_notes: e.target.value }))
                        }
                        rows={4}
                        maxLength={4000}
                        placeholder="e.g. I use TypeScript + Tauri. Keep answers concise."
                        className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50 resize-y"
                      />
                    </Field>

                    <div className="border-t border-bd/[0.06] pt-3">
                      <Toggle
                        label="Auto-memory (semantic recall)"
                        hint="Remembers past chats and recalls the relevant bits automatically. Uses embeddings (small per-message cost)."
                        checked={state.auto_memory}
                        onChange={(v) => setState((s) => ({ ...s, auto_memory: v }))}
                      />
                      <div className="flex items-center justify-between pt-3">
                        <span className="text-[11px] text-tx-mut tabular-nums">
                          {memoryCount} {memoryCount === 1 ? "memory" : "memories"} stored
                        </span>
                        {memoryCount > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              void apiFetch(`${API_BASE}/memories/clear`, { method: "POST" })
                                .then(() => {
                                  setMemoryCount(0);
                                  setMemoryList([]);
                                })
                                .catch(() => undefined)
                            }
                            className="rounded-lg border border-bd/[0.1] px-2.5 py-1 text-xs text-tx-dim hover:text-error hover:bg-bd/[0.05] transition-colors"
                          >
                            Clear all
                          </button>
                        )}
                      </div>

                      {/* Manual teach */}
                      <div className="flex items-center gap-2 pt-2">
                        <input
                          type="text"
                          value={newMemory}
                          onChange={(e) => setNewMemory(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              const text = newMemory.trim();
                              if (!text) return;
                              setNewMemory("");
                              void apiFetch(`${API_BASE}/memories`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ text }),
                              })
                                .then((r) => (r.ok ? r.json() : null))
                                .then((d) => {
                                  if (!d || !d.id) return;
                                  setMemoryList((l) => [
                                    { id: d.id, text, created_at: Date.now() / 1000 },
                                    ...l,
                                  ]);
                                  setMemoryCount((c) => c + 1);
                                })
                                .catch(() => undefined);
                            }
                          }}
                          placeholder="Teach a fact (press Enter)…"
                          className="flex-1 rounded-lg bg-bg border border-bd/[0.08] px-3 py-1.5 text-xs text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
                        />
                      </div>

                      {/* Recent list */}
                      {memoryList.length > 0 && (
                        <div className="mt-2 max-h-52 overflow-y-auto rounded-lg border border-bd/[0.06] divide-y divide-bd/[0.05]">
                          {memoryList.map((m) => (
                            <div
                              key={m.id}
                              className="group flex items-start gap-2 px-2.5 py-1.5 hover:bg-bd/[0.04]"
                            >
                              <span className="flex-1 text-[11px] leading-relaxed text-tx-dim break-words">
                                {m.text}
                              </span>
                              <span className="text-[10px] text-tx-mut tabular-nums whitespace-nowrap pt-0.5">
                                {relTime(m.created_at)}
                              </span>
                              <button
                                type="button"
                                title="Forget this"
                                onClick={() =>
                                  void apiFetch(`${API_BASE}/memories/${m.id}`, {
                                    method: "DELETE",
                                  })
                                    .then(() => {
                                      setMemoryList((l) =>
                                        l.filter((x) => x.id !== m.id),
                                      );
                                      setMemoryCount((c) => Math.max(0, c - 1));
                                    })
                                    .catch(() => undefined)
                                }
                                className="text-tx-mut opacity-0 group-hover:opacity-100 hover:text-error transition-opacity text-sm leading-none pt-0.5"
                              >
                                ×
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <PersonalTopics />
                  </>
                )}

                {/* ---------------- PROVIDERS ---------------- */}
                {tab === "providers" && (
                  <>
                    <div className="space-y-1 -mt-1">
                      <p className="text-sm text-tx">Connect the services Infinity Code can use.</p>
                      <p className="text-xs leading-relaxed text-tx-mut">
                        Keys are saved locally outside the project, masked after saving, and sent only when that provider is used.
                      </p>
                    </div>

                    {noProvidersConfigured && (
                      <div className="prov-empty">
                        <strong>Nothing configured yet.</strong>{" "}
                        The free DashScope tier is the fastest way to start
                        — paste a key below, save it, then test the connection.
                      </div>
                    )}

                    {/* Local */}
                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">
                          Local · free
                        </p>
                        <span className="text-[10px] text-tx-mut">ComfyUI</span>
                      </div>
                      <input
                        value={provEdit.comfyui_url ?? prov?.comfyui_url ?? ""}
                        onChange={(e) =>
                          setProvEdit((p) => ({ ...p, comfyui_url: e.target.value }))
                        }
                        placeholder="http://127.0.0.1:8188"
                        className={inputClass}
                      />
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void saveAndTestProvider("comfyui_url", "comfyui", "ComfyUI")}
                          disabled={provBusy === "comfyui"}
                          className="rounded-lg border border-bd/[0.1] px-2.5 py-1 text-xs text-tx-dim hover:text-tx hover:bg-bd/[0.05] transition-colors"
                        >
                          {provBusy === "comfyui" ? "Testing…" : "Save & test"}
                        </button>
                        {provTest.comfyui && (
                          <span className="text-[11px] text-tx-mut">{provTest.comfyui}</span>
                        )}
                      </div>
                    </div>

                    {/* Cloud API providers */}
                    <div className="space-y-2.5">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">
                        Cloud providers
                      </p>
                      {CLOUD_PROVIDERS.map((row) => (
                        <section key={row.testName} className="prov-card">
                          <div className="prov-card-header">
                            <div>
                              <span className="prov-card-title">{row.label}</span>
                              <p className="prov-card-note">{row.note}</p>
                            </div>
                            <ProvStatusChip status={chipStatus(row.testName)} />
                          </div>
                          {row.editable ? (
                            <>
                              <label className="block space-y-1.5">
                                <span className="text-[11px] text-tx-dim">API key</span>
                                <div className="relative">
                                  <input
                                    ref={row.testName === "dashscope" ? dashRef : undefined}
                                    type={provVisible[row.field] ? "text" : "password"}
                                    value={provEdit[row.field] ?? ""}
                                    onChange={(e) =>
                                      setProvEdit((p) => ({ ...p, [row.field]: e.target.value }))
                                    }
                                    placeholder={
                                      prov?.configured?.[row.testName]
                                        ? "Saved — type to replace"
                                        : "Paste API key"
                                    }
                                    autoComplete="off"
                                    className={`${inputClass} pr-14`}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => setProvVisible((current) => ({ ...current, [row.field]: !current[row.field] }))}
                                    className="absolute inset-y-0 right-0 px-3 text-[11px] text-tx-mut hover:text-tx"
                                  >
                                    {provVisible[row.field] ? "Hide" : "Show"}
                                  </button>
                                </div>
                              </label>
                              <div className="flex flex-wrap items-center gap-2">
                                {provEdit[row.field]?.trim() && (
                                  <button
                                    type="button"
                                    onClick={() => void runProviderSave(row.field, row.testName, row.label)}
                                    disabled={provBusy === row.testName}
                                    className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-black transition-colors hover:bg-accent-hover disabled:opacity-35"
                                  >
                                    {provBusy === row.testName ? "Saving…" : "Save key"}
                                  </button>
                                )}
                                <button
                                  type="button"
                                  onClick={() => void runProviderTest(row.testName, row.label)}
                                  disabled={provBusy === row.testName || (!provEdit[row.field]?.trim() && !prov?.configured?.[row.testName])}
                                  className="rounded-lg border border-bd/[0.1] px-2.5 py-1 text-xs text-tx-dim hover:text-tx hover:bg-bd/[0.05] transition-colors"
                                >
                                  {provBusy === row.testName ? "Testing…" : "Test connection"}
                                </button>
                                {provTest[row.testName] && (
                                  <span className={`text-[11px] truncate ${provTest[row.testName].startsWith("Error") ? "text-error" : "text-success"}`}>
                                    {provTest[row.testName]}
                                  </span>
                                )}
                              </div>
                            </>
                          ) : (
                            <p className="prov-env-note">
                              Key loads from the MOONSHOT_API_KEY environment variable or the
                              moonshot.key file; it is not edited here.
                            </p>
                          )}
                        </section>
                      ))}
                    </div>

                    {/* Other */}
                    {prov?.other && prov.other.length > 0 && (
                      <div className="rounded-lg border border-bd/[0.06] bg-bg p-3">
                        <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-2">
                          Other apps of interest
                        </p>
                        <div className="space-y-1">
                          {prov.other.map((o) => (
                            <div
                              key={o.name}
                              className="flex items-center justify-between text-xs"
                            >
                              <span className="text-tx-dim">
                                {o.name}
                                <span className="text-tx-mut"> · {o.note}</span>
                              </span>
                              <span className="text-[10px] text-tx-mut uppercase">
                                {o.kind}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}

                {/* ---------------- BUILD & SWARM ---------------- */}
                {tab === "build" && (
                  <>
                    <Field
                      label="Daily budget (AUD)"
                      hint="Calls that would exceed today's budget are refused and downgraded."
                    >
                      <input
                        type="number"
                        min={0}
                        step={5}
                        value={state.daily_budget_aud}
                        onChange={(e) =>
                          setState((s) => ({
                            ...s,
                            daily_budget_aud: Number(e.target.value),
                          }))
                        }
                        className={inputClass}
                      />
                    </Field>
                    <div className="grid grid-cols-2 gap-4">
                      <Field label="Default mode">
                        <select
                          value={state.default_mode}
                          onChange={(e) =>
                            setState((s) => ({ ...s, default_mode: e.target.value }))
                          }
                          className={inputClass}
                        >
                          {MODES.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.label}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Default effort">
                        <select
                          value={state.default_effort}
                          onChange={(e) =>
                            setState((s) => ({ ...s, default_effort: e.target.value }))
                          }
                          className={inputClass}
                        >
                          {EFFORTS.map((ef) => (
                            <option key={ef.id} value={ef.id}>
                              {ef.label}
                            </option>
                          ))}
                        </select>
                      </Field>
                    </div>
                    <Toggle
                      label="Fast mode by default"
                      hint="Skips Director planning for quicker, cheaper runs."
                      checked={state.default_fast}
                      onChange={(v) => setState((s) => ({ ...s, default_fast: v }))}
                    />
                    <Field label={`Pass threshold: ${state.pass_threshold.toFixed(2)}`}>
                      <input
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={state.pass_threshold}
                        onChange={(e) =>
                          setState((s) => ({
                            ...s,
                            pass_threshold: Number(e.target.value),
                          }))
                        }
                        className="w-full accent-[color:var(--accent)]"
                      />
                    </Field>
                    <Field
                      label={`Tournament candidates: ${state.tournament_candidates}`}
                      hint="How many strategies the top effort races in parallel (1-20)."
                    >
                      <input
                        type="range"
                        min={1}
                        max={20}
                        step={1}
                        value={state.tournament_candidates}
                        onChange={(e) =>
                          setState((s) => ({
                            ...s,
                            tournament_candidates: Number(e.target.value),
                          }))
                        }
                        className="w-full accent-[color:var(--accent)]"
                      />
                    </Field>

                    {/* Model market */}
                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3">
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">
                          Model market
                        </p>
                        {market?.cheapest_capable && (
                          <span className="text-[10px] text-tx-mut">
                            best value:{" "}
                            <span className="accent-text">
                              {market.cheapest_capable.split("/").pop()}
                            </span>
                          </span>
                        )}
                      </div>
                      {market ? (
                        <div className="space-y-1">
                          {market.rows.slice(0, 8).map((r) => (
                            <div
                              key={`${r.role}-${r.model_id}`}
                              className="flex items-center justify-between text-xs gap-3"
                            >
                              <span className="text-tx-dim capitalize truncate">
                                {r.role}
                              </span>
                              <span className="flex items-center gap-3 flex-none text-[10px] tabular-nums">
                                <span className="text-tx-mut">
                                  ${r.blended_per_million.toFixed(2)}/M
                                </span>
                                <span className="text-tx w-14 text-right">
                                  {r.capability_per_dollar >= 9999
                                    ? "local ∞"
                                    : `${r.capability_per_dollar.toFixed(1)}/$`}
                                </span>
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-tx-mut">Loading live prices…</p>
                      )}
                    </div>

                    {proposals && proposals.length > 0 && (
                      <div className="rounded-lg border border-bd/[0.06] bg-bg p-3">
                        <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-2">
                          System review
                        </p>
                        <div className="space-y-2">
                          {proposals.map((p, i) => (
                            <div key={i} className="text-xs">
                              <div className="flex items-center gap-2">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium border ${
                                    p.severity === "high"
                                      ? "border-error/20 bg-error/10 text-error"
                                      : p.severity === "med"
                                        ? "border-warning/20 bg-warning/10 text-warning"
                                        : "border-bd/[0.1] bg-bd/[0.04] text-tx-dim"
                                  }`}
                                >
                                  {p.severity}
                                </span>
                                <span className="text-tx">{p.finding}</span>
                              </div>
                              <p className="text-tx-mut mt-0.5 pl-1">{p.proposal}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-2">
                        Model council
                      </p>
                      <div className="space-y-1">
                        {Object.entries(state.models).map(([role, id]) => (
                          <div
                            key={role}
                            className="flex items-center justify-between text-xs"
                          >
                            <span className="text-tx-dim capitalize">{role}</span>
                            <span className="text-tx-mut truncate ml-3">{id}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}

                {/* ---------------- KNOWLEDGE ---------------- */}
                {tab === "knowledge" && (
                  <>
                    <p className="text-xs text-tx-mut -mt-1">
                      Your own notes power grounded, cited answers (and refuse to invent).
                      Sources are indexed with embeddings; stray Downloads notes get
                      collected into your vault Inbox automatically.
                    </p>
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        disabled={reindexing}
                        onClick={() => {
                          setReindexing(true);
                          setReindexMsg("Indexing…");
                          void apiFetch(`${API_BASE}/knowledge/reindex`, { method: "POST" })
                            .then((r) => r.json())
                            .then((d) => {
                              setReindexMsg(`Indexed ${d.files_indexed} changed · ${d.collected} collected · ${d.counts?.chunks} chunks total`);
                              loadKnowledge();
                            })
                            .catch(() => setReindexMsg("Reindex failed"))
                            .finally(() => setReindexing(false));
                        }}
                        className="rounded-lg bg-accent hover:bg-accent-hover text-black px-3 py-1.5 text-sm font-medium disabled:opacity-50 transition-colors"
                      >
                        {reindexing ? "Indexing…" : "Re-index now"}
                      </button>
                      <span className="text-[11px] text-tx-mut tabular-nums">
                        {know ? `${know.counts.files} files · ${know.counts.chunks} chunks` : "…"}
                      </span>
                      {reindexMsg && <span className="text-[11px] text-tx-dim">{reindexMsg}</span>}
                    </div>
                    <div className="space-y-2">
                      {(know?.sources ?? []).map((s) => (
                        <div key={s.id} className="flex items-center gap-3 rounded-lg border border-bd/[0.06] bg-bg p-2.5">
                          <span className={`text-[10px] px-2 py-0.5 rounded-full ${s.kind === "downloads" ? "bg-accent/10 accent-text" : "bg-bd/[0.06] text-tx-dim"}`}>{s.kind}</span>
                          <span className="flex-1 text-xs text-tx-dim truncate" title={s.path}>{s.path}</span>
                          <span className="text-[10px] text-tx-mut tabular-nums">{s.chunk_count} chunks</span>
                          <button
                            type="button"
                            onClick={() => void apiFetch(`${API_BASE}/knowledge/sources/${s.id}`, { method: "DELETE" }).then(loadKnowledge)}
                            className="text-tx-mut hover:text-error text-xs"
                            title="Remove source"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                    {/* Self-test / gap analysis */}
                    <div className="border-t border-bd/[0.06] pt-3 mt-1">
                      <div className="flex items-center gap-3">
                        <span className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">Self-test (free, local)</span>
                        <span className="text-[10px] text-tx-mut">{selftest?.local_model ? `LM Studio: ${selftest.local_model}` : "LM Studio not running"}</span>
                      </div>
                      <p className="text-[11px] text-tx-mut mt-1">
                        Quizzes a local model on your notes, grades its unaided answers, and logs the gaps (what it must retrieve, never free-recall).
                      </p>
                      <div className="flex items-center gap-3 mt-2">
                        <button
                          type="button"
                          disabled={testing}
                          onClick={() => {
                            setTesting(true);
                            setTestMsg("Testing…");
                            void apiFetch(`${API_BASE}/selftest/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ n: 6 }) })
                              .then((r) => r.json())
                              .then((d) => {
                                setTestMsg(d.status === "ok" ? `Tested ${d.tested}, found ${d.gaps_found} gaps` : (d.detail || d.status));
                                loadKnowledge();
                              })
                              .catch(() => setTestMsg("Self-test failed"))
                              .finally(() => setTesting(false));
                          }}
                          className="rounded-lg border border-bd/[0.12] px-3 py-1.5 text-sm text-tx-dim hover:text-tx hover:border-accent/50 disabled:opacity-50 transition-colors"
                        >
                          {testing ? "Testing…" : "Run self-test"}
                        </button>
                        <span className="text-[11px] text-tx-dim">{testMsg || (selftest ? `${selftest.count} known gaps` : "")}</span>
                      </div>
                      {(selftest?.gaps ?? []).slice(0, 5).length > 0 && (
                        <div className="mt-2 space-y-1">
                          {(selftest?.gaps ?? []).slice(0, 5).map((g, i) => (
                            <div key={i} className="text-[11px] text-tx-mut flex gap-2">
                              <span className="text-error tabular-nums">{g.score.toFixed(2)}</span>
                              <span className="truncate">{g.question}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </>
                )}

                {/* ---------------- SELF-IMPROVE ---------------- */}
                {tab === "improve" && (
                  <>
                    <p className="text-xs text-tx-mut -mt-1">
                      The engine grades its own work, drafts fixes, and logs every
                      step — but nothing is applied without you. Pending patches
                      wait here for review; the evolution trail shows what each
                      cycle learned.
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      {siStatus ? (
                        <>
                          <span className={`text-[10px] px-2 py-0.5 rounded-full ${siStatus.enabled ? "bg-accent/10 accent-text" : "bg-bd/[0.06] text-tx-dim"}`}>
                            {siStatus.enabled ? "Governance on" : "Governance off"}
                          </span>
                          <span
                            className={`text-[10px] px-2 py-0.5 rounded-full ${siStatus.auto_approve ? "bg-error/10 text-error" : "bg-bd/[0.06] text-tx-dim"}`}
                            title={siStatus.auto_approve ? "Patches may be applied without review" : "Every patch waits for a human"}
                          >
                            {siStatus.auto_approve ? "Auto-approve ON" : "Manual approval"}
                          </span>
                          {siStatus.daily_patch_limit != null && (
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-bd/[0.06] text-tx-dim tabular-nums">
                              ≤ {siStatus.daily_patch_limit} patches/day
                            </span>
                          )}
                          {siStatus.daily_budget_aud != null && (
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-bd/[0.06] text-tx-dim tabular-nums">
                              ≤ ${Number(siStatus.daily_budget_aud).toFixed(2)} AUD/day
                            </span>
                          )}
                          <span className="text-[10px] text-tx-mut">
                            auto-fix {siStatus.auto_fix_ready ? "ready" : "offline"} · evolve {siStatus.evolve_ready ? "ready" : "offline"}
                            {siStatus.constitution_loaded ? "" : " · constitution missing"}
                          </span>
                        </>
                      ) : (
                        <span className="text-[11px] text-tx-mut">Loading governance state…</span>
                      )}
                    </div>

                    <div className="border-t border-bd/[0.06] pt-3 mt-1">
                      <span className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">Pending patches</span>
                      {siPatches.length === 0 ? (
                        <p className="text-[11px] text-tx-mut mt-1.5">No patches waiting for review.</p>
                      ) : (
                        <div className="space-y-2 mt-2">
                          {siPatches.map((p) => (
                            <div key={p.filename} className="flex items-center gap-3 rounded-lg border border-bd/[0.06] bg-bg p-2.5">
                              <span className="flex-1 text-xs text-tx-dim truncate" title={p.path}>{p.filename}</span>
                              <span className="text-[10px] text-tx-mut tabular-nums">{(p.size_bytes / 1024).toFixed(1)} KB</span>
                              <span className="text-[10px] text-tx-mut tabular-nums">
                                {Number.isNaN(Date.parse(p.modified_at)) ? p.modified_at : new Date(p.modified_at).toLocaleString()}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                      <p className="text-[11px] text-tx-mut mt-1.5">
                        Drafts live under <span className="font-mono">patches/</span> in your data folder — open, review, and apply them yourself.
                      </p>
                    </div>

                    <div className="border-t border-bd/[0.06] pt-3 mt-1">
                      <span className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">Evolution trail</span>
                      {siEvolution.length === 0 ? (
                        <p className="text-[11px] text-tx-mut mt-1.5">No evolution runs logged yet.</p>
                      ) : (
                        <div className="space-y-2 mt-2">
                          {siEvolution.map((e, i) => (
                            <div key={i} className="rounded-lg border border-bd/[0.06] bg-bg p-2.5 space-y-1">
                              <div className="flex flex-wrap items-center gap-3">
                                <span className="text-xs text-tx-dim tabular-nums">
                                  {e.date ? (Number.isNaN(Date.parse(e.date)) ? e.date : new Date(e.date).toLocaleString()) : "—"}
                                </span>
                                <span className="text-[10px] text-tx-mut">{e.proposals_count ?? 0} proposals · {e.queued_drills ?? 0} drills</span>
                                {typeof e.cost_aud === "number" && (
                                  <span className="text-[10px] text-tx-mut tabular-nums">${e.cost_aud.toFixed(4)} AUD</span>
                                )}
                              </div>
                              {e.scores && Object.keys(e.scores).length > 0 && (
                                <div className="flex flex-wrap gap-1.5">
                                  {Object.entries(e.scores).map(([lane, score]) => (
                                    <span key={lane} className="text-[10px] px-2 py-0.5 rounded-full bg-bd/[0.06] text-tx-dim tabular-nums">
                                      {lane}: {typeof score === "number" ? score.toFixed(3) : "?"}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </>
                )}

                {/* ---------------- SHORTCUTS ---------------- */}
                {tab === "shortcuts" && (
                  <div className="space-y-1.5">
                    {[
                      ["Ctrl/Ctrl K", "Command palette"],
                      ["Ctrl/Ctrl N", "New chat"],
                      ["Ctrl/Ctrl Shift B", "Cycle mode (Chat → Assistant → Build)"],
                      ["Ctrl/Ctrl Shift L", "Toggle theme"],
                      ["Ctrl T", "Toggle Fast mode"],
                      ["/", "Slash commands (/build, /agent, /skill, /web, /tools)"],
                      ["/build <goal>", "Run the quality-gated Code loop, grounded in your notes"],
                      ["Enter", "Send (Shift+Enter = newline)"],
                    ].map(([k, d]) => (
                      <div key={k} className="flex items-center justify-between rounded-lg border border-bd/[0.05] bg-bg px-3 py-2">
                        <span className="text-xs text-tx-dim">{d}</span>
                        <kbd className="text-[11px] font-mono text-tx bg-bd/[0.08] rounded px-2 py-0.5">{k}</kbd>
                      </div>
                    ))}
                  </div>
                )}

                {/* ---------------- ABOUT ---------------- */}
                {tab === "about" && (
                  <>
                    <div className="flex items-center gap-3">
                      <div className="h-11 w-11 rounded-2xl bg-accent/10 accent-text flex items-center justify-center">
                        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M18.6 6.62a4 4 0 10-.02 5.64L12 12l-6.58 5.74a4 4 0 11.02-5.64L12 12l6.6-5.38z" /></svg>
                      </div>
                      <div>
                        <div className="text-sm font-semibold text-tx">Infinity Code</div>
                        <div className="text-[11px] text-tx-mut">v{sysInfo?.version ?? "…"}</div>
                      </div>
                    </div>
                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3 space-y-1.5">
                      <div className="flex justify-between text-xs"><span className="text-tx-mut">Knowledge</span><span className="text-tx-dim tabular-nums">{sysInfo ? `${sysInfo.knowledge.files} files · ${sysInfo.knowledge.chunks} chunks` : "…"}</span></div>
                      <div className="flex justify-between text-xs"><span className="text-tx-mut">Learning gaps logged</span><span className="text-tx-dim tabular-nums">{sysInfo?.gaps ?? 0}</span></div>
                      <div className="flex justify-between text-xs"><span className="text-tx-mut">Local model</span><span className="text-tx-dim truncate ml-3">{sysInfo?.local_model ?? "none running"}</span></div>
                      <div className="flex justify-between text-xs"><span className="text-tx-mut">Data folder</span><span className="text-tx-dim truncate ml-3" title={sysInfo?.data_dir}>{sysInfo?.data_dir ? "…" + sysInfo.data_dir.slice(-28) : "…"}</span></div>
                    </div>
                    <div className="rounded-lg border border-bd/[0.06] bg-bg p-3">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-tx-mut mb-2">Databases</p>
                      <div className="space-y-1">
                        {Object.entries(sysInfo?.db_sizes ?? {}).map(([n, sz]) => (
                          <div key={n} className="flex justify-between text-xs"><span className="text-tx-dim">{n}</span><span className="text-tx-mut tabular-nums">{Math.round(sz / 1024)} KB</span></div>
                        ))}
                      </div>
                    </div>
                    <p className="text-[11px] text-tx-mut">
                      Agent Library: 245 personas from agency-agents (MIT). Knowledge grounding + free
                      local self-testing. Built with Tauri · React · FastAPI.
                    </p>
                  </>
                )}

                {error && (
                  <div className="rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3">
                    {error}
                  </div>
                )}
              </>
            )}
          </div>

          {/* Footer: only the server-persisted tabs need a Save. */}
          <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-bd/[0.07]">
            {saved && <span className="text-xs text-success">Saved</span>}
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-3 py-2 text-sm text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
            >
              Close
            </button>
            {tab !== "providers" && (
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving}
                className="rounded-lg bg-accent hover:bg-accent-hover text-black px-4 py-2 text-sm font-medium disabled:opacity-50 transition-colors"
              >
                {saving ? "Saving…" : "Save"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

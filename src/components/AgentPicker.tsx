import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

export interface AgentLite {
  id: string;
  division: string;
  name: string;
  description: string;
  emoji: string;
  color: string;
  vibe: string;
}
interface Division {
  label: string;
  color: string;
}

export interface AgentPickerProps {
  open: boolean;
  activeId?: string | null;
  onClose: () => void;
  onPick: (agent: AgentLite | null) => void;
  /** Build mode turns the library into a bounded multi-select crew picker. */
  multiSelect?: boolean;
  selectedIds?: string[];
  combineWithDefaultSwarm?: boolean;
  onPickMany?: (agents: AgentLite[]) => void;
  onCombineWithDefaultSwarmChange?: (combine: boolean) => void;
}

/** Browse the bundled Agent Library (245 specialist personas) and activate one. */
export default function AgentPicker({
  open,
  activeId,
  onClose,
  onPick,
  multiSelect = false,
  selectedIds = [],
  combineWithDefaultSwarm = true,
  onPickMany,
  onCombineWithDefaultSwarmChange,
}: AgentPickerProps): JSX.Element | null {
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [divisions, setDivisions] = useState<Record<string, Division>>({});
  const [query, setQuery] = useState("");
  const [division, setDivision] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const [crewIds, setCrewIds] = useState<string[]>(selectedIds);
  const [combine, setCombine] = useState(combineWithDefaultSwarm);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open || agents.length) return;
    let ok = true;
    void apiFetch(`${API_BASE}/agents`)
      .then((r) => (r.ok ? r.json() : { agents: [], divisions: {} }))
      .then((d) => {
        if (!ok) return;
        setAgents(d.agents ?? []);
        setDivisions(d.divisions ?? {});
        setLoading(false);
      })
      .catch(() => ok && setLoading(false));
    return () => {
      ok = false;
    };
  }, [open, agents.length]);

  useEffect(() => {
    if (!open || !multiSelect) return;
    setCrewIds(selectedIds.slice(0, 3));
    setCombine(combineWithDefaultSwarm);
  }, [open, multiSelect, selectedIds, combineWithDefaultSwarm]);

  // Keyboard nav is defined below (needs `filtered` in scope). Escape is
  // handled inline in the arrow-key listener. Cursor/filtered live in refs
  // so the effect can be registered ONCE per `open` — otherwise it re-runs
  // on every arrow-key press and its 40 ms focus timer keeps yanking focus
  // back to the search input, breaking arrow-key navigation entirely.
  const [cursor, setCursor] = useState(0);
  const cursorRef = useRef(cursor);
  useEffect(() => {
    cursorRef.current = cursor;
  }, [cursor]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const a of agents) c[a.division] = (c[a.division] ?? 0) + 1;
    return c;
  }, [agents]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return agents.filter((a) => {
      if (division !== "all" && a.division !== division) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        a.vibe.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q)
      );
    });
  }, [agents, query, division]);
  // Ref-mirror so the (open-scoped) keyboard effect can read the latest list
  // without re-registering the listener + focus timer on every keystroke.
  const filteredRef = useRef(filtered);
  useEffect(() => {
    filteredRef.current = filtered;
  }, [filtered]);

  const chooseAgent = (agent: AgentLite): void => {
    if (!multiSelect) {
      onPick(agent);
      onClose();
      return;
    }
    setCrewIds((current) => {
      if (current.includes(agent.id)) return current.filter((id) => id !== agent.id);
      return current.length < 3 ? [...current, agent.id] : current;
    });
  };

  const applyCrew = (): void => {
    onPickMany?.(agents.filter((agent) => crewIds.includes(agent.id)));
    onCombineWithDefaultSwarmChange?.(combine);
    onClose();
  };

  // Reset the highlight when the visible set changes (search, division swap).
  useEffect(() => {
    setCursor(0);
  }, [query, division, agents.length]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // Arrow-key nav is 2 columns on ≥640px, 1 column below.
      const cols = window.matchMedia("(min-width: 640px)").matches
        ? window.matchMedia("(min-width: 1024px)").matches
          ? 3
          : 2
        : 1;
      const list = filteredRef.current;
      const len = list.length;
      if (!len) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setCursor((c) => Math.min(len - 1, c + cols));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setCursor((c) => Math.max(0, c - cols));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setCursor((c) => Math.min(len - 1, c + 1));
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setCursor((c) => Math.max(0, c - 1));
      } else if (e.key === "Enter") {
        const pick = list[cursorRef.current];
        if (pick) {
          e.preventDefault();
          chooseAgent(pick);
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const focusTimer = window.setTimeout(() => searchRef.current?.focus(), 40);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
    };
    // Deliberately depends only on open/onClose/onPick. `filtered` and
    // `cursor` are read via refs above so this effect registers ONCE per
    // open — otherwise the 40 ms focus timer would fire on every arrow key
    // and steal focus back to the search input.
  }, [open, onClose, onPick, multiSelect]);

  if (!open) return null;

  const divEntries = Object.entries(divisions).sort((a, b) =>
    a[1].label.localeCompare(b[1].label),
  );

  return (
    <div
      className="agent-picker fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/50 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        className="agent-picker__panel w-full max-w-4xl h-[78vh] flex flex-col rounded-[22px] material-overlay border border-bd/[0.1] shadow-2xl overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-bd/[0.08]">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-bd/[0.1] text-tx-dim" aria-hidden="true">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="8" r="3" />
                <path d="M5 20c.8-4 3.1-6 7-6s6.2 2 7 6" />
              </svg>
            </span>
            <div>
              <div className="text-sm font-semibold text-tx">Agent Library</div>
              <div className="text-[11px] text-tx-mut">
                {agents.length} specialist personas · pick one to lead the chat
              </div>
            </div>
          </div>
          <div className="flex-1" />
          {!multiSelect && activeId && (
            <button
              type="button"
              onClick={() => {
                onPick(null);
                onClose();
              }}
              className="text-xs text-tx-dim hover:text-error rounded-lg border border-bd/[0.1] px-2.5 py-1.5 transition-colors"
            >
              Clear active
            </button>
          )}
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-tx-mut hover:text-tx rounded-lg p-1.5 hover:bg-bd/[0.06]"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>

        {/* search */}
        <div className="px-5 py-3 border-b border-bd/[0.06]">
          <div className="relative">
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-tx-mut"
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${agents.length} agents — role, skill, vibe…`}
              className="w-full rounded-lg bg-bg border border-bd/[0.08] pl-9 pr-3.5 py-2.5 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
            />
          </div>
        </div>

        <div className="flex-1 min-h-0 flex">
          {/* divisions */}
          <div className="agent-picker__divisions w-48 flex-none overflow-y-auto border-r border-bd/[0.06] py-2">
            <button
              type="button"
              onClick={() => setDivision("all")}
              className={`w-full text-left px-4 py-1.5 text-[13px] flex items-center justify-between transition-colors ${division === "all" ? "text-accent bg-accent/[0.08]" : "text-tx-dim hover:text-tx hover:bg-bd/[0.04]"}`}
            >
              <span>All</span><span className="text-[11px] tabular-nums text-tx-mut">{agents.length}</span>
            </button>
            {divEntries.map(([slug, d]) => (
              <button
                key={slug}
                type="button"
                onClick={() => setDivision(slug)}
                className={`w-full text-left px-4 py-1.5 text-[13px] flex items-center justify-between transition-colors ${division === slug ? "text-accent bg-accent/[0.08]" : "text-tx-dim hover:text-tx hover:bg-bd/[0.04]"}`}
              >
                <span className="flex items-center gap-2 truncate">
                  <span className="w-2 h-2 rounded-full flex-none" style={{ background: d.color }} />
                  <span className="truncate">{d.label}</span>
                </span>
                <span className="text-[11px] tabular-nums text-tx-mut">{counts[slug] ?? 0}</span>
              </button>
            ))}
          </div>

          {/* agent grid */}
          <div className="flex-1 overflow-y-auto p-4">
            {loading ? (
              <div className="text-sm text-tx-mut p-6 text-center">Loading agents…</div>
            ) : filtered.length === 0 ? (
              <div className="text-sm text-tx-mut p-6 text-center">No agents match “{query}”.</div>
            ) : (
              <div className="agent-picker__grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {filtered.map((a, idx) => {
                  const isActive = multiSelect ? crewIds.includes(a.id) : a.id === activeId;
                  const isCursor = idx === cursor;
                  return (
                    <button
                      key={a.id}
                      type="button"
                      aria-pressed={multiSelect ? isActive : undefined}
                      onMouseEnter={() => setCursor(idx)}
                      onClick={() => chooseAgent(a)}
                      className={`text-left rounded-xl border p-4 transition-all hover:-translate-y-0.5 ${
                        isActive
                          ? "border-accent bg-accent/[0.06]"
                          : isCursor
                          ? "border-bd/[0.24] bg-bd/[0.04]"
                          : "border-bd/[0.08] bg-bg hover:border-bd/[0.16]"
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <span
                          className="w-9 h-9 rounded-lg bg-bd/[0.05] flex items-center justify-center text-lg flex-none"
                          style={{ boxShadow: `inset 0 0 0 1px ${a.color || "transparent"}22` }}
                        >
                          {a.name.slice(0, 2).toUpperCase()}
                        </span>
                        <span className="text-[14px] font-semibold text-tx truncate">{a.name}</span>
                        {isActive && (
                          <span className="ml-auto text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded-full bg-accent/15 accent-text">
                            {multiSelect ? "Selected" : "Active"}
                          </span>
                        )}
                      </div>
                      <div className="mt-2 text-[12.5px] text-tx-mut leading-relaxed line-clamp-2">
                        {a.vibe || a.description}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
        {multiSelect && (
          <div className="flex flex-wrap items-center gap-3 px-5 py-3 border-t border-bd/[0.08] bg-bd/[0.025]">
            <label className="flex items-center gap-2 text-xs text-tx-dim cursor-pointer select-none">
              <input
                type="checkbox"
                checked={combine}
                onChange={(event) => setCombine(event.target.checked)}
                className="accent-accent"
              />
              Keep the core swarm for planning, verification, and recovery
            </label>
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => setCrewIds([])}
              className="text-xs text-tx-mut hover:text-tx transition-colors"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={applyCrew}
              className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-black hover:bg-accent-hover active:scale-[0.98] transition-all"
            >
              Use crew ({crewIds.length}/3)
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// Persona switcher — one chip, one integrated popover.
// Replaces the split "variant chip + toolbar Agent button" surfaces:
// featured Code Variants up top, quick picks, search across the whole
// library, and the full Agent Library modal kept as the deep-dive.
import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";
import type { AgentLite } from "../components/AgentPicker";

interface PersonaPickerProps {
  active: AgentLite | null;
  onPick: (agent: AgentLite | null) => void;
  onOpenLibrary: () => void;
}

const VARIANT_DIVISION = "code-variants";
const QUICK_PICKS = 5;
const SEARCH_CAP = 30;

export default function PersonaPicker({
  active,
  onPick,
  onOpenLibrary,
}: PersonaPickerProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [agents, setAgents] = useState<AgentLite[]>([]);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  // One fetch, cached for the life of the chat view.
  useEffect(() => {
    let ok = true;
    void apiFetch(`${API_BASE}/agents`)
      .then((r) => (r.ok ? r.json() : { agents: [] }))
      .then((d) => ok && setAgents(Array.isArray(d.agents) ? d.agents : []))
      .catch(() => undefined);
    return () => {
      ok = false;
    };
  }, []);

  // Outside click closes the popover.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const variants = useMemo(
    () => agents.filter((a) => a.division === VARIANT_DIVISION),
    [agents],
  );
  const quickPicks = useMemo(
    () =>
      agents
        .filter((a) => a.division !== VARIANT_DIVISION)
        .slice(0, QUICK_PICKS),
    [agents],
  );
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return agents
      .filter(
        (a) =>
          a.name.toLowerCase().includes(q) ||
          a.vibe.toLowerCase().includes(q) ||
          a.description.toLowerCase().includes(q),
      )
      .slice(0, SEARCH_CAP);
  }, [agents, query]);

  const choose = (agent: AgentLite): void => {
    onPick(agent);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={
          active
            ? `Persona: ${active.name} — click to change`
            : "Pick a persona (DaCoder & crew)"
        }
        className={`h-8 rounded-full border px-3.5 text-[12.5px] font-medium transition-colors flex items-center gap-1.5 press ${
          active
            ? "border-accent/40 accent-text bg-accent/[0.07]"
            : "border-bd/[0.12] text-tx-dim hover:text-tx hover:border-accent/50"
        }`}
      >
        {active ? (
          <>
            <span className="leading-none text-[10px] uppercase text-tx-mut">{active.name.slice(0, 2)}</span>
            {active.name}
          </>
        ) : (
          <>Persona</>
        )}
        <svg
          className={`w-3 h-3 text-tx-mut transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="fade-in-up absolute bottom-full left-0 mb-2 z-30 w-[320px] rounded-2xl border border-bd/[0.11] material-overlay p-2 shadow-2xl shadow-black/50">
          <div className="flex items-center justify-between px-1.5 pt-0.5 pb-1.5">
            <span className="text-[11px] text-tx-mut">Persona</span>
            {active && (
              <button
                type="button"
                onClick={() => {
                  onPick(null);
                  setOpen(false);
                }}
                className="text-[11px] text-tx-mut hover:text-error transition-colors"
              >
                Clear
              </button>
            )}
          </div>

          {/* Search narrows the whole library inline — no modal needed. */}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search personas…"
            className="mb-1.5 w-full rounded-lg bg-bg border border-bd/[0.08] px-2.5 py-1.5 text-[12px] text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
          />

          {query.trim() ? (
            <div className="max-h-64 overflow-y-auto">
              {matches.length === 0 ? (
                <div className="px-2 py-3 text-center text-[12px] text-tx-mut">
                  No personas match “{query}”.
                </div>
              ) : (
                matches.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => choose(a)}
                    className={`w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-bd/[0.06] ${
                      active?.id === a.id ? "bg-accent/[0.08]" : ""
                    }`}
                  >
                    <span className="w-5 text-[10px] uppercase text-tx-mut flex-none">{a.name.slice(0, 2)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[12.5px] text-tx truncate">{a.name}</span>
                      <span className="block text-[10.5px] text-tx-mut truncate">{a.vibe}</span>
                    </span>
                  </button>
                ))
              )}
            </div>
          ) : (
            <div className="max-h-64 overflow-y-auto space-y-2">
              {variants.length > 0 && (
                <div>
                  <div className="px-1.5 pb-1 text-[10px] uppercase tracking-wide text-tx-mut">
                    Code Variants
                  </div>
                  <div className="grid grid-cols-2 gap-1.5">
                    {variants.map((a) => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => choose(a)}
                        title={a.vibe}
                        style={
                          active?.id === a.id && a.color
                            ? { boxShadow: `0 0 0 1.5px ${a.color}66` }
                            : undefined
                        }
                        className={`rounded-xl border p-2 text-left transition-all hover:-translate-y-0.5 ${
                          active?.id === a.id
                            ? "border-accent/50 bg-accent/[0.07]"
                            : "border-bd/[0.08] bg-bg hover:border-bd/[0.2]"
                        }`}
                      >
                        <span className="flex items-center gap-1.5 text-[12px] font-semibold text-tx">
                          <span className="text-[10px] uppercase text-tx-mut">{a.name.slice(0, 2)}</span>
                          <span className="truncate">{a.name}</span>
                        </span>
                        <span className="mt-0.5 block text-[10.5px] leading-tight text-tx-mut line-clamp-2">
                          {a.vibe}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {quickPicks.length > 0 && (
                <div>
                  <div className="px-1.5 pb-1 text-[10px] uppercase tracking-wide text-tx-mut">
                    Quick picks
                  </div>
                  {quickPicks.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => choose(a)}
                      className={`w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-bd/[0.06] ${
                        active?.id === a.id ? "bg-accent/[0.08]" : ""
                      }`}
                    >
                      <span className="w-5 text-[10px] uppercase text-tx-mut flex-none">{a.name.slice(0, 2)}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-[12.5px] text-tx truncate">{a.name}</span>
                        <span className="block text-[10.5px] text-tx-mut truncate">{a.vibe}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="mt-1.5 border-t border-bd/[0.07] pt-1.5 px-1">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onOpenLibrary();
              }}
              className="w-full flex items-center justify-between rounded-lg px-2 py-1.5 text-[11.5px] text-tx-mut hover:text-tx hover:bg-bd/[0.06] transition-colors"
            >
              Browse the full library ({agents.length})
              <svg className="w-3 h-3 flex-none" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18l6-6-6-6" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

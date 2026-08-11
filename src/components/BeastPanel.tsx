// Variant Arena — race one prompt across multiple coding variants side-by-side.
// Blocking POST to /arena; the judge picks a winner when all variants return.
// CSS-only animations (index.css .lt-* classes), no new dependencies.

import { useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface ArenaEntry {
  variant_id: string;
  name: string;
  emoji?: string;
  response?: string;
  error?: string;
  cost_usd: number;
  duration_ms: number;
}

interface ArenaResult {
  prompt: string;
  entries: ArenaEntry[];
  winner: string | null;
  reason: string;
  cost_aud: number;
  duration_ms: number;
  budget_exceeded?: boolean;
}

interface VariantDef {
  id: string;
  name: string;
  emoji: string;
  color: string;
  vibe: string;
}

// Fallback only: the live roster comes from the Agent Library's
// code-variants division (see fetch below), so new variants flow in.
const FALLBACK_ROSTER: VariantDef[] = [
  { id: "dacoder", name: "DaCoder", emoji: "\uD83E\uDDBE", color: "#7C3AED", vibe: "No-fear full-stack beast" },
  { id: "x-coder", name: "X-Coder", emoji: "\u26A1", color: "#06B6D4", vibe: "Minimal-diff speed demon" },
  { id: "deepcoder", name: "DeepCoder", emoji: "\uD83E\uDDE0", color: "#10B981", vibe: "Root-cause architect" },
  { id: "vibecoder", name: "VibeCoder", emoji: "\uD83C\uDFA8", color: "#F59E0B", vibe: "Frontend artist, anti-slop" },
];

const RACING_COPY = [
  "DaCoder is cooking…",
  "Judges reading diffs…",
  "X-Coder speedrunning…",
  "DeepCoder tracing the root cause…",
  "VibeCoder polishing pixels…",
  "Tallying the cost of victory…",
];

function pickCopy(): string {
  return RACING_COPY[Math.floor(Math.random() * RACING_COPY.length)];
}

function fmtSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function BeastPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const [prompt, setPrompt] = useState("");
  const [roster, setRoster] = useState<VariantDef[]>(FALLBACK_ROSTER);
  const [selected, setSelected] = useState<string[]>(FALLBACK_ROSTER.map((v) => v.id));
  const [racing, setRacing] = useState(false);
  const [result, setResult] = useState<ArenaResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copy, setCopy] = useState(RACING_COPY[0]);

  // Rotate the playful status copy while the blocking POST is in flight.
  useEffect(() => {
    if (!racing) return;
    const timer = window.setInterval(() => setCopy(pickCopy()), 1800);
    return () => window.clearInterval(timer);
  }, [racing]);

  // Single source of truth: pull Code Variants from the Agent Library.
  useEffect(() => {
    let ok = true;
    void apiFetch(`${API_BASE}/agents`)
      .then((r) => (r.ok ? r.json() : { agents: [] }))
      .then((d) => {
        if (!ok) return;
        const live: VariantDef[] = (Array.isArray(d.agents) ? d.agents : [])
          .filter((a: { division?: string }) => a.division === "code-variants")
          .map((a: { id: string; name: string; emoji?: string; color?: string; vibe?: string }) => ({
            id: a.id,
            name: a.name,
            emoji: a.name.slice(0, 2).toUpperCase(),
            color: a.color || "#7C3AED",
            vibe: a.vibe || "",
          }));
        if (live.length >= 2) {
          setRoster(live);
          // Re-sync the default "all in" selection unless the user
          // already trimmed it below the full fallback roster.
          setSelected((prev) =>
            prev.length === FALLBACK_ROSTER.length ? live.map((v) => v.id) : prev,
          );
        }
      })
      .catch(() => undefined);
    return () => {
      ok = false;
    };
  }, []);

  const toggleVariant = (id: string): void => {
    if (racing) return;
    setSelected((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  };

  const canRace = !racing && prompt.trim().length > 0 && selected.length >= 2;

  const race = (): void => {
    if (!canRace) return;
    setErr(null);
    setResult(null);
    setRacing(true);
    setCopy(pickCopy());
    void apiFetch(`${API_BASE}/arena`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: prompt.trim(), variants: selected }),
    })
      .then((r) =>
        r.ok
          ? r.json()
          : r.json().then((d) => Promise.reject(new Error(d?.detail ?? `HTTP ${r.status}`))),
      )
      .then((d: ArenaResult) => setResult(d))
      .catch((e: Error) => setErr(e.message))
      .finally(() => setRacing(false));
  };

  const raceAgain = (): void => {
    setResult(null);
    setErr(null);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/50 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[82vh] flex flex-col rounded-[22px] material-overlay border border-bd/[0.1] elev-3 overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-bd/[0.08]">
          <span className="lt-orb relative flex h-3 w-3 shrink-0">
            <span
              className={`absolute inline-flex h-full w-full rounded-full opacity-60 ${
                racing ? "bg-accent lt-ping" : "bg-tx-mut"
              }`}
            />
            <span
              className={`relative inline-flex h-3 w-3 rounded-full ${
                racing ? "bg-accent" : "bg-tx-mut"
              }`}
            />
          </span>
          <div>
            <div className="text-sm font-semibold text-tx">Variant Arena</div>
            <div className="text-[11px] text-tx-mut">
              {racing
                ? copy
                : "One prompt. The whole crew races it. The judge crowns a winner."}
            </div>
          </div>
          <div className="flex-1" />
          {result && (
            <span className="text-[11px] font-mono text-accent tabular-nums">
              ${result.cost_aud.toFixed(3)} AUD · {fmtSeconds(result.duration_ms)}
            </span>
          )}
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-tx-mut hover:text-tx rounded-lg p-1.5 hover:bg-bd/[0.06] press"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Form */}
          {!result && (
            <div className="space-y-2 fade-in-up">
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Drop one task — the variants race on it…"
                rows={3}
                disabled={racing}
                className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50 resize-none"
              />
              <div className="flex flex-wrap gap-2">
                {roster.map((v) => {
                  const on = selected.includes(v.id);
                  return (
                    <button
                      key={v.id}
                      type="button"
                      onClick={() => toggleVariant(v.id)}
                      disabled={racing}
                      title={v.vibe}
                      style={on ? { boxShadow: `0 0 0 1.5px ${v.color}55` } : undefined}
                      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] transition-all press disabled:opacity-40 ${
                        on
                          ? "border-bd/40 bg-bd/[0.06] text-tx"
                          : "border-bd/[0.08] bg-bg/40 text-tx-mut hover:text-tx-dim"
                      }`}
                    >
                      <span>{v.emoji}</span>
                      <span>{v.name}</span>
                    </button>
                  );
                })}
                <span className="self-center text-[11px] text-tx-mut">
                  {selected.length < 2 ? "Pick at least 2" : `${selected.length} racing`}
                </span>
              </div>
              {err && <div className="text-[12px] text-red-400">{err}</div>}
              <button
                type="button"
                onClick={race}
                disabled={!canRace}
                className="w-full rounded-lg bg-accent text-bg font-medium text-sm py-2.5 press disabled:opacity-40 disabled:cursor-not-allowed transition-transform hover:scale-[1.01]"
              >
                {racing ? copy : "Race the variants"}
              </button>
            </div>
          )}

          {/* Results */}
          {result && (
            <div className="space-y-3 fade-in-up">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {result.entries.map((e) => {
                  const def = roster.find((v) => v.id === e.variant_id);
                  const color = def?.color;
                  const isWinner = result.winner === e.variant_id;
                  return (
                    <div
                      key={e.variant_id}
                      className={`lt-step-in rounded-xl border bg-bg/40 overflow-hidden flex flex-col ${
                        isWinner ? "ring-2 ring-accent border-transparent" : "border-bd/[0.08]"
                      }`}
                    >
                      {color && <div className="h-0.5 shrink-0" style={{ backgroundColor: color }} />}
                      <div className="flex items-center gap-2 px-3 pt-2.5 pb-1.5">
                        <span className="text-[14px] leading-none">{e.emoji ?? def?.emoji ?? ""}</span>
                        <span className="text-[12px] font-semibold text-tx truncate flex-1">{e.name}</span>
                        {isWinner && (
                          <span className="lt-step-in rounded-full bg-accent text-bg text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5">
                            Winner
                          </span>
                        )}
                      </div>
                      <div className="px-3 pb-2 flex-1">
                        {e.error ? (
                          <div className="text-[12px] text-red-400 whitespace-pre-wrap break-words">
                            {e.error}
                          </div>
                        ) : e.response ? (
                          <div className="text-[12px] text-tx-dim whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
                            {e.response}
                          </div>
                        ) : (
                          <div className="text-[12px] text-tx-mut italic">(no output)</div>
                        )}
                      </div>
                      <div className="flex items-center justify-between px-3 py-1.5 border-t border-bd/[0.06] text-[11px] font-mono tabular-nums">
                        <span className="text-tx-mut">{fmtSeconds(e.duration_ms)}</span>
                        <span className="text-tx-mut">${e.cost_usd.toFixed(3)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>

              {result.reason && (
                <div className="rounded-lg border border-bd/[0.08] bg-bg/40 px-3 py-2 text-[12px] text-tx-dim italic">
                  “{result.reason}”
                </div>
              )}
              {result.budget_exceeded && (
                <div className="text-[12px] text-amber-400">
                  Budget exceeded — results were cut short.
                </div>
              )}
              <div className="flex items-center justify-between text-[11px] font-mono text-tx-mut tabular-nums">
                <span>
                  ${result.cost_aud.toFixed(3)} AUD · {fmtSeconds(result.duration_ms)}
                </span>
                <button
                  type="button"
                  onClick={raceAgain}
                  className="rounded-lg border border-bd/[0.1] px-3 py-1.5 text-[12px] font-sans text-tx-dim hover:text-tx hover:bg-bd/[0.06] transition-colors press"
                >
                  Race again
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { verbFor } from "../lib/agentVerbs";

// TurboActivity — a Cursor-"Composer"-style live activity panel.
//
// Design intent: show that the swarm is HARD AT WORK without over-explaining.
// Collapsed, it's a single confident line with a live pulse, the current agent,
// an elapsed timer and running cost. Expanded, it reveals a compact phase trace
// (Planner → Architect → Engineer → Critic …) accumulated on the client from the
// websocket's `agents_active` stream — no backend changes needed.

export interface TurboLive {
  status: string;
  cost: number;
  agents_active: string[];
}

interface TurboActivityProps {
  live: TurboLive;
  running: boolean;
  // Fallback cost when the ws hasn't pushed one yet (from the mission row).
  fallbackCost?: number;
}

interface Phase {
  agent: string;
  at: number; // ms epoch-ish (performance.now)
}

// The verb vocabulary lives in ../lib/agentVerbs so the mission list narrates
// the same moment with the same words.

export default function TurboActivity({
  live,
  running,
  fallbackCost = 0,
}: TurboActivityProps): JSX.Element | null {
  const [open, setOpen] = useState<boolean>(false);
  const [phases, setPhases] = useState<Phase[]>([]);
  const [elapsed, setElapsed] = useState<number>(0);
  const startRef = useRef<number | null>(null);
  const lastAgentRef = useRef<string>("");

  // Accumulate a phase whenever the lead active agent changes.
  useEffect(() => {
    if (!running) return;
    const lead = live.agents_active[0] ?? "";
    if (lead && lead !== lastAgentRef.current) {
      lastAgentRef.current = lead;
      setPhases((p) =>
        p.length && p[p.length - 1].agent === lead
          ? p
          : [...p, { agent: lead, at: performance.now() }],
      );
    }
  }, [live.agents_active, running]);

  // Elapsed timer (ticks while running). Reset when a fresh run begins.
  useEffect(() => {
    if (running) {
      if (startRef.current === null) startRef.current = performance.now();
      const id = window.setInterval(() => {
        if (startRef.current !== null) {
          setElapsed((performance.now() - startRef.current) / 1000);
        }
      }, 200);
      return () => window.clearInterval(id);
    }
    // stopped: freeze, and reset the accumulator for next time
    startRef.current = null;
    lastAgentRef.current = "";
    return;
  }, [running]);

  // Reset the trace when a run ends so the next mission starts clean.
  useEffect(() => {
    if (!running) {
      const t = window.setTimeout(() => {
        setPhases([]);
        setElapsed(0);
      }, 1200);
      return () => window.clearTimeout(t);
    }
    return;
  }, [running]);

  const lead = live.agents_active[0];
  const cost = live.cost || fallbackCost;
  const elapsedLabel = useMemo(() => {
    const s = Math.floor(elapsed);
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  }, [elapsed]);

  if (!running && phases.length === 0) return null;

  return (
    <div className="turbo-panel select-none rounded-xl border border-accent/20 bg-surface/80 overflow-hidden">
      {/* Header line — collapsed summary, always visible */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-bd/[0.03] transition-colors"
      >
        <span className="turbo-orb relative flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inline-flex h-full w-full rounded-full bg-accent opacity-60 turbo-ping" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
        </span>

        <span className="flex-1 min-w-0 text-sm text-tx truncate">
          {running ? (
            <>
              <span className="text-accent font-medium">{lead ?? "Swarm"}</span>
              <span className="text-tx-dim"> is {verbFor(lead)}</span>
              <span className="turbo-cursor text-accent">▍</span>
            </>
          ) : (
            <span className="text-tx-dim">Swarm finished</span>
          )}
        </span>

        <span className="shrink-0 flex items-center gap-2.5 text-[11px] font-mono text-tx-mut">
          <span className="tabular-nums">{elapsedLabel}</span>
          {cost > 0 && <span className="tabular-nums">${cost.toFixed(3)}</span>}
          <svg
            className={`w-3 h-3 transition-transform ${open ? "rotate-180" : ""}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>

      {/* Expanded phase trace */}
      {open && (
        <div className="border-t border-bd/[0.06] px-3.5 py-2.5 space-y-1.5">
          {phases.length === 0 && (
            <p className="text-xs text-tx-mut">Spinning up the swarm…</p>
          )}
          {phases.map((p, i) => {
            const isLast = i === phases.length - 1;
            const activeNow = running && isLast;
            return (
              <div
                key={`${p.agent}-${i}`}
                className="turbo-step flex items-center gap-2.5 text-xs"
                style={{ animationDelay: `${Math.min(i * 40, 240)}ms` }}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                    activeNow ? "bg-accent turbo-ping-dot" : "bg-tx-mut/50"
                  }`}
                />
                <span className={activeNow ? "text-tx" : "text-tx-dim"}>
                  {p.agent}
                </span>
                <span className="text-tx-mut">{verbFor(p.agent)}</span>
                {activeNow && (
                  <span className="ml-auto flex items-end gap-0.5">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

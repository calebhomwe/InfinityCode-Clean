import { useEffect, useMemo, useState } from "react";
import type { MissionEvent } from "../hooks/useWebSocket";
import { verbFor } from "../lib/agentVerbs";
import { apiFetch, API_BASE  } from "../lib/api";
import DeploymentChips from "./DeploymentChips";

// SwarmPanel — what the swarm is actually made of, and what it just did.
//
// This replaces the old SwarmMap, which rendered a hardcoded array of six
// fictional agents. The lesson from that: do not invent a cast. Everything
// here comes from somewhere real —
//
//   * the pipeline is built from live `phase` and `attempt` events, so it only
//     ever shows stages this mission genuinely walked;
//   * the council comes from GET /api/v1/swarm/council, which is derived from
//     the router itself, so it cannot drift from what actually runs.
//
// Note the 245 agents in /api/v1/agents are chat *personas*, not mission
// workers. They are deliberately not shown here.

interface CouncilRole {
  role: string;
  primary: string | null;
  chain: string[];
  depth: number;
}

interface CouncilResponse {
  roles: CouncilRole[];
  count: number;
  problems: string[];
}

export interface SwarmPanelProps {
  events: MissionEvent[];
  running: boolean;
  missedEvents?: boolean;
  /** Live role → model deployment map from the mission WebSocket. */
  roleModels?: Record<string, string>;
  /** Agent names currently working (drives the pulsing chips). */
  agentsActive?: string[];
}

interface Stage {
  agent: string;
  at: number;
  endedAt: number | null;
  score: number | null;
}

/** Fold the raw event stream into the ordered stages a mission walked. */
function buildStages(events: MissionEvent[]): Stage[] {
  const stages: Stage[] = [];
  for (const event of events) {
    if (event.type === "phase") {
      // A phase with no lead means the run cleared its active set — close the
      // open stage rather than opening an empty one.
      const previous = stages[stages.length - 1];
      if (previous && previous.endedAt === null) {
        previous.endedAt = event.at;
      }
      if (event.lead) {
        stages.push({ agent: event.lead, at: event.at, endedAt: null, score: null });
      }
    } else if (event.type === "attempt" && typeof event.score === "number") {
      // Attach the score to whichever stage was open when it landed.
      const open = stages[stages.length - 1];
      if (open) {
        open.score = event.score;
      }
    }
  }
  return stages;
}

function duration(from: number, to: number | null, now: number): string {
  const seconds = Math.max(0, ((to ?? now) - from));
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export default function SwarmPanel({
  events,
  running,
  missedEvents = false,
  roleModels = {},
  agentsActive = [],
}: SwarmPanelProps): JSX.Element {
  const [council, setCouncil] = useState<CouncilRole[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [showCouncil, setShowCouncil] = useState<boolean>(false);
  const [partialProgress, setPartialProgress] = useState<{ completed: number; total: number } | null>(null);
  // Drives the live elapsed counter on the open stage.
  const [now, setNow] = useState<number>(() => Date.now() / 1000);

  useEffect(() => {
    let cancelled = false;
    void apiFetch(`${API_BASE}/swarm/council`)
      .then((r) => (r.ok ? r.json() : { roles: [], problems: [] }))
      .then((data: CouncilResponse) => {
        if (cancelled) return;
        setCouncil(Array.isArray(data.roles) ? data.roles : []);
        setProblems(Array.isArray(data.problems) ? data.problems : []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now() / 1000), 500);
    return () => window.clearInterval(id);
  }, [running]);

  useEffect(() => {
    const partials = events.filter((e) => e.type === "partial");
    if (partials.length === 0) {
      setPartialProgress(null);
      return;
    }
    const last = partials[partials.length - 1];
    if (typeof last.lambdaCompleted === "number") {
      const total = Math.round(partials.length / last.lambdaCompleted);
      setPartialProgress({ completed: partials.length, total });
    }
  }, [events]);

  const stages = useMemo(() => buildStages(events), [events]);

  return (
    <div className="rounded-xl border border-bd/[0.07] bg-surface/60 overflow-hidden">
      <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-bd/[0.06]">
        <div className="flex items-center gap-2 flex-1">
          <h2 className="text-xs font-medium uppercase tracking-widest text-tx-mut">
            {running ? (
              <span className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
                Swarm Active
              </span>
            ) : stages.length > 0 ? (
              "Swarm Complete"
            ) : (
              "Swarm"
            )}
          </h2>
          {running && stages.length > 0 && (
            <span className="text-[10px] text-tx-dim">
              {stages.filter(s => s.endedAt === null).length} of {stages.length} stages
            </span>
          )}
        </div>
        {partialProgress && (
          <span className="text-[10px] font-mono text-accent bg-accent/10 px-1.5 py-0.5 rounded">
            {partialProgress.completed}/{partialProgress.total}
          </span>
        )}
        {missedEvents && (
          <span
            title="The server dropped frames for this client; the timeline may have gaps."
            className="text-[10px] text-warning"
          >
            gaps
          </span>
        )}
        <button
          type="button"
          onClick={() => setShowCouncil((v) => !v)}
          className="text-[11px] text-tx-mut hover:text-tx transition-colors"
        >
          {showCouncil ? "pipeline" : `council (${council.length})`}
        </button>
      </div>

      {/* Live deployment badges: which model each agent role is actually
          running (K3 Architect, 2.6 Inspector, 35B Engineer…). */}
      <DeploymentChips
        roleModels={roleModels}
        agentsActive={agentsActive}
        running={running}
      />

      {showCouncil ? (
        <div className="px-3.5 py-2.5 space-y-1.5">
          {council.length === 0 && (
            <p className="text-xs text-tx-mut">Council unavailable.</p>
          )}
          {council.map((role) => (
            <div key={role.role} className="flex items-baseline gap-2 text-xs">
              <span className="text-tx w-20 shrink-0">{role.role}</span>
              <span className="font-mono text-[11px] text-tx-dim truncate flex-1">
                {role.primary ?? "unbound"}
              </span>
              {role.depth > 1 && (
                <span
                  className="text-[10px] text-tx-mut shrink-0"
                  title={role.chain.join("  →  ")}
                >
                  +{role.depth - 1}
                </span>
              )}
            </div>
          ))}
          {problems.length > 0 && (
            <p className="text-[11px] text-error pt-1">
              {problems.length} routing problem(s)
            </p>
          )}
        </div>
      ) : (
        <div className="px-3.5 py-2.5 space-y-1.5">
          {stages.length === 0 && (
            <p className="text-xs text-tx-mut">
              {running ? "Spinning up the swarm…" : "No activity yet."}
            </p>
          )}
          {stages.map((stage, index) => {
            const isOpen = stage.endedAt === null;
            const activeNow = running && isOpen && index === stages.length - 1;
            return (
              <div
                key={`${stage.agent}-${stage.at}-${index}`}
                className="flex items-center gap-2.5 text-xs"
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                    activeNow ? "bg-accent animate-pulse" : "bg-tx-mut/50"
                  }`}
                />
                <span className={activeNow ? "text-tx" : "text-tx-dim"}>
                  {stage.agent}
                </span>
                <span className="text-tx-mut truncate">{verbFor(stage.agent)}</span>
                <span className="ml-auto shrink-0 flex items-center gap-2 font-mono text-[10px] text-tx-mut">
                  {stage.score !== null && (
                    <span
                      className={
                        stage.score >= 0.8
                          ? "text-success"
                          : stage.score >= 0.5
                            ? "text-warning"
                            : "text-error"
                      }
                    >
                      {stage.score.toFixed(2)}
                    </span>
                  )}
                  <span className="tabular-nums">
                    {duration(stage.at, stage.endedAt, now)}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import { modelInfoFor } from "./SwarmDeploymentBadge";

// DeploymentChips — the live "who is actually deployed right now" row.
//
// Renders one chip per agent role that has served the mission so far, labelled
// by the MODEL that answered (K3 Architect, 2.6 Inspector, 35B Engineer…), so
// the user can see the swarm deploy real models — including when the local
// 35B brain is doing the work vs. a cloud swarm (Kimi / GLM / MiniMax).
//
// Data is real: role_models streams over the mission WebSocket (sync frames +
// attempt events). No invented cast — a role appears only after it answered.

export interface DeploymentChipsProps {
  roleModels: Record<string, string>;
  agentsActive: string[];
  running: boolean;
}

const ROLE_TITLE: Record<string, string> = {
  architect: "Architect",
  engineer: "Engineer",
  inspector: "Inspector",
  debugger: "Debugger",
  eye: "Eye",
  artist: "Artist",
  creative: "Creative",
  worker: "Worker",
};

// Which live agent names mean this role is the one working right now.
const ROLE_TO_AGENT: Record<string, string[]> = {
  architect: ["Engineer"],
  engineer: ["Engineer"],
  debugger: ["Engineer"],
  worker: ["Director", "Tester"],
  inspector: ["Inspector", "Critic", "Tester"],
  eye: ["Critic"],
  artist: ["Artist"],
  creative: ["Scribe"],
};

const TIER_CHIP: Record<string, string> = {
  local: "text-success border-success/30 bg-success/10",
  premium: "text-warning border-warning/30 bg-warning/10",
  standard: "text-info border-info/25 bg-info/10",
  free: "text-tx-dim border-bd/[0.12] bg-surface",
};

const TIER_DOT: Record<string, string> = {
  local: "bg-success",
  premium: "bg-warning",
  standard: "bg-info",
  free: "bg-tx-mut",
};

export default function DeploymentChips({
  roleModels,
  agentsActive,
  running,
}: DeploymentChipsProps): JSX.Element | null {
  const entries = Object.entries(roleModels).filter(([, model]) => Boolean(model));
  if (entries.length === 0) return null;

  const isActive = (role: string): boolean => {
    if (!running) return false;
    const targets = ROLE_TO_AGENT[role] ?? [];
    return agentsActive.some((agent) =>
      targets.some((target) => agent.startsWith(target)),
    );
  };

  // Active roles first, then alphabetical — stable and scannable mid-run.
  const sorted = [...entries].sort(([a], [b]) => {
    const delta = Number(isActive(b)) - Number(isActive(a));
    return delta !== 0 ? delta : a.localeCompare(b);
  });

  const anyLocal = entries.some(([, model]) => model.startsWith("local"));

  return (
    <div className="flex items-center gap-1.5 flex-wrap px-3.5 py-2 border-b border-bd/[0.06]">
      <span
        className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${
          anyLocal ? TIER_CHIP.local : TIER_CHIP.premium
        }`}
        title={
          anyLocal
            ? "Running on your GPU (zero cost) — cloud swarm backing it up"
            : "Local server offline — cloud orchestration (Kimi swarm + MiniMax M3 + GLM-5)"
        }
      >
        <span
          className={`h-1 w-1 rounded-full ${anyLocal ? "bg-success" : "bg-warning"} ${
            running ? "animate-pulse" : ""
          }`}
        />
        {anyLocal ? "Local 35B" : "Cloud"}
      </span>
      {sorted.map(([role, model]) => {
        const info = modelInfoFor(model);
        const tier = info?.tier ?? "standard";
        const active = isActive(role);
        return (
          <span
            key={role}
            title={`${ROLE_TITLE[role] ?? role} → ${model}`}
            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${
              TIER_CHIP[tier] ?? TIER_CHIP.standard
            } ${active ? "ring-1 ring-accent/40" : "opacity-80"}`}
          >
            <span
              className={`h-1 w-1 rounded-full ${TIER_DOT[tier] ?? TIER_DOT.standard} ${
                active ? "animate-pulse" : ""
              }`}
            />
            {info?.shortLabel ?? model} {ROLE_TITLE[role] ?? role}
          </span>
        );
      })}
    </div>
  );
}

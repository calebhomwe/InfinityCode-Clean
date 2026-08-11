import type { Mission } from "../hooks/useMissions";
import { missionStatusLabel } from "../lib/missionStatus";

function Chip({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "accent" }): JSX.Element {
  return (
    <span className={`rounded-md border px-2 py-1 text-[10px] font-mono ${
      tone === "accent"
        ? "border-accent/30 bg-accent/[0.08] text-accent"
        : "border-bd/[0.10] bg-bd/[0.04] text-tx-dim"
    }`}>
      {children}
    </span>
  );
}

/**
 * The plain-English mission contract. It is deliberately compact: the live
 * Swarm and Evidence panels below it hold the detailed trace, while this makes
 * the job, its setup and its next decision legible at a glance.
 */
export default function MissionBrief({ mission }: { mission: Mission }): JSX.Element {
  const params = mission.params ?? {};
  const agents = params.agents ?? [];
  const tools = params.tools ?? [];
  const attempts = mission.evidence?.attempts ?? [];
  const active = mission.status === "queued" || mission.status === "running";
  const completed = mission.status === "completed";
  const failed = mission.status === "failed" || mission.status === "rejected";
  const decision = active
    ? "Working through the plan. Live stages and evidence appear below."
    : completed
      ? "Result is ready for review. Approve it or reject it with feedback."
      : failed
        ? "This run needs a retry or a narrower follow-up mission."
        : "This mission is archived.";

  return (
    <section className="rounded-xl border border-bd/[0.08] bg-surface/70 p-4 fade-in-up">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-tx-mut">Mission brief</p>
        <Chip tone={active ? "accent" : "muted"}>{missionStatusLabel(mission.status)}</Chip>
      </div>
      <p className="mt-2 text-sm leading-6 text-tx-dim whitespace-pre-wrap">{mission.goal}</p>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Chip tone="accent">{params.mode ?? mission.mode ?? "auto"}</Chip>
        <Chip>{params.effort ?? mission.effort ?? "med"} effort</Chip>
        {params.fast && <Chip>fast lane</Chip>}
        {params.vision_loop && <Chip>visual checks</Chip>}
        {params.speculative && <Chip>exploration on</Chip>}
        {params.combine_with_default_swarm !== false && <Chip>core swarm</Chip>}
        {mission.reference_image_path && <Chip>reference attached</Chip>}
      </div>

      {(agents.length > 0 || tools.length > 0) && (
        <div className="mt-3 grid gap-3 border-t border-bd/[0.07] pt-3 sm:grid-cols-2">
          {agents.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-tx-mut">Specialists</p>
              <p className="mt-1 text-xs text-tx-dim break-words">{agents.join(" · ")}</p>
            </div>
          )}
          {tools.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-tx-mut">Allowed tools</p>
              <p className="mt-1 text-xs text-tx-dim break-words">{tools.join(" · ")}</p>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between gap-3 border-t border-bd/[0.07] pt-3 text-xs">
        <span className="text-tx-dim">{decision}</span>
        <span className="shrink-0 font-mono text-tx-mut">{attempts.length} attempt{attempts.length === 1 ? "" : "s"}</span>
      </div>
    </section>
  );
}

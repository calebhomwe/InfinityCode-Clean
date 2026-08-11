import { useMemo, useState } from "react";
import type { Mission } from "../hooks/useMissions";
import { API_BASE, API_ORIGIN } from "../lib/api";
import { verbFor } from "../lib/agentVerbs";
import { missionStatusLabel } from "../lib/missionStatus";

interface StatusStyle {
  dot: string;
  pulse: boolean;
}

const STATUS_STYLES: Record<string, StatusStyle> = {
  queued: { dot: "bg-tx-mut/40", pulse: false },
  running: { dot: "bg-warning", pulse: true },
  completed: { dot: "bg-success", pulse: false },
  failed: { dot: "bg-error", pulse: false },
  approved: { dot: "bg-success", pulse: false },
  rejected: { dot: "bg-warning", pulse: false },
};

const DEFAULT_STYLE: StatusStyle = STATUS_STYLES.queued;

// Matches the backend's default PASS_THRESHOLD. Used only to colour the
// sparkline, so drifting slightly out of sync is cosmetic, not wrong.
const PASS_THRESHOLD = 0.8;

/**
 * The attempt-score trajectory, drawn small enough to live on a list row.
 *
 * This is the whole point of the card: a mission that clawed its way up from
 * 0.35 to 0.91 across four attempts has a *story*, and until now the sidebar
 * rendered that story as the word "completed".
 */
function Sparkline({ scores }: { scores: number[] }): JSX.Element | null {
  if (scores.length < 2) {
    return null;
  }
  const width = 34;
  const height = 12;
  const last = scores[scores.length - 1];
  // Scores are 0..1, so the scale is fixed rather than data-relative — a flat
  // run of 0.9s should look high, not look like a flat line at mid-height.
  const points = scores
    .map((score, index) => {
      const x = (index / (scores.length - 1)) * (width - 2) + 1;
      const y = height - 1 - Math.max(0, Math.min(1, score)) * (height - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const stroke =
    last >= PASS_THRESHOLD
      ? "var(--success, #4ade80)"
      : last >= 0.5
        ? "var(--warning, #fbbf24)"
        : "var(--error, #f87171)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="shrink-0 opacity-80"
      aria-hidden="true"
    >
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth="1.2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** A tiny labelled signal. Only rendered when there is something to say. */
function Chip({
  title,
  tone = "mut",
  children,
}: {
  title: string;
  tone?: "mut" | "good" | "bad";
  children: React.ReactNode;
}): JSX.Element {
  const toneClass =
    tone === "good" ? "text-success" : tone === "bad" ? "text-error" : "text-tx-mut";
  return (
    <span title={title} className={`inline-flex items-center gap-0.5 ${toneClass}`}>
      {children}
    </span>
  );
}

export interface MissionCardProps {
  mission: Mission;
  selected: boolean;
  onSelect: (missionId: string) => void;
  onChanged: () => void;
  /** Lead agent right now, for the selected mission only. Drives the live verb. */
  livePhase?: string | null;
}

export default function MissionCard({
  mission,
  selected,
  onSelect,
  onChanged,
  livePhase = null,
}: MissionCardProps): JSX.Element {
  const [expanded, setExpanded] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  // Inline reject-feedback form (replaces the old window.prompt).
  const [rejecting, setRejecting] = useState<boolean>(false);
  const [rejectFeedback, setRejectFeedback] = useState<string>("");

  const style = STATUS_STYLES[mission.status] ?? DEFAULT_STYLE;
  const attempts = mission.evidence?.attempts ?? [];

  // Everything below is already fetched with the mission list every 1.5s — it
  // was simply being thrown away at render time in favour of the status word.
  const signal = useMemo(() => {
    const scores = attempts
      .map((attempt) => attempt.critique?.overall)
      .filter((score): score is number => typeof score === "number");
    const raced = attempts.find((attempt) => attempt.tournament && attempt.candidates);
    const redteam = [...attempts].reverse().find((attempt) => attempt.redteam)?.redteam;
    return {
      scores,
      best: scores.length ? Math.max(...scores) : null,
      candidates: raced?.candidates?.length ?? 0,
      winner: raced?.winner_letter ?? null,
      redteam,
      hasAny:
        scores.length > 0 || (raced?.candidates?.length ?? 0) > 0 || redteam != null,
    };
  }, [attempts]);

  const isRunning = mission.status === "running";
  // While running, narrate what the swarm is doing instead of restating the
  // status the dot already conveys.
  const subtitle = isRunning && livePhase
    ? `${livePhase} ${verbFor(livePhase)}`
    : missionStatusLabel(mission.status);

  const postAction = async (
    action: "approve" | "reject",
    feedback?: string
  ): Promise<boolean> => {
    setBusy(true);
    setActionError(null);
    try {
      const response = await fetch(
        `${API_BASE}/missions/${mission.id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: action === "reject" ? JSON.stringify({ feedback: feedback ?? "" }) : "{}",
        }
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      onChanged();
      return true;
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : `Failed to ${action} mission`
      );
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`rounded-lg px-3 py-2.5 cursor-pointer transition-colors border-l-2 hover:bg-bd/[0.04] card-lift ${
        selected
          ? "bg-bd/[0.06] border-accent"
          : "border-transparent"
      }`}
      onClick={() => {
        onSelect(mission.id);
        setExpanded((previous) => !previous);
      }}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span
          className={`h-1.5 w-1.5 rounded-full shrink-0 ${style.dot} ${
            style.pulse ? "animate-pulse badge-glow" : ""
          }`}
        />
        <h3 className="text-sm text-tx truncate flex-1">{mission.title}</h3>
        <Sparkline scores={signal.scores} />
      </div>

      <p className="text-xs text-tx-mut mt-0.5 pl-3.5 font-mono truncate">
        {isRunning && livePhase ? (
          <span className="text-accent">{subtitle}</span>
        ) : (
          subtitle
        )}
        {" · "}${mission.total_cost_aud.toFixed(3)} AUD
      </p>

      {/* The run's receipts: how hard it worked, and whether it held up. */}
      {signal.hasAny && (
        <div className="mt-1 pl-3.5 flex items-center gap-2.5 text-[10px] font-mono">
          {signal.scores.length > 0 && (
            <Chip
              title={`${signal.scores.length} scored attempt(s), best ${signal.best?.toFixed(2)}`}
              tone={
                (signal.best ?? 0) >= PASS_THRESHOLD ? "good" : "mut"
              }
            >
              {signal.scores.length}×{" "}
              <span className="tabular-nums">{signal.best?.toFixed(2)}</span>
            </Chip>
          )}
          {signal.candidates > 0 && (
            <Chip
              title={`Tournament: ${signal.candidates} strategies raced${
                signal.winner ? `, ${signal.winner} won` : ""
              }`}
            >
              Race {signal.candidates}
              {signal.winner && <span className="text-accent">{signal.winner}</span>}
            </Chip>
          )}
          {signal.redteam && (
            <Chip
              title={`Red team: ${
                signal.redteam.total - signal.redteam.failed
              }/${signal.redteam.total} attacks survived`}
              tone={signal.redteam.passed_all ? "good" : "bad"}
            >
              Test {signal.redteam.total - signal.redteam.failed}/{signal.redteam.total}
            </Chip>
          )}
        </div>
      )}

      {expanded && (
        <div
          className="mt-2 pt-2 border-t border-bd/[0.07] space-y-2"
          onClick={(event) => event.stopPropagation()}
        >
          <p className="text-xs text-tx-dim whitespace-pre-wrap">
            {mission.goal}
          </p>

          {attempts.length > 0 && (
            <ul className="space-y-1">
              {attempts.map((attempt) => (
                <li
                  key={attempt.attempt}
                  className="text-xs text-tx-mut flex items-center gap-2"
                >
                  <span className="font-mono">#{attempt.attempt}</span>
                  <span>
                    exit {attempt.returncode ?? "?"}
                    {attempt.critique
                      ? `, score ${attempt.critique.overall.toFixed(2)}`
                      : ""}
                  </span>
                  {attempt.image_url && (
                    <a
                      href={`${API_ORIGIN}${attempt.image_url}`}
                      target="_blank"
                      rel="noreferrer"
                      className="accent-text hover:underline"
                    >
                      image
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}

          {mission.evidence?.failure_reason && (
            <p className="text-xs text-error">
              {mission.evidence.failure_reason}
            </p>
          )}

          {actionError && <p className="text-xs text-error">{actionError}</p>}

          {mission.status === "completed" && !rejecting && (
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  void postAction("approve");
                }}
                className="rounded-lg border border-success/20 bg-success/10 text-success hover:bg-success/15 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
              >
                Approve
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setActionError(null);
                  setRejectFeedback("");
                  setRejecting(true);
                }}
                className="rounded-lg border border-error/20 bg-error/10 text-error hover:bg-error/15 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
              >
                Reject…
              </button>
            </div>
          )}

          {/* Inline reject feedback — replaces the old native window.prompt. */}
          {mission.status === "completed" && rejecting && (
            <div className="space-y-2">
              <textarea
                autoFocus
                rows={2}
                value={rejectFeedback}
                onChange={(event) => setRejectFeedback(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setRejecting(false);
                    setRejectFeedback("");
                  }
                }}
                placeholder="Why are you rejecting this result?"
                disabled={busy}
                className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-xs text-tx placeholder:text-tx-mut focus:outline-none focus:border-accent/50 resize-none"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    // Empty feedback is allowed, matching the old prompt's
                    // OK-with-empty-text path (cancel = no reject at all).
                    void postAction("reject", rejectFeedback.trim()).then((ok) => {
                      if (ok) {
                        setRejecting(false);
                        setRejectFeedback("");
                      }
                    });
                  }}
                  className="rounded-lg border border-error/20 bg-error/10 text-error hover:bg-error/15 px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
                >
                  Send feedback
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setRejecting(false);
                    setRejectFeedback("");
                  }}
                  className="rounded-lg border border-bd/[0.12] px-3 py-1.5 text-xs font-medium text-tx-dim hover:bg-bd/[0.06] transition-colors disabled:opacity-40"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

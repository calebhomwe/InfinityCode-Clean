import { ReactNode, useState } from "react";
import type {
  DrillApplication,
  Mission,
  MissionEvidenceAttempt,
} from "../hooks/useMissions";
import { API_ORIGIN } from "../lib/api";

export interface EvidencePanelProps {
  mission: Mission | null;
  // Live screenshot pushed over the WebSocket while a mission runs — lets the
  // "planning…" dead-time show real, updating progress instead of a static line.
  liveScreenshot?: string | null;
}

function resolveAsset(url: string): string {
  if (/^(https?:|data:|blob:)/.test(url)) return url;
  return `${API_ORIGIN}${url.startsWith("/") ? "" : "/"}${url}`;
}

interface ScoreBarProps {
  label: string;
  value: number;
}

function ScoreBar({ label, value }: ScoreBarProps): JSX.Element {
  const percent = Math.min(100, Math.max(0, value * 100));
  return (
    <div>
      <div className="flex justify-between text-xs text-tx-dim mb-1">
        <span>{label}</span>
        <span className="font-mono">{value.toFixed(2)}</span>
      </div>
      <div className="h-1 rounded-full bg-bd/[0.06] overflow-hidden">
        <div
          className={`h-full rounded-full ${
            value >= 0.8 ? "bg-success" : value >= 0.5 ? "bg-warning" : "bg-error"
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

interface FeedRowProps {
  letter: string;
  name: string;
  children: ReactNode;
}

/** One agent-attributed message in the mission timeline. */
function FeedRow({ letter, name, children }: FeedRowProps): JSX.Element {
  return (
    <div className="flex gap-3 fade-in-up">
      <span className="flex-none h-6 w-6 mt-0.5 rounded-full bg-accent/10 accent-text text-[10px] font-medium font-mono flex items-center justify-center select-none">
        {letter}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-tx">{name}</p>
        <div className="mt-1 text-sm text-tx-dim">{children}</div>
      </div>
    </div>
  );
}

function fileName(path: string | undefined, fallback: string): string {
  if (!path) {
    return fallback;
  }
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || fallback;
}

function clamp(text: string | undefined, max: number): string {
  if (!text) {
    return "";
  }
  const trimmed = text.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

function isVideoArtifact(attempt: MissionEvidenceAttempt): boolean {
  const url = attempt.image_url;
  if (!url) return false;
  if (attempt.mediaType === "video" || attempt.media_type === "video") return true;
  const lower = url.toLowerCase();
  return lower.endsWith(".mp4") || lower.endsWith(".webm");
}

function AttemptArtifact({
  attempt,
}: {
  attempt: MissionEvidenceAttempt;
}): JSX.Element | null {
  if (!attempt.image_url) {
    return null;
  }
  const src = `${API_ORIGIN}${attempt.image_url}`;
  if (isVideoArtifact(attempt)) {
    return (
      <video
        src={src}
        controls
        className="mt-3 rounded-lg border border-bd/[0.06] bg-bg max-h-72 w-full object-contain"
      />
    );
  }
  return (
    <img
      src={src}
      alt={`Attempt ${attempt.attempt} output`}
      className="mt-3 rounded-lg border border-bd/[0.06] bg-bg max-h-72 object-contain"
    />
  );
}

function CriticRow({
  critique,
}: {
  critique: NonNullable<MissionEvidenceAttempt["critique"]>;
}): JSX.Element {
  return (
    <FeedRow letter="C" name="Critic">
      <div className="flex items-center gap-2">
        <span
          className={`text-lg font-medium font-mono ${
            critique.overall >= 0.8
              ? "text-success"
              : critique.overall >= 0.5
                ? "text-warning"
                : "text-error"
          }`}
        >
          {critique.overall.toFixed(2)}
        </span>
        <span className="text-xs text-tx-mut">overall match</span>
      </div>
      <div className="mt-3 space-y-2 max-w-sm">
        <ScoreBar label="Composition" value={critique.composition} />
        <ScoreBar label="Color" value={critique.color} />
        <ScoreBar label="Style match" value={critique.style_match} />
        <ScoreBar label="Technical execution" value={critique.technical_execution} />
      </div>
      {critique.fixes.length > 0 && (
        <ol className="mt-3 list-decimal list-inside space-y-1 marker:text-tx-mut">
          {critique.fixes.map((fix, index) => (
            <li key={index}>{fix}</li>
          ))}
        </ol>
      )}
    </FeedRow>
  );
}

function DrillRow({ drills }: { drills: DrillApplication[] }): JSX.Element | null {
  if (drills.length === 0) return null;

  return (
    <FeedRow letter="B" name="Benchmark drills">
      <p className="text-xs text-tx-dim">
        Applied learned checks from earlier benchmark failures before this run.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {drills.map((drill) => (
          <span
            key={drill.id}
            title={drill.failure_reason}
            className="rounded-full border border-accent/20 bg-accent/10 px-2 py-0.5 text-[10px] font-mono accent-text"
          >
            {drill.capability ?? "general"}: {drill.task_id}
          </span>
        ))}
      </div>
    </FeedRow>
  );
}

export default function EvidencePanel({
  mission,
  liveScreenshot = null,
}: EvidencePanelProps): JSX.Element {
  const [logsOpenFor, setLogsOpenFor] = useState<number | null>(null);

  if (!mission) {
    return (
      <div className="text-sm text-tx-mut">
        Select a mission to inspect its evidence.
      </div>
    );
  }

  const attempts: MissionEvidenceAttempt[] = mission.evidence?.attempts ?? [];
  const missionMode: string =
    mission.params?.mode ?? mission.mode ?? "auto";

  if (attempts.length === 0) {
    const isActive = mission.status === "running" || mission.status === "queued";
    return (
      <div className="space-y-3">
        <div className="text-sm text-tx-mut">
          {mission.evidence?.failure_reason
            ? mission.evidence.failure_reason
            : isActive
              ? "Planning and generating — the first attempt will appear here shortly."
              : "This mission finished without producing evidence."}
        </div>
        {isActive && liveScreenshot && (
          <div className="fade-in-up rounded-xl border border-bd/[0.06] bg-bg overflow-hidden">
            <img
              src={resolveAsset(liveScreenshot)}
              alt="Live preview"
              className="w-full max-h-80 object-contain"
            />
            <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] text-tx-mut border-t border-bd/[0.06]">
              <span className="h-1.5 w-1.5 rounded-full bg-accent turbo-ping-dot" />
              live preview
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {attempts.map((attempt) => {
        const mode: string = attempt.mode ?? missionMode;
        const isImage = mode === "image";
        const isVideo = mode === "video" || attempt.mediaType === "video" || attempt.media_type === "video";
        const is3d = mode === "3d";
        const exitOk = attempt.returncode === 0;
        const logs = `--- stdout ---\n${attempt.stdout ?? ""}\n--- stderr ---\n${attempt.stderr ?? ""}`;
        const logsOpen = logsOpenFor === attempt.attempt;

        return (
          <div key={attempt.attempt} className="space-y-4">
            <div className="flex items-center gap-3">
              <span className="text-[10px] font-medium uppercase tracking-widest text-tx-mut whitespace-nowrap">
                Attempt {attempt.attempt}
                {typeof attempt.cost_aud === "number"
                  ? ` · $${attempt.cost_aud.toFixed(3)} AUD`
                  : ""}
              </span>
              <span className="h-px flex-1 bg-bd/[0.06]" />
            </div>

            {attempt.plan && (
              <FeedRow letter="D" name="Director">
                <p className="whitespace-pre-wrap">{clamp(attempt.plan, 280)}</p>
              </FeedRow>
            )}

            <DrillRow drills={attempt.drills_applied ?? []} />

            {isVideo ? (
              <FeedRow letter="V" name="Video artist">
                {attempt.image_url ? (
                  <>
                    <p>Generated a frame-to-frame clip.</p>
                    <AttemptArtifact attempt={attempt} />
                  </>
                ) : (
                  <div className="rounded-lg border border-error/20 bg-error/[0.06] p-3 text-error">
                    <p>{attempt.video_error ?? "No video produced."}</p>
                    <p className="mt-1 text-xs text-tx-mut">Check the FAL provider key and both frame files, then retry.</p>
                  </div>
                )}
              </FeedRow>
            ) : isImage ? (
              // Image generation: the Artist produces the image directly.
              <FeedRow letter="A" name="Artist">
                {attempt.image_url ? (
                  <>
                    <p>Generated an image.</p>
                    <AttemptArtifact attempt={attempt} />
                  </>
                ) : (
                  <span className="rounded-full px-2 py-0.5 text-[10px] font-medium border border-error/20 bg-error/10 text-error">
                    no image produced
                  </span>
                )}
              </FeedRow>
            ) : attempt.tournament ? null : (
              <>
                <FeedRow letter="E" name="Engineer">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span>Wrote</span>
                    <span className="font-mono text-xs text-tx bg-bd/[0.06] rounded px-1.5 py-0.5">
                      {is3d
                        ? fileName(attempt.render_path, "scene.py")
                        : fileName(attempt.code_path, "main.py")}
                    </span>
                    {attempt.code_url && (
                      <a
                        href={`${API_ORIGIN}${attempt.code_url}`}
                        target="_blank"
                        rel="noreferrer"
                        download
                        className="text-xs accent-text hover:underline"
                      >
                        download
                      </a>
                    )}
                  </div>
                  {attempt.code && (
                    <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-bg border border-bd/[0.06] p-3 text-xs font-mono text-tx-dim leading-relaxed">
                      <code>{attempt.code}</code>
                    </pre>
                  )}
                </FeedRow>

                <FeedRow letter="T" name={is3d ? "Render" : "Tester"}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-medium border ${
                        attempt.image_url || exitOk
                          ? "border-success/20 bg-success/10 text-success"
                          : "border-error/20 bg-error/10 text-error"
                      }`}
                    >
                      {is3d
                        ? attempt.image_url
                          ? "rendered"
                          : "render failed"
                        : exitOk
                          ? "ran clean · exit 0"
                          : `failed · exit ${attempt.returncode ?? "?"}`}
                    </span>
                    {!is3d && (attempt.stderr ?? "").trim() !== "" && (
                      <button
                        type="button"
                        onClick={() =>
                          setLogsOpenFor(logsOpen ? null : attempt.attempt)
                        }
                        className="text-xs accent-text hover:underline"
                      >
                        {logsOpen ? "hide errors" : "errors"}
                      </button>
                    )}
                  </div>
                  {/* The actual program output, shown, not hidden. */}
                  {!is3d && (attempt.stdout ?? "").trim() !== "" && (
                    <div className="mt-2">
                      <p className="text-[11px] text-tx-mut mb-1">Output</p>
                      <pre className="max-h-56 overflow-auto rounded-lg bg-bg border border-bd/[0.06] p-3 text-xs font-mono text-tx whitespace-pre-wrap">
                        {attempt.stdout}
                      </pre>
                    </div>
                  )}
                  {logsOpen && !is3d && (
                    <textarea
                      readOnly
                      value={logs}
                      rows={6}
                      className="mt-2 w-full bg-bg border border-error/20 rounded-lg p-3 text-xs font-mono text-error resize-y focus:outline-none"
                    />
                  )}
                  <AttemptArtifact attempt={attempt} />
                </FeedRow>
              </>
            )}

            {attempt.critique && <CriticRow critique={attempt.critique} />}

            {attempt.tournament && attempt.candidates && (
              <FeedRow letter="∞" name="Tournament">
                <p className="text-xs text-tx-mut mb-2">
                  {attempt.candidates.length} strategies raced, best won.
                </p>
                <div className="space-y-1">
                  {attempt.candidates.map((candidate) => (
                    <div
                      key={candidate.letter}
                      className={`flex items-center gap-2 text-xs rounded-lg px-2 py-1.5 ${
                        candidate.selected
                          ? "bg-accent/10 accent-text"
                          : "text-tx-dim"
                      }`}
                    >
                      <span className="font-mono w-4 flex-none">
                        {candidate.letter}
                      </span>
                      <span className="flex-1 truncate">
                        {candidate.strategy}
                      </span>
                      <span className="font-mono flex-none text-[11px]">
                        {typeof candidate.score === "number"
                          ? candidate.score.toFixed(2)
                          : "—"}
                      </span>
                      {candidate.selected && (
                        <span className="text-[10px] flex-none">winner</span>
                      )}
                    </div>
                  ))}
                </div>
              </FeedRow>
            )}

            {attempt.redteam && (
              <FeedRow letter="R" name="Red-Team">
                <p className="text-xs mb-2">
                  <span
                    className={
                      attempt.redteam.passed_all
                        ? "text-success"
                        : "text-warning"
                    }
                  >
                    {attempt.redteam.failed} of {attempt.redteam.total} attacks
                    broke it
                  </span>
                </p>
                <ul className="space-y-1">
                  {attempt.redteam.attacks.map((attack, index) => (
                    <li
                      key={index}
                      className="flex items-center gap-2 text-xs text-tx-dim"
                    >
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium flex-none border ${
                          attack.passed
                            ? "border-success/20 bg-success/10 text-success"
                            : "border-error/20 bg-error/10 text-error"
                        }`}
                      >
                        {attack.passed ? "held" : "broke"}
                      </span>
                      <span className="truncate">{attack.vector}</span>
                    </li>
                  ))}
                </ul>
              </FeedRow>
            )}
          </div>
        );
      })}

      {mission.evidence?.failure_reason && (
        <p className="text-xs text-error">{mission.evidence.failure_reason}</p>
      )}
    </div>
  );
}

// Long Task panel — launch long-horizon agentic runs and watch them live.
// CSS-only animations (index.css .lt-* classes), no new dependencies.
// POST returns 202 with the journal row immediately (the engine runs in a
// backend worker thread); live updates arrive over SSE GET /{id}/events.

import { useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface LongTaskStep {
  seq: number;
  kind: string;
  summary?: string | null;
  status?: string | null;
  args_json?: string | null;
}

interface QuestArtifact {
  id: string;
  kind: string;
  path: string;
  gates_json?: string | null;
}

interface LongTask {
  id: string;
  goal: string;
  status: string;
  result?: string | null;
  cost_aud?: number | null;
  steps?: LongTaskStep[];
}

const RUNNING_COPY = [
  "Thinking deep…",
  "Reading the repo…",
  "Sketching a plan…",
  "Shipping pixels…",
  "Refining the details…",
  "Double-checking its own work…",
  "Asking the reviewer…",
];

const KIND_LABEL: Record<string, string> = {
  plan: "Plan",
  act: "Action",
  review: "Review",
  finish: "Finish",
  error: "Error",
};

function pickCopy(): string {
  return RUNNING_COPY[Math.floor(Math.random() * RUNNING_COPY.length)];
}

// Compact preview of a gated action's payload (diff-style for edits).
function toolPreview(p: { action: string; args: Record<string, unknown> }): string {
  const a = p.args ?? {};
  if (p.action === "write_file") {
    return `→ ${String(a.path ?? "")}\n${String(a.content ?? "").slice(0, 400)}`;
  }
  if (p.action === "edit_file") {
    return `→ ${String(a.path ?? "")}\n- ${String(a.old ?? "").slice(0, 160)}\n+ ${String(a.new ?? "").slice(0, 160)}`;
  }
  if (p.action === "run_command") {
    return `$ ${String(a.command ?? "")}`;
  }
  return JSON.stringify(a).slice(0, 400);
}

function statusTone(status: string): string {
  if (status === "completed") return "text-emerald-400";
  if (status === "failed" || status === "budget_exceeded" || status === "error") return "text-red-400";
  if (status === "cancelled") return "text-amber-400";
  if (status === "awaiting_plan") return "text-amber-400";
  return "text-accent";
}

export default function LongTaskPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const [goal, setGoal] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [maxCost, setMaxCost] = useState(2);
  const [maxSteps, setMaxSteps] = useState(40);
  const [launching, setLaunching] = useState(false);
  const [task, setTask] = useState<LongTask | null>(null);
  const [recent, setRecent] = useState<LongTask[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [copy, setCopy] = useState(RUNNING_COPY[0]);
  const [cancelling, setCancelling] = useState(false);
  const [specMode, setSpecMode] = useState(false);
  const [artifacts, setArtifacts] = useState<QuestArtifact[]>([]);
  const [approving, setApproving] = useState(false);
  const [pendingTool, setPendingTool] = useState<
    { action: string; args: Record<string, unknown> } | null
  >(null);
  const [toolBusy, setToolBusy] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const running = launching || task?.status === "running"
    || task?.status === "awaiting_plan";

  // Recent runs shown while idle.
  useEffect(() => {
    void apiFetch(`${API_BASE}/longtasks`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setRecent(Array.isArray(d) ? d.slice(-8).reverse() : []))
      .catch(() => setErr("Long-task engine unavailable — is the backend running?"));
  }, [task?.status]);

  // While the POST blocks server-side, discover the running row via the list
  // endpoint and rotate the playful status copy on every poll.
  useEffect(() => {
    if (!launching) return;
    const timer = window.setInterval(() => {
      setCopy(pickCopy());
      void apiFetch(`${API_BASE}/longtasks`)
        .then((r) => (r.ok ? r.json() : []))
        .then(async (d) => {
          const live = (Array.isArray(d) ? d : []).find(
            (t: LongTask) => t.status === "running",
          );
          if (!live) return;
          const full = await apiFetch(`${API_BASE}/longtasks/${live.id}`)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null);
          if (full) setTask(full);
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [launching]);

  // Stream the tracked task over SSE while it is alive (running or parked
  // at its plan). Every frame re-syncs the full row; done closes the stream.
  useEffect(() => {
    const id = task?.id;
    const alive = task?.status === "running" || task?.status === "awaiting_plan";
    if (!id || !alive) return;
    const es = new EventSource(`${API_BASE}/longtasks/${id}/events`);
    const sync = (): void => {
      void apiFetch(`${API_BASE}/longtasks/${id}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setTask(d))
        .catch(() => undefined);
    };
    es.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data) as {
          kind?: string;
          payload?: Record<string, unknown>;
        };
        if (frame.kind === "awaiting_tool_approval") {
          setPendingTool({
            action: String(frame.payload?.action ?? ""),
            args: (frame.payload?.args as Record<string, unknown>) ?? {},
          });
        } else if (frame.kind === "tool_approved" || frame.kind === "tool_rejected") {
          setPendingTool(null);
        }
        if (frame.kind === "done") {
          sync();
          es.close();
          return;
        }
        setCopy(pickCopy());
        sync();
      } catch {
        /* a malformed frame never breaks the stream */
      }
    };
    es.onerror = () => {
      /* SSE hiccup: one slow sync keeps the view moving */
      sync();
    };
    return () => es.close();
  }, [task?.id, task?.status]);

  // Artifact list for the tracked task (synced whenever its state moves).
  useEffect(() => {
    const id = task?.id;
    if (!id) return;
    void apiFetch(`${API_BASE}/longtasks/${id}/artifacts`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setArtifacts(Array.isArray(d) ? d : []))
      .catch(() => undefined);
  }, [task?.id, task?.status]);

  const cancelRun = (): void => {
    if (!task?.id || cancelling) return;
    setCancelling(true);
    void apiFetch(`${API_BASE}/longtasks/${task.id}/cancel`, { method: "POST" })
      .catch(() => undefined)
      .finally(() => setCancelling(false));
  };

  const approvePlan = (): void => {
    if (!task?.id || approving) return;
    setApproving(true);
    void apiFetch(`${API_BASE}/longtasks/${task.id}/approve-plan`, { method: "POST" })
      .catch(() => undefined)
      .finally(() => setApproving(false));
  };

  const approveTool = (): void => {
    if (!task?.id || toolBusy) return;
    setToolBusy(true);
    void apiFetch(`${API_BASE}/longtasks/${task.id}/approve-tool`, { method: "POST" })
      .catch(() => undefined)
      .finally(() => setToolBusy(false));
  };

  const rejectTool = (): void => {
    if (!task?.id || toolBusy) return;
    setToolBusy(true);
    void apiFetch(`${API_BASE}/longtasks/${task.id}/reject-tool`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: rejectReason.trim() || "not wanted" }),
    })
      .catch(() => undefined)
      .finally(() => {
        setToolBusy(false);
        setRejectReason("");
      });
  };

  const launch = (): void => {
    if (!goal.trim() || !repoPath.trim() || launching) return;
    setErr(null);
    setTask(null);
    setArtifacts([]);
    setLaunching(true);
    setCopy(pickCopy());
    void apiFetch(`${API_BASE}/longtasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: goal.trim(),
        repo_path: repoPath.trim(),
        autonomy: "full",
        max_steps: maxSteps,
        max_cost_aud: maxCost,
        max_wall_min: 120,
        spec_mode: specMode,
      }),
    })
      .then((r) =>
        r.ok
          ? r.json()
          : r.json().then((d) => Promise.reject(new Error(d?.detail ?? `HTTP ${r.status}`))),
      )
      .then((d: LongTask) => setTask(d))
      .catch((e: Error) => setErr(e.message))
      .finally(() => setLaunching(false));
  };

  const steps = task?.steps ?? [];
  const done = task && task.status !== "running" && task.status !== "awaiting_plan";
  const planText = (() => {
    const last = [...steps].reverse().find((s) => s.kind === "plan" && s.args_json);
    if (!last || !last.args_json) return "";
    try {
      return String((JSON.parse(last.args_json) as { plan?: string }).plan ?? "");
    } catch {
      return "";
    }
  })();

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
                running ? "bg-accent lt-ping" : "bg-tx-mut"
              }`}
            />
            <span
              className={`relative inline-flex h-3 w-3 rounded-full ${
                running ? "bg-accent" : "bg-tx-mut"
              }`}
            />
          </span>
          <div>
            <div className="text-sm font-semibold text-tx">Long Tasks</div>
            <div className="text-[11px] text-tx-mut">
              {running
                ? copy
                : "Plan → act → journal → review → finish. The engine learns from every run."}
            </div>
          </div>
          <div className="flex-1" />
          {done && (
            <span className={`lt-burst-badge text-[11px] font-mono ${statusTone(task.status)}`}>
              {task.status}
              {typeof task.cost_aud === "number" && task.cost_aud > 0 && (
                <> · ${task.cost_aud.toFixed(3)} AUD</>
              )}
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
          {/* Create form */}
          <div className="space-y-2">
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="What should the agent build? e.g. Add a pricing page to this site with 3 tiers…"
              rows={2}
              disabled={launching}
              className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50 resize-none"
            />
            <input
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="Repo path, e.g. C:\code\my-app"
              disabled={launching}
              className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
            />
            <div className="flex items-center gap-4 text-[12px] text-tx-dim">
              <label className="flex items-center gap-2 flex-1">
                Budget
                <input
                  type="range"
                  min={0.5}
                  max={10}
                  step={0.5}
                  value={maxCost}
                  disabled={launching}
                  onChange={(e) => setMaxCost(Number(e.target.value))}
                  className="flex-1 accent-current text-accent"
                />
                <span className="tabular-nums font-mono text-tx w-20 text-right">
                  ${maxCost.toFixed(2)} AUD
                </span>
              </label>
              <label className="flex items-center gap-2">
                Steps
                <input
                  type="range"
                  min={10}
                  max={120}
                  step={5}
                  value={maxSteps}
                  disabled={launching}
                  onChange={(e) => setMaxSteps(Number(e.target.value))}
                  className="accent-current text-accent"
                />
                <span className="tabular-nums font-mono text-tx">{maxSteps}</span>
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer select-none" title="Spec mode parks the run at its plan and waits for your approval">
                <input
                  type="checkbox"
                  checked={specMode}
                  disabled={launching}
                  onChange={(e) => setSpecMode(e.target.checked)}
                  className="accent-current text-accent"
                />
                Spec
              </label>
            </div>
            {err && <div className="text-[12px] text-red-400">{err}</div>}
            <button
              type="button"
              onClick={launch}
              disabled={launching || !goal.trim() || !repoPath.trim()}
              className="w-full rounded-lg bg-accent text-bg font-medium text-sm py-2.5 press disabled:opacity-40 disabled:cursor-not-allowed transition-transform hover:scale-[1.01]"
            >
              {launching ? copy : "Launch long task"}
            </button>
          </div>

          {/* Live run view */}
          {task && (
            <div className="rounded-xl border border-bd/[0.08] bg-bg/40 p-3 space-y-1.5">
              <div className="flex items-center gap-2 text-[12px]">
                {done ? (
                  <span className="lt-burst relative flex h-4 w-4 items-center justify-center">
                    <span className={`absolute inset-0 rounded-full border-2 ${
                      task.status === "completed"
                        ? "border-emerald-400"
                        : task.status === "cancelled"
                          ? "border-amber-400"
                          : "border-red-400"
                    }`} />
                  </span>
                ) : (
                  <span className="lt-orb relative flex h-3 w-3">
                    <span className="absolute inline-flex h-full w-full rounded-full bg-accent opacity-60 lt-ping" />
                    <span className="relative inline-flex h-3 w-3 rounded-full bg-accent" />
                  </span>
                )}
                <span className="text-tx truncate flex-1">{task.goal}</span>
                <span className={`font-mono ${statusTone(task.status)}`}>{task.status}</span>
                {typeof task.cost_aud === "number" && (
                  <span className="font-mono text-tx-mut tabular-nums">
                    ${task.cost_aud.toFixed(3)}
                  </span>
                )}
                {!done && (
                  <button
                    type="button"
                    onClick={cancelRun}
                    disabled={cancelling}
                    title="Stop this run before its next step"
                    className="shrink-0 rounded-md border border-bd/[0.1] px-2 py-0.5 text-[11px] text-tx-dim hover:text-red-400 hover:border-red-400/40 transition-colors press disabled:opacity-40"
                  >
                    {cancelling ? "\u2026" : "Cancel"}
                  </button>
                )}
              </div>
              {task.status === "awaiting_plan" && (
                <div className="rounded-lg border border-amber-400/30 bg-amber-400/[0.06] p-2.5 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] text-amber-400">
                      Plan ready — approve to let it run
                    </span>
                    <button
                      type="button"
                      onClick={approvePlan}
                      disabled={approving}
                      className="shrink-0 rounded-md bg-accent text-bg px-2.5 py-1 text-[11px] font-medium press disabled:opacity-40"
                    >
                      {approving ? "\u2026" : "Approve plan"}
                    </button>
                  </div>
                  {planText && (
                    <div className="text-[11.5px] text-tx-dim whitespace-pre-wrap max-h-32 overflow-y-auto">
                      {planText}
                    </div>
                  )}
                </div>
              )}
              {pendingTool && (
                <div className="rounded-lg border border-accent/30 bg-accent/[0.05] p-2.5 space-y-2">
                  <div className="text-[12px] text-accent font-medium">
                    Approve action:{" "}
                    <code className="font-mono">{pendingTool.action}</code>
                  </div>
                  <pre className="text-[11px] text-tx-dim bg-bg/60 rounded-md p-2 max-h-32 overflow-auto whitespace-pre-wrap break-all">
                    {toolPreview(pendingTool)}
                  </pre>
                  <input
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    placeholder="Optional reason to reject…"
                    className="w-full rounded-md bg-bg border border-bd/[0.08] px-2 py-1 text-[12px] text-tx placeholder-tx-mut focus:outline-none focus:border-accent/40"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={approveTool}
                      disabled={toolBusy}
                      className="rounded-md bg-accent text-bg px-3 py-1 text-[11px] font-medium press disabled:opacity-40"
                    >
                      {toolBusy ? "\u2026" : "Approve"}
                    </button>
                    <button
                      type="button"
                      onClick={rejectTool}
                      disabled={toolBusy}
                      className="rounded-md border border-red-400/40 text-red-400 px-3 py-1 text-[11px] font-medium press disabled:opacity-40"
                    >
                      {toolBusy ? "\u2026" : "Reject"}
                    </button>
                  </div>
                </div>
              )}
              {steps.length > 0 && (
                <ul className="space-y-1 max-h-52 overflow-y-auto pl-1">
                  {steps.map((s) => (
                    <li key={s.seq} className="lt-step-in flex items-baseline gap-2 text-[12px]">
                      <span className="font-mono text-tx-mut shrink-0 tabular-nums">
                        {String(s.seq).padStart(2, "0")}
                      </span>
                      <span className="text-accent shrink-0 w-12">
                        {KIND_LABEL[s.kind] ?? s.kind}
                      </span>
                      <span className={`truncate ${s.kind === "error" ? "text-red-400" : "text-tx-dim"}`}>
                        {s.summary ?? ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {done && task.result && (
                <div className="text-[12px] text-tx-dim border-t border-bd/[0.06] pt-2 whitespace-pre-wrap">
                  {task.result}
                </div>
              )}
              {artifacts.length > 0 && (
                <div className="border-t border-bd/[0.06] pt-2 space-y-1">
                  <div className="text-[10px] uppercase tracking-wide text-tx-mut">Artifacts</div>
                  {artifacts.map((a) => {
                    let gate = "";
                    try {
                      gate = String((JSON.parse(a.gates_json ?? "{}") as { compile?: string }).compile ?? "");
                    } catch {
                      gate = "";
                    }
                    return (
                      <div key={a.id} className="flex items-center gap-2 text-[11.5px]">
                        <span className="font-mono text-tx truncate flex-1">{a.path}</span>
                        {gate === "pass" && <span className="text-emerald-400 shrink-0">compile ok</span>}
                        {gate === "fail" && <span className="text-red-400 shrink-0">compile fail</span>}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Recent runs */}
          {!task && recent.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] uppercase tracking-wide text-tx-mut">Recent runs</div>
              {recent.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => {
                    void apiFetch(`${API_BASE}/longtasks/${t.id}`)
                      .then((r) => (r.ok ? r.json() : null))
                      .then((d) => d && setTask(d))
                      .catch(() => undefined);
                  }}
                  className="w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] hover:bg-bd/[0.05] transition-colors"
                >
                  <span className={`font-mono shrink-0 ${statusTone(t.status)}`}>{t.status}</span>
                  <span className="text-tx-dim truncate flex-1">{t.goal}</span>
                  {typeof t.cost_aud === "number" && t.cost_aud > 0 && (
                    <span className="font-mono text-tx-mut tabular-nums shrink-0">
                      ${t.cost_aud.toFixed(3)}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

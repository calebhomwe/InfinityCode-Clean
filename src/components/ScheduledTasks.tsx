import { useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface Schedule {
  id: string;
  title: string;
  prompt: string;
  cadence: string;
  mode: string;
  enabled: number;
  next_run: number;
  last_run: number | null;
  last_status: string | null;
}

const CADENCES = ["15m", "1h", "6h", "12h", "1d", "daily"];

interface ScheduleTemplate {
  id: string;
  title: string;
  prompt: string;
  cadence: string;
  description: string;
}

function rel(ts: number | null): string {
  if (!ts) return "—";
  const d = ts * 1000 - Date.now();
  const abs = Math.abs(d);
  const m = Math.round(abs / 60000);
  if (m < 60) return d > 0 ? `in ${m}m` : `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return d > 0 ? `in ${h}h` : `${h}h ago`;
  return d > 0 ? `in ${Math.round(h / 24)}d` : `${Math.round(h / 24)}d ago`;
}

/** Human label for a run's stored status ("ok"/"error: …" are backend-speak). */
function statusLabel(status: string | null): string {
  const s = (status ?? "ok").trim().toLowerCase();
  if (s === "ok" || s === "success" || s === "completed") return "Completed";
  if (s.startsWith("error") || s.startsWith("fail")) return "Failed";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function statusIsFailure(status: string | null): boolean {
  const s = (status ?? "").trim().toLowerCase();
  return s.startsWith("error") || s.startsWith("fail");
}

/** Kimi-style Scheduled Tasks: set-and-forget prompts with a Working/Completed feel. */
export default function ScheduledTasks({ onClose }: { onClose: () => void }): JSX.Element {
  const [items, setItems] = useState<Schedule[]>([]);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [cadence, setCadence] = useState("1d");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [templates, setTemplates] = useState<ScheduleTemplate[]>([]);

  useEffect(() => {
    void apiFetch(`${API_BASE}/schedule-templates`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setTemplates(Array.isArray(d) ? d : []))
      .catch(() => undefined);
  }, []);

  const useTemplate = (tpl: ScheduleTemplate): void => {
    setTitle(tpl.title);
    setPrompt(tpl.prompt);
    setCadence(tpl.cadence);
  };

  const load = (): void => {
    void apiFetch(`${API_BASE}/schedules`)
      .then((r) => (r.ok ? r.json() : { schedules: [] }))
      .then((d) => setItems(d.schedules ?? []))
      .catch(() => setErr("Scheduler unavailable — restart the app to enable it."));
  };
  useEffect(load, []);

  const create = (): void => {
    if (!title.trim() || !prompt.trim() || busy) return;
    setBusy(true);
    setErr(null);
    void apiFetch(`${API_BASE}/schedules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, prompt, cadence }),
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("create failed"))))
      .then(() => {
        setTitle("");
        setPrompt("");
        load();
      })
      .catch(() => setErr("Could not create — is the backend updated?"))
      .finally(() => setBusy(false));
  };

  const act = (path: string, method = "POST", body?: unknown): void => {
    void apiFetch(`${API_BASE}/schedules/${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then(load).catch(() => undefined);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/50 backdrop-blur-sm" onMouseDown={onClose}>
      <div className="w-full max-w-2xl max-h-[82vh] flex flex-col rounded-[22px] material-overlay border border-bd/[0.1] elev-3 overflow-hidden" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-bd/[0.08]">
          
          <div>
            <div className="text-sm font-semibold text-tx">Scheduled Tasks</div>
            <div className="text-[11px] text-tx-mut">Run a prompt on a cadence — results land as chats. Read-only (no unattended actions).</div>
          </div>
          <div className="flex-1" />
          <button type="button" aria-label="Close" onClick={onClose} className="text-tx-mut hover:text-tx rounded-lg p-1.5 hover:bg-bd/[0.06] press">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>

        <div className="px-5 py-3 border-b border-bd/[0.06] space-y-2">
          {templates.length > 0 && (
            <div className="flex gap-1.5 flex-wrap">
              {templates.map((tpl) => (
                <button
                  key={tpl.id}
                  type="button"
                  onClick={() => useTemplate(tpl)}
                  title={tpl.description}
                  className="rounded-full bg-bd/[0.05] px-2.5 py-1 text-[11px] text-tx-dim hover:bg-accent/10 hover:text-accent transition-colors"
                >
                  {tpl.title}
                </button>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Task name (e.g. Morning brief)"
              className="flex-1 rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50" />
            <select value={cadence} onChange={(e) => setCadence(e.target.value)}
              className="rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx focus:outline-none focus:border-accent/50">
              {CADENCES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="flex gap-2">
            <input value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} placeholder="Prompt to run…"
              className="flex-1 rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50" />
            <button type="button" disabled={busy} onClick={create}
              className="rounded-lg bg-accent hover:bg-accent-hover text-black px-4 py-2 text-sm font-medium disabled:opacity-50 press transition-colors">
              Schedule
            </button>
          </div>
          {err && <p className="text-[11px] text-error">{err}</p>}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {items.length === 0 ? (
            <div className="text-sm text-tx-mut text-center py-10">No scheduled tasks yet. Create one above.</div>
          ) : (
            items.map((s) => (
              <div key={s.id} className="rounded-xl border border-bd/[0.07] bg-bg p-3 lift">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full flex-none ${s.enabled ? "bg-accent" : "bg-bd/[0.2]"}`} />
                  <span className="text-sm font-medium text-tx truncate">{s.title}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-bd/[0.06] text-tx-dim">{s.cadence}</span>
                  <div className="flex-1" />
                  <span className="text-[10px] text-tx-mut tabular-nums">next {rel(s.next_run)}</span>
                </div>
                <p className="text-[12px] text-tx-mut mt-1 truncate">{s.prompt}</p>
                <div className="flex items-center gap-2 mt-2">
                  {s.last_run && (
                    <span
                      className={`text-[10px] ${statusIsFailure(s.last_status) ? "text-error" : "text-tx-mut"}`}
                      title={s.last_status ?? undefined}
                    >
                      last run {rel(s.last_run)} · {statusLabel(s.last_status)}
                    </span>
                  )}
                  <div className="flex-1" />
                  <button type="button" onClick={() => act(`${s.id}/run-now`)} className="text-[11px] text-tx-dim hover:text-accent px-2 py-1 rounded press">Run now</button>
                  <button type="button" onClick={() => act(`${s.id}/toggle`, "POST", { enabled: !s.enabled })} className="text-[11px] text-tx-dim hover:text-tx px-2 py-1 rounded press">{s.enabled ? "Pause" : "Resume"}</button>
                  <button type="button" onClick={() => act(s.id, "DELETE")} className="text-[11px] text-tx-mut hover:text-error px-2 py-1 rounded press">Delete</button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

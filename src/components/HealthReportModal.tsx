// HealthReportModal — fetches /api/v1/health-report and shows a system
// status dashboard: uptime, schedules, long tasks, wiki, memory.

import { useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface HealthReport {
  status: string;
  version: string;
  uptime_s: number;
  schedules: { total: number; active: number };
  longtasks: { running: number; completed: number; total_cost_aud: number };
  wiki_repos: number;
  memory_items: number;
}

function fmtUptime(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export default function HealthReportModal({
  onClose,
}: {
  onClose: () => void;
}): JSX.Element {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void apiFetch(`${API_BASE}/health-report`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d: HealthReport) => {
        setReport(d);
        setErr(null);
      })
      .catch(() => setErr("Could not fetch health report — is the backend running?"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/50 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-lg flex flex-col rounded-[22px] material-overlay border border-bd/[0.1] elev-3 overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-bd/[0.08]">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-bd/[0.1] text-tx-dim" aria-hidden="true">
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 13h4l2-7 4 13 2-6h4" />
            </svg>
          </span>
          <div>
            <div className="text-sm font-semibold text-tx">System Health</div>
            <div className="text-[11px] text-tx-mut">
              Backend status and resource overview
            </div>
          </div>
          <div className="flex-1" />
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

        <div className="p-5 space-y-4">
          {loading && (
            <p className="text-sm text-tx-mut animate-pulse">Loading report…</p>
          )}
          {err && <p className="text-sm text-error">{err}</p>}

          {report && !loading && (
            <>
              {/* Status row */}
              <div className="flex items-center gap-3">
                <span
                  className={`w-2.5 h-2.5 rounded-full ${
                    report.status === "ok" ? "bg-emerald-400" : "bg-red-400"
                  }`}
                />
                <span className="text-sm font-medium text-tx">
                  {report.status === "ok" ? "Healthy" : "Degraded"}
                </span>
                <span className="text-[11px] text-tx-mut ml-auto">
                  v{report.version} · up {fmtUptime(report.uptime_s)}
                </span>
              </div>

              {/* Stat grid */}
              <div className="grid grid-cols-2 gap-3">
                <StatCard
                  label="Scheduled Tasks"
                  value={`${report.schedules.active}/${report.schedules.total}`}
                  hint="active / total"
                />
                <StatCard
                  label="Long Tasks"
                  value={`${report.longtasks.running}`}
                  hint={`${report.longtasks.completed} completed`}
                  accent={report.longtasks.running > 0}
                />
                <StatCard
                  label="Total Cost"
                  value={`$${report.longtasks.total_cost_aud.toFixed(2)}`}
                  hint="AUD this session"
                />
                <StatCard
                  label="Wiki Repos"
                  value={`${report.wiki_repos}`}
                  hint="indexed"
                />
                <StatCard
                  label="Memory"
                  value={`${report.memory_items}`}
                  hint="items stored"
                />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: string;
  hint: string;
  accent?: boolean;
}): JSX.Element {
  return (
    <div className="rounded-xl border border-bd/[0.07] bg-bg p-3">
      <div className="text-[10px] text-tx-mut uppercase tracking-wide">{label}</div>
      <div
        className={`text-lg font-semibold tabular-nums mt-0.5 ${
          accent ? "text-accent" : "text-tx"
        }`}
      >
        {value}
      </div>
      <div className="text-[10px] text-tx-mut">{hint}</div>
    </div>
  );
}

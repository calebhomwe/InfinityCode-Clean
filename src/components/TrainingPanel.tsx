// TrainingPanel — flywheel telemetry overlay (P10). Shows how many accepted
// trajectories are banked, average fidelity, and lets the user export the SFT
// dataset for a weekly LoRA refresh. CSS-only, no new dependencies.

import { useCallback, useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface TrainingStats {
  accepted_tasks: number;
  avg_fidelity: number;
  total_messages: number;
  threshold: number;
  out_dir: string;
}

export default function TrainingPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const [stats, setStats] = useState<TrainingStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await apiFetch(`${API_BASE}/training/stats`);
      if (!r.ok) throw new Error(`stats ${r.status}`);
      setStats(await r.json());
      setErr(null);
    } catch {
      setErr("Training stats unavailable — is the backend running?");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const exportDataset = async () => {
    setExporting(true);
    setExportMsg(null);
    try {
      const r = await apiFetch(`${API_BASE}/training/export`, { method: "POST" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(j.detail ?? `export ${r.status}`);
      }
      const d = await r.json();
      setExportMsg(`Exported ${d.exported} trajectories → ${d.path}`);
      await load();
    } catch (e: unknown) {
      setExportMsg(e instanceof Error ? e.message : "export failed");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/50 backdrop-blur-sm"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-md flex flex-col rounded-[22px] material-overlay border border-bd/[0.1] elev-3 overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-bd/[0.08]">
          <div>
            <div className="text-sm font-semibold text-tx">Training Flywheel</div>
            <div className="text-[11px] text-tx-mut">
              Accepted runs become training data. The gap compounds.
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

        <div className="px-5 py-4 space-y-4">
          {err && <div className="text-[12px] text-red-400">{err}</div>}

          {stats ? (
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-lg bg-bg/50 border border-bd/[0.06] p-3 text-center">
                <div className="text-xl font-semibold text-accent tabular-nums">
                  {stats.accepted_tasks}
                </div>
                <div className="text-[10px] text-tx-mut mt-0.5">Accepted</div>
              </div>
              <div className="rounded-lg bg-bg/50 border border-bd/[0.06] p-3 text-center">
                <div className="text-xl font-semibold text-tx tabular-nums">
                  {stats.avg_fidelity.toFixed(2)}
                </div>
                <div className="text-[10px] text-tx-mut mt-0.5">Avg fidelity</div>
              </div>
              <div className="rounded-lg bg-bg/50 border border-bd/[0.06] p-3 text-center">
                <div className="text-xl font-semibold text-tx tabular-nums">
                  {stats.total_messages}
                </div>
                <div className="text-[10px] text-tx-mut mt-0.5">Messages</div>
              </div>
            </div>
          ) : (
            !err && <div className="text-sm text-tx-mut animate-pulse">Loading…</div>
          )}

          {stats && (
            <div className="text-[11px] text-tx-mut">
              Threshold ≥ {stats.threshold.toFixed(2)} fidelity · stored in{" "}
              <code className="font-mono">{stats.out_dir}</code>
            </div>
          )}

          {exportMsg && (
            <div className="text-[12px] text-tx-dim break-all">{exportMsg}</div>
          )}

          <button
            type="button"
            onClick={() => void exportDataset()}
            disabled={exporting || !stats}
            className="w-full rounded-lg bg-accent text-bg font-medium text-sm py-2.5 press disabled:opacity-40 transition-transform hover:scale-[1.01]"
          >
            {exporting ? "Exporting…" : "Export SFT dataset"}
          </button>
        </div>
      </div>
    </div>
  );
}

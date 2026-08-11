// VisionPanel — vision-in-the-loop verification overlay (P7).
// Lists locked reference images, verifies a candidate image against one via
// the deterministic pixel-diff endpoint, and renders the score gauge plus
// coarse diff-region boxes. CSS-only, no new dependencies.

import { useCallback, useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface RefItem {
  id: string;
  name: string;
  size: number;
}

interface DiffRegion {
  x: number;
  y: number;
  w: number;
  h: number;
  severity: number;
}

interface VerifyResp {
  pass: boolean;
  score: number;
  threshold: number;
  diff_regions: DiffRegion[];
  width: number;
  height: number;
}

export default function VisionPanel({ onClose }: { onClose: () => void }): JSX.Element {
  const [refs, setRefs] = useState<RefItem[]>([]);
  const [reference, setReference] = useState("");
  const [candidate, setCandidate] = useState("");
  const [threshold, setThreshold] = useState(0.85);
  const [result, setResult] = useState<VerifyResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadRefs = useCallback(async () => {
    try {
      const r = await apiFetch(`${API_BASE}/vision/references`);
      if (!r.ok) throw new Error(`refs ${r.status}`);
      const data = await r.json();
      setRefs(data.references ?? []);
      if (!reference && data.references?.length) {
        setReference(data.references[0].id);
      }
      setErr(null);
    } catch {
      setErr("Vision references unavailable — is the backend running?");
    }
  }, [reference]);

  useEffect(() => {
    void loadRefs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const verify = async () => {
    if (!candidate.trim() || !reference) return;
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await apiFetch(`${API_BASE}/vision/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate: candidate.trim(),
          reference,
          threshold,
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(j.detail ?? `verify ${r.status}`);
      }
      setResult(await r.json());
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "verify failed");
    } finally {
      setBusy(false);
    }
  };

  const pct = result ? Math.round(result.score * 100) : 0;

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-bg/90 backdrop-blur-sm">
      <div className="flex w-full max-w-3xl flex-col mx-4 my-6 rounded-xl border border-bd/[0.08] bg-surface shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-bd/[0.07] px-5 py-3">
          <h2 className="text-base font-semibold text-tx">Vision Verify</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-tx-dim hover:bg-bd/[0.08] hover:text-tx transition-colors"
          >
            ×
          </button>
        </div>

        {/* Controls */}
        <div className="flex flex-col gap-2 border-b border-bd/[0.05] px-5 py-3">
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Candidate image path (absolute)"
              value={candidate}
              onChange={(e) => setCandidate(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void verify()}
              className="flex-1 rounded-md border border-bd/[0.1] bg-bg px-3 py-1.5 text-sm text-tx placeholder:text-tx-mut focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
            <select
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              className="rounded-md border border-bd/[0.1] bg-bg px-2 py-1.5 text-sm text-tx focus:border-accent focus:outline-none"
            >
              {refs.length === 0 && <option value="">no references</option>}
              {refs.map((rf) => (
                <option key={rf.id} value={rf.id}>
                  {rf.id}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void verify()}
              disabled={busy || !candidate.trim() || !reference}
              className="rounded-md bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent hover:bg-accent/20 disabled:opacity-40 transition-colors"
            >
              {busy ? "Verifying…" : "Verify"}
            </button>
          </div>
          <div className="flex items-center gap-2 text-xs text-tx-mut">
            <span>Threshold</span>
            <input
              type="range"
              min={0.5}
              max={0.99}
              step={0.01}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-40 accent-accent"
            />
            <span className="tabular-nums">{threshold.toFixed(2)}</span>
          </div>
        </div>

        {err && <div className="px-5 py-2 text-xs text-red-400">{err}</div>}

        {/* Result */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {!result ? (
            <p className="text-sm text-tx-mut">
              Pick a candidate image and a locked reference, then click Verify.
              The score is a deterministic pixel-similarity in [0, 1].
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              {/* Score gauge + pass badge */}
              <div className="flex items-center gap-4">
                <div className="relative h-24 w-24 shrink-0">
                  <svg viewBox="0 0 36 36" className="h-full w-full -rotate-90">
                    <circle cx="18" cy="18" r="15.9" fill="none" stroke="currentColor"
                      strokeWidth="3" className="text-bd/20" />
                    <circle cx="18" cy="18" r="15.9" fill="none" stroke="currentColor"
                      strokeWidth="3" strokeDasharray={`${pct}, 100`}
                      className={result.pass ? "text-emerald-400" : "text-red-400"} />
                  </svg>
                  <span className="absolute inset-0 flex items-center justify-center text-lg font-semibold text-tx">
                    {pct}%
                  </span>
                </div>
                <div className="flex flex-col gap-1">
                  <span
                    className={`inline-flex w-fit rounded-md px-2 py-0.5 text-xs font-medium ${
                      result.pass
                        ? "bg-emerald-400/10 text-emerald-400"
                        : "bg-red-400/10 text-red-400"
                    }`}
                  >
                    {result.pass ? "PASS" : "FAIL"}
                  </span>
                  <span className="text-sm text-tx-dim tabular-nums">
                    score {result.score.toFixed(3)} · threshold {result.threshold.toFixed(2)}
                  </span>
                  <span className="text-xs text-tx-mut">
                    {result.diff_regions.length} divergent region
                    {result.diff_regions.length === 1 ? "" : "s"}
                  </span>
                </div>
              </div>

              {/* Diff region overlay grid */}
              {result.diff_regions.length > 0 && (
                <div className="relative aspect-square w-48 rounded-md border border-bd/[0.1] bg-bg overflow-hidden">
                  {result.diff_regions.map((rg, i) => (
                    <div
                      key={i}
                      className="absolute border border-red-400/70 bg-red-400/20"
                      style={{
                        left: `${rg.x * 100}%`,
                        top: `${rg.y * 100}%`,
                        width: `${rg.w * 100}%`,
                        height: `${rg.h * 100}%`,
                      }}
                      title={`severity ${rg.severity.toFixed(3)}`}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

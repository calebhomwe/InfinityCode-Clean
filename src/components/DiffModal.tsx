import { useCallback, useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";
import { toast } from "sonner";

export interface DiffPreview {
  file_path: string;
  original: string;
  patched: string;
  diff: string;
  search: string;
  replace: string;
}

interface DiffModalProps {
  initialCode: string;
  suggestedPath?: string;
  workspacePath: string;
  onClose: () => void;
  onApplied?: () => void;
}

export default function DiffModal({
  initialCode,
  suggestedPath,
  workspacePath,
  onClose,
  onApplied,
}: DiffModalProps): JSX.Element {
  const [filePath, setFilePath] = useState(suggestedPath || "");
  const [preview, setPreview] = useState<DiffPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generatePreview = useCallback(async () => {
    if (!filePath.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`${API_BASE}/workspace/preview-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: filePath,
          search: "",
          replace: initialCode,
        }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setPreview((await res.json()) as DiffPreview);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
      setPreview(null);
    } finally {
      setLoading(false);
    }
  }, [filePath, initialCode]);

  useEffect(() => {
    if (filePath.trim()) {
      const timer = window.setTimeout(() => void generatePreview(), 400);
      return () => window.clearTimeout(timer);
    }
  }, [filePath, generatePreview]);

  const apply = useCallback(async () => {
    if (!preview) return;
    setApplying(true);
    try {
      const res = await apiFetch(`${API_BASE}/workspace/apply-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: preview.file_path,
          search: preview.search,
          replace: preview.replace,
        }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      toast.success("Applied", { description: preview.file_path });
      onApplied?.();
      onClose();
    } catch (err) {
      toast.error("Could not apply", {
        description: err instanceof Error ? err.message : "unknown error",
      });
    } finally {
      setApplying(false);
    }
  }, [preview, onApplied, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-[22px] border border-bd/[0.1] material-overlay shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-bd/[0.07]">
          <span className="text-sm font-medium text-tx">Apply to workspace file</span>
          <button
            type="button"
            onClick={onClose}
            className="h-7 w-7 rounded-lg flex items-center justify-center text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-tx-dim">Target file (workspace-relative)</label>
            <input
              type="text"
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              placeholder="e.g. src/components/Button.tsx"
              className="w-full rounded-lg bg-bg border border-bd/[0.08] px-3 py-2 text-sm text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
            />
            <p className="text-[11px] text-tx-mut">Workspace: {workspacePath || "none selected"}</p>
          </div>

          {error && (
            <div className="rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3">
              {error}
            </div>
          )}

          {loading && <p className="text-sm text-tx-mut">Generating diff…</p>}

          {preview && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="block text-xs font-medium text-tx-dim">Diff preview</label>
                <span className="text-[11px] text-tx-mut">
                  {preview.original ? "Creating file" : "New file"}
                </span>
              </div>
              <pre className="rounded-lg bg-bg border border-bd/[0.08] p-3 text-[11px] font-mono leading-relaxed overflow-auto max-h-96 whitespace-pre">
                {preview.diff.split("\n").map((line, i) => {
                  let color = "text-tx";
                  if (line.startsWith("+") && !line.startsWith("+++")) color = "text-success";
                  else if (line.startsWith("-") && !line.startsWith("---")) color = "text-error";
                  else if (line.startsWith("@@")) color = "text-accent";
                  return (
                    <div key={i} className={color}>
                      {line || " "}
                    </div>
                  );
                })}
              </pre>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-bd/[0.07]">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-bd/[0.12] px-3 py-1.5 text-xs text-tx-dim hover:bg-bd/[0.05] transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void apply()}
            disabled={!preview || applying}
            className="rounded-lg bg-accent hover:bg-accent-hover disabled:opacity-50 text-black px-3 py-1.5 text-xs font-medium transition-colors"
          >
            {applying ? "Applying…" : "Apply change"}
          </button>
        </div>
      </div>
    </div>
  );
}

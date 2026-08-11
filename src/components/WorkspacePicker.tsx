import { useCallback, useState } from "react";
import { open } from "@tauri-apps/api/dialog";
import { useWorkspace, type WorkspaceTreeEntry } from "../hooks/useWorkspace";
import { toast } from "sonner";

function TreeNode({ entry, depth = 0 }: { entry: WorkspaceTreeEntry; depth?: number }): JSX.Element {
  const [expanded, setExpanded] = useState(depth < 1);
  const hasChildren = entry.type === "dir" && entry.children && entry.children.length > 0;
  return (
    <div>
      <button
        type="button"
        onClick={() => hasChildren && setExpanded((e) => !e)}
        className="flex items-center gap-1 text-[11px] text-tx-dim hover:text-tx w-full text-left py-0.5"
        style={{ paddingLeft: `${depth * 12}px` }}
      >
        {hasChildren ? (
          <svg
            className={`w-3 h-3 flex-none transition-transform ${expanded ? "rotate-90" : ""}`}
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round"
          >
            <path d="M9 18l6-6-6-6" />
          </svg>
        ) : (
          <span className="w-3 flex-none" />
        )}
        <span className="truncate">{entry.name}</span>
      </button>
      {expanded && hasChildren && (
        <div>
          {entry.children!.map((child) => (
            <TreeNode key={child.name + (child.type === "dir" ? "/" : "")} entry={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function WorkspacePicker(): JSX.Element {
  const { workspace, loading, setPath } = useWorkspace();
  const [picking, setPicking] = useState(false);
  // Non-null while the inline path entry (browser dev fallback) is open.
  const [manualPath, setManualPath] = useState<string | null>(null);
  const isTauri = typeof window !== "undefined" && "__TAURI_IPC__" in window;

  const pickFolder = useCallback(async () => {
    if (!isTauri) {
      // Browser dev fallback: inline path entry instead of a native prompt.
      // Tauri is the target shell, so this only ever shows in dev browsers.
      setManualPath("");
      return;
    }
    setPicking(true);
    try {
      const selected = await open({ directory: true, multiple: false });
      const path = Array.isArray(selected) ? selected[0] : selected;
      if (!path) return;
      await setPath(path);
      toast.success("Workspace set", { description: path });
    } catch (err) {
      toast.error("Could not set workspace", {
        description: err instanceof Error ? err.message : "unknown error",
      });
    } finally {
      setPicking(false);
    }
  }, [isTauri, setPath]);

  const confirmManualPath = useCallback(async () => {
    const path = (manualPath ?? "").trim();
    if (!path) return;
    try {
      await setPath(path);
      toast.success("Workspace set", { description: path });
      setManualPath(null);
    } catch (err) {
      toast.error("Could not set workspace", {
        description: err instanceof Error ? err.message : "unknown error",
      });
    }
  }, [manualPath, setPath]);

  const clear = useCallback(async () => {
    try {
      await setPath("");
      toast.success("Workspace cleared");
    } catch (err) {
      toast.error("Could not clear workspace");
    }
  }, [setPath]);

  return (
    <div className="mx-3 mb-2 rounded-lg border border-bd/[0.06] bg-surface/[0.4] p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-widest text-tx-mut">Workspace</span>
        {workspace.valid && (
          <button
            type="button"
            onClick={clear}
            className="text-[10px] text-tx-mut hover:text-error transition-colors"
          >
            Clear
          </button>
        )}
      </div>
      {workspace.valid ? (
        <>
          <p className="mt-1 text-[11px] text-tx-dim truncate" title={workspace.path}>
            {workspace.path}
          </p>
          <div className="mt-1.5 max-h-32 overflow-y-auto border-t border-bd/[0.05] pt-1.5">
            {workspace.tree.map((entry) => (
              <TreeNode key={entry.name} entry={entry} />
            ))}
          </div>
        </>
      ) : (
        <p className="mt-1 text-[11px] text-tx-mut">
          {loading ? "Loading…" : "No project folder selected."}
        </p>
      )}
      {manualPath === null ? (
        <button
          type="button"
          onClick={() => void pickFolder()}
          disabled={picking}
          className="mt-2 w-full rounded-md bg-accent/90 hover:bg-accent disabled:opacity-50 text-black text-[11px] font-medium py-1.5 transition-colors"
        >
          {picking ? "Choosing…" : workspace.valid ? "Change folder" : "Open folder"}
        </button>
      ) : (
        <div className="mt-2 space-y-1.5">
          <input
            autoFocus
            type="text"
            value={manualPath}
            onChange={(event) => setManualPath(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void confirmManualPath();
              if (event.key === "Escape") setManualPath(null);
            }}
            placeholder="Absolute workspace folder path…"
            className="w-full rounded-md bg-bg border border-bd/[0.08] px-2.5 py-1.5 text-[11px] text-tx placeholder:text-tx-mut focus:outline-none focus:border-accent/50"
          />
          <div className="flex gap-1.5">
            <button
              type="button"
              disabled={!manualPath.trim()}
              onClick={() => void confirmManualPath()}
              className="flex-1 rounded-md bg-accent/90 hover:bg-accent disabled:opacity-50 text-black text-[11px] font-medium py-1.5 transition-colors"
            >
              Use folder
            </button>
            <button
              type="button"
              onClick={() => setManualPath(null)}
              className="rounded-md border border-bd/[0.12] px-3 text-[11px] text-tx-dim hover:bg-bd/[0.06] transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

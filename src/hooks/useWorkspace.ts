import { useCallback, useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

export interface WorkspaceTreeEntry {
  name: string;
  type: "file" | "dir";
  children?: WorkspaceTreeEntry[];
}

export interface WorkspaceInfo {
  path: string;
  tree: WorkspaceTreeEntry[];
  valid: boolean;
}

export interface DiffPreview {
  file_path: string;
  original: string;
  patched: string;
  diff: string;
  search: string;
  replace: string;
}

export function useWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo>({
    path: "",
    tree: [],
    valid: false,
  });
  const [loading, setLoading] = useState<boolean>(true);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch(`${API_BASE}/workspace`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as WorkspaceInfo;
      setWorkspace(data);
    } catch {
      setWorkspace({ path: "", tree: [], valid: false });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const onWorkspaceChanged = (): void => {
      void load();
    };
    window.addEventListener("infinity:workspace-changed", onWorkspaceChanged);
    return () => window.removeEventListener("infinity:workspace-changed", onWorkspaceChanged);
  }, [load]);

  const setPath = useCallback(async (path: string) => {
    const res = await apiFetch(`${API_BASE}/workspace`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const err = (await res.json().catch(() => ({}))) as { detail?: string };
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = (await res.json()) as WorkspaceInfo;
    setWorkspace(data);
    window.dispatchEvent(new CustomEvent("infinity:workspace-changed"));
    return data;
  }, []);

  const previewEdit = useCallback(
    async (filePath: string, search: string, replace: string): Promise<DiffPreview> => {
      const res = await apiFetch(`${API_BASE}/workspace/preview-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: filePath, search, replace }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      return (await res.json()) as DiffPreview;
    },
    [],
  );

  const applyEdit = useCallback(
    async (filePath: string, search: string, replace: string): Promise<{ applied: boolean; chars: number }> => {
      const res = await apiFetch(`${API_BASE}/workspace/apply-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: filePath, search, replace }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      return (await res.json()) as { applied: boolean; chars: number };
    },
    [],
  );

  return { workspace, loading, setPath, previewEdit, applyEdit };
}

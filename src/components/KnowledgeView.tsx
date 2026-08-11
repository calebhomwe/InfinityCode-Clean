// KnowledgeView — repo wiki overlay. Lists generated wiki pages from the
// backend, fetches page content on click, and lets the user regenerate the
// wiki for a given repo path. CSS-only, no new dependencies.

import { useCallback, useEffect, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

interface WikiPage {
  path: string;
  title: string;
  model?: string;
  refreshed_at?: number;
  body_md?: string;
}

interface WikiListResp {
  repo: string;
  repos: string[];
  pages: WikiPage[];
}

export default function KnowledgeView({
  onClose,
}: {
  onClose: () => void;
}): JSX.Element {
  const [repo, setRepo] = useState("");
  const [repos, setRepos] = useState<string[]>([]);
  const [pages, setPages] = useState<WikiPage[]>([]);
  const [active, setActive] = useState<WikiPage | null>(null);
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [genPath, setGenPath] = useState("");

  const fetchList = useCallback(async () => {
    try {
      const r = await apiFetch(`${API_BASE}/wiki${repo ? `?repo=${encodeURIComponent(repo)}` : ""}`);
      if (!r.ok) throw new Error(`wiki list ${r.status}`);
      const data: WikiListResp = await r.json();
      setRepo(data.repo);
      setRepos(data.repos ?? []);
      setPages(data.pages ?? []);
      setErr(null);
    } catch {
      setErr("Wiki unavailable — is the backend running?");
    }
  }, [repo]);

  useEffect(() => {
    void fetchList();
  }, [fetchList]);

  const openPage = async (pg: WikiPage) => {
    setLoading(true);
    setActive(pg);
    setBody("");
    try {
      const r = await fetch(
        `${API_BASE}/wiki/page?repo=${encodeURIComponent(repo)}&path=${encodeURIComponent(pg.path)}`,
      );
      if (!r.ok) throw new Error(`page ${r.status}`);
      const data = await r.json();
      setBody(data.body_md ?? "");
    } catch {
      setBody("Failed to load page content.");
    } finally {
      setLoading(false);
    }
  };

  const generate = async () => {
    if (!genPath.trim()) return;
    setGenerating(true);
    setErr(null);
    try {
      const r = await apiFetch(`${API_BASE}/wiki/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_path: genPath.trim() }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(j.detail ?? `generate ${r.status}`);
      }
      await r.json();
      setRepo(genPath.trim());
      await fetchList();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "generation failed");
    } finally {
      setGenerating(false);
    }
  };

  const switchRepo = (r: string) => {
    setRepo(r);
    setActive(null);
    setBody("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-bg/90 backdrop-blur-sm">
      <div className="flex w-full max-w-5xl flex-col mx-4 my-6 rounded-xl border border-bd/[0.08] bg-surface shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-bd/[0.07] px-5 py-3">
          <h2 className="text-base font-semibold text-tx">Knowledge Wiki</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-tx-dim hover:bg-bd/[0.08] hover:text-tx transition-colors"
          >
            ×
          </button>
        </div>

        {/* Generate bar */}
        <div className="flex items-center gap-2 border-b border-bd/[0.05] px-5 py-2.5">
          <input
            type="text"
            placeholder="Repo path (e.g. C:/projects/myapp)"
            value={genPath}
            onChange={(e) => setGenPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void generate()}
            className="flex-1 rounded-md border border-bd/[0.1] bg-bg px-3 py-1.5 text-sm text-tx placeholder:text-tx-mut focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
          />
          <button
            type="button"
            onClick={() => void generate()}
            disabled={generating || !genPath.trim()}
            className="rounded-md bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent hover:bg-accent/20 disabled:opacity-40 transition-colors"
          >
            {generating ? "Generating…" : "Generate"}
          </button>
        </div>

        {err && (
          <div className="px-5 py-2 text-xs text-red-400">{err}</div>
        )}

        {/* Repo chips */}
        {repos.length > 1 && (
          <div className="flex gap-1.5 border-b border-bd/[0.05] px-5 py-2 overflow-x-auto">
            {repos.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => switchRepo(r)}
                className={`shrink-0 rounded-md px-2.5 py-1 text-xs transition-colors ${
                  r === repo
                    ? "bg-accent/10 text-accent"
                    : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
                }`}
              >
                {r.split(/[/\\]/).pop() ?? r}
              </button>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="flex flex-1 overflow-hidden">
          {/* Page list */}
          <div className="w-56 shrink-0 overflow-y-auto border-r border-bd/[0.06] py-2">
            {pages.length === 0 ? (
              <p className="px-4 py-3 text-xs text-tx-mut">
                No wiki pages yet. Enter a repo path above and click Generate.
              </p>
            ) : (
              pages.map((pg) => (
                <button
                  key={pg.path}
                  type="button"
                  onClick={() => void openPage(pg)}
                  className={`block w-full text-left px-4 py-2 text-sm transition-colors ${
                    active?.path === pg.path
                      ? "bg-accent/8 text-tx"
                      : "text-tx-dim hover:bg-bd/[0.04] hover:text-tx"
                  }`}
                >
                  <span className="block truncate">{pg.title}</span>
                  <span className="block text-[10px] text-tx-mut truncate">
                    {pg.path}
                  </span>
                </button>
              ))
            )}
          </div>

          {/* Page content */}
          <div className="flex-1 overflow-y-auto px-6 py-4">
            {loading ? (
              <p className="text-sm text-tx-mut animate-pulse">Loading…</p>
            ) : active ? (
              <article className="prose-wiki">
                <h1 className="text-lg font-semibold text-tx mb-3">
                  {active.title}
                </h1>
                <div className="text-sm text-tx leading-relaxed whitespace-pre-wrap font-mono">
                  {body}
                </div>
                {active.model && (
                  <p className="mt-4 text-[10px] text-tx-mut">
                    Generated by {active.model}
                    {active.refreshed_at
                      ? ` · ${new Date(active.refreshed_at * 1000).toLocaleDateString()}`
                      : ""}
                  </p>
                )}
              </article>
            ) : (
              <p className="text-sm text-tx-mut">
                Select a page from the list, or generate a new wiki.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

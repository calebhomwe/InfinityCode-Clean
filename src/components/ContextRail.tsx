import { useMemo, useState } from "react";
import { useWorkspace, type WorkspaceTreeEntry } from "../hooks/useWorkspace";

function FileGlyph({ type }: { type: WorkspaceTreeEntry["type"] }): JSX.Element {
  if (type === "dir") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M3 6.5h6l2 2h10v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function filterTree(entries: WorkspaceTreeEntry[], query: string): WorkspaceTreeEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  return entries.flatMap((entry) => {
    if (entry.type === "file") {
      return entry.name.toLowerCase().includes(needle) ? [entry] : [];
    }
    const children = filterTree(entry.children ?? [], query);
    return entry.name.toLowerCase().includes(needle) || children.length > 0
      ? [{ ...entry, children }]
      : [];
  });
}

function TreeRow({
  entry,
  path,
  depth,
  searching,
  selected,
  onSelect,
}: {
  entry: WorkspaceTreeEntry;
  path: string;
  depth: number;
  searching: boolean;
  selected: string | null;
  onSelect: (path: string) => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(depth < 1);
  const open = searching || expanded;
  const fullPath = path ? `${path}/${entry.name}` : entry.name;
  const isDirectory = entry.type === "dir";

  return (
    <>
      <button
        type="button"
        className={`light-sweep-control context-tree-row ${selected === fullPath ? "context-tree-row--selected" : ""}`}
        style={{ paddingLeft: 10 + depth * 14 }}
        onClick={() => {
          if (isDirectory) setExpanded((value) => !value);
          else onSelect(fullPath);
        }}
        title={fullPath}
      >
        <svg className={`context-tree-chevron ${isDirectory && open ? "rotate-90" : ""} ${isDirectory ? "" : "opacity-0"}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="m9 6 6 6-6 6" />
        </svg>
        <span className="context-tree-glyph"><FileGlyph type={entry.type} /></span>
        <span className="light-sweep-text truncate">{entry.name}</span>
      </button>
      {isDirectory && open && (entry.children ?? []).map((child) => (
        <TreeRow
          key={`${fullPath}/${child.name}`}
          entry={child}
          path={fullPath}
          depth={depth + 1}
          searching={searching}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </>
  );
}

export default function ContextRail(): JSX.Element {
  const { workspace, loading } = useWorkspace();
  const [open, setOpen] = useState<boolean>(
    () => localStorage.getItem("infinity-context-rail") !== "0",
  );
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const tree = useMemo(() => filterTree(workspace.tree, query), [workspace.tree, query]);
  const rootName = workspace.path.split(/[\\/]/).filter(Boolean).pop() ?? "Workspace";

  const toggle = (): void => {
    setOpen((current) => {
      const next = !current;
      localStorage.setItem("infinity-context-rail", next ? "1" : "0");
      return next;
    });
  };

  const copyPath = async (): Promise<void> => {
    if (!selected) return;
    const value = workspace.path ? `${workspace.path}\\${selected.replace(/\//g, "\\")}` : selected;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <aside className="context-rail-shell flex-none" aria-label="Coding sidebar">
      {open && (
        <section className="context-panel panel-enter" aria-label="Files">
          <header className="context-panel__header">
            <div>
              <p className="context-panel__eyebrow">Workspace</p>
              <h2>Files</h2>
            </div>
            <button type="button" onClick={toggle} aria-label="Close files panel" title="Close files panel">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="m8 5 8 7-8 7" /></svg>
            </button>
          </header>

          <label className="context-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter files" />
          </label>

          <div className="context-root">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6.5h6l2 2h10v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
            <span className="truncate">{workspace.valid ? rootName : "No workspace"}</span>
          </div>

          <div className="context-tree">
            {loading ? (
              <div className="context-empty">Loading files…</div>
            ) : !workspace.valid ? (
              <div className="context-empty">Open a project folder from the left sidebar to browse its files here.</div>
            ) : tree.length === 0 ? (
              <div className="context-empty">{query ? "No matching files." : "This workspace is empty."}</div>
            ) : (
              tree.map((entry) => (
                <TreeRow
                  key={entry.name}
                  entry={entry}
                  path=""
                  depth={0}
                  searching={query.trim().length > 0}
                  selected={selected}
                  onSelect={setSelected}
                />
              ))
            )}
          </div>

          {selected && (
            <footer className="context-selection">
              <span className="truncate" title={selected}>{selected}</span>
              <button type="button" onClick={() => void copyPath()}>{copied ? "Copied" : "Copy path"}</button>
            </footer>
          )}
        </section>
      )}

      <nav className="context-activity" aria-label="Coding tools">
        <button type="button" onClick={toggle} className={open ? "context-activity__active" : ""} aria-label="Files" title="Files">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6.5h6l2 2h10v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
        </button>
      </nav>
    </aside>
  );
}

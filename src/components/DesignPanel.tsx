import { useMemo, useState } from "react";
import { toSrcDoc, type Artifact } from "../lib/artifact";

export interface DesignPanelProps {
  artifact: Artifact;
  onClose: () => void;
}

type Tab = "preview" | "code";

/** Infinity Design: a live preview panel for generated HTML / SVG (Artifacts). */
export default function DesignPanel({
  artifact,
  onClose,
}: DesignPanelProps): JSX.Element {
  const [tab, setTab] = useState<Tab>("preview");
  const [copied, setCopied] = useState<boolean>(false);
  const srcDoc = useMemo(() => toSrcDoc(artifact), [artifact]);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(artifact.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be blocked; ignore.
    }
  };

  const download = (): void => {
    try {
      const ext = artifact.kind === "svg" ? "svg" : "html";
      const blob = new Blob([artifact.code], {
        type: artifact.kind === "svg" ? "image/svg+xml" : "text/html",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `infinity-design.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // Ignore download failures.
    }
  };

  const openInBrowser = (): void => {
    try {
      const blob = new Blob([srcDoc], { type: "text/html" });
      window.open(URL.createObjectURL(blob), "_blank");
    } catch {
      // Ignore.
    }
  };

  const tabClass = (t: Tab): string =>
    `px-3 py-1 rounded-md text-xs font-medium transition-colors ${
      tab === t
        ? "bg-surface text-tx"
        : "text-tx-mut hover:text-tx-dim"
    }`;

  const iconBtn =
    "h-7 w-7 rounded-lg flex items-center justify-center text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors";

  return (
    <div className="w-[46%] min-w-[400px] flex-none border-l border-bd/[0.07] flex flex-col bg-bg">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-bd/[0.07]">
        <div className="flex items-center gap-2 min-w-0">
          <svg
            className="w-4 h-4 accent-text flex-none"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 19l7-7 3 3-7 7-3-3z" />
            <path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z" />
            <path d="M2 2l7.586 7.586" />
            <circle cx="11" cy="11" r="2" />
          </svg>
          <span className="text-sm font-medium text-tx truncate">
            {artifact.title}
          </span>
          <span className="text-[10px] uppercase tracking-wider text-tx-mut font-mono">
            Infinity Design
          </span>
        </div>
        <div className="flex items-center gap-1">
          <div className="flex gap-1 rounded-lg bg-bd/[0.04] p-0.5 mr-1">
            <button type="button" className={tabClass("preview")} onClick={() => setTab("preview")}>
              Preview
            </button>
            <button type="button" className={tabClass("code")} onClick={() => setTab("code")}>
              Code
            </button>
          </div>
          <button type="button" onClick={() => void copy()} className={iconBtn} title="Copy code">
            {copied ? (
              <svg className="w-4 h-4 accent-text" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 6L9 17l-5-5" />
              </svg>
            ) : (
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
              </svg>
            )}
          </button>
          <button type="button" onClick={download} className={iconBtn} title="Download">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
              <path d="M7 10l5 5 5-5" />
              <path d="M12 15V3" />
            </svg>
          </button>
          <button type="button" onClick={openInBrowser} className={iconBtn} title="Open in browser">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
              <path d="M15 3h6v6" />
              <path d="M10 14L21 3" />
            </svg>
          </button>
          <button type="button" onClick={onClose} className={iconBtn} title="Close" aria-label="Close design panel">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 p-3">
        {tab === "preview" ? (
          <iframe
            title="Infinity Design preview"
            srcDoc={srcDoc}
            sandbox="allow-scripts allow-forms"
            referrerPolicy="no-referrer"
            loading="lazy"
            className="w-full h-full rounded-lg border border-bd/[0.07] bg-white"
          />
        ) : (
          <pre className="w-full h-full overflow-auto rounded-lg border border-bd/[0.07] bg-bg p-4 text-xs font-mono text-tx leading-relaxed">
            <code>{artifact.code}</code>
          </pre>
        )}
      </div>
    </div>
  );
}

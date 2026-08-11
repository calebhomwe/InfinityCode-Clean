import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, API_BASE } from "../lib/api";

/**
 * In-app web browser (docs/BROWSER.md).
 *
 * Pages are fetched by the backend proxy (/api/v1/browser/fetch), rewritten
 * so every navigation stays same-origin with the API, and rendered in a
 * sandboxed iframe WITHOUT allow-same-origin — the remote page is an opaque
 * origin that can never reach the app shell. An injected script posts each
 * navigation back ({__infinityNav}) so the address bar stays truthful.
 * "Send to agent" extracts readable text (/api/v1/browser/text) and hands it
 * to the chat composer via the infinity:compose event.
 */

interface ToolStatus {
  configured: boolean;
  enabled: boolean;
  launcher_ready: boolean;
}

interface BrowserStatus {
  playwright: ToolStatus;
  browser_use: ToolStatus;
  node: boolean;
}

const HOME_URL = "https://duckduckgo.com/";
const MAX_SEND_CHARS = 6000;

function withScheme(raw: string): string {
  const t = raw.trim();
  if (!t) return "";
  return /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(t) ? t : `https://${t}`;
}

export default function BrowserView(): JSX.Element {
  const [url, setUrl] = useState<string>(HOME_URL);
  const [address, setAddress] = useState<string>(HOME_URL);
  const [history, setHistory] = useState<string[]>([HOME_URL]);
  const [index, setIndex] = useState<number>(0);
  const [frameKey, setFrameKey] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState<boolean>(false);
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);

  // Agent-side browsing tools readiness (Settings -> MCP servers manages them).
  useEffect(() => {
    let mounted = true;
    void apiFetch(`${API_BASE}/browser/status`)
      .then((r) => (r.ok ? (r.json() as Promise<BrowserStatus>) : null))
      .then((body) => {
        if (mounted && body) setStatus(body);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  // Re-focus the iframe when the view becomes visible so typing lands in the
  // page immediately.
  useEffect(() => {
    const t = window.setTimeout(() => frameRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, []);

  // Nav bridge: the proxied page posts {__infinityNav: location.href} on
  // load. The iframe's real location is the proxy URL, so decode the target
  // page back out of the query string for a truthful address bar.
  useEffect(() => {
    const onMessage = (e: MessageEvent): void => {
      const d = e.data as { __infinityNav?: unknown };
      if (!d || typeof d.__infinityNav !== "string") return;
      let page = d.__infinityNav;
      try {
        const u = new URL(page);
        if (u.pathname === "/api/v1/browser/fetch") {
          const target = u.searchParams.get("url");
          if (target) page = decodeURIComponent(target);
        }
      } catch {
        // Keep the raw value.
      }
      setAddress(page);
      setUrl(page);
      setError(null);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const navigate = useCallback(
    (raw: string, push = true): void => {
      const target = withScheme(raw);
      if (!target) return;
      setUrl(target);
      setAddress(target);
      setError(null);
      setLoading(true);
      if (push) {
        setHistory((h) => [...h.slice(0, index + 1), target]);
        setIndex((i) => i + 1);
      }
    },
    [index],
  );

  const goBack = (): void => {
    if (index <= 0) return;
    const target = history[index - 1];
    setIndex(index - 1);
    setUrl(target);
    setAddress(target);
  };

  const goForward = (): void => {
    if (index >= history.length - 1) return;
    const target = history[index + 1];
    setIndex(index + 1);
    setUrl(target);
    setAddress(target);
  };

  const reload = (): void => setFrameKey((k) => k + 1);

  const openExternal = (): void => {
    window.open(url, "_blank", "noopener");
  };

  const sendToAgent = async (): Promise<void> => {
    setSending(true);
    setError(null);
    try {
      const r = await apiFetch(`${API_BASE}/browser/text?url=${encodeURIComponent(url)}`);
      if (!r.ok) {
        const detail = await r.text();
        setError(`Could not read the page: ${detail.slice(0, 220)}`);
        return;
      }
      const body = (await r.json()) as {
        url: string;
        title: string;
        description: string;
        text: string;
        char_count: number;
      };
      const excerpt = body.text.slice(0, MAX_SEND_CHARS);
      const lines = [
        "Help me with this page I found while browsing:",
        "",
        `URL: ${body.url}`,
        body.title ? `Title: ${body.title}` : null,
        body.description ? `Description: ${body.description}` : null,
        "",
        "Page text:",
        excerpt,
        body.text.length > excerpt.length
          ? `[page text truncated at ${MAX_SEND_CHARS.toLocaleString()} characters]`
          : null,
      ].filter((l) => l !== null);
      window.dispatchEvent(
        new CustomEvent("infinity:compose", { detail: { text: lines.join("\n") } }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read the page");
    } finally {
      setSending(false);
    }
  };

  const iframeSrc = `${API_BASE}/browser/fetch?url=${encodeURIComponent(url)}`;

  const toolState = (t: ToolStatus): "ready" | "off" | "missing" =>
    t.enabled && t.launcher_ready && t.configured
      ? "ready"
      : t.configured
        ? "off"
        : "missing";

  const statusChip = (label: string, t: ToolStatus | boolean): JSX.Element => {
    const state = typeof t === "boolean" ? (t ? "ready" : "missing") : toolState(t);
    const dot =
      state === "ready"
        ? "bg-emerald-400"
        : state === "off"
          ? "bg-amber-400"
          : "bg-tx-mut/40";
    const title =
      typeof t === "boolean"
        ? t
          ? "Node.js launcher present"
          : "Node.js not found on PATH"
        : `${label}: ${t.configured ? "configured" : "not configured"} · ${
            t.enabled ? "enabled" : "disabled"
          } · launcher ${t.launcher_ready ? "ready" : "missing"}`;
    return (
      <button
        key={label}
        type="button"
        title={title}
        onClick={() =>
          window.dispatchEvent(
            new CustomEvent("infinity:open-settings", { detail: { tab: "mcp" } }),
          )
        }
        className="flex items-center gap-1.5 rounded-md border border-bd/[0.08] bg-surface px-2 py-1 text-[11px] font-mono text-tx-dim hover:border-accent/40 hover:text-tx transition-colors press"
      >
        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} aria-hidden="true" />
        {label}
      </button>
    );
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-bd/[0.07] bg-surface/40">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={goBack}
            disabled={index <= 0}
            aria-label="Back"
            title="Back"
            className="rounded-lg border border-bd/[0.08] p-1.5 text-tx-dim hover:text-tx hover:border-accent/40 disabled:opacity-35 disabled:hover:border-bd/[0.08] transition-colors press"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
          <button
            type="button"
            onClick={goForward}
            disabled={index >= history.length - 1}
            aria-label="Forward"
            title="Forward"
            className="rounded-lg border border-bd/[0.08] p-1.5 text-tx-dim hover:text-tx hover:border-accent/40 disabled:opacity-35 disabled:hover:border-bd/[0.08] transition-colors press"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>
          <button
            type="button"
            onClick={reload}
            aria-label="Reload"
            title="Reload"
            className="rounded-lg border border-bd/[0.08] p-1.5 text-tx-dim hover:text-tx hover:border-accent/40 transition-colors press"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21 12a9 9 0 11-2.64-6.36M21 3v6h-6" />
            </svg>
          </button>
        </div>

        <form
          className="flex flex-1 items-center gap-2 min-w-52"
          onSubmit={(e) => {
            e.preventDefault();
            navigate(address);
          }}
        >
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="Search or enter address"
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-lg border border-bd/[0.1] bg-surface-2 px-3 py-1.5 text-sm text-tx outline-none focus:border-accent/50 transition-colors"
          />
          <button
            type="submit"
            className="rounded-lg border border-bd/[0.1] bg-surface px-3 py-1.5 text-sm text-tx-dim hover:text-tx hover:border-accent/50 transition-colors press"
          >
            Go
          </button>
        </form>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={openExternal}
            title="Open this page in your system browser"
            className="rounded-lg border border-bd/[0.08] bg-surface px-2.5 py-1.5 text-xs text-tx-dim hover:text-tx hover:border-accent/40 transition-colors press"
          >
            Open in browser
          </button>
          <button
            type="button"
            onClick={() => void sendToAgent()}
            disabled={sending}
            title="Extract readable text and drop it in the chat composer"
            className="rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1.5 text-xs text-tx hover:bg-accent/[0.14] disabled:opacity-50 transition-colors press"
          >
            {sending ? "Reading…" : "Send to agent"}
          </button>
        </div>

        {status && (
          <div className="flex items-center gap-1.5">
            {statusChip("Playwright", status.playwright)}
            {statusChip("Browser Use", status.browser_use)}
            {statusChip("Node", status.node)}
          </div>
        )}
      </div>

      {error && (
        <div className="mx-3 mt-2 rounded-lg border border-error/20 bg-error/[0.06] text-error text-xs px-3 py-2">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 px-3 pt-2 text-[11px] font-mono text-tx-mut">
          <span
            className="w-3 h-3 rounded-full border border-tx-mut/30 border-t-accent animate-spin"
            aria-hidden="true"
          />
          Fetching {url}
        </div>
      )}

      <iframe
        key={frameKey}
        ref={frameRef}
        title={`Browser: ${url}`}
        src={iframeSrc}
        onLoad={() => setLoading(false)}
        className="flex-1 w-full border-0 bg-bg"
        sandbox="allow-scripts allow-forms allow-popups allow-modals"
      />
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

export interface PaletteCommand {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

interface SearchResult {
  chat_id: string;
  title: string;
  snippet: string;
}

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
  onOpenChat: (id: string) => void;
}

/** Ctrl+K palette: fuzzy commands + full-text search across every chat. */
export default function CommandPalette({
  open,
  onClose,
  commands,
  onOpenChat,
}: CommandPaletteProps): JSX.Element | null {
  const [query, setQuery] = useState<string>("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [active, setActive] = useState<number>(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset + focus on open.
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      setActive(0);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // Debounced full-text search.
  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setResults([]);
      return;
    }
    const handle = window.setTimeout(() => {
      void apiFetch(`${API_BASE}/search?q=${encodeURIComponent(query.trim())}`)
        .then((r) => (r.ok ? r.json() : { results: [] }))
        .then((data: { results: SearchResult[] }) => setResults(data.results))
        .catch(() => setResults([]));
    }, 160);
    return () => window.clearTimeout(handle);
  }, [query, open]);

  if (!open) {
    return null;
  }

  const q = query.trim().toLowerCase();
  const filteredCommands = q
    ? commands.filter((c) => c.label.toLowerCase().includes(q))
    : commands;
  const total = filteredCommands.length + results.length;
  const clamped = Math.min(active, Math.max(0, total - 1));

  const runIndex = (index: number): void => {
    if (index < filteredCommands.length) {
      filteredCommands[index].run();
    } else {
      const hit = results[index - filteredCommands.length];
      if (hit) {
        onOpenChat(hit.chat_id);
      }
    }
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-[2px] flex items-start justify-center pt-[18vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="fade-in-up w-[560px] max-w-[92vw] rounded-xl border border-bd/[0.12] bg-surface shadow-2xl shadow-black/60 overflow-hidden">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              onClose();
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, total - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter" && total > 0) {
              e.preventDefault();
              runIndex(clamped);
            }
          }}
          placeholder="Search chats or type a command..."
          className="w-full bg-transparent border-0 border-b border-bd/[0.08] px-4 py-3.5 text-[15px] text-tx placeholder-tx-mut focus:outline-none"
        />
        <div className="max-h-[46vh] overflow-y-auto p-1.5">
          {filteredCommands.length > 0 && (
            <p className="px-2.5 pt-1.5 pb-1 text-[11px] text-tx-mut">Commands</p>
          )}
          {filteredCommands.map((cmd, i) => (
            <button
              key={cmd.id}
              type="button"
              onClick={() => runIndex(i)}
              onMouseEnter={() => setActive(i)}
              className={`w-full flex items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left transition-colors ${
                clamped === i ? "bg-bd/[0.07]" : ""
              }`}
            >
              <span className="text-sm text-tx">{cmd.label}</span>
              {cmd.hint && (
                <span className="font-mono text-[10px] text-tx-mut">{cmd.hint}</span>
              )}
            </button>
          ))}
          {results.length > 0 && (
            <p className="px-2.5 pt-2 pb-1 text-[11px] text-tx-mut">Chats</p>
          )}
          {results.map((hit, i) => {
            const index = filteredCommands.length + i;
            return (
              <button
                key={hit.chat_id + String(i)}
                type="button"
                onClick={() => runIndex(index)}
                onMouseEnter={() => setActive(index)}
                className={`w-full rounded-lg px-2.5 py-2 text-left transition-colors ${
                  clamped === index ? "bg-bd/[0.07]" : ""
                }`}
              >
                <span className="block text-sm text-tx truncate">{hit.title}</span>
                <span className="block text-xs text-tx-mut truncate mt-0.5">
                  {hit.snippet}
                </span>
              </button>
            );
          })}
          {total === 0 && (
            <p className="px-2.5 py-6 text-center text-sm text-tx-mut">
              No matches.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

import { useEffect, useRef } from "react";
import { API_BASE } from "../lib/api";

/**
 * Browser IDE mode — embeds the self-contained Replit-style IDE
 * (public/repl.html) in a full-bleed iframe. Files are persisted by the
 * backend workspace API with localStorage retained as an offline cache.
 * Session and agent settings remain local to avoid leaking credentials.
 */
export default function ReplMode(): JSX.Element {
  const ref = useRef<HTMLIFrameElement>(null);

  // Re-focus the iframe whenever the mode becomes visible so keyboard
  // shortcuts (Ctrl+K palette etc.) land inside the IDE immediately.
  useEffect(() => {
    const t = window.setTimeout(() => ref.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, []);

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-[#0e1525]">
      <iframe
        ref={ref}
        title="Browser IDE"
        src={`/repl.html?api=${encodeURIComponent(API_BASE)}`}
        className="w-full h-full border-0"
        sandbox="allow-scripts allow-same-origin allow-modals allow-forms allow-popups allow-downloads"
      />
    </div>
  );
}

// Horizontal editor-style tab bar. Renders the open tab set, highlights the
// active one, and lets the user click to activate / middle-click to close.
// Deliberately dumb: state lives in useTabs, this component only shows it.

import { useRef, useEffect, useState, useCallback } from "react";
import type { Tab, TabKind } from "../hooks/useTabs";

function KindIcon({ kind }: { kind: TabKind }): JSX.Element {
  const common = { className: "h-3.5 w-3.5", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (kind === "chat") return <svg {...common}><path d="M5 5h14v10H9l-4 4V5z" /></svg>;
  if (kind === "mission") return <svg {...common}><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /><path d="M12 4v2M20 12h-2M12 20v-2M4 12h2" /></svg>;
  if (kind === "agent-lib") return <svg {...common}><circle cx="12" cy="8" r="3" /><path d="M5 20c.8-3.6 3-5.4 7-5.4s6.2 1.8 7 5.4" /></svg>;
  if (kind === "vault") return <svg {...common}><path d="M4 5h16v14H4z" /><path d="M8 9h8M8 13h5" /></svg>;
  if (kind === "settings") return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 01-.2 1.6l2 1.5-2 3.4-2.4-1a8 8 0 01-2.7 1.5L13.5 21h-3l-.3-2a8 8 0 01-2.7-1.5l-2.4 1-2-3.4 2-1.5A7 7 0 015 12c0-.6.1-1.1.2-1.6l-2-1.5 2-3.4 2.4 1A8 8 0 0110.5 5l.3-2h3l.3 2a8 8 0 012.7 1.5l2.4-1 2 3.4-2 1.5c.1.5.2 1 .2 1.6z" /></svg>;
  return <svg {...common}><circle cx="12" cy="12" r="8" /><path d="M12 8v4l2.5 2.5" /></svg>;
}

export interface TabBarProps {
  tabs: Tab[];
  activeId: string | null;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onNewChat?: () => void;
}

export default function TabBar({
  tabs,
  activeId,
  onActivate,
  onClose,
  onNewChat,
}: TabBarProps): JSX.Element {
  const tabListRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [pillStyle, setPillStyle] = useState<{ transform: string; width: number; opacity: number }>({
    transform: "translateX(0px)",
    width: 0,
    opacity: 0,
  });

  const measurePill = useCallback(() => {
    if (!activeId || !tabListRef.current) {
      setPillStyle((s) => ({ ...s, opacity: 0 }));
      return;
    }
    const el = tabRefs.current[activeId];
    if (!el) {
      setPillStyle((s) => ({ ...s, opacity: 0 }));
      return;
    }
    const listRect = tabListRef.current.getBoundingClientRect();
    const tabRect = el.getBoundingClientRect();
    setPillStyle({
      transform: `translateX(${tabRect.left - listRect.left + tabListRef.current.scrollLeft}px)`,
      width: tabRect.width,
      opacity: 1,
    });
  }, [activeId]);

  useEffect(() => {
    measurePill();
    const list = tabListRef.current;
    if (!list) return;
    const ro = new ResizeObserver(measurePill);
    ro.observe(list);
    return () => ro.disconnect();
  }, [measurePill]);

  useEffect(() => {
    measurePill();
  }, [tabs, measurePill]);

  // Always mounted: the reserved strip keeps the layout stable when the
  // first tab opens, and the + button stays reachable with zero tabs.
  return (
    <div
      className="tab-bar shell-enter stagger-3 flex items-center gap-1 border-b border-bd/[0.07] bg-surface px-2 overflow-x-auto"
    >
      {tabs.length > 0 && (
        <div
          ref={tabListRef}
          role="tablist"
          aria-label="Open tabs"
          className="tab-list flex h-[30px] items-center gap-1 relative"
        >
          {/* Gliding active pill — transform only (60fps) */}
          <div
            className="tab-active-pill pill-glide absolute top-0 bottom-0 pointer-events-none"
            style={{
              width: pillStyle.width,
              transform: pillStyle.transform,
              opacity: pillStyle.opacity,
              willChange: "transform, opacity",
            }}
          />
          {tabs.map((t) => {
            const active = t.id === activeId;
            return (
              <div
                key={t.id}
                ref={(el) => { tabRefs.current[t.id] = el; }}
                role="tab"
                aria-selected={active}
                tabIndex={0}
                className={`light-sweep-control tab-item group flex h-[30px] min-w-[112px] max-w-[210px] items-center gap-2 rounded-lg px-2.5 text-[12.5px] cursor-pointer select-none relative z-10 ${
                  active
                    ? "text-tx"
                    : "text-tx-dim hover:text-tx"
                }`}
                onClick={() => onActivate(t.id)}
                onKeyDown={(e) => {
                  // Keyboard activation matches the click behaviour.
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onActivate(t.id);
                  }
                }}
                onAuxClick={(e) => {
                  if (e.button === 1) {
                    // Middle-click closes, matching VS Code and browsers.
                    e.preventDefault();
                    onClose(t.id);
                  }
                }}
                title={t.title}
              >
                <span aria-hidden="true" className={`tab-kind flex-none ${active ? "accent-text" : "text-tx-mut"}`}><KindIcon kind={t.kind} /></span>
                <span className="light-sweep-text min-w-0 flex-1 truncate">{t.title}</span>
                <button
                  type="button"
                  aria-label={`Close ${t.title}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onClose(t.id);
                  }}
                  className={`tab-close flex h-[18px] w-[18px] flex-none items-center justify-center rounded-md hover:bg-bd/[0.1] ${
                    active ? "opacity-45 group-hover:opacity-90" : "opacity-0 group-hover:opacity-70"
                  }`}
                >
                  <svg
                    className="w-3 h-3"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  >
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>
            );
          })}
        </div>
      )}
      {onNewChat && (
        <button
          type="button"
          onClick={onNewChat}
          aria-label="New chat tab"
          title="New chat tab (Ctrl+T)"
          className="tab-new-button flex h-[30px] w-[30px] flex-none items-center justify-center rounded-lg text-tx-mut hover:text-tx"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      )}
    </div>
  );
}

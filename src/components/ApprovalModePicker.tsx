import type { RefObject } from "react";

export type ApprovalMode = "ask" | "smart" | "full" | "custom";

export const APPROVAL_MODES: Record<
  ApprovalMode,
  { label: string; description: string }
> = {
  ask: {
    label: "Ask first",
    description: "Ask before internet, files, screen access, or actions.",
  },
  smart: {
    label: "Smart access",
    description: "Only pause for actions detected as potentially unsafe.",
  },
  full: {
    label: "Full access",
    description: "Run enabled tools without per-step approval prompts.",
  },
  custom: {
    label: "Custom",
    description: "Use the advanced approval rules from your configuration.",
  },
};

function ShieldIcon({ className = "" }: { className?: string }): JSX.Element {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3 4.5 6v5.7c0 4.7 3.2 7.9 7.5 9.3 4.3-1.4 7.5-4.6 7.5-9.3V6L12 3Z" />
      <path d="M12 8v4.5" />
      <path d="M12 16h.01" />
    </svg>
  );
}

function ModeIcon({ mode }: { mode: ApprovalMode }): JSX.Element {
  if (mode === "ask") {
    return <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 21s7-3.8 7-10V5l-7-2-7 2v6c0 6.2 7 10 7 10Z" /><path d="M9.5 12.5 11 14l3.5-4" /></svg>;
  }
  if (mode === "smart") {
    return <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 4.5 6v5.7c0 4.7 3.2 7.9 7.5 9.3 4.3-1.4 7.5-4.6 7.5-9.3V6L12 3Z" /><path d="m9.5 12 1.7 1.7 3.8-4" /></svg>;
  }
  if (mode === "full") return <ShieldIcon className="h-4 w-4" />;
  return <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.65 1.65 0 0 0 15.08 19a1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6h.09A1.65 1.65 0 0 0 10 3.09V3a2 2 0 0 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09A1.65 1.65 0 0 0 20.91 10H21a2 2 0 0 1 0 4h-.09A1.65 1.65 0 0 0 19.4 15Z" /></svg>;
}

export function isApprovalMode(value: string | null): value is ApprovalMode {
  return value === "ask" || value === "smart" || value === "full" || value === "custom";
}

export default function ApprovalModePicker({
  mode,
  open,
  onToggle,
  onChange,
  containerRef,
}: {
  mode: ApprovalMode;
  open: boolean;
  onToggle: () => void;
  onChange: (mode: ApprovalMode) => void;
  containerRef: RefObject<HTMLDivElement>;
}): JSX.Element {
  const current = APPROVAL_MODES[mode];
  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={`Agent access: ${current.label}`}
        className={`light-sweep-control h-8 rounded-full px-2.5 text-xs transition-colors flex items-center gap-1.5 ${
          mode === "full" ? "bg-accent/12 accent-text" : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
        }`}
      >
        <ModeIcon mode={mode} />
        <span className="light-sweep-text">{current.label}</span>
      </button>
      {open && (
        <div role="dialog" aria-label="Agent access mode" className="composer-popover-enter absolute bottom-full left-0 z-40 mb-2 w-[min(392px,calc(100vw-32px))] overflow-hidden rounded-2xl border border-bd/[0.12] bg-surface-2 p-1.5 shadow-2xl shadow-black/35">
          <div className="flex items-center justify-between gap-3 px-2.5 py-2">
            <p className="text-sm text-tx-dim">How should agent actions be approved?</p>
            <span className="text-[11px] text-tx-mut">Applies to new messages</span>
          </div>
          {(Object.keys(APPROVAL_MODES) as ApprovalMode[]).map((id) => {
            const option = APPROVAL_MODES[id];
            const selected = id === mode;
            return (
              <button
                key={id}
                type="button"
                onClick={() => onChange(id)}
                className={`flex w-full items-start gap-3 rounded-xl px-2.5 py-2.5 text-left transition-colors ${
                  selected ? "bg-bd/[0.11]" : "hover:bg-bd/[0.06]"
                } ${id === "full" ? "accent-text" : "text-tx"}`}
              >
                <span className="mt-0.5 flex-none"><ModeIcon mode={id} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{option.label}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-tx-mut">{option.description}</span>
                </span>
                {selected && (
                  <svg className="mt-0.5 h-4 w-4 flex-none" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-label="Selected">
                    <path d="m5 12 4.2 4.2L19 6.5" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

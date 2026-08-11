import { useEffect, useRef, type RefObject } from "react";
import {
  EFFORTS,
  MODES,
  TOOLS,
  type Effort,
  type Mode,
  type ToolId,
} from "../lib/composer";

/** Closes the popover on any pointerdown outside its root, only while open. */
function useOutsideClose(
  open: boolean,
  onClose: () => void
): RefObject<HTMLDivElement> {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) {
      return;
    }
    const handlePointerDown = (event: MouseEvent): void => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open, onClose]);
  return ref;
}

function CheckIcon(): JSX.Element {
  return (
    <svg
      className="w-3.5 h-3.5 accent-text flex-none"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M20 6L9 17l-5-5" />
    </svg>
  );
}

function BoltIcon({ className }: { className: string }): JSX.Element {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2L3 14h7l-1 8 11-14h-7z" />
    </svg>
  );
}

function K3Badge(): JSX.Element {
  return (
    <span className="flex-none px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-black bg-gradient-to-r from-cyan-400 to-blue-500 rounded">
      K3
    </span>
  );
}

function PaperclipIcon(): JSX.Element {
  return (
    <svg
      className="w-3.5 h-3.5 text-tx-dim flex-none"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21.44 11.05l-9.19 9.19a5.5 5.5 0 01-7.78-7.78l9.19-9.19a3.5 3.5 0 014.95 4.95l-9.19 9.19a1.5 1.5 0 01-2.12-2.12l8.49-8.49" />
    </svg>
  );
}

const popoverClass =
  "fade-in-up absolute bottom-full left-0 mb-2 z-20 w-[272px] rounded-xl border border-bd/[0.09] bg-surface-2 p-1.5 shadow-2xl shadow-black/50";
const sectionLabelClass =
  "px-2.5 pt-1.5 pb-1 text-[11px] text-tx-mut";
const rowClass =
  "w-full flex items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left hover:bg-bd/[0.06] transition-colors";
const shortcutClass =
  "text-[11px] text-tx-mut font-mono w-3 text-center flex-none";

interface RowProps {
  label: string;
  hint?: string;
  selected: boolean;
  shortcut?: number;
  right?: React.ReactNode;
  onClick: () => void;
}

/** A single menu row: title (+ optional subtitle), then check / badge / number. */
function Row({
  label,
  hint,
  selected,
  shortcut,
  right,
  onClick,
}: RowProps): JSX.Element {
  return (
    <button type="button" onClick={onClick} className={rowClass}>
      <span className="min-w-0">
        <span className="block text-sm text-tx truncate">{label}</span>
        {hint && (
          <span className="block text-[11px] text-tx-mut truncate">
            {hint}
          </span>
        )}
      </span>
      <span className="flex items-center gap-2 flex-none">
        {right}
        {selected ? (
          <CheckIcon />
        ) : (
          shortcut !== undefined && (
            <span className={shortcutClass}>{shortcut}</span>
          )
        )}
      </span>
    </button>
  );
}

export interface ModeMenuProps {
  open: boolean;
  value: Mode;
  onChange: (mode: Mode) => void;
  onClose: () => void;
}

export function ModeMenu({
  open,
  value,
  onChange,
  onClose,
}: ModeMenuProps): JSX.Element | null {
  const ref = useOutsideClose(open, onClose);
  if (!open) {
    return null;
  }
  return (
    <div ref={ref} className={popoverClass}>
      <p className={sectionLabelClass}>Mode</p>
      {MODES.map((mode, index) => (
        <Row
          key={mode.id}
          label={mode.label}
          hint={mode.hint}
          selected={value === mode.id}
          shortcut={index + 1}
          onClick={() => onChange(mode.id)}
        />
      ))}
    </div>
  );
}

export interface EffortMenuProps {
  open: boolean;
  value: Effort;
  onChange: (effort: Effort) => void;
  fast: boolean;
  onFastChange: (fast: boolean) => void;
  onClose: () => void;
}

export function EffortMenu({
  open,
  value,
  onChange,
  fast,
  onFastChange,
  onClose,
}: EffortMenuProps): JSX.Element | null {
  const ref = useOutsideClose(open, onClose);
  if (!open) {
    return null;
  }
  return (
    <div ref={ref} className={popoverClass}>
      <p className={sectionLabelClass}>Effort</p>
      {EFFORTS.map((effort, index) => (
        <Row
          key={effort.id}
          label={effort.label}
          hint={effort.hint}
          selected={value === effort.id}
          shortcut={index + 1}
          right={
            effort.bolt ? (
              <span className="flex items-center gap-0.5 text-[11px] accent-text">
                <BoltIcon className="w-3 h-3" />
                {effort.agents}
              </span>
            ) : (
              <span className="text-[11px] text-tx-mut whitespace-nowrap">
                {effort.agents}×
              </span>
            )
          }
          onClick={() => onChange(effort.id)}
        />
      ))}

      <div className="my-1 h-px bg-bd/[0.07]" />
      <p className={sectionLabelClass}>Speed</p>
      <button
        type="button"
        onClick={() => onFastChange(!fast)}
        className={rowClass}
      >
        <span className="min-w-0">
          <span className="block text-sm text-tx">Fast mode</span>
          <span className="block text-[11px] text-tx-mut">
            Prefer the quickest model
          </span>
        </span>
        <span
          className={`relative h-4 w-7 rounded-full flex-none transition-colors ${
            fast ? "bg-accent" : "bg-bd/[0.14]"
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 h-3 w-3 rounded-full bg-black transition-transform ${
              fast ? "translate-x-3" : "translate-x-0"
            }`}
          />
        </span>
      </button>
    </div>
  );
}

export interface PlusMenuProps {
  open: boolean;
  onClose: () => void;
  onAttachClick: () => void;
  uploading: boolean;
  referencePath: string;
  onReferencePathChange: (value: string) => void;
  referenceName?: string;
  referencePreview?: string;
  endReferencePath?: string;
  onEndReferencePathChange?: (value: string) => void;
  endReferenceName?: string;
  endReferencePreview?: string;
  onReferenceFile?: (file: File, slot: "start" | "end") => void;
  // Optional composer state: when provided, PlusMenu also renders Mode /
  // Effort / Tools sections inline so the composer only needs one visible
  // trigger button (Kimi-style restraint). When omitted the menu falls back
  // to Attach + Reference only for legacy callers.
  mode?: Mode;
  onModeChange?: (mode: Mode) => void;
  effort?: Effort;
  onEffortChange?: (effort: Effort) => void;
  fast?: boolean;
  onFastChange?: (fast: boolean) => void;
  visionLoop?: boolean;
  onVisionLoopChange?: (visionLoop: boolean) => void;
  speculative?: boolean;
  onSpeculativeChange?: (speculative: boolean) => void;
  tools?: ToolId[];
  onToolsChange?: (tools: ToolId[]) => void;
}

export function PlusMenu({
  open,
  onClose,
  onAttachClick,
  uploading,
  referencePath,
  onReferencePathChange,
  referenceName,
  referencePreview,
  endReferencePath = "",
  onEndReferencePathChange,
  endReferenceName,
  endReferencePreview,
  onReferenceFile,
  mode,
  onModeChange,
  effort,
  onEffortChange,
  fast,
  onFastChange,
  visionLoop,
  onVisionLoopChange,
  speculative,
  onSpeculativeChange,
  tools,
  onToolsChange,
}: PlusMenuProps): JSX.Element | null {
  const ref = useOutsideClose(open, onClose);
  if (!open) {
    return null;
  }
  const showMode = mode !== undefined && onModeChange !== undefined;
  const showEffort = effort !== undefined && onEffortChange !== undefined;
  const showTools = tools !== undefined && onToolsChange !== undefined;
  const toggleTool = (id: ToolId): void => {
    if (!tools || !onToolsChange) return;
    onToolsChange(tools.includes(id) ? tools.filter((t) => t !== id) : [...tools, id]);
  };

  const isVideo = mode === "video";

  const referenceSlot = (
    slot: "start" | "end",
    path: string,
    name: string | undefined,
    preview: string | undefined,
  ): JSX.Element => {
    const label = slot === "start" ? "Start frame" : "End frame";
    const inputId = `onebox-reference-${slot}`;
    return (
      <div
        className="group relative rounded-lg border border-dashed border-bd/[0.16] bg-surface p-2 transition-colors hover:border-accent/50"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files?.[0];
          if (file && file.type.startsWith("image/")) onReferenceFile?.(file, slot);
        }}
      >
        <input
          id={inputId}
          type="file"
          accept="image/*"
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onReferenceFile?.(file, slot);
            event.target.value = "";
          }}
        />
        <label htmlFor={inputId} className="flex cursor-pointer items-center gap-2">
          {preview ? (
            <img src={preview} alt="" className="h-10 w-10 rounded-md object-cover border border-bd/[0.1]" />
          ) : (
            <span className="flex h-10 w-10 items-center justify-center rounded-md bg-bd/[0.06] text-tx-mut" aria-hidden="true">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 16l4-5 3 3 3-4 6 6" /><rect x="3" y="4" width="18" height="16" rx="2" />
              </svg>
            </span>
          )}
          <span className="min-w-0">
            <span className="block text-xs font-medium text-tx">{label}</span>
            <span className="block truncate text-[11px] text-tx-mut">
              {name || (path ? path.split(/[\\/]/).pop() : "Drop or choose an image")}
            </span>
          </span>
        </label>
        {path && (
          <button
            type="button"
            aria-label={`Remove ${label.toLowerCase()}`}
            onClick={() => (slot === "end" ? onEndReferencePathChange?.("") : onReferencePathChange(""))}
            className="absolute right-1.5 top-1.5 rounded p-0.5 text-tx-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100"
          >
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        )}
      </div>
    );
  };
  return (
    // Wider popover to hold the extra sections cleanly.
    <div ref={ref} className={`${popoverClass} min-w-[240px]`}>
      <button
        type="button"
        onClick={() => {
          onAttachClick();
          onClose();
        }}
        disabled={uploading}
        className={`${rowClass} disabled:opacity-50 disabled:cursor-wait`}
      >
        <span className="flex items-center gap-2 text-sm text-tx">
          <PaperclipIcon />
          Attach files
        </span>
      </button>

      {showMode && (
        <>
          <div className="my-1 h-px bg-bd/[0.07]" />
          <p className={sectionLabelClass}>Mode</p>
          {MODES.map((m, index) => (
            <Row
              key={m.id}
              label={m.label}
              hint={m.hint}
              selected={mode === m.id}
              shortcut={index + 1}
              onClick={() => onModeChange?.(m.id)}
            />
          ))}
        </>
      )}

      {showEffort && (
        <>
          <div className="my-1 h-px bg-bd/[0.07]" />
          <p className={sectionLabelClass}>Effort</p>
          {EFFORTS.map((e, index) => (
            <Row
              key={e.id}
              label={e.label}
              hint={e.hint}
              selected={effort === e.id}
              shortcut={index + 1}
              right={
                e.bolt ? (
                  <span className="flex items-center gap-0.5 text-[11px] accent-text">
                    <BoltIcon className="w-3 h-3" />
                    {e.agents}
                  </span>
                ) : (
                  <span className="text-[11px] text-tx-mut whitespace-nowrap">
                    {e.agents}×
                  </span>
                )
              }
              onClick={() => onEffortChange?.(e.id)}
            />
          ))}
          {onFastChange !== undefined && (
            <button
              type="button"
              onClick={() => onFastChange?.(!fast)}
              className={rowClass}
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="block text-sm text-tx">Fast mode</span>
                  <K3Badge />
                </span>
                <span className="block text-[11px] text-tx-mut">
                  Partial Rollout RL — λNK early stopping
                </span>
              </span>
              <span
                className={`relative h-4 w-7 rounded-full flex-none transition-colors ${
                  fast ? "bg-accent" : "bg-bd/[0.14]"
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 h-3 w-3 rounded-full bg-black transition-transform ${
                    fast ? "translate-x-3" : "translate-x-0"
                  }`}
                />
              </span>
            </button>
          )}
          {onVisionLoopChange !== undefined && (
            <button
              type="button"
              onClick={() => onVisionLoopChange?.(!visionLoop)}
              className={rowClass}
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="block text-sm text-tx">Vision-in-the-loop</span>
                  <K3Badge />
                </span>
                <span className="block text-[11px] text-tx-mut">
                  Iterative visual critic with pixel-level feedback
                </span>
              </span>
              <span
                className={`relative h-4 w-7 rounded-full flex-none transition-colors ${
                  visionLoop ? "bg-accent" : "bg-bd/[0.14]"
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 h-3 w-3 rounded-full bg-black transition-transform ${
                    visionLoop ? "translate-x-3" : "translate-x-0"
                  }`}
                />
              </span>
            </button>
          )}
          {onSpeculativeChange !== undefined && (
            <button
              type="button"
              onClick={() => onSpeculativeChange?.(!speculative)}
              className={rowClass}
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="block text-sm text-tx">Speculative decode</span>
                  <K3Badge />
                </span>
                <span className="block text-[11px] text-tx-mut">
                  Draft-then-verify with small-big model cascade
                </span>
              </span>
              <span
                className={`relative h-4 w-7 rounded-full flex-none transition-colors ${
                  speculative ? "bg-accent" : "bg-bd/[0.14]"
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 h-3 w-3 rounded-full bg-black transition-transform ${
                    speculative ? "translate-x-3" : "translate-x-0"
                  }`}
                />
              </span>
            </button>
          )}
        </>
      )}

      {showTools && (
        <>
          <div className="my-1 h-px bg-bd/[0.07]" />
          <p className={sectionLabelClass}>Mission capabilities</p>
          {TOOLS.map((tool) => {
            const checked = tools!.includes(tool.id);
            return (
              <button
                key={tool.id}
                type="button"
                onClick={() => toggleTool(tool.id)}
                className={rowClass}
              >
                <span className="text-sm text-tx">{tool.label}</span>
                <span
                  className={`w-4 h-4 rounded flex items-center justify-center border flex-none transition-colors ${
                    checked
                      ? "bg-accent border-accent"
                      : "border-bd/[0.2] bg-transparent"
                  }`}
                >
                  {checked && (
                    <svg
                      className="w-3 h-3 text-black"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M20 6L9 17l-5-5" />
                    </svg>
                  )}
                </span>
              </button>
            );
          })}
        </>
      )}

      <div className="my-1 h-px bg-bd/[0.07]" />
      <div className="space-y-2 px-2.5 py-1.5">
        <p className="text-[11px] text-tx-dim">{isVideo ? "Frame-to-frame video" : "Reference image"}</p>
        {referenceSlot("start", referencePath, referenceName, referencePreview)}
        {isVideo && referenceSlot("end", endReferencePath, endReferenceName, endReferencePreview)}
        <input
          id="onebox-reference-path"
          type="text"
          value={referencePath}
          onChange={(event) => onReferencePathChange(event.target.value)}
          placeholder="Or paste a local path"
          className="w-full rounded-lg bg-surface border border-bd/[0.08] px-2.5 py-1.5 text-xs text-tx placeholder-tx-mut focus:outline-none focus:border-accent/50"
        />
        <p className="text-[11px] text-tx-mut">
          {isVideo ? "Add an end frame for a controlled transition. The start frame is required." : "Drop an image here or choose one from your device."}
        </p>
      </div>
    </div>
  );
}

export interface ToolsMenuProps {
  open: boolean;
  values: ToolId[];
  onChange: (tools: ToolId[]) => void;
  onClose: () => void;
}

export function ToolsMenu({
  open,
  values,
  onChange,
  onClose,
}: ToolsMenuProps): JSX.Element | null {
  const ref = useOutsideClose(open, onClose);
  if (!open) {
    return null;
  }
  const toggle = (id: ToolId): void => {
    if (values.includes(id)) {
      onChange(values.filter((toolId) => toolId !== id));
    } else {
      onChange([...values, id]);
    }
  };
  return (
    <div ref={ref} className={popoverClass}>
      <p className={sectionLabelClass}>Mission capabilities</p>
      {TOOLS.map((tool) => {
        const checked = values.includes(tool.id);
        return (
          <button
            key={tool.id}
            type="button"
            onClick={() => toggle(tool.id)}
            className={rowClass}
          >
            <span className="text-sm text-tx">{tool.label}</span>
            <span
              className={`w-4 h-4 rounded flex items-center justify-center border flex-none transition-colors ${
                checked
                  ? "bg-accent border-accent"
                  : "border-bd/[0.2] bg-transparent"
              }`}
            >
              {checked && (
                <svg
                  className="w-3 h-3 text-black"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              )}
            </span>
          </button>
        );
      })}
      <p className="text-[10px] text-tx-mut px-3 pt-2 pb-1 leading-snug border-t border-bd/[0.06] mt-1">
        Swarm mission flags. The full 15-tool toolbelt + MCP servers live in
        Chat &amp; Assistant (wrench) and Settings › Tools.
      </p>
    </div>
  );
}

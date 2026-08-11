import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
// Kimi-style restraint: only the PlusMenu is visible in the composer chrome.
// The old standalone ToolsMenu / ModeMenu / EffortMenu buttons are gone;
// PlusMenu now renders their sections inline.
import { PlusMenu } from "./ComposerMenus";
import AgentPicker, { type AgentLite } from "./AgentPicker";
import { SwarmDeploymentBadge } from "./SwarmDeploymentBadge";
import { toast } from "sonner";
import {
  DEFAULT_EFFORT,
  DEFAULT_MODE,
  DEFAULT_TOOLS,
  effortMeta,
  type Attachment,
  type ComposerSubmission,
  type Effort,
  type Mode,
  type ToolId,
} from "../lib/composer";
import { API_BASE } from "../lib/api";

const UPLOAD_URL = `${API_BASE}/upload`;

// Only the plus menu remains; the mode/effort/tools popovers are now inlined
// inside PlusMenu. The union stays as a discriminator in case future menus
// (attach picker, @ picker, etc.) are added.
type OpenMenu = "plus" | null;

/** Minimal shape of the Web Speech API this composer relies on. */
interface SpeechRecognitionResultLike {
  [index: number]: { transcript: string };
  length: number;
}
interface SpeechRecognitionEventLike extends Event {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}
interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}

interface ReferenceUpload {
  path: string;
  name: string;
  preview: string;
}
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const SpeechRecognitionCtor: SpeechRecognitionConstructor | undefined =
  (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

export interface OneBoxProps {
  onSubmit: (submission: ComposerSubmission) => Promise<void> | void;
  initialMode?: Mode;
  initialEffort?: Effort;
  initialFast?: boolean;
  initialVisionLoop?: boolean;
  initialSpeculative?: boolean;
  /** True when no LLM provider key is configured (first-run keyless UX).
   *  Sending is blocked with an inline hint instead of an opaque error. */
  noProviderConfigured?: boolean;
  /** Deep-links into Settings > Providers from the inline keyless hint. */
  onOpenProviderSettings?: () => void;
}

function PlusIcon(): JSX.Element {
  return (
    <svg
      className="w-4 h-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function SpinnerIcon({ className }: { className: string }): JSX.Element {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function MicIcon(): JSX.Element {
  return (
    <svg
      className="w-4 h-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0014 0" />
      <path d="M12 19v3" />
    </svg>
  );
}

function ArrowUpIcon(): JSX.Element {
  return (
    <svg
      className="w-4 h-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 19V5" />
      <path d="M5 12l7-7 7 7" />
    </svg>
  );
}

function XIcon(): JSX.Element {
  return (
    <svg
      className="w-3 h-3"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M18 6L6 18" />
      <path d="M6 6l12 12" />
    </svg>
  );
}

export default function OneBox({
  onSubmit,
  initialMode,
  initialEffort,
  initialFast,
  initialVisionLoop,
  initialSpeculative,
  noProviderConfigured,
  onOpenProviderSettings,
}: OneBoxProps): JSX.Element {
  const [goal, setGoal] = useState<string>("");
  const [referencePath, setReferencePath] = useState<string>("");
  const [referenceUpload, setReferenceUpload] = useState<ReferenceUpload | null>(null);
  const [endReferenceUpload, setEndReferenceUpload] = useState<ReferenceUpload | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [mode, setMode] = useState<Mode>(initialMode ?? DEFAULT_MODE);
  const [effort, setEffort] = useState<Effort>(initialEffort ?? DEFAULT_EFFORT);
  const [fast, setFast] = useState<boolean>(initialFast ?? false);
  const [visionLoop, setVisionLoop] = useState<boolean>(initialVisionLoop ?? false);
  const [speculative, setSpeculative] = useState<boolean>(initialSpeculative ?? false);
  const [tools, setTools] = useState<ToolId[]>(DEFAULT_TOOLS);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [crew, setCrew] = useState<AgentLite[]>([]);
  const [crewOpen, setCrewOpen] = useState<boolean>(false);
  const [combineWithDefaultSwarm, setCombineWithDefaultSwarm] = useState(true);
  const [uploading, setUploading] = useState<boolean>(false);
  const [openMenu, setOpenMenu] = useState<OpenMenu>(null);
  const [listening, setListening] = useState<boolean>(false);
  // Voice errors now surface as a sonner toast — no permanent balloon reserving
  // space in the composer chrome (Kimi restraint).
  // One-shot ring burst when a top-tier effort is engaged (iOS "power up" feel).
  const [maxBurst, setMaxBurst] = useState<boolean>(false);
  // First-run keyless UX: escalated once a send is attempted without keys.
  const [keyHintWarned, setKeyHintWarned] = useState<boolean>(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const baseGoalRef = useRef<string>("");

  const isTauri = typeof window !== "undefined" && "__TAURI_IPC__" in window;
  // WebView2 exposes the SpeechRecognition constructor but has no speech
  // backend — start() fails instantly, making the mic a silent dead button.
  // Treat voice as unavailable in the desktop shell rather than lie.
  const voiceSupported = Boolean(SpeechRecognitionCtor) && !isTauri;

  // Turbo: toggling Fast on fires a global event (App shows the burst + toast).
  const toggleFast = (next: boolean): void => {
    setFast(next);
    if (next) {
      window.dispatchEvent(new CustomEvent("infinity:turbo"));
    }
  };

  // Ctrl/Cmd+T toggles turbo from anywhere the composer is mounted.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent): void => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "t") {
        event.preventDefault();
        setFast((current) => {
          const next = !current;
          if (next) window.dispatchEvent(new CustomEvent("infinity:turbo"));
          return next;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Native OS file picker (Tauri) — the HTML <input type=file> is what triggers
  // the "can't open files from File Explorer" error inside the desktop webview.
  const pickFilesNative = async (): Promise<void> => {
    try {
      const { open } = await import("@tauri-apps/api/dialog");
      const selected = await open({ multiple: true });
      if (!selected) {
        return;
      }
      const paths = Array.isArray(selected) ? selected : [selected];
      setAttachments((prev) => [
        ...prev,
        ...paths.map((p) => ({
          path: p,
          name: p.split(/[\\/]/).pop() || p,
          size: 0,
        })),
      ]);
    } catch {
      // Fall back to the HTML input if the Tauri dialog is unavailable.
      fileInputRef.current?.click();
    }
  };

  const startAttach = (): void => {
    if (isTauri) {
      void pickFilesNative();
    } else {
      fileInputRef.current?.click();
    }
  };

  const toggleMenu = (menu: OpenMenu): void => {
    setOpenMenu((current) => (current === menu ? null : menu));
  };

  const removeAttachment = (index: number): void => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };

  const handleFilesSelected = async (
    event: ChangeEvent<HTMLInputElement>
  ): Promise<void> => {
    const files = event.target.files;
    if (!files || files.length === 0) {
      return;
    }
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        try {
          const formData = new FormData();
          formData.append("file", file);
          const response = await fetch(UPLOAD_URL, {
            method: "POST",
            body: formData,
          });
          if (!response.ok) {
            continue;
          }
          const saved = (await response.json()) as Attachment;
          setAttachments((prev) => [...prev, saved]);
        } catch {
          // Ignore individual upload failures silently.
        }
      }
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  };

  const uploadReference = async (file: File, slot: "start" | "end"): Promise<void> => {
    if (!file.type.startsWith("image/")) {
      toast.error("Reference frames must be image files.");
      return;
    }
    setUploading(true);
    const preview = URL.createObjectURL(file);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(UPLOAD_URL, { method: "POST", body: formData });
      if (!response.ok) {
        throw new Error((await response.text()) || `Upload failed (${response.status})`);
      }
      const saved = (await response.json()) as Attachment;
      const uploaded: ReferenceUpload = { path: saved.path, name: file.name, preview };
      if (slot === "start") {
        setReferencePath(saved.path);
        setReferenceUpload((current) => {
          if (current) URL.revokeObjectURL(current.preview);
          return uploaded;
        });
      } else {
        setEndReferenceUpload((current) => {
          if (current) URL.revokeObjectURL(current.preview);
          return uploaded;
        });
      }
    } catch (error) {
      URL.revokeObjectURL(preview);
      toast.error(error instanceof Error ? error.message : "Reference upload failed.");
    } finally {
      setUploading(false);
    }
  };

  useEffect(() => () => {
    if (referenceUpload) URL.revokeObjectURL(referenceUpload.preview);
    if (endReferenceUpload) URL.revokeObjectURL(endReferenceUpload.preview);
  }, [referenceUpload, endReferenceUpload]);

  const flagVoiceError = (): void => {
    setListening(false);
    // Toast instead of the old absolutely-positioned balloon that used to
    // reserve permanent space above the mic button.
    toast.error("Voice input isn't available here — type or paste instead.");
  };

  const toggleListening = (): void => {
    if (!voiceSupported || !SpeechRecognitionCtor) {
      flagVoiceError();
      return;
    }
    try {
      if (listening) {
        recognitionRef.current?.stop();
        return;
      }
      const recognition = new SpeechRecognitionCtor();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = "en-US";
      baseGoalRef.current = goal;
      recognition.onresult = (event) => {
        let transcript = "";
        for (let i = 0; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript;
        }
        const base = baseGoalRef.current;
        setGoal(base && transcript ? `${base} ${transcript}` : base || transcript);
      };
      recognition.onend = () => setListening(false);
      // A backend-less engine (or denied mic) fails here — say so visibly
      // instead of resetting to a state indistinguishable from "nothing".
      recognition.onerror = flagVoiceError;
      recognitionRef.current = recognition;
      recognition.start();
      setListening(true);
    } catch {
      flagVoiceError();
    }
  };

  const handleSubmit = async (): Promise<void> => {
    const trimmedGoal = goal.trim();
    if (!trimmedGoal || submitting) {
      return;
    }
    // No LLM provider key is set up, so this send would only fail on the
    // backend. Escalate the inline hint instead of firing an opaque error.
    if (noProviderConfigured) {
      setKeyHintWarned(true);
      return;
    }
    const title = trimmedGoal.split("\n")[0].slice(0, 60);
    setSubmitting(true);
    try {
      await onSubmit({
        title,
        goal: trimmedGoal,
        referenceImagePath: referencePath.trim(),
        endReferenceImagePath: endReferenceUpload?.path ?? "",
        mode,
        effort,
        fast,
        vision_loop: visionLoop,
        speculative,
        attachments,
        tools,
        agents: crew.map((agent) => agent.id),
        combine_with_default_swarm: combineWithDefaultSwarm,
      });
      setGoal("");
      setAttachments([]);
      setReferencePath("");
      setReferenceUpload((current) => {
        if (current) URL.revokeObjectURL(current.preview);
        return null;
      });
      setEndReferenceUpload((current) => {
        if (current) URL.revokeObjectURL(current.preview);
        return null;
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const ghostButtonClass =
    "h-8 rounded-lg px-2 text-tx-dim hover:bg-bd/[0.06] hover:text-tx transition-colors flex items-center gap-1.5 text-xs flex-none";

  return (
    <div className={`rounded-[20px] border border-bd/[0.09] material p-3 shadow-2xl shadow-black/30 focus-within:border-accent/50 transition-colors ${maxBurst ? "max-burst" : ""}`}>
      <textarea
        id="onebox-input"
        value={goal}
        onChange={(event) => setGoal(event.target.value)}
        onKeyDown={handleKeyDown}
        onInput={(event) => {
          const element = event.currentTarget;
          element.style.height = "auto";
          element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
        }}
        placeholder="Describe what to build. Code, art, a game, anything."
        rows={1}
        className="block w-full bg-transparent border-0 focus:outline-none resize-none text-[15px] leading-6 placeholder-tx-mut px-1 text-tx min-h-[24px] max-h-[200px]"
      />

      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-1 pt-2">
          {attachments.map((attachment, index) => (
            <div
              key={`${attachment.path}-${index}`}
              className="flex items-center gap-1.5 rounded-lg bg-bd/[0.05] px-2 py-1 text-xs text-tx-dim"
            >
              <span className="truncate max-w-[160px]">
                {attachment.name}
              </span>
              <button
                type="button"
                onClick={() => removeAttachment(index)}
                aria-label={`Remove ${attachment.name}`}
                className="text-tx-mut hover:text-tx transition-colors"
              >
                <XIcon />
              </button>
            </div>
          ))}
        </div>
      )}

      {(referenceUpload || endReferenceUpload) && (
        <div className="flex flex-wrap gap-1.5 px-1 pt-2">
          {referenceUpload && (
            <div className="flex items-center gap-1.5 rounded-lg border border-accent/20 bg-accent/[0.06] px-1.5 py-1 text-xs text-tx-dim">
              <img src={referenceUpload.preview} alt="" className="h-5 w-5 rounded object-cover" />
              <span className="max-w-[140px] truncate">Start · {referenceUpload.name}</span>
              <button type="button" onClick={() => setReferenceUpload((current) => { if (current) URL.revokeObjectURL(current.preview); setReferencePath(""); return null; })} aria-label="Remove start frame" className="text-tx-mut hover:text-tx"><XIcon /></button>
            </div>
          )}
          {endReferenceUpload && (
            <div className="flex items-center gap-1.5 rounded-lg border border-accent/20 bg-accent/[0.06] px-1.5 py-1 text-xs text-tx-dim">
              <img src={endReferenceUpload.preview} alt="" className="h-5 w-5 rounded object-cover" />
              <span className="max-w-[140px] truncate">End · {endReferenceUpload.name}</span>
              <button type="button" onClick={() => setEndReferenceUpload((current) => { if (current) URL.revokeObjectURL(current.preview); return null; })} aria-label="Remove end frame" className="text-tx-mut hover:text-tx"><XIcon /></button>
            </div>
          )}
        </div>
      )}

      {noProviderConfigured && (
        <div
          className={`flex items-center gap-2 px-1 pt-2 text-xs transition-colors ${
            keyHintWarned ? "text-error" : "text-tx-mut"
          }`}
        >
          <span className="min-w-0 leading-snug">
            No API key is set up yet, so sending won't work. Add a free DashScope
            key in{" "}
            <button
              type="button"
              onClick={() => onOpenProviderSettings?.()}
              className="inline underline underline-offset-2 text-accent hover:text-accent-hover transition-colors"
            >
              Settings &gt; Providers
            </button>
            .
          </span>
          <button
            type="button"
            onClick={() => onOpenProviderSettings?.()}
            className="flex-none rounded-md border border-bd/[0.12] px-2 py-0.5 text-[11px] text-tx-dim hover:text-tx hover:border-bd/[0.22] transition-colors"
          >
            Set up keys
          </button>
        </div>
      )}

      <div className="flex items-center gap-1.5 flex-wrap pt-2">
        <div className="flex items-center gap-1.5 flex-wrap flex-1 min-w-0">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              void handleFilesSelected(event);
            }}
          />
          <div className="relative">
            <button
              type="button"
              onClick={() => toggleMenu("plus")}
              disabled={uploading}
              aria-label="Add"
              className={`${ghostButtonClass} disabled:opacity-50 disabled:cursor-wait`}
            >
              {uploading ? (
                <SpinnerIcon className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <PlusIcon />
              )}
            </button>
            <PlusMenu
              open={openMenu === "plus"}
              onClose={() => setOpenMenu(null)}
              onAttachClick={startAttach}
              uploading={uploading}
              referencePath={referencePath}
              onReferencePathChange={(value) => {
                setReferencePath(value);
                if (!value) {
                  setReferenceUpload((current) => {
                    if (current) URL.revokeObjectURL(current.preview);
                    return null;
                  });
                }
              }}
              referenceName={referenceUpload?.name}
              referencePreview={referenceUpload?.preview}
              endReferencePath={endReferenceUpload?.path ?? ""}
              onEndReferencePathChange={(value) => {
                if (value === "") {
                  setEndReferenceUpload((current) => {
                    if (current) URL.revokeObjectURL(current.preview);
                    return null;
                  });
                }
              }}
              endReferenceName={endReferenceUpload?.name}
              endReferencePreview={endReferenceUpload?.preview}
              onReferenceFile={(file, slot) => void uploadReference(file, slot)}
              mode={mode}
              onModeChange={(next) => setMode(next)}
              effort={effort}
              onEffortChange={(next) => {
                setEffort(next);
                if (next === "max" || next === "ultracode") {
                  setMaxBurst(false);
                  requestAnimationFrame(() => setMaxBurst(true));
                  window.setTimeout(() => setMaxBurst(false), 850);
                }
              }}
              fast={fast}
              onFastChange={toggleFast}
              visionLoop={visionLoop}
              onVisionLoopChange={setVisionLoop}
              speculative={speculative}
              onSpeculativeChange={setSpeculative}
              tools={tools}
              onToolsChange={setTools}
            />
          </div>
          <button
            type="button"
            onClick={() => setCrewOpen(true)}
            className={`${ghostButtonClass} ${crew.length > 0 ? "text-accent bg-accent/[0.08]" : ""}`}
            aria-label="Choose build crew"
            title="Choose up to three specialist perspectives for this mission"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <circle cx="9" cy="8" r="3" />
              <path d="M3.5 20c.7-3.4 2.6-5.1 5.5-5.1s4.8 1.7 5.5 5.1" />
              <path d="M16 5.5a3 3 0 0 1 0 5" />
              <path d="M17.2 14.9c1.8.4 3 2 3.3 4.3" />
            </svg>
            <span>Crew{crew.length ? ` ${crew.length}` : ""}</span>
          </button>
        </div>

        <div className="flex items-center gap-2 flex-none">
          {/* Swarm Deployment Indicator - shows model + agent count */}
          <SwarmDeploymentBadge
            effort={effort}
            fast={fast}
            size="md"
            pulse={effort === "ultracode" || effort === "max"}
          />
          
          <div className="relative">
            <button
              type="button"
              onClick={toggleListening}
              disabled={!voiceSupported}
              title={
                voiceSupported
                  ? "Voice input"
                  : "Voice input isn't available in the desktop app yet — type or paste instead"
              }
              aria-label="Voice input"
              className={`h-9 w-9 rounded-full border border-bd/[0.08] flex items-center justify-center transition-colors disabled:opacity-25 disabled:cursor-not-allowed ${
                listening
                  ? "accent-text animate-pulse"
                  : "text-tx-dim hover:bg-bd/[0.06] hover:text-tx"
              }`}
            >
              <MicIcon />
            </button>
          </div>

          <button
            type="button"
            onClick={() => {
              void handleSubmit();
            }}
            disabled={submitting || goal.trim().length === 0}
            aria-label={`Spawn ${effortMeta(effort).agents > 1 ? "swarm" : "agent"}`}
            title={`Spawn ${effortMeta(effort).agents > 1 ? "swarm" : "agent"}: ${effortMeta(effort).label}`}
            className="h-9 w-9 rounded-full bg-accent hover:bg-accent-hover text-black flex items-center justify-center disabled:opacity-25 disabled:cursor-not-allowed btn-press transition-all"
          >
            {submitting ? (
              <SpinnerIcon className="w-4 h-4 text-black animate-spin" />
            ) : (
              <ArrowUpIcon />
            )}
          </button>
        </div>
      </div>
      {crew.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-1 pt-2 text-xs text-tx-dim">
          <span className="text-tx-mut">Briefing:</span>
          {crew.map((agent) => (
            <span key={agent.id} className="rounded-md bg-accent/[0.08] px-1.5 py-0.5 text-accent">
              {agent.name}
            </span>
          ))}
          {!combineWithDefaultSwarm && (
            <span className="text-tx-mut">without core swarm</span>
          )}
        </div>
      )}

      <AgentPicker
        open={crewOpen}
        activeId={null}
        onClose={() => setCrewOpen(false)}
        onPick={() => undefined}
        multiSelect
        selectedIds={crew.map((agent) => agent.id)}
        combineWithDefaultSwarm={combineWithDefaultSwarm}
        onPickMany={setCrew}
        onCombineWithDefaultSwarmChange={setCombineWithDefaultSwarm}
      />
    </div>
  );
}

// First-run onboarding wizard. A short, dismissible flow that sets up the
// personality system (name + tone) and highlights what Infinity Code can do.
// It writes through the personality store so the empty-state greeting is
// personalized immediately, and marks itself done via the onboarding store.
import { useState, type MouseEvent } from "react";
import { setPersonality, type PersonalityTone } from "../personality";
import { markOnboarded } from "./store";

const TONES: { id: PersonalityTone; label: string; hint: string; icon: string }[] = [
  { id: "warm", label: "Warm", hint: "Friendly and supportive", icon: "W" },
  { id: "sharp", label: "Sharp", hint: "Direct and efficient", icon: "S" },
  { id: "minimal", label: "Minimal", hint: "Calm and quiet", icon: "M" },
  { id: "energetic", label: "Energetic", hint: "Fast and upbeat", icon: "E" },
];

const FEATURES = [
  {
    title: "Long-horizon missions",
    body: "Set a goal and the swarm plans, builds, tests, and shows you the evidence.",
    icon: "01",
  },
  {
    title: "Smart model routing",
    body: "A council spends cheap models on easy work and premium models for hard problems.",
    icon: "02",
  },
  {
    title: "Runs on your machine",
    body: "Reads files, runs sandboxed code, searches the web, and drives your PC.",
    icon: "03",
  },
];

type Step = "welcome" | "name" | "tone" | "features" | "done";
const STEPS: Step[] = ["welcome", "name", "tone", "features", "done"];

export interface OnboardingModalProps {
  open: boolean;
  onFinish: () => void;
}

function ArrowIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M1 7H13M8 2L13 7L8 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 10L8 14L16 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function OnboardingModal({ open, onFinish }: OnboardingModalProps) {
  const [step, setStep] = useState<Step>("welcome");
  const [name, setName] = useState("");
  const [tone, setTone] = useState<PersonalityTone>("warm");

  if (!open) return null;

  const stepIndex = STEPS.indexOf(step);
  const progress = ((stepIndex + 1) / STEPS.length) * 100;

  const finish = () => {
    markOnboarded();
    onFinish();
  };

  const next = () => {
    const idx = STEPS.indexOf(step);
    if (idx >= STEPS.length - 1) {
      finish();
      return;
    }
    setStep(STEPS[idx + 1]);
  };

  const handleBackdropClick = (e: MouseEvent) => {
    // Backdrop clicks must never silently complete onboarding — a stray click
    // would permanently mark the user as onboarded. Only explicit buttons do.
    if (e.target === e.currentTarget) e.stopPropagation();
  };

  const primaryLabel =
    step === "done" ? "Start building" : step === "tone" ? "Looks good" : "Continue";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md"
      onClick={handleBackdropClick}
      style={{ animation: "onboarding-fade-in 0.25s ease-out" }}
    >
      {/* Card */}
      <div
        className="relative w-full max-w-[420px] flex flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-[#1a1d23]/95 shadow-[0_32px_64px_-12px_rgba(0,0,0,0.6)]"
        onClick={(e) => e.stopPropagation()}
        style={{ animation: "onboarding-slide-up 0.3s cubic-bezier(0.16, 1, 0.3, 1)" }}
      >
        {/* Close button */}
        <button
          type="button"
          onClick={finish}
          className="absolute top-4 right-4 z-10 flex h-8 w-8 items-center justify-center rounded-lg text-white/40 transition-all hover:bg-white/[0.08] hover:text-white/80 active:scale-95"
          aria-label="Skip onboarding"
        >
          Skip
        </button>

        {/* Progress bar */}
        <div className="h-0.5 w-full bg-white/[0.06]">
          <div
            className="h-full bg-accent transition-all duration-500 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>

        {/* Content area */}
        <div className="px-7 pt-7 pb-2 flex-1 flex flex-col min-h-[280px]">
          {/* Step transitions */}
          <div
            key={step}
            className="flex-1 flex flex-col justify-center"
            style={{ animation: "onboarding-step-in 0.35s ease-out" }}
          >
            {step === "welcome" && (
              <>
                <div className="mb-1 flex items-center gap-2.5">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10 text-accent">
                    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                      <path d="M10 2L12.5 7.5L18 8.5L14 12.5L15 18L10 15.5L5 18L6 12.5L2 8.5L7.5 7.5L10 2Z" fill="currentColor" />
                    </svg>
                  </div>
                  <span className="text-xs font-medium uppercase tracking-wider text-accent/80">
                    Infinity Code
                  </span>
                </div>
                <h2 className="font-display text-[22px] font-semibold leading-tight tracking-tight text-white mt-3">
                  Welcome
                </h2>
                <p className="mt-2.5 text-[14px] leading-relaxed text-white/55">
                  A coding assistant that thinks in hours, not turns. Set your preferences and start building something great.
                </p>
                <div className="mt-6 flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.03] px-4 py-3.5">
                  <span className="text-[11px] uppercase tracking-wider text-accent">Quick setup</span>
                  <div>
                    <p className="text-[13px] font-medium text-white/85">Takes 30 seconds</p>
                    <p className="text-[12px] text-white/40 mt-0.5">Just 4 quick steps to personalize your experience</p>
                  </div>
                </div>
              </>
            )}

            {step === "name" && (
              <>
                <h2 className="font-display text-[22px] font-semibold leading-tight tracking-tight text-white">
                  What should we call you?
                </h2>
                <p className="mt-2 text-[14px] text-white/55">
                  We'll use it in your greeting. Skip if you prefer to stay anonymous.
                </p>
                <div className="mt-5">
                  <input
                    autoFocus
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        setPersonality({ userName: name.trim() });
                        next();
                      }
                    }}
                    placeholder="Enter your first name"
                    className="block w-full rounded-xl border border-white/[0.1] bg-white/[0.04] px-4 py-3 text-[15px] text-white placeholder-white/30 transition-all focus:border-accent/50 focus:bg-white/[0.06] focus:outline-none focus:ring-2 focus:ring-accent/20"
                  />
                </div>
              </>
            )}

            {step === "tone" && (
              <>
                <h2 className="font-display text-[22px] font-semibold leading-tight tracking-tight text-white">
                  Pick a personality
                </h2>
                <p className="mt-2 text-[14px] text-white/55">
                  This shapes greetings and suggestions. Change anytime in Settings.
                </p>
                <div className="mt-4 grid grid-cols-2 gap-2.5">
                  {TONES.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => {
                        setTone(t.id);
                        setPersonality({ tone: t.id });
                      }}
                      className={`group relative flex items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition-all ${
                        tone === t.id
                          ? "border-accent/50 bg-accent/10 shadow-[0_0_0_1px_rgba(var(--accent-rgb),0.3)]"
                          : "border-white/[0.08] hover:border-white/[0.15] hover:bg-white/[0.03]"
                      }`}
                    >
                      <span className="text-lg">{t.icon}</span>
                      <div className="min-w-0 flex-1 pr-6">
                        <span className={`block text-[13px] font-medium ${tone === t.id ? "text-accent" : "text-white/85"}`}>
                          {t.label}
                        </span>
                        <span className="block text-[11px] text-white/40 mt-0.5">{t.hint}</span>
                      </div>
                      {tone === t.id && (
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-accent">
                          <CheckIcon />
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </>
            )}

            {step === "features" && (
              <>
                <h2 className="font-display text-[22px] font-semibold leading-tight tracking-tight text-white">
                  What Infinity Code can do
                </h2>
                <div className="mt-4 space-y-2.5">
                  {FEATURES.map((f) => (
                    <div
                      key={f.title}
                      className="flex items-start gap-3.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3.5"
                    >
                      <span className="text-xl mt-0.5">{f.icon}</span>
                      <div className="min-w-0">
                        <p className="text-[14px] font-medium text-white/90">{f.title}</p>
                        <p className="text-[12px] text-white/45 leading-relaxed mt-0.5">{f.body}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}

            {step === "done" && (
              <div className="flex-1 flex flex-col items-center justify-center text-center py-4">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent mb-4">
                  <CheckIcon />
                </div>
                <h2 className="font-display text-[22px] font-semibold tracking-tight text-white">
                  You're all set
                </h2>
                <p className="mt-2 text-[14px] text-white/55 max-w-[260px] leading-relaxed">
                  {name.trim()
                    ? `Nice to meet you, ${name.trim()}. Ask for anything — a refactor, an explanation, or a whole feature.`
                    : "Ask for anything — a refactor, an explanation, or a whole feature."}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-7 py-4 border-t border-white/[0.06]">
          <button
            type="button"
            onClick={finish}
            className="text-[13px] text-white/40 transition-colors hover:text-white/70"
          >
            {step === "done" ? "Close" : "Skip"}
          </button>
          <button
            type="button"
            onClick={() => {
              if (step === "name") setPersonality({ userName: name.trim() });
              next();
            }}
            className="flex items-center gap-2 rounded-xl bg-accent px-5 py-2.5 text-[13px] font-medium text-white transition-all hover:brightness-110 active:scale-[0.97] shadow-[0_2px_8px_-2px_rgba(var(--accent-rgb),0.5)]"
          >
            {primaryLabel}
            {step !== "done" && <ArrowIcon />}
          </button>
        </div>
      </div>

      {/* Animations */}
      <style>{`
        @keyframes onboarding-fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes onboarding-slide-up {
          from { opacity: 0; transform: translateY(20px) scale(0.96); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes onboarding-step-in {
          from { opacity: 0; transform: translateX(8px); }
          to { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}

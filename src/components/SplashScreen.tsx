import { useEffect, useRef, useState } from "react";
import logoUrl from "../assets/logo.webp";
import splashVideo from "../assets/splash-loop.mp4";
import { apiFetch, API_BASE  } from "../lib/api";

const POLL_INTERVAL_MS = 1000;
const SLOW_START_AFTER_MS = 30000;

export interface SplashScreenProps {
  onReady: () => void;
}

/**
 * Full-screen overlay shown while the Python backend boots.
 *
 * Readiness is detected two ways so it works in both the plain browser and
 * the Tauri shell: (1) polling GET /api/v1/cost, (2) the Tauri
 * "backend-ready" event emitted by the Rust process supervisor.
 */
export default function SplashScreen({ onReady }: SplashScreenProps): JSX.Element {
  const inTauri = "__TAURI_IPC__" in window;
  const [failedMessage, setFailedMessage] = useState<string | null>(null);
  const [slowStart, setSlowStart] = useState<boolean>(false);
  // Prefer the animated video; fall back to the breathing logo if it can't play.
  const [useVideo, setUseVideo] = useState<boolean>(true);
  const videoElRef = useRef<HTMLVideoElement | null>(null);
  const doneRef = useRef<boolean>(false);

  // If the video hasn't started within 2.5s (blocked autoplay, missing codec),
  // switch to the CSS-animated logo so the splash is never static.
  useEffect(() => {
    const t = window.setTimeout(() => {
      const v = videoElRef.current;
      if (!v || v.readyState < 3 || v.paused) {
        setUseVideo(false);
      }
    }, 2500);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    let mounted = true;
    const startedAt = Date.now();

    const finish = (): void => {
      if (!doneRef.current && mounted) {
        doneRef.current = true;
        onReady();
      }
    };

    // Path 1: HTTP polling (works everywhere).
    const poll = async (): Promise<void> => {
      try {
        const response = await apiFetch(`${API_BASE}/cost`);
        if (response.ok) {
          finish();
          return;
        }
      } catch {
        // Backend not up yet; keep waiting.
      }
      if (mounted && !doneRef.current) {
        if (Date.now() - startedAt > SLOW_START_AFTER_MS) {
          setSlowStart(true);
        }
        window.setTimeout(() => {
          void poll();
        }, POLL_INTERVAL_MS);
      }
    };
    void poll();

    // Path 2: Tauri events from the Rust supervisor (desktop shell only).
    let unlistenReady: (() => void) | null = null;
    let unlistenFailed: (() => void) | null = null;
    if (inTauri) {
      void import("@tauri-apps/api/event")
        .then(async ({ listen }) => {
          unlistenReady = await listen("backend-ready", () => finish());
          unlistenFailed = await listen<string>("backend-failed", (event) => {
            if (mounted) {
              setFailedMessage(
                typeof event.payload === "string"
                  ? event.payload
                  : "Backend failed to start."
              );
            }
          });
        })
        .catch(() => {
          // Tauri API unavailable; HTTP polling still covers us.
        });
    }

    return () => {
      mounted = false;
      if (unlistenReady) unlistenReady();
      if (unlistenFailed) unlistenFailed();
    };
  }, [onReady]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-bg">
      {useVideo ? (
        <video
          ref={videoElRef}
          src={splashVideo}
          poster={logoUrl}
          autoPlay
          loop
          muted
          playsInline
          onError={() => setUseVideo(false)}
          className="w-28 h-28 rounded-[24px] object-cover select-none shadow-2xl shadow-black/40"
        />
      ) : (
        <img
          src={logoUrl}
          alt="Infinity Code"
          className="w-28 h-28 rounded-[24px] logo-breathe select-none"
          draggable={false}
        />
      )}
      <p className="mt-7 text-tx-dim text-base">
        Starting Infinity Engine...
      </p>

      <div className="mt-8 w-56 h-1 rounded-full bg-bd/[0.07] overflow-hidden">
        <div className="h-full w-1/3 accent-bg animate-[loading_1.4s_ease-in-out_infinite]" />
      </div>

      {slowStart && !failedMessage && (
        <p className="mt-4 text-xs text-tx-mut">
          Still starting. First launch can take a while.
        </p>
      )}

      {failedMessage && (
        <div className="mt-6 max-w-md rounded-lg border border-error/20 bg-error/[0.06] text-error text-sm p-3 text-center">
          {failedMessage}
          <p className="mt-1 text-xs text-error/70">
            Still retrying in the background. Fix the backend and this screen
            will clear itself.
          </p>
        </div>
      )}

      <div className="mt-8 flex items-center gap-3">
        <button type="button" className="splash-action" onClick={onReady}>
          Continue without backend
        </button>
        {inTauri && (
          <button
            type="button"
            className="splash-action splash-action--ghost"
            onClick={() => {
              void import("@tauri-apps/api/process")
                .then((m) => m.exit(0))
                .catch(() => undefined);
            }}
          >
            Quit
          </button>
        )}
      </div>
    </div>
  );
}

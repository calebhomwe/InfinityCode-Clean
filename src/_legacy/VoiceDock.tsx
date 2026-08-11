// VoiceDock — persistent floating voice control dock at the bottom-right.
// Provides always-accessible mic input via the Web Speech API, independent
// of the composer. Pulses while listening, shows a transcript preview.

import { useCallback, useEffect, useRef, useState } from "react";

interface SpeechRecognitionResultLike {
  [index: number]: { transcript: string };
}

interface SpeechRecognitionEventLike extends Event {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: Event & { error: string }) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

const SpeechRecognitionCtor: SpeechRecognitionConstructor | undefined =
  (typeof window !== "undefined" &&
    ((window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition)) ||
  undefined;

export default function VoiceDock(): JSX.Element | null {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [supported, setSupported] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  const isTauri = typeof window !== "undefined" && "__TAURI_IPC__" in window;

  useEffect(() => {
    setSupported(Boolean(SpeechRecognitionCtor) && !isTauri);
  }, [isTauri]);

  const startListening = useCallback(() => {
    if (!SpeechRecognitionCtor) return;
    const recognition = new SpeechRecognitionCtor();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: SpeechRecognitionEventLike) => {
      let text = "";
      for (let i = 0; i < event.results.length; i++) {
        text += event.results[i][0].transcript;
      }
      setTranscript(text);
    };

    recognition.onerror = () => {
      setListening(false);
    };

    recognition.onend = () => {
      setListening(false);
    };

    recognitionRef.current = recognition;
    setTranscript("");
    setListening(true);
    try {
      recognition.start();
    } catch {
      setListening(false);
    }
  }, []);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setListening(false);
  }, []);

  // Copy transcript to clipboard on stop if non-empty.
  const toggleListen = () => {
    if (listening) {
      stopListening();
      if (transcript.trim()) {
        void navigator.clipboard.writeText(transcript.trim());
      }
    } else {
      startListening();
    }
  };

  if (!supported) return null;

  return (
    <div className="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-2">
      {/* Transcript preview */}
      {expanded && transcript && (
        <div className="max-w-xs rounded-xl border border-bd/[0.08] bg-surface px-3 py-2 shadow-lg">
          <p className="text-xs text-tx leading-relaxed max-h-24 overflow-y-auto">
            {transcript}
          </p>
          <p className="text-[9px] text-tx-mut mt-1">
            {listening ? "Listening…" : "Stopped — copied to clipboard"}
          </p>
        </div>
      )}

      {/* Dock buttons */}
      <div className="flex items-center gap-2">
        {expanded && (
          <button
            type="button"
            onClick={() => setExpanded(false)}
            className="w-8 h-8 rounded-full bg-surface border border-bd/[0.08] flex items-center justify-center text-tx-mut hover:text-tx shadow-md transition-colors"
            title="Collapse"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        )}

        <button
          type="button"
          onClick={expanded ? toggleListen : () => setExpanded(true)}
          className={`w-11 h-11 rounded-full flex items-center justify-center shadow-lg transition-all press ${
            listening
              ? "bg-red-500 text-white animate-pulse"
              : "bg-accent text-black hover:bg-accent-hover"
          }`}
          title={listening ? "Stop listening" : expanded ? "Start listening" : "Open voice dock"}
        >
          {expanded ? (
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
              <path d="M19 10v2a7 7 0 01-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
              <line x1="8" y1="23" x2="16" y2="23" />
            </svg>
          ) : (
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
              <path d="M19 10v2a7 7 0 01-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="23" />
              <line x1="8" y1="23" x2="16" y2="23" />
            </svg>
          )}
        </button>

        {expanded && listening && (
          <button
            type="button"
            onClick={stopListening}
            className="w-8 h-8 rounded-full bg-red-500/20 border border-red-500/30 flex items-center justify-center text-red-400 hover:bg-red-500/30 shadow-md transition-colors"
            title="Stop"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

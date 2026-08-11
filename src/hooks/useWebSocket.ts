import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../lib/api";

const RECONNECT_DELAY_MS = 2000;
// How much of a run's story to keep in memory for the live feed. A long
// mission can emit hundreds of events; the UI only ever shows the recent tail.
const MAX_EVENTS = 200;

/** The rolling snapshot — unchanged shape, so existing consumers keep working. */
export interface LiveUpdate {
  status: string;
  cost: number;
  agents_active: string[];
  screenshot: string | null;
  /** Live deployment map: which model actually served each agent role
   *  (e.g. { engineer: "local/fable-max-35b" }). Drives the deployment badges. */
  role_models: Record<string, string>;
}

/** One thing the swarm announced. Mirrors backend/core/events.py. */
export interface MissionEvent {
  type: "sync" | "phase" | "attempt" | "partial" | "status";
  seq: number;
  at: number;
  /** phase */
  lead?: string | null;
  previous?: string | null;
  agents?: string[];
  /** attempt */
  attempt_id?: string;
  role?: string;
  model?: string;
  score?: number | null;
  cost?: number;
  artifact?: string | null;
  /** partial */
  candidateLetter?: string | null;
  strategy?: string | null;
  lambdaCompleted?: number;
  /** status + sync */
  status?: string;
}

export interface UseWebSocketResult extends LiveUpdate {
  connected: boolean;
  /** The run's story so far, oldest first. Replayed on connect. */
  events: MissionEvent[];
  /** True when the server told us it dropped frames (seq gap). */
  missedEvents: boolean;
}

const IDLE_UPDATE: LiveUpdate = {
  status: "idle",
  cost: 0,
  agents_active: [],
  screenshot: null,
  role_models: {},
};

export function useWebSocket(missionId: string | null): UseWebSocketResult {
  const [update, setUpdate] = useState<LiveUpdate>(IDLE_UPDATE);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [missedEvents, setMissedEvents] = useState<boolean>(false);
  const [connected, setConnected] = useState<boolean>(false);
  const shouldReconnectRef = useRef<boolean>(true);
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  // Highest seq seen, so a gap (the server dropping frames for a slow client)
  // is detectable rather than silently producing a wrong-looking timeline.
  const lastSeqRef = useRef<number>(0);

  useEffect(() => {
    if (!missionId) {
      setUpdate(IDLE_UPDATE);
      setEvents([]);
      setMissedEvents(false);
      setConnected(false);
      return;
    }
    shouldReconnectRef.current = true;
    lastSeqRef.current = 0;
    setEvents([]);
    setMissedEvents(false);

    const connect = (): void => {
      try {
        const socket = new WebSocket(`${WS_BASE}/missions/${missionId}/ws`);
        socketRef.current = socket;

        socket.onopen = (): void => {
          setConnected(true);
          // The server replays the whole run on every connect, so a reconnect
          // would otherwise append a second copy of the timeline.
          lastSeqRef.current = 0;
          setEvents([]);
          setMissedEvents(false);
        };

        socket.onmessage = (event: MessageEvent<string>): void => {
          let data: Partial<MissionEvent> & Partial<LiveUpdate> & { error?: string };
          try {
            data = JSON.parse(event.data) as typeof data;
          } catch {
            return; // malformed frame; wait for the next
          }
          if (data.error) {
            return;
          }

          // Track sequence gaps. The server drops frames for a subscriber that
          // has fallen behind rather than buffering without limit.
          if (typeof data.seq === "number") {
            if (lastSeqRef.current && data.seq > lastSeqRef.current + 1) {
              setMissedEvents(true);
            }
            lastSeqRef.current = Math.max(lastSeqRef.current, data.seq);
          }

          // A frame with no `type` is the pre-0.1.55 snapshot format. Treat it
          // as a sync so an older backend still drives the UI.
          const kind = data.type ?? "sync";

          if (kind === "sync") {
            setUpdate((u) => ({
              status: typeof data.status === "string" ? data.status : "unknown",
              cost: typeof data.cost === "number" ? data.cost : 0,
              agents_active: Array.isArray(data.agents_active)
                ? data.agents_active.map(String)
                : [],
              screenshot:
                typeof data.screenshot === "string" ? data.screenshot : null,
              role_models:
                data.role_models && typeof data.role_models === "object"
                  ? { ...(data.role_models as Record<string, string>) }
                  : u.role_models,
            }));
            return;
          }

          // Everything else is a real event: fold it into the snapshot and
          // append it to the story.
          if (kind === "phase") {
            const agents = Array.isArray(data.agents) ? data.agents.map(String) : [];
            setUpdate((u) => ({ ...u, agents_active: agents }));
          } else if (kind === "status" && typeof data.status === "string") {
            const next = data.status;
            setUpdate((u) => ({ ...u, status: next }));
          } else if (kind === "attempt" && typeof data.cost === "number") {
            // Attempt costs are per-attempt; the authoritative running total
            // still arrives via sync, so only move it forward.
            const attemptCost = data.cost;
            setUpdate((u) => ({ ...u, cost: Math.max(u.cost, attemptCost) }));
          }
          // Attempt events carry role + model — fold them straight into the
          // deployment map so badges update the moment an agent answers.
          if (kind === "attempt" && data.role && typeof data.model === "string") {
            const role = String(data.role);
            const model = String(data.model);
            setUpdate((u) => ({
              ...u,
              role_models: { ...u.role_models, [role]: model },
            }));
          } else if (kind === "partial" && typeof data.cost === "number") {
            // Streamed partial candidate cost — accumulate into running total.
            const partialCost = data.cost;
            setUpdate((u) => ({ ...u, cost: u.cost + partialCost }));
          }

          setEvents((previous) => {
            const next = [...previous, data as MissionEvent];
            return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
          });
        };

        socket.onerror = (): void => {
          socket.close();
        };

        socket.onclose = (): void => {
          setConnected(false);
          if (shouldReconnectRef.current) {
            timerRef.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
          }
        };
      } catch {
        if (shouldReconnectRef.current) {
          timerRef.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
        }
      }
    };

    connect();

    return () => {
      shouldReconnectRef.current = false;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      if (socketRef.current !== null) {
        socketRef.current.close();
      }
    };
  }, [missionId]);

  return { ...update, connected, events, missedEvents };
}

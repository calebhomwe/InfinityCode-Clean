import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch, API_BASE  } from "../lib/api";

// Adaptive cadence: poll fast only while work is actually in flight, and back
// off hard when everything is idle. This kills the constant 5s churn that made
// the Build view feel laggy while the WebSocket carried the real live data.
const POLL_ACTIVE_MS = 1500;
const POLL_IDLE_MS = 10000;
const TERMINAL = new Set(["completed", "failed", "approved", "rejected"]);

export interface CritiqueScores {
  overall: number;
  composition: number;
  color: number;
  style_match: number;
  technical_execution: number;
  fixes: string[];
}

export interface TournamentCandidate {
  letter: string;
  strategy: string;
  returncode?: number;
  score?: number;
  exec_ms?: number;
  image_url?: string | null;
  selected?: boolean;
}

export interface RedTeamAttack {
  vector: string;
  passed: boolean;
}

export interface RedTeamSummary {
  passed_all: boolean;
  total: number;
  failed: number;
  attacks: RedTeamAttack[];
}

/** A benchmark-derived lesson injected into an agent run. */
export interface DrillApplication {
  id: string;
  task_id: string;
  capability?: string;
  failure_reason?: string;
}

export interface MissionDrillTrace {
  applied?: DrillApplication[];
  outcome?: string;
}

export interface MissionEvidenceAttempt {
  attempt: number;
  mode?: string;
  plan?: string;
  prompt?: string;
  code_path?: string;
  code_url?: string | null;
  code?: string;
  returncode?: number;
  stdout?: string;
  stderr?: string;
  image_url?: string | null;
  image_path?: string;
  mediaType?: string;
  media_type?: string;
  video_error?: string;
  render_path?: string;
  critique?: CritiqueScores | null;
  cost_aud?: number;
  tournament?: boolean;
  winner_letter?: string | null;
  candidates?: TournamentCandidate[];
  redteam?: RedTeamSummary;
  drills_applied?: DrillApplication[];
}

export interface MissionParams {
  mode?: string;
  end_reference_image_path?: string | null;
  effort?: string;
  fast?: boolean;
  attachments?: string[];
  tools?: string[];
  agents?: string[];
  combine_with_default_swarm?: boolean;
  vision_loop?: boolean;
  speculative?: boolean;
}

export interface MissionEvidence {
  attempts?: MissionEvidenceAttempt[];
  final_status?: string;
  failure_reason?: string;
  summary?: string;
  rejection_feedback?: string;
  drill_trace?: MissionDrillTrace;
}

export interface Mission {
  id: string;
  title: string;
  goal: string;
  status: string;
  priority: number;
  created_at: string | null;
  completed_at: string | null;
  total_cost_aud: number;
  reference_image_path: string | null;
  output_path: string | null;
  evidence: MissionEvidence | null;
  params?: MissionParams | null;
  mode?: string;
  effort?: string;
  fast?: boolean;
}

export interface UseMissionsResult {
  missions: Mission[];
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export function useMissions(): UseMissionsResult {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef<boolean>(true);

  const refetch = useCallback(async (): Promise<void> => {
    try {
      const response = await apiFetch(`${API_BASE}/missions`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} from /missions`);
      }
      const data = (await response.json()) as Mission[];
      if (mountedRef.current) {
        setMissions(data);
        setError(null);
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(
          err instanceof Error ? err.message : "Failed to fetch missions"
        );
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // Keep a live ref to the current missions so the self-scheduling loop can
  // pick the next delay without re-subscribing on every data change.
  const missionsRef = useRef<Mission[]>(missions);
  missionsRef.current = missions;

  useEffect(() => {
    mountedRef.current = true;
    let timer = 0;
    const tick = async (): Promise<void> => {
      await refetch();
      if (!mountedRef.current) return;
      const anyActive = missionsRef.current.some(
        (m) => !TERMINAL.has(m.status),
      );
      timer = window.setTimeout(
        () => void tick(),
        anyActive ? POLL_ACTIVE_MS : POLL_IDLE_MS,
      );
    };
    void tick();
    return () => {
      mountedRef.current = false;
      window.clearTimeout(timer);
    };
  }, [refetch]);

  return { missions, loading, error, refetch };
}

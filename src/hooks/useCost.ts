import { useEffect, useRef, useState } from "react";

import { apiFetch, API_BASE  } from "../lib/api";

const POLL_INTERVAL_MS = 12000;

export interface CostReport {
  date: string;
  spent_today_aud: number;
  remaining_aud: number;
  daily_budget_aud: number;
  breakdown: Record<string, number>;
}

export interface UseCostResult {
  cost: CostReport | null;
  loading: boolean;
}

export function useCost(): UseCostResult {
  const [cost, setCost] = useState<CostReport | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    mountedRef.current = true;

    const fetchCost = async (): Promise<void> => {
      try {
        const response = await apiFetch(`${API_BASE}/cost`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status} from /cost`);
        }
        const data = (await response.json()) as CostReport;
        if (mountedRef.current) {
          setCost(data);
        }
      } catch {
        // Cost widget degrades silently; missions list surfaces API errors.
      } finally {
        if (mountedRef.current) {
          setLoading(false);
        }
      }
    };

    void fetchCost();
    const timer = window.setInterval(() => {
      void fetchCost();
    }, POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      window.clearInterval(timer);
    };
  }, []);

  return { cost, loading };
}

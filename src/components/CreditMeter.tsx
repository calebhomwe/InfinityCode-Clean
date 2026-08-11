import { useEffect, useState } from "react";
import { API_BASE, apiFetch } from "../lib/api";

interface Balance {
  enabled: boolean;
  balance_credits?: number;
  plan?: string;
}

/** Sidebar meter: live credit balance, polled quietly. */
export default function CreditMeter(): JSX.Element | null {
  const [balance, setBalance] = useState<Balance | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await apiFetch(`${API_BASE}/credits`);
        if (!alive) return;
        setBalance((await res.json()) as Balance);
      } catch {
        if (alive) setBalance({ enabled: false });
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 30_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  if (!balance?.enabled) return null;
  return (
    <div className="px-1 mt-2">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-medium text-tx-dim">Credits</span>
        <span className="text-[11px] tabular-nums text-tx-mut">
          {(balance.balance_credits ?? 0).toLocaleString(undefined, {
            maximumFractionDigits: 2,
          })}
          <span className="text-tx-mut"> · {balance.plan}</span>
        </span>
      </div>
    </div>
  );
}

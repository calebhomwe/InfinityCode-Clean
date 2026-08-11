import type { CostReport } from "../hooks/useCost";

export interface CostTrackerProps {
  cost: CostReport | null;
}

/** Claude-style usage bar: one label + a full-width progress track, driven by
 *  today's spend against the daily budget (from the /cost endpoint). */
export default function CostTracker({ cost }: CostTrackerProps): JSX.Element {
  const spent = cost?.spent_today_aud ?? 0;
  const budget = cost?.daily_budget_aud ?? 0;
  const pct = budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
  const warn = pct >= 80;

  return (
    <div className="px-1">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[11px] font-medium text-tx-dim">Daily usage</span>
        <span
          className={`text-[11px] tabular-nums ${warn ? "text-error" : "text-tx-mut"}`}
        >
          ${spent.toFixed(2)}
          <span className="text-tx-mut"> / ${budget.toFixed(2)}</span>
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-bd/[0.08] overflow-hidden">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            warn ? "bg-error" : "bg-accent"
          }`}
          style={{ width: `${Math.max(pct, spent > 0 ? 2 : 0)}%` }}
        />
      </div>
    </div>
  );
}

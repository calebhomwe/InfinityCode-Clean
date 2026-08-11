import { useCallback, useEffect, useState } from "react";
import { API_BASE, apiFetch } from "../lib/api";

/** Credit Engine UI: wallet, plans, packs, license, ledger, analytics. */

interface Balance {
  enabled: boolean;
  balance_credits?: number;
  plan?: string;
  plan_credits?: number;
  byok?: boolean;
  monthly_used?: number;
  monthly_limit?: number;
  monthly_pct?: number;
  rollover_credits?: number;
}

interface PlansCatalog {
  enabled: boolean;
  plans?: Record<string, { credits: number; price_usd: number }>;
  packs?: Record<string, { credits: number; price_usd: number; validity_days?: number }>;
}

interface LedgerRow {
  ts: number;
  delta: number;
  reason: string;
  model: string;
  feature: string;
  balance_after: number;
}

interface Analytics {
  enabled: boolean;
  total_spend_credits?: number;
  per_feature?: Record<string, number>;
  cache_savings_usd?: number;
  cache_hits?: number;
  daily_burn_credits?: number;
  forecast_30d_credits?: number;
  alerts?: string[];
}

function fmtCredits(n: number | undefined): string {
  return (n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

export default function CreditsView({ onClose }: { onClose: () => void }): JSX.Element {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [catalog, setCatalog] = useState<PlansCatalog | null>(null);
  const [ledger, setLedger] = useState<LedgerRow[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [licenseKey, setLicenseKey] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [b, c, l, a] = await Promise.all([
        apiFetch(`${API_BASE}/credits`),
        apiFetch(`${API_BASE}/credits/plans`),
        apiFetch(`${API_BASE}/credits/ledger?limit=25`),
        apiFetch(`${API_BASE}/credits/analytics`),
      ]);
      setBalance((await b.json()) as Balance);
      setCatalog((await c.json()) as PlansCatalog);
      setLedger(((await l.json()) as { entries: LedgerRow[] }).entries ?? []);
      setAnalytics((await a.json()) as Analytics);
    } catch {
      setBalance({ enabled: false });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (path: string, method: string, body?: unknown) => {
    setBusy(true);
    setNotice("");
    try {
      const res = await apiFetch(`${API_BASE}${path}`, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null)) as { detail?: string } | null;
        setNotice(detail?.detail ?? `HTTP ${res.status}`);
      } else {
        await refresh();
      }
    } catch (err) {
      setNotice(String(err));
    } finally {
      setBusy(false);
    }
  };

  const downloadExport = async (format: "csv" | "json") => {
    try {
      const res = await apiFetch(`${API_BASE}/credits/export?format=${format}`);
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `credits-export.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* silent: export is best-effort */
    }
  };

  if (!balance?.enabled) {
    return (
      <section className="models-view" aria-labelledby="credits-heading">
        <div className="models-content">
          <div className="models-heading-row">
            <h1 id="credits-heading">Credits</h1>
            <button type="button" onClick={onClose} className="models-add-button">
              Close
            </button>
          </div>
          <p className="text-tx-dim">Credit engine is disabled or unreachable.</p>
        </div>
      </section>
    );
  }

  const pct = Math.min(100, balance.monthly_pct ?? 0);
  const plans = Object.entries(catalog?.plans ?? {});
  const packs = Object.entries(catalog?.packs ?? {});
  const features = Object.entries(analytics?.per_feature ?? {});

  return (
    <section className="models-view" aria-labelledby="credits-heading">
      <div className="models-content overflow-y-auto">
        <div className="models-heading-row">
          <h1 id="credits-heading">Credits</h1>
          <button type="button" onClick={onClose} className="models-add-button">
            Close
          </button>
        </div>

        {notice && <p className="text-error text-sm mb-3">{notice}</p>}

        {/* Balance card */}
        <div className="rounded-xl border border-bd/[0.08] bg-bd/[0.03] p-4 mb-4">
          <div className="flex items-baseline justify-between">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-tx-dim">
                Balance · {balance.plan} plan
              </div>
              <div className="text-3xl font-semibold tabular-nums mt-1">
                {fmtCredits(balance.balance_credits)}
                <span className="text-sm text-tx-dim font-normal"> credits</span>
              </div>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void act("/credits/byok", "POST", { enabled: !balance.byok })}
              className={`rounded-lg px-3 py-1.5 text-xs border transition-colors press ${
                balance.byok
                  ? "bg-accent/15 text-accent border-accent/30"
                  : "border-bd/[0.12] text-tx-dim hover:text-tx"
              }`}
            >
              BYOK {balance.byok ? "ON" : "OFF"}
            </button>
          </div>
          <div className="mt-3">
            <div className="flex justify-between text-[11px] text-tx-dim mb-1">
              <span>Monthly usage</span>
              <span className="tabular-nums">
                {fmtCredits(balance.monthly_used)} / {fmtCredits(balance.monthly_limit)}
              </span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-bd/[0.08] overflow-hidden">
              <div
                className={`h-full rounded-full transition-[width] duration-500 ${
                  pct >= 80 ? "bg-error" : "bg-accent"
                }`}
                style={{ width: `${Math.max(pct, 2)}%` }}
              />
            </div>
            {balance.rollover_credits !== undefined && balance.rollover_credits > 0 && (
              <p className="text-[11px] text-tx-mut mt-1">
                {fmtCredits(balance.rollover_credits)} rolled over from last month
              </p>
            )}
          </div>
        </div>

        {/* Plans + packs + license */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
          {plans.map(([id, p]) => (
            <div key={id} className="rounded-xl border border-bd/[0.08] p-3 flex flex-col">
              <div className="text-sm font-medium capitalize">{id}</div>
              <div className="text-2xl font-semibold tabular-nums mt-1">
                {p.credits.toLocaleString()}
                <span className="text-xs text-tx-dim font-normal"> credits</span>
              </div>
              <div className="text-[11px] text-tx-dim">
                ${p.price_usd}/month
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => void act("/credits/plan", "POST", { plan: id })}
                className="mt-2 rounded-lg border border-bd/[0.12] px-2 py-1 text-xs hover:bg-bd/[0.05] transition-colors press"
              >
                {balance.plan === id ? "Current" : "Switch"}
              </button>
            </div>
          ))}
        </div>

        <div className="rounded-xl border border-bd/[0.08] p-3 mb-4 flex flex-wrap items-center gap-2">
          {packs.map(([id, p]) => (
            <button
              key={id}
              type="button"
              disabled={busy}
              onClick={() => void act("/credits/packs", "POST", { pack_id: id })}
              className="rounded-lg bg-accent/15 text-accent border border-accent/30 px-3 py-1.5 text-xs transition-colors press"
            >
              Buy {p.credits.toLocaleString()} credits · ${p.price_usd}
            </button>
          ))}
          <input
            value={licenseKey}
            onChange={(e) => setLicenseKey(e.target.value)}
            placeholder="License key"
            className="rounded-lg border border-bd/[0.12] bg-transparent px-2 py-1.5 text-xs w-44"
          />
          <button
            type="button"
            disabled={busy || !licenseKey.trim()}
            onClick={() => void act("/credits/license", "POST", { key: licenseKey.trim() })}
            className="rounded-lg border border-bd/[0.12] px-3 py-1.5 text-xs hover:bg-bd/[0.05] transition-colors press"
          >
            Activate
          </button>
        </div>

        {/* Analytics */}
        <div className="rounded-xl border border-bd/[0.08] p-4 mb-4">
          <h2 className="text-sm font-medium mb-2">Analytics</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <div className="text-tx-dim">Spend (30d)</div>
              <div className="text-lg font-semibold tabular-nums">
                {fmtCredits(analytics?.forecast_30d_credits)}
              </div>
            </div>
            <div>
              <div className="text-tx-dim">Daily burn</div>
              <div className="text-lg font-semibold tabular-nums">
                {fmtCredits(analytics?.daily_burn_credits)}
              </div>
            </div>
            <div>
              <div className="text-tx-dim">Cache savings</div>
              <div className="text-lg font-semibold tabular-nums">
                ${(analytics?.cache_savings_usd ?? 0).toFixed(4)}
              </div>
            </div>
            <div>
              <div className="text-tx-dim">Cache hits</div>
              <div className="text-lg font-semibold tabular-nums">
                {(analytics?.cache_hits ?? 0).toLocaleString()}
              </div>
            </div>
          </div>
          {features.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {features.map(([f, v]) => (
                <span
                  key={f}
                  className="rounded-full bg-bd/[0.06] px-2 py-0.5 text-[11px] text-tx-dim tabular-nums"
                >
                  {f}: {fmtCredits(v)}
                </span>
              ))}
            </div>
          )}
          {(analytics?.alerts ?? []).map((a) => (
            <p key={a} className="text-[11px] text-error mt-2">
              {a}
            </p>
          ))}
        </div>

        {/* Ledger + export */}
        <div className="rounded-xl border border-bd/[0.08] p-4">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-medium">Transactions</h2>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void downloadExport("csv")}
                className="rounded-lg border border-bd/[0.12] px-2 py-1 text-[11px] hover:bg-bd/[0.05] transition-colors press"
              >
                Export CSV
              </button>
              <button
                type="button"
                onClick={() => void downloadExport("json")}
                className="rounded-lg border border-bd/[0.12] px-2 py-1 text-[11px] hover:bg-bd/[0.05] transition-colors press"
              >
                Export JSON
              </button>
            </div>
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-tx-dim text-left">
                <th className="py-1 pr-2 font-medium">When</th>
                <th className="py-1 pr-2 font-medium">Reason</th>
                <th className="py-1 pr-2 font-medium">Model</th>
                <th className="py-1 pr-2 font-medium text-right">Delta</th>
                <th className="py-1 font-medium text-right">Balance</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((row, i) => (
                <tr key={i} className="border-t border-bd/[0.05] tabular-nums">
                  <td className="py-1.5 pr-2 text-tx-mut">{fmtTs(row.ts)}</td>
                  <td className="py-1.5 pr-2">{row.reason}</td>
                  <td className="py-1.5 pr-2 text-tx-mut max-w-[140px] truncate">
                    {row.model || row.feature}
                  </td>
                  <td
                    className={`py-1.5 pr-2 text-right ${
                      row.delta < 0 ? "text-error" : "text-tx-dim"
                    }`}
                  >
                    {row.delta > 0 ? "+" : ""}
                    {fmtCredits(row.delta)}
                  </td>
                  <td className="py-1.5 text-right text-tx-dim">{fmtCredits(row.balance_after)}</td>
                </tr>
              ))}
              {ledger.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-2 text-tx-dim">
                    No transactions yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

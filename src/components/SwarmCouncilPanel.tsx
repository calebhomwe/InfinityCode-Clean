// Swarm Council: ascension dial, model cards, protection rules, log,
// owner card, gauntlet button, and the MR X FINAL owner override.
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import AscensionDial from "./AscensionDial";
import {
  AscensionLogEntry,
  AscensionSnapshot,
  ROLE_LABEL,
  auraClassFor,
  deescalate,
  escalate,
  fetchLog,
  fetchOwner,
  fetchState,
  override,
  runGauntletGap,
} from "../lib/ascensionClient";
import type { OwnerInfo } from "../lib/ascensionClient";
import { playAscension } from "../lib/ascensionAudio";

const POLL_MS = 5000;
const AURA_CLASSES = [
  "aura-xcode",
  "aura-ss1",
  "aura-ss2",
  "aura-ss3",
  "aura-blue",
  "aura-final",
];

const PROTECTION_RULES = [
  "One-step ascension",
  "Role lock",
  "Form lock",
  "Dwell timer",
  "Cooldown",
  "Owner-only final form",
];

interface GauntletResult {
  gap: number | null;
  effort: number;
  suggested: string | null;
}

export default function SwarmCouncilPanel({
  onClose,
}: {
  onClose: () => void;
}): JSX.Element {
  const [snap, setSnap] = useState<AscensionSnapshot | null>(null);
  const [entries, setEntries] = useState<AscensionLogEntry[]>([]);
  const [owner, setOwner] = useState<OwnerInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [gauntlet, setGauntlet] = useState<GauntletResult | null>(null);
  const [gauntletBusy, setGauntletBusy] = useState(false);
  const lastLevel = useRef<number>(0);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchState();
      setSnap(s);
      if (s.level !== lastLevel.current) {
        playAscension(s.level);
        lastLevel.current = s.level;
      }
      setEntries(await fetchLog(12));
    } catch {
      // Backend offline: keep the last snapshot, show a quiet offline state.
      setSnap((prev) => prev ?? null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  // Body aura follows the current form; removed on unmount/state leave.
  useEffect(() => {
    if (!snap) return;
    const cls = auraClassFor(snap.level);
    document.body.classList.remove(...AURA_CLASSES);
    document.body.classList.add(cls);
    return () => document.body.classList.remove(...AURA_CLASSES);
  }, [snap]);

  useEffect(() => {
    void fetchOwner()
      .then(setOwner)
      .catch(() => setOwner(null));
  }, []);

  const act = useCallback(
    async (kind: "up" | "down" | "final", reason: string) => {
      setBusy(kind);
      try {
        if (kind === "up") {
          const r = await escalate(reason);
          if (!r.ok) toast.error(r.blocked_by ?? "ascension blocked");
          else toast.success(`Ascended to ${r.to}`);
        } else if (kind === "down") {
          const r = await deescalate(reason);
          if (!r.ok) toast.error(r.blocked_by ?? "de-escalation blocked");
          else toast.success(`De-escalated to ${r.to}`);
        } else {
          if (!passphrase.trim()) {
            toast.error("Passphrase required for Mr X Final");
            return;
          }
          const r = await override("MR_X_FINAL", passphrase, reason);
          if (!r.ok) toast.error(r.blocked_by ?? "override blocked");
          else toast.success("MR X FINAL engaged");
          setPassphrase("");
        }
        void refresh();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [passphrase, refresh],
  );

  const runGauntlet = useCallback(async () => {
    setGauntletBusy(true);
    try {
      const r = await runGauntletGap();
      setGauntlet({ gap: r.gap, effort: r.effort, suggested: r.suggested });
      if (r.suggested) {
        toast.info(`Benchmark gap ${r.gap.toFixed(1)} → suggested ${r.suggested}`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setGauntletBusy(false);
    }
  }, []);

  const level = snap?.level ?? 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Swarm Council"
    >
      <div className="council-panel">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-tx">
              Swarm Council
            </h2>
            <p className="text-xs text-tx-mut">
              Ascension Engine · deterministic routing · credit-first
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-bd/[0.06] bg-surface px-2.5 py-1.5 text-xs text-tx-mut hover:bg-bd/[0.06] press"
            aria-label="Close council"
          >
            Close
          </button>
        </div>

        {snap ? (
          <>
            <div className="council-grid">
              <AscensionDial
                level={level}
                dwellRemaining={snap.dwell_remaining_s}
                cooldownRemaining={snap.cooldown_remaining_s}
              />
              <div className="council-stats">
                <div className="council-stat">
                  <span className="text-[10px] uppercase tracking-wider text-tx-mut">
                    Speed
                  </span>
                  <span className="text-lg font-semibold">
                    {Math.round(snap.speed * 100)}
                  </span>
                </div>
                <div className="council-stat">
                  <span className="text-[10px] uppercase tracking-wider text-tx-mut">
                    Effort
                  </span>
                  <span className="text-lg font-semibold">
                    {Math.round(snap.effort * 100)}
                  </span>
                </div>
                <div className="council-stat">
                  <span className="text-[10px] uppercase tracking-wider text-tx-mut">
                    Dwell
                  </span>
                  <span className="text-lg font-semibold">
                    {Math.ceil(snap.dwell_remaining_s)}s
                  </span>
                </div>
                <div className="council-stat">
                  <span className="text-[10px] uppercase tracking-wider text-tx-mut">
                    Cooldown
                  </span>
                  <span className="text-lg font-semibold">
                    {Math.ceil(snap.cooldown_remaining_s)}s
                  </span>
                </div>
              </div>
            </div>

            <div className="council-actions">
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void act("up", "council escalate")}
                className="council-btn council-btn-up"
              >
                Ascend ↑
              </button>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void act("down", "council de-escalate")}
                className="council-btn"
              >
                De-escalate ↓
              </button>
              <div className="council-override">
                <input
                  type="password"
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  placeholder="Owner passphrase"
                  aria-label="Owner passphrase"
                  className="council-input"
                />
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void act("final", "owner override")}
                  className="council-btn council-btn-final"
                  title="Owner-only: fuse the full swarm (Mr X Final)"
                >
                  MR X FINAL - Locked
                </button>
              </div>
            </div>

            {owner && (
              <div className="council-owner">
                <span className="font-medium">{owner.name}</span>
                <span className="text-tx-mut">· {owner.alias} ·</span>
                <span className="text-tx-mut">{owner.line}</span>
              </div>
            )}

            <div className="council-cards">
              {(snap.cards ?? []).map((card) => {
                const active = (snap.models ?? []).some((m) => m.id === card.id);
                return (
                  <div
                    key={card.id}
                    className={`council-card${active ? " council-card-active" : ""}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{card.label}</span>
                      {card.locked ? (
                        <span className="council-badge council-badge-locked">
                          LOCKED
                        </span>
                      ) : active ? (
                        <span className="council-badge council-badge-active">
                          ACTIVE
                        </span>
                      ) : (
                        <span className="council-badge">STAND BY</span>
                      )}
                    </div>
                    <div className="text-[11px] text-tx-mut">
                      {ROLE_LABEL[card.role] ?? card.role} · {card.tier} tier
                    </div>
                    <div className="text-[11px] text-tx-mut">
                      from {card.min_form || "-"}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="council-protection">
              {PROTECTION_RULES.map((rule) => (
                <span key={rule} className="council-rule">
                  {rule}
                </span>
              ))}
            </div>

            <div className="council-gauntlet">
              <button
                type="button"
                disabled={gauntletBusy}
                onClick={() => void runGauntlet()}
                className="council-btn"
              >
                {gauntletBusy ? "Scoring gap…" : "Benchmark Gauntlet Gap"}
              </button>
              {gauntlet && (
                <span className="text-xs text-tx-mut">
                  gap {gauntlet.gap != null ? gauntlet.gap.toFixed(1) : "n/a"} · effort{" "}
                  {Math.round(gauntlet.effort * 100)}
                  {gauntlet.suggested ? ` · suggested ${gauntlet.suggested}` : ""}
                </span>
              )}
            </div>

            <div className="council-log">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-tx-mut">
                Ascension Log
              </h3>
              <ul className="space-y-1">
                {entries.slice(0, 12).map((e, i) => (
                  <li
                    key={`${e.ts}-${i}`}
                    className="flex items-baseline gap-2 text-[11px] text-tx-mut"
                  >
                    <span className="font-mono">
                      {new Date(e.ts * 1000).toLocaleTimeString()}
                    </span>
                    <span className="font-medium text-tx">
                      {e.event}
                      {e.from_state && e.to_state
                        ? ` ${e.from_state} → ${e.to_state}`
                        : ""}
                    </span>
                    <span className="truncate">{e.reason}</span>
                  </li>
                ))}
                {entries.length === 0 && (
                  <li className="text-[11px] text-tx-mut">
                    No ascension events yet.
                  </li>
                )}
              </ul>
            </div>
          </>
        ) : (
          <div className="py-8 text-center text-sm text-tx-mut">
            Council offline — is the backend running on :8000?
          </div>
        )}
      </div>
    </div>
  );
}

"""Credit Engine — user-facing wallet + ledger for Infinity Code.

1 credit = $0.01 USD of metered LLM spend (configurable rate). Zero-credit
lanes: local models, free-quota models, `:free` slugs, and llm_cache hits —
they already price at 0, so the wallet inherits them for free. BYOK mode
(bring your own key) bypasses the wallet entirely.

Metering is idempotent per exact call (120s dedup window): the same call can
route through several layers (client, swarm, longtask) but is only ever
charged once. Metering never blocks or raises — the app keeps working even
when the wallet is empty (free/local fallbacks already exist), and hard
gating is a later phase.

The engine is wired into the app via `configure()`; until then `meter()` is a
no-op so tests and scripts can construct the wallet independently.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DEDUP_WINDOW_S: float = 120.0
DEFAULT_ROLLOVER_FRACTION: float = 0.25
DEFAULT_ROLLOVER_VALIDITY_DAYS: int = 90
DEFAULT_RATE_CREDITS_PER_USD: float = 100.0

DEFAULT_PLANS: Dict[str, Dict[str, Any]] = {
    "free": {"credits": 300, "price_usd": 0, "trial_days": 14},
    "pro": {"credits": 2000, "price_usd": 20},
    "pro_plus": {"credits": 6000, "price_usd": 60},
    "ultra": {"credits": 20000, "price_usd": 200},
}
DEFAULT_PACKS: Dict[str, Dict[str, Any]] = {
    "starter": {"credits": 1500, "price_usd": 20, "validity_days": 30},
}

_WALLET_SQL = """
CREATE TABLE IF NOT EXISTS credit_wallet (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance_credits REAL NOT NULL DEFAULT 0,
    plan TEXT NOT NULL DEFAULT 'free',
    byok INTEGER NOT NULL DEFAULT 0,
    monthly_used REAL NOT NULL DEFAULT 0,
    monthly_limit REAL NOT NULL DEFAULT 0,
    rollover_credits REAL NOT NULL DEFAULT 0,
    rollover_expires REAL NOT NULL DEFAULT 0,
    plan_period TEXT NOT NULL DEFAULT ''
)
"""
_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS credit_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    delta REAL NOT NULL,
    reason TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    feature TEXT NOT NULL DEFAULT '',
    balance_after REAL NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}'
)
"""
_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_credit_ledger_ts ON credit_ledger(ts)"


class CreditEngine:
    """SQLite-backed wallet with a transaction ledger."""

    def __init__(
        self,
        db_path: Path,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.db_path = Path(db_path)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.enabled: bool = bool(cfg.get("enabled", True))
        self.rate_credits_per_usd: float = float(
            cfg.get("rate_credits_per_usd", DEFAULT_RATE_CREDITS_PER_USD)
        )
        self.plans: Dict[str, Dict[str, Any]] = dict(DEFAULT_PLANS)
        self.packs: Dict[str, Dict[str, Any]] = dict(DEFAULT_PACKS)
        raw_plans = cfg.get("plans")
        if isinstance(raw_plans, dict):
            self.plans.update({k: dict(v) for k, v in raw_plans.items() if isinstance(v, dict)})
        raw_packs = cfg.get("packs")
        if isinstance(raw_packs, dict):
            self.packs.update({k: dict(v) for k, v in raw_packs.items() if isinstance(v, dict)})
        self.rollover_fraction: float = float(
            cfg.get("rollover_fraction", DEFAULT_ROLLOVER_FRACTION)
        )
        self.rollover_validity_days: int = int(
            cfg.get("rollover_validity_days", DEFAULT_ROLLOVER_VALIDITY_DAYS)
        )
        raw_keys = cfg.get("license_keys")
        self.license_keys: List[Dict[str, str]] = (
            [dict(k) for k in raw_keys if isinstance(k, dict)] if isinstance(raw_keys, list) else []
        )
        self._dedup: Dict[str, float] = {}
        self._init_db()

    # --- internals -------------------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_WALLET_SQL)
                conn.execute(_LEDGER_SQL)
                conn.execute(_INDEX_SQL)
                conn.execute(
                    "INSERT OR IGNORE INTO credit_wallet (id) VALUES (1)"
                )
                # First run: grant the free plan's allowance.
                row = conn.execute(
                    "SELECT balance_credits FROM credit_wallet WHERE id = 1"
                ).fetchone()
                if float(row["balance_credits"] or 0.0) == 0.0:
                    grant = float(self.plans.get("free", {}).get("credits", 0.0))
                    if grant > 0:
                        conn.execute(
                            "UPDATE credit_wallet SET balance_credits = ?, "
                            "monthly_limit = ?, plan_period = ? WHERE id = 1",
                            (grant, grant, self._period()),
                        )
                        conn.execute(
                            "INSERT INTO credit_ledger (ts, delta, reason, model, "
                            "feature, balance_after, extra) "
                            "VALUES (?, ?, 'grant', '', 'free_trial', ?, '{}')",
                            (self._now(), grant, grant),
                        )
        except sqlite3.Error as exc:
            logger.error("Could not initialise credit wallet %s: %s", self.db_path, exc)

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _period(ts: Optional[float] = None) -> str:
        dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return dt.strftime("%Y-%m")

    def _wallet(self) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM credit_wallet WHERE id = 1").fetchone()
        return row

    def _rollover_if_due(self) -> None:
        """Monthly plan refresh: roll up to 25% of unused credits (90-day
        validity) into the balance — Qoder does not roll over; this does."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM credit_wallet WHERE id = 1").fetchone()
            period = self._period()
            if row["plan_period"] == period:
                return
            unused = max(0.0, float(row["monthly_limit"] or 0.0) - float(row["monthly_used"] or 0.0))
            roll = round(unused * self.rollover_fraction, 2)
            expires = self._now() + self.rollover_validity_days * 86400
            new_balance = float(row["balance_credits"] or 0.0) + roll
            conn.execute(
                "UPDATE credit_wallet SET plan_period = ?, monthly_used = 0, "
                "rollover_credits = ?, rollover_expires = ?, balance_credits = ? "
                "WHERE id = 1",
                (period, roll, expires, new_balance),
            )
            if roll > 0:
                conn.execute(
                    "INSERT INTO credit_ledger (ts, delta, reason, model, feature, "
                    "balance_after, extra) VALUES (?, ?, 'rollover', '', '', ?, ?)",
                    (self._now(), roll, new_balance,
                     json.dumps({"expires": expires})),
                )

    def _dedup_key(self, cost_usd: float, model: str, feature: str,
                   in_tokens: int, out_tokens: int) -> str:
        raw = "|".join([
            str(model or ""), str(feature or ""),
            str(round(float(cost_usd or 0.0), 6)),
            str(int(in_tokens or 0)), str(int(out_tokens or 0)),
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _is_duplicate(self, key: str) -> bool:
        now = self._now()
        seen = self._dedup.get(key)
        if seen is not None and now - seen < DEFAULT_DEDUP_WINDOW_S:
            return True
        self._dedup[key] = now
        return False

    # --- public API -------------------------------------------------------- #

    def balance(self) -> Dict[str, Any]:
        """Wallet state for the UI (never exposes raw keys)."""
        if not self.enabled:
            return {"enabled": False}
        self._rollover_if_due()
        row = self._wallet()
        plan_cfg = self.plans.get(row["plan"], {})
        pct = 0.0
        limit = float(row["monthly_limit"] or 0.0)
        if limit > 0:
            pct = round(float(row["monthly_used"] or 0.0) / limit * 100.0, 1)
        return {
            "enabled": True,
            "balance_credits": round(float(row["balance_credits"] or 0.0), 2),
            "plan": row["plan"],
            "plan_credits": int(plan_cfg.get("credits", 0)),
            "byok": bool(row["byok"]),
            "monthly_used": round(float(row["monthly_used"] or 0.0), 2),
            "monthly_limit": round(limit, 2),
            "monthly_pct": pct,
            "rollover_credits": round(float(row["rollover_credits"] or 0.0), 2),
        }

    def meter(
        self,
        cost_usd: float,
        model: str = "",
        feature: str = "",
        in_tokens: int = 0,
        out_tokens: int = 0,
        avoided_usd: float = 0.0,
    ) -> None:
        """Debit the wallet for a metered call. Never raises.

        Zero-cost lanes (local, free quota, cache hits) write zero-credit rows
        so analytics can show what the user saved; BYOK skips the wallet.
        """
        if not self.enabled:
            return
        try:
            self._rollover_if_due()
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM credit_wallet WHERE id = 1").fetchone()
                if bool(row["byok"]):
                    return
                cost = float(cost_usd or 0.0)
                if cost > 0 and self._is_duplicate(
                    self._dedup_key(cost, model, feature, in_tokens, out_tokens)
                ):
                    return  # already charged by another layer
                credits = round(cost * self.rate_credits_per_usd, 2)
                if credits <= 0 and cost > 0:
                    credits = 0.01  # minimum charge: micro-calls still register
                if credits <= 0 and avoided_usd <= 0 and "cache" not in feature:
                    return  # silence noise; only interesting zero rows survive
                reason = "cache_hit" if credits <= 0 and "cache" in feature else (
                    "free" if credits <= 0 else "usage"
                )
                balance = float(row["balance_credits"] or 0.0)
                new_balance = round(balance - credits, 2)
                extra = {"avoided_usd": round(float(avoided_usd or 0.0), 4)}
                conn.execute(
                    "UPDATE credit_wallet SET balance_credits = ?, "
                    "monthly_used = monthly_used + ? WHERE id = 1",
                    (new_balance, credits),
                )
                conn.execute(
                    "INSERT INTO credit_ledger (ts, delta, reason, model, feature, "
                    "balance_after, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self._now(), -credits if credits else 0.0, reason,
                     str(model or "")[:120], str(feature or "")[:120],
                     new_balance, json.dumps(extra)),
                )
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("credit meter failed (non-fatal): %s", exc)

    def add_pack(self, pack_id: str) -> Dict[str, Any]:
        """Credit pack purchase (MVP: simulated; Stripe checkout is later)."""
        pack = self.packs.get(str(pack_id or "").strip())
        if pack is None:
            return {"ok": False, "detail": "unknown pack"}
        credits = float(pack.get("credits", 0.0))
        if credits <= 0:
            return {"ok": False, "detail": "pack has no credits"}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM credit_wallet WHERE id = 1").fetchone()
            new_balance = round(float(row["balance_credits"] or 0.0) + credits, 2)
            conn.execute(
                "UPDATE credit_wallet SET balance_credits = ? WHERE id = 1",
                (new_balance,),
            )
            conn.execute(
                "INSERT INTO credit_ledger (ts, delta, reason, model, feature, "
                "balance_after, extra) VALUES (?, ?, 'purchase', '', '', ?, ?)",
                (self._now(), credits, new_balance,
                 json.dumps({"pack": pack_id, "price_usd": pack.get("price_usd", 0)})),
            )
        return {"ok": True, "pack": pack_id, "credits": credits}

    def set_plan(self, plan: str) -> Dict[str, Any]:
        """Switch plan (MVP: config-driven; license-gated activation is below)."""
        plan = str(plan or "").strip().lower()
        if plan not in self.plans:
            return {"ok": False, "detail": f"unknown plan '{plan}'"}
        credits = float(self.plans[plan].get("credits", 0.0))
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM credit_wallet WHERE id = 1").fetchone()
            new_balance = round(float(row["balance_credits"] or 0.0) + credits, 2)
            conn.execute(
                "UPDATE credit_wallet SET plan = ?, monthly_limit = ?, "
                "plan_period = ?, balance_credits = ? WHERE id = 1",
                (plan, credits, self._period(), new_balance),
            )
            conn.execute(
                "INSERT INTO credit_ledger (ts, delta, reason, model, feature, "
                "balance_after, extra) VALUES (?, ?, 'plan', '', '', ?, ?)",
                (self._now(), credits, new_balance,
                 json.dumps({"plan": plan})),
            )
        return {"ok": True, "plan": plan, "credits": credits}

    def activate_license(self, key: str) -> Dict[str, Any]:
        """Offline license validation: key -> plan. Stripe checkout is later."""
        key = str(key or "").strip()
        for entry in self.license_keys:
            if entry.get("key") == key:
                plan = str(entry.get("plan") or "pro")
                result = self.set_plan(plan)
                result["ok"] = result.get("ok") is True
                return result
        return {"ok": False, "detail": "invalid license key"}

    def set_byok(self, enabled: bool) -> None:
        """BYOK mode: bring your own key, the wallet is bypassed entirely."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE credit_wallet SET byok = ? WHERE id = 1", (int(bool(enabled)),)
            )

    def ledger(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, delta, reason, model, feature, balance_after, extra "
                "FROM credit_ledger ORDER BY ts DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [dict(r) for r in rows]

    def export(self, fmt: str = "json") -> str:
        rows = self.ledger(500)
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(
                buf, fieldnames=["ts", "delta", "reason", "model", "feature", "balance_after"]
            )
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in writer.fieldnames})
            return buf.getvalue()
        return json.dumps(rows, ensure_ascii=False, indent=2)

    def analytics(self) -> Dict[str, Any]:
        """Sales-ready usage analytics: per-feature spend, savings, forecast."""
        rows = self.ledger(500)
        per_feature: Dict[str, float] = {}
        total_spend = 0.0
        cache_saved = 0.0
        cache_hits = 0
        now = self._now()
        for r in rows:
            delta = float(r.get("delta") or 0.0)
            reason = str(r.get("reason") or "")
            if delta < 0:
                feature = str(r.get("feature") or "other") or "other"
                per_feature[feature] = per_feature.get(feature, 0.0) + abs(delta)
                total_spend += abs(delta)
            if reason == "cache_hit":
                cache_hits += 1
                extra = r.get("extra") or "{}"
                try:
                    cache_saved += float(json.loads(extra).get("avoided_usd", 0.0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    pass
        # Burn-rate: average daily spend over the ledger window, extrapolated.
        window_days = max(1.0, (now - min((float(r["ts"]) for r in rows), default=now)) / 86400.0)
        daily = total_spend / window_days
        forecast_30d = round(daily * 30.0, 2)
        bal = self.balance()
        limit = float(bal.get("monthly_limit") or 0.0)
        alerts: List[str] = []
        if limit > 0:
            pct = float(bal.get("monthly_pct") or 0.0)
            if pct >= 80:
                alerts.append(f"{pct:.0f}% of monthly plan used — near the cap")
            elif pct >= 50:
                alerts.append(f"{pct:.0f}% of monthly plan used")
        return {
            "total_spend_credits": round(total_spend, 2),
            "per_feature": {k: round(v, 2) for k, v in sorted(per_feature.items(), key=lambda kv: -kv[1])},
            "cache_savings_usd": round(cache_saved, 4),
            "cache_hits": cache_hits,
            "daily_burn_credits": round(daily, 2),
            "forecast_30d_credits": forecast_30d,
            "alerts": alerts,
        }

    def estimate_cost_usd(self, model_id: str, prompt: str, max_tokens: int = 4000) -> float:
        """Pre-execution cost preview — the 'no surprises' feature."""
        try:
            from backend.core.router import MODEL_SPECS
        except ImportError:  # running with backend/ as the working directory
            from core.router import MODEL_SPECS  # type: ignore[no-redef]
        spec = MODEL_SPECS.get(str(model_id or ""))
        if spec is None:
            return 0.0
        tin = max(1, len(str(prompt or "")) // 4)
        tout = max(1, int(max_tokens or 0))
        usd = (tin * float(spec.cost_in_per_million) + tout * float(spec.cost_out_per_million)) / 1_000_000.0
        return round(usd, 6)


# --- app-wide singleton (no-op until configured) --------------------------- #

_ENGINE: Optional[CreditEngine] = None


def configure(engine: CreditEngine) -> None:
    global _ENGINE
    _ENGINE = engine


def meter(
    cost_usd: float,
    model: str = "",
    feature: str = "",
    in_tokens: int = 0,
    out_tokens: int = 0,
    avoided_usd: float = 0.0,
) -> None:
    if _ENGINE is not None:
        _ENGINE.meter(cost_usd, model, feature, in_tokens, out_tokens, avoided_usd)


__all__ = ["CreditEngine", "configure", "meter", "DEFAULT_PLANS", "DEFAULT_PACKS"]

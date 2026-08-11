"""Token cost accounting for Infinity Code.

Every model call is priced BEFORE it runs. If the estimated AUD cost would
blow the daily budget, the call is refused and the caller can downgrade to a
cheaper council member via `get_fallback()`.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any, Dict, List

try:
    from backend.core.router import ModelSpec, USD_PER_AUD
except ImportError:  # running with backend/ as the working directory
    from core.router import ModelSpec, USD_PER_AUD  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


class CostTracker:
    """Tracks daily AUD spend across all model calls with a hard budget."""

    # Ordered cheapest -> most expensive; get_fallback walks DOWN this list.
    FALLBACK_HIERARCHY: List[str] = [
        "dashscope/qwen-turbo",
        "dashscope/qwen-plus",
        "dashscope/qwen-max",
    ]

    def __init__(self, daily_budget_aud: float = 100.0) -> None:
        self.daily_budget_aud: float = float(daily_budget_aud)
        self._spent_today_aud: float = 0.0
        self._breakdown_aud: Dict[str, float] = {}
        self._day: date = date.today()
        self._lock: threading.Lock = threading.Lock()

    @property
    def remaining_aud(self) -> float:
        """AUD left in today's budget (never negative). Public for guards."""
        with self._lock:
            self._roll_day_if_needed()
            return max(0.0, self.daily_budget_aud - self._spent_today_aud)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _roll_day_if_needed(self) -> None:
        """Reset counters at local midnight. Caller must hold the lock."""
        today: date = date.today()
        if today != self._day:
            logger.info(
                "New day (%s): resetting spend (yesterday: $%.4f AUD)",
                today.isoformat(),
                self._spent_today_aud,
            )
            self._day = today
            self._spent_today_aud = 0.0
            self._breakdown_aud = {}

    @staticmethod
    def _estimate_cost_aud(
        spec: ModelSpec, estimated_input: int, estimated_output: int
    ) -> float:
        try:
            usd: float = (
                estimated_input * spec.cost_in_per_million / 1_000_000.0
                + estimated_output * spec.cost_out_per_million / 1_000_000.0
            )
            return usd / USD_PER_AUD
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            logger.error("Cost estimation failed: %s", exc)
            return 0.0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def approve_call(
        self, spec: ModelSpec, estimated_input: int, estimated_output: int
    ) -> bool:
        """Approve or refuse a call against the remaining daily budget.

        If approved, the estimated cost is committed to today's spend
        immediately so concurrent callers cannot double-spend the budget.
        """
        try:
            cost_aud: float = self._estimate_cost_aud(
                spec, estimated_input, estimated_output
            )
            with self._lock:
                self._roll_day_if_needed()
                remaining: float = self.daily_budget_aud - self._spent_today_aud
                if cost_aud > remaining:
                    logger.warning(
                        "REFUSED %s: est $%.4f AUD > remaining $%.4f AUD",
                        spec.id,
                        cost_aud,
                        remaining,
                    )
                    return False
                self._spent_today_aud += cost_aud
                self._breakdown_aud[spec.id] = (
                    self._breakdown_aud.get(spec.id, 0.0) + cost_aud
                )
                logger.debug(
                    "APPROVED %s: est $%.4f AUD (spent today: $%.4f AUD)",
                    spec.id,
                    cost_aud,
                    self._spent_today_aud,
                )
                return True
        except (AttributeError, TypeError) as exc:
            logger.error("approve_call() received bad inputs: %s", exc)
            return False

    def get_fallback(self, spec: ModelSpec) -> str:
        """Return the next-cheaper model ID from the fallback hierarchy.

        Unknown models (and the cheapest tier itself) fall back to the
        cheapest model in the hierarchy.
        """
        try:
            current_index: int = self.FALLBACK_HIERARCHY.index(spec.id)
            if current_index > 0:
                return self.FALLBACK_HIERARCHY[current_index - 1]
            return self.FALLBACK_HIERARCHY[0]
        except (ValueError, AttributeError):
            return self.FALLBACK_HIERARCHY[0]

    def record_actual(self, cost_aud: float) -> None:
        """Record an already-incurred cost (e.g. from a streaming chat whose
        exact usage is only known after the last chunk)."""
        if cost_aud <= 0:
            return
        try:
            with self._lock:
                self._roll_day_if_needed()
                self._spent_today_aud += float(cost_aud)
        except (TypeError, ValueError) as exc:
            logger.error("record_actual() received bad cost: %s", exc)

    def reconcile(
        self,
        spec: ModelSpec,
        estimated_input: int,
        estimated_output: int,
        actual_cost_aud: float,
    ) -> None:
        """Replace a reserved estimate with the actual cost.

        Called after a model call finishes so the budget stays accurate when
        the real cost differs from the pre-flight estimate.
        """
        try:
            estimated: float = self._estimate_cost_aud(
                spec, estimated_input, estimated_output
            )
            diff: float = float(actual_cost_aud) - estimated
            if diff == 0:
                return
            with self._lock:
                self._roll_day_if_needed()
                self._spent_today_aud += diff
        except (TypeError, ValueError) as exc:
            logger.error("reconcile() failed: %s", exc)

    def report(self) -> Dict[str, Any]:
        """Snapshot of today's spend, remaining budget, and per-model breakdown."""
        try:
            with self._lock:
                self._roll_day_if_needed()
                return {
                    "date": self._day.isoformat(),
                    "spent_today_aud": round(self._spent_today_aud, 4),
                    "remaining_aud": round(
                        max(0.0, self.daily_budget_aud - self._spent_today_aud), 4
                    ),
                    "daily_budget_aud": self.daily_budget_aud,
                    "breakdown": {
                        model: round(aud, 4)
                        for model, aud in sorted(self._breakdown_aud.items())
                    },
                }
        except Exception as exc:  # noqa: BLE001 - report must never raise
            logger.error("report() failed: %s", exc)
            return {
                "date": date.today().isoformat(),
                "spent_today_aud": 0.0,
                "remaining_aud": self.daily_budget_aud,
                "daily_budget_aud": self.daily_budget_aud,
                "breakdown": {},
            }


__all__ = ["CostTracker"]

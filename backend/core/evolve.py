"""Evolution engine for Infinity Code.

Runs the eval harness across model lanes, compares scores, and surfaces
promotion/demotion proposals.  Logs every decision to
`DATA_DIR/evolution_log.jsonl`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from backend.core.constitution import Constitution
    from backend.core.benchmark_curriculum import BenchmarkCurriculum
    from backend.core.eval_harness import EvalHarness
except ImportError:  # running with backend/ as the working directory
    from core.constitution import Constitution  # type: ignore[no-redef]
    from core.benchmark_curriculum import BenchmarkCurriculum  # type: ignore[no-redef]
    from core.eval_harness import EvalHarness  # type: ignore[no-redef]

try:
    from backend.core.router import LANE_LOCAL, USD_PER_AUD
except ImportError:  # running with backend/ as the working directory
    from core.router import LANE_LOCAL, USD_PER_AUD  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Lanes valid per the router's defaults. "fable" was never one of them — it
# silently degraded to the cheap chain — but the owner's FABLE stack is the
# local llama.cpp serving, which lives in the `local` lane, so remap the
# legacy name instead of benchmarking a phantom lane.
_DEFAULT_LANES: List[str] = ["cheap", "smart", LANE_LOCAL]
_LANE_ALIASES: Dict[str, str] = {"fable": LANE_LOCAL}

# --------------------------------------------------------------------------- #
# Evolve
# --------------------------------------------------------------------------- #

class Evolve:
    """Benchmark lanes, score models, and propose cheaper alternatives."""

    def __init__(
        self,
        data_dir: Path,
        constitution: Constitution,
        eval_harness_factory: Any,
        self_review: Any,
        lanes: Optional[List[str]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.constitution = constitution
        self.eval_harness_factory = eval_harness_factory
        self.self_review = self_review
        # Config/caller overrides pass through (only the legacy alias is
        # remapped); an unset/empty list falls back to router-valid defaults.
        resolved = list(lanes) if lanes else list(_DEFAULT_LANES)
        self.lanes = [_LANE_ALIASES.get(str(lane), str(lane)) for lane in resolved]
        self.evolution_log = self.data_dir / "evolution_log.jsonl"
        self.evolution_log.parent.mkdir(parents=True, exist_ok=True)
        self._baseline_scores: Dict[str, float] = {}
        self.curriculum = BenchmarkCurriculum(self.data_dir)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def step(self) -> Dict[str, Any]:
        """Entry-point called by the scheduler every 6 h."""
        result: Dict[str, Any] = {
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lane_reports": {},
            "proposals": [],
            "promotions": [],
            "weaknesses": {},
            "curriculum": {},
            "errors": [],
        }

        # Budget guard
        today = time.strftime("%Y-%m-%d")
        daily_cost = self._daily_cost_so_far(today)
        if self.constitution.daily_budget_exceeded(daily_cost):
            result["errors"].append("Daily evolution budget exceeded.")
            return result

        harness: Optional[EvalHarness] = None
        try:
            harness = self.eval_harness_factory()
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"EvalHarness factory failed: {exc}")
            return result

        # Run eval for every lane
        for lane in self.lanes:
            try:
                report = harness.run(lane=lane)
                result["lane_reports"][lane] = report
                score = float(report.get("aggregate_score", 0.0))
                self._baseline_scores[lane] = score
                lane_weaknesses = list(report.get("weaknesses", []))[:3]
                result["weaknesses"][lane] = lane_weaknesses
                queued_drills = self.curriculum.record(lane, report)
                result["curriculum"][lane] = queued_drills
                for drill in queued_drills[:3]:
                    result["proposals"].append({
                        "type": "benchmark_drill",
                        "lane": lane,
                        "capability": drill["capability"],
                        "task_id": drill["task_id"],
                        "reason": f"Practise {drill['capability']} using a targeted drill, then rerun {drill['task_name']}.",
                    })
                for weakness in lane_weaknesses:
                    result["proposals"].append({
                        "type": "capability_regression",
                        "lane": lane,
                        "capability": weakness.get("capability", "general"),
                        "score": weakness.get("score", 0.0),
                        "failed_tasks": weakness.get("failed_tasks", []),
                        "reason": (
                            f"{lane} is weak at {weakness.get('capability', 'general')} "
                            f"({float(weakness.get('score', 0.0)):.3f}). Add targeted examples, "
                            "tool guidance, or route this capability to a stronger lane."
                        ),
                    })
                logger.info("Evolve lane=%s score=%.3f", lane, score)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Evolve eval failed for lane %s: %s", lane, exc)
                result["errors"].append(f"lane {lane}: {exc}")

        # Self-review proposals feed into the prompt doctor
        try:
            sr_proposals = self.self_review.proposals()
            if not isinstance(sr_proposals, list):
                sr_proposals = []
        except Exception as exc:  # noqa: BLE001
            sr_proposals = []
            result["errors"].append(f"SelfReview failed: {exc}")
        result["proposals"].extend(sr_proposals)

        # Generate promotions: if a cheaper lane is within 0.05 of a dearer lane,
        # propose swapping the dearer lane's primary model down.
        promotions = self._compute_promotions(result["lane_reports"])
        result["promotions"] = promotions

        # Persist. Eval reports the spend in USD; the daily budget guard and
        # every other cost ledger in the app are AUD, so convert before
        # logging (AUD = USD / USD_PER_AUD) — logging raw USD here used to
        # understate spend against `max_daily_cost_aud`.
        log_entry = {
            "date": result["ran_at"],
            "event": "evolution_step",
            "scores": self._baseline_scores,
            "proposals_count": len(result["proposals"]),
            "promotions": promotions,
            "weaknesses": result["weaknesses"],
            "queued_drills": sum(len(items) for items in result["curriculum"].values()),
            "cost_aud": round(
                sum(float(report.get("total_cost_usd", 0.0)) for report in result["lane_reports"].values())
                / USD_PER_AUD,
                6,
            ),
        }
        with self.evolution_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_entry) + "\n")

        return result

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _compute_promotions(self, reports: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find cases where a cheaper lane is almost as good as an expensive one."""
        promotions: List[Dict[str, Any]] = []
        # Ordered from cheapest to most expensive (heuristic), using only
        # lanes that exist in the router by default. Any extra lane this run
        # benchmarked (e.g. an owner-configured fable/local stack) slots in
        # right after the free local tier: such lanes are the owner's own
        # zero-cost endpoints, so they must never pose as the dear option.
        price_order = [LANE_LOCAL, "cheap", "vision", "smart", "custom"]
        extras = sorted(lane for lane in reports if lane not in price_order)
        if extras:
            index = 1 if LANE_LOCAL in reports else 0
            price_order[index:index] = extras
        for i, cheap_lane in enumerate(price_order):
            if cheap_lane not in reports:
                continue
            cheap_score = float(reports[cheap_lane].get("aggregate_score", 0.0))
            for expensive_lane in price_order[i + 1 :]:
                if expensive_lane not in reports:
                    continue
                expensive_score = float(reports[expensive_lane].get("aggregate_score", 0.0))
                if expensive_score > 0.0 and abs(expensive_score - cheap_score) <= 0.05:
                    promotions.append({
                        "type": "demote",
                        "from_lane": expensive_lane,
                        "to_lane": cheap_lane,
                        "expensive_score": expensive_score,
                        "cheap_score": cheap_score,
                        "reason": (
                            f"{cheap_lane} score {cheap_score:.3f} is within 0.05 of "
                            f"{expensive_lane} ({expensive_score:.3f}); consider using the cheaper lane."
                        ),
                    })
        return promotions

    def _daily_cost_so_far(self, today: str) -> float:
        total = 0.0
        if not self.evolution_log.exists():
            return total
        with self.evolution_log.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("date", "").startswith(today):
                    total += float(entry.get("cost_aud", 0.0))
        return total


__all__ = ["Evolve"]

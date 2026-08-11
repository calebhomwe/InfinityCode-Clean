"""Focused, offline tests for the evolution scorecard loop."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.evolve import Evolve
from core.router import USD_PER_AUD


class Budget:
    def daily_budget_exceeded(self, _cost: float) -> bool:
        return False


class Harness:
    def run(self, lane: str) -> dict:
        return {
            "aggregate_score": 0.5 if lane == "cheap" else 0.9,
            "total_cost_usd": 0.012,
            "weaknesses": [{
                "capability": "browser",
                "score": 0.0,
                "tasks_total": 1,
                "tasks_passed": 0,
                "failed_tasks": [{"id": "browser-1", "name": "browser test", "reason": "exact mismatch"}],
            }],
            "results": [{
                "task_id": "browser-1", "name": "browser test", "capability": "browser",
                "passed": False, "score": 0.0, "reason": "exact mismatch",
            }],
        }


class Review:
    def proposals(self) -> list[dict]:
        return [{"type": "prompt_fix", "reason": "existing self-review finding"}]


def test_step_turns_weaknesses_into_actionable_proposals() -> None:
    with tempfile.TemporaryDirectory() as directory:
        evolve = Evolve(Path(directory), Budget(), lambda: Harness(), Review(), lanes=["cheap", "smart"])
        result = evolve.step()
        regression = next(p for p in result["proposals"] if p["type"] == "capability_regression")
        assert regression["lane"] == "cheap"
        assert regression["capability"] == "browser"
        assert any(p["type"] == "prompt_fix" for p in result["proposals"])
        assert any(p["type"] == "benchmark_drill" for p in result["proposals"])
        assert result["curriculum"]["cheap"][0]["capability"] == "browser"
        assert result["weaknesses"]["smart"][0]["score"] == 0.0
        log = (Path(directory) / "evolution_log.jsonl").read_text(encoding="utf-8")
        # Both lanes report 0.012 USD each; the log must carry the AUD
        # conversion (AUD = USD / USD_PER_AUD), not the raw USD sum.
        expected_aud = round((0.012 + 0.012) / USD_PER_AUD, 6)
        assert f'"cost_aud": {expected_aud}' in log


if __name__ == "__main__":
    test_step_turns_weaknesses_into_actionable_proposals()
    print("1/1 passed")

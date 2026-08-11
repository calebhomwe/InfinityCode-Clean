"""
QuestBench Benchmark Runner for Infinity Code
Processes batches of sessions and generates reports
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from .session_recorder import InfinityCodeSession, SessionOutcome, Turn
from .quality_scorer import QualityScorer, QualityScores

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Runs benchmark analysis on Infinity Code sessions"""

    def __init__(self, sessions_dir: Path, results_dir: Path):
        self.sessions_dir = sessions_dir
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.scorer = QualityScorer()

    def load_session(self, session_file: Path) -> InfinityCodeSession:
        """Load a session from disk"""
        with open(session_file) as f:
            data = json.load(f)

        data["start_time"] = datetime.fromisoformat(data["start_time"])
        data["end_time"] = (
            datetime.fromisoformat(data["end_time"])
            if data["end_time"] else None
        )
        data["outcome"] = SessionOutcome(data["outcome"])

        # Reconstruct Turn objects from dicts
        data["turns"] = [
            Turn(
                role=t["role"],
                content=t["content"],
                timestamp=datetime.fromisoformat(t["timestamp"]),
                tool_calls=t.get("tool_calls"),
                files_touched=t.get("files_touched"),
                lines_added=t.get("lines_added", 0),
                lines_removed=t.get("lines_removed", 0),
                cascade_breaks=t.get("cascade_breaks", 0),
            )
            for t in data["turns"]
        ]

        return InfinityCodeSession(**data)

    def run_benchmark(self, session_pattern: str = "*.json") -> dict[str, Any]:
        """Run benchmark on all matching sessions"""
        logger.info(f"Running benchmark on {self.sessions_dir}/{session_pattern}")

        session_files = list(self.sessions_dir.glob(session_pattern))

        if not session_files:
            logger.warning("No sessions found")
            return {"sessions": [], "summary": {}}

        scored_sessions: list[QualityScores] = []
        for session_file in session_files:
            try:
                session = self.load_session(session_file)
                scores = self.scorer.score_session(session)
                scored_sessions.append(scores)
                logger.info(f"Scored {session.session_id}: {scores.overall_score:.1f}")
            except Exception as e:
                logger.error(f"Failed to score {session_file}: {e}")

        summary = self._generate_summary(scored_sessions)

        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "sessions_count": len(scored_sessions),
            "sessions": [s.__dict__ for s in scored_sessions],
            "summary": summary
        }

        output_file = (
            self.results_dir /
            f"benchmark_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        )

        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)

        logger.info(f"Saved benchmark results to {output_file}")

        return results

    def _generate_summary(self, sessions: list[QualityScores]) -> dict[str, Any]:
        """Generate aggregate statistics"""
        if not sessions:
            return {}

        avg_overall = sum(s.overall_score for s in sessions) / len(sessions)
        avg_completion = sum(s.completion_rate for s in sessions) / len(sessions)
        avg_cascade = sum(s.cascade_break_score for s in sessions) / len(sessions)
        avg_credits = sum(s.credit_efficiency for s in sessions) / len(sessions)
        avg_time = sum(s.time_efficiency for s in sessions) / len(sessions)
        avg_satisfaction = sum(s.user_satisfaction for s in sessions) / len(sessions)

        score_distribution = self._bucket_scores([s.overall_score for s in sessions])
        worst_sessions = sorted(sessions, key=lambda s: s.overall_score)[:5]

        return {
            "averages": {
                "overall_score": avg_overall,
                "completion_rate": avg_completion,
                "cascade_break_score": avg_cascade,
                "credit_efficiency": avg_credits,
                "time_efficiency": avg_time,
                "user_satisfaction": avg_satisfaction
            },
            "score_distribution": score_distribution,
            "worst_sessions": [
                {
                    "session_id": s.session_id,
                    "score": s.overall_score,
                    "cascade_breaks": s.details.get("cascade_breaks", 0)
                }
                for s in worst_sessions
            ],
            "insights": self._generate_insights(sessions)
        }

    def _bucket_scores(self, scores: list[float]) -> dict[str, int]:
        """Bucket scores into ranges"""
        buckets = {
            "0-20 (Critical)": 0,
            "21-40 (Poor)": 0,
            "41-60 (Fair)": 0,
            "61-80 (Good)": 0,
            "81-100 (Excellent)": 0
        }

        for score in scores:
            if score <= 20:
                buckets["0-20 (Critical)"] += 1
            elif score <= 40:
                buckets["21-40 (Poor)"] += 1
            elif score <= 60:
                buckets["41-60 (Fair)"] += 1
            elif score <= 80:
                buckets["61-80 (Good)"] += 1
            else:
                buckets["81-100 (Excellent)"] += 1

        return buckets

    def _generate_insights(self, sessions: list[QualityScores]) -> list[str]:
        """Generate actionable insights"""
        insights = []

        high_cascade = [s for s in sessions if s.cascade_break_score < 0.7]
        if len(high_cascade) > len(sessions) * 0.3:
            insights.append(
                f"WARNING: {len(high_cascade)} sessions have high cascade breaks. "
                f"Change isolation engine needs improvement."
            )

        avg_credits = sum(s.credit_efficiency for s in sessions) / len(sessions)
        if avg_credits < 0.5:
            insights.append(
                f"WARNING: Low credit efficiency ({avg_credits:.2f}). "
                f"Users burning credits faster than expected."
            )

        avg_time = sum(s.time_efficiency for s in sessions) / len(sessions)
        if avg_time < 0.4:
            insights.append(
                f"WARNING: Sessions taking longer than expected ({avg_time:.2f}). "
                f"Consider performance optimization."
            )

        completed = [s for s in sessions if s.completion_rate > 0.8]
        if completed:
            avg_sat = sum(s.user_satisfaction for s in completed) / len(completed)
            if avg_sat < 0.6:
                insights.append(
                    f"WARNING: Completed sessions have low satisfaction ({avg_sat:.2f}). "
                    f"Quality issues beyond completion."
                )

        return insights

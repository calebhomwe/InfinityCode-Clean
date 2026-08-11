"""
QuestBench Quality Scorer for Infinity Code
Multi-dimensional scoring addressing real user pain points
"""
import logging
from dataclasses import dataclass
from .session_recorder import InfinityCodeSession, SessionOutcome

logger = logging.getLogger(__name__)


@dataclass
class QualityScores:
    """Multi-dimensional quality assessment"""
    session_id: str

    # Core metrics (addressing top complaints)
    completion_rate: float  # 0-1
    cascade_break_score: float  # 0-1: Penalty for unintended modifications
    credit_efficiency: float  # 0-1: Credits per successful change
    time_efficiency: float  # 0-1: Speed vs expected
    user_satisfaction: float  # 0-1: User rating normalized

    # Composite score
    overall_score: float  # 0-100

    # Breakdown
    details: dict


class QualityScorer:
    """Scores Infinity Code sessions across multiple dimensions"""

    def __init__(
        self,
        avg_credits_per_task: float = 100.0,
        expected_duration_minutes: float = 15.0
    ):
        self.avg_credits = avg_credits_per_task
        self.expected_duration = expected_duration_minutes

    def score_session(self, session: InfinityCodeSession) -> QualityScores:
        """Score a complete session"""

        completion = self._score_completion(session)
        cascade = self._score_cascade_breaks(session)
        credits = self._score_credit_efficiency(session)
        time_eff = self._score_time_efficiency(session)
        satisfaction = self._score_user_satisfaction(session)

        # Composite: weighted average
        weights = {
            "completion": 0.30,
            "cascade": 0.25,
            "credits": 0.20,
            "time": 0.15,
            "satisfaction": 0.10
        }

        overall = (
            completion * weights["completion"] +
            cascade * weights["cascade"] +
            credits * weights["credits"] +
            time_eff * weights["time"] +
            satisfaction * weights["satisfaction"]
        ) * 100

        return QualityScores(
            session_id=session.session_id,
            completion_rate=completion,
            cascade_break_score=cascade,
            credit_efficiency=credits,
            time_efficiency=time_eff,
            user_satisfaction=satisfaction,
            overall_score=overall,
            details={
                "files_intended": len(session.files_intended),
                "files_modified": len(session.files_modified),
                "cascade_breaks": self._count_cascade_breaks(session),
                "duration_minutes": session.duration_seconds() / 60,
                "turns": len(session.turns),
                "weights": weights
            }
        )

    def _score_completion(self, session: InfinityCodeSession) -> float:
        """Score: 1.0 = fully completed, 0.0 = failed/abandoned"""
        if session.outcome == SessionOutcome.COMPLETED:
            return 1.0
        elif session.outcome == SessionOutcome.PARTIAL:
            return 0.5
        elif session.outcome == SessionOutcome.FAILED:
            return 0.2
        else:
            return 0.0

    def _score_cascade_breaks(self, session: InfinityCodeSession) -> float:
        """Penalty for modifying files outside explicit scope"""
        if not session.files_intended:
            return 1.0

        intended_set = set(session.files_intended)
        modified_set = set(session.files_modified)
        unintended = modified_set - intended_set

        if not unintended:
            return 1.0

        penalty = len(unintended) * 0.1
        return max(0.0, 1.0 - penalty)

    def _score_credit_efficiency(self, session: InfinityCodeSession) -> float:
        """Score credit usage relative to baseline"""
        if session.credits_used == 0:
            return 1.0

        if session.outcome != SessionOutcome.COMPLETED:
            return min(1.0, (self.avg_credits / session.credits_used) * 0.5)

        ratio = self.avg_credits / session.credits_used
        return min(1.0, ratio)

    def _score_time_efficiency(self, session: InfinityCodeSession) -> float:
        """Score duration vs expected"""
        duration_min = session.duration_seconds() / 60

        if duration_min == 0:
            return 0.0

        ratio = self.expected_duration / duration_min
        return min(1.0, ratio)

    def _score_user_satisfaction(self, session: InfinityCodeSession) -> float:
        """Normalize user rating (1-5 stars) to 0-1"""
        if session.user_rating is None:
            if session.outcome == SessionOutcome.COMPLETED:
                return 0.7
            else:
                return 0.3

        return (session.user_rating - 1) / 4.0

    def _count_cascade_breaks(self, session: InfinityCodeSession) -> int:
        """Count total cascade breaks across all turns"""
        total = 0
        for turn in session.turns:
            total += turn.cascade_breaks
        return total

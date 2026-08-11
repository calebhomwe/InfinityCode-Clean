"""
QuestBench Session Recorder for Infinity Code
Captures session telemetry for benchmark evaluation
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class SessionOutcome(Enum):
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class Turn:
    """Single conversation turn in a session"""
    role: str  # user | assistant | system
    content: str
    timestamp: datetime
    tool_calls: list[dict[str, Any]] | None = None
    files_touched: list[str] | None = None
    lines_added: int = 0
    lines_removed: int = 0
    cascade_breaks: int = 0  # Files modified outside explicit scope

    def to_dict(self) -> dict:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class InfinityCodeSession:
    """Complete Infinity Code session"""
    session_id: str
    user_id: str
    start_time: datetime
    end_time: datetime | None
    goal: str
    turns: list[Turn]
    outcome: SessionOutcome
    files_intended: list[str]  # Files user explicitly asked to modify
    files_modified: list[str]  # Files actually modified
    credits_used: int
    model_used: str
    user_rating: int | None  # 1-5 stars
    user_feedback: str | None

    def duration_seconds(self) -> int:
        if not self.end_time:
            return 0
        return int((self.end_time - self.start_time).total_seconds())

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start_time"] = self.start_time.isoformat()
        data["end_time"] = self.end_time.isoformat() if self.end_time else None
        data["outcome"] = self.outcome.value
        data["turns"] = [t.to_dict() for t in self.turns]
        return data


class SessionRecorder:
    """Captures and persists Infinity Code sessions for benchmarking"""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.current_session: InfinityCodeSession | None = None

    def start_session(
        self,
        session_id: str,
        user_id: str,
        goal: str,
        files_intended: list[str],
        model: str
    ) -> InfinityCodeSession:
        """Start recording a new session"""
        self.current_session = InfinityCodeSession(
            session_id=session_id,
            user_id=user_id,
            start_time=datetime.utcnow(),
            end_time=None,
            goal=goal,
            turns=[],
            outcome=SessionOutcome.ABANDONED,
            files_intended=files_intended,
            files_modified=[],
            credits_used=0,
            model_used=model,
            user_rating=None,
            user_feedback=None
        )
        logger.info(f"Started recording session {session_id}")
        return self.current_session

    def record_turn(self, turn: Turn) -> None:
        """Record a conversation turn"""
        if not self.current_session:
            raise RuntimeError("No active session")

        self.current_session.turns.append(turn)

        if turn.files_touched:
            for f in turn.files_touched:
                if f not in self.current_session.files_modified:
                    self.current_session.files_modified.append(f)

    def record_tool_call(
        self,
        tool_name: str,
        tool_params: dict[str, Any],
        cascade_breaks: int = 0
    ) -> None:
        """Record a tool call with cascade break detection"""
        if not self.current_session:
            return

        for turn in reversed(self.current_session.turns):
            if turn.role == "assistant":
                if turn.tool_calls is None:
                    turn.tool_calls = []
                turn.tool_calls.append({
                    "tool": tool_name,
                    "params": tool_params,
                    "cascade_breaks": cascade_breaks
                })
                turn.cascade_breaks += cascade_breaks
                break

    def end_session(
        self,
        outcome: SessionOutcome,
        credits_used: int,
        user_rating: int | None = None,
        user_feedback: str | None = None
    ) -> None:
        """End and save the session"""
        if not self.current_session:
            return

        self.current_session.end_time = datetime.utcnow()
        self.current_session.outcome = outcome
        self.current_session.credits_used = credits_used
        self.current_session.user_rating = user_rating
        self.current_session.user_feedback = user_feedback

        self._save_session()
        logger.info(f"Ended session {self.current_session.session_id}")
        self.current_session = None

    def _save_session(self) -> None:
        """Save session to disk"""
        if not self.current_session:
            return

        filename = f"{self.current_session.session_id}.json"
        filepath = self.storage_dir / filename

        with open(filepath, 'w') as f:
            json.dump(self.current_session.to_dict(), f, indent=2)

        logger.info(f"Saved session to {filepath}")

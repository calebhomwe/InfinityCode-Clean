"""Partial Rollout Execution — K3-inspired intermediate result types.

Based on Kimi K3's partial rollout scheme (λNK early stopping) where long-horizon
tool executions can yield intermediate results as soon as a fraction λ of
trajectories complete, rather than waiting for all to finish.
"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class PartialResult(TypedDict):
    """Intermediate result from partial execution."""

    status: str  # "partial" | "complete"
    output: str
    artifacts: List[str]
    lambda_completed: float  # 0.0 - 1.0
    execution_time_ms: int
    attempt_id: str


class PartialRolloutConfig:
    """Configuration for partial rollout behaviour."""

    def __init__(
        self,
        enabled: bool = True,
        lambda_threshold: float = 0.6,
        stream_interval_ms: int = 500,
    ) -> None:
        self.enabled = enabled
        self.lambda_threshold = lambda_threshold
        self.stream_interval_ms = stream_interval_ms


__all__ = ["PartialResult", "PartialRolloutConfig"]

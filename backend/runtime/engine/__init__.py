"""Deterministic orchestration primitives for the Team Engine.

The state machine never calls an LLM — agents are injected callables and
every payload crossing the boundary is a validated HandoffArtifact."""

from runtime.engine.state_machine import (
    RED_TEAM_FAILURE_TAGS,
    AgentExecutionError,
    EngineEvent,
    EventType,
    InvalidArtifactError,
    InvalidVerdictError,
    MaxRetriesExceededError,
    Producer,
    RedTeamer,
    RetryContext,
    TeamEngine,
    TeamEngineError,
    TeamState,
    Verdict,
    Verifier,
)

__all__ = [
    "RED_TEAM_FAILURE_TAGS",
    "AgentExecutionError",
    "EngineEvent",
    "EventType",
    "InvalidArtifactError",
    "InvalidVerdictError",
    "MaxRetriesExceededError",
    "Producer",
    "RedTeamer",
    "RetryContext",
    "TeamEngine",
    "TeamEngineError",
    "TeamState",
    "Verdict",
    "Verifier",
]

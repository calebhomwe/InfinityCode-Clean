"""Typed contracts for the Team Engine. Every value that crosses an
agent boundary lives here and is strictly validated."""

from runtime.schema.handoff import (
    HandoffArtifact,
    HandoffContext,
    HandoffArtifacts,
    HandoffValidationError,
)

__all__ = [
    "HandoffArtifact",
    "HandoffContext",
    "HandoffArtifacts",
    "HandoffValidationError",
]

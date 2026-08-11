"""Accept + reject tests for HandoffArtifact.

Runnable two ways:
  * `python -m runtime.schema.tests.test_handoff` (from backend/) — self-check
    with a small assertion harness, exit-code non-zero on any failure
  * `pytest runtime/schema/tests/test_handoff.py`

Deliberately zero external test-runner dependencies so this runs in the
same venv the app already uses.
"""

from __future__ import annotations

import sys
import traceback
from typing import Callable, List, Tuple

from runtime.schema.handoff import (
    HandoffArtifact,
    HandoffArtifacts,
    HandoffContext,
    HandoffValidationError,
    MAX_SUMMARY_TOKENS,
    SCHEMA_VERSION,
)


# --- Accept paths ---------------------------------------------------------- #

def test_accept_minimal() -> None:
    """The smallest legal payload — required fields only."""
    ha = HandoffArtifact.build(
        fromAgent="planner",
        toAgent="writer",
        taskId="task-1",
        context=HandoffContext(summary="Do the thing."),
    )
    assert ha.version == SCHEMA_VERSION
    assert ha.verificationRequired is False
    assert ha.acceptanceCriteria == []


def test_accept_full_payload() -> None:
    """Every optional field populated with typical values."""
    ha = HandoffArtifact.build(
        fromAgent="research",
        toAgent="drafter",
        taskId="task-42",
        context=HandoffContext(
            summary="Researched X, found Y, need Z.",
            keyDecisions=["Chose Y over W because of freshness."],
            openQuestions=["Should we cite Z or wait?"],
            risks=["Source Y may be rate-limited."],
        ),
        artifacts=HandoffArtifacts(
            filePaths=["/tmp/notes.md"],
            toolOutputs=[{"score": 0.83}, 42],
            searchCacheIds=["cache:abc123"],
        ),
        verificationRequired=True,
        acceptanceCriteria=["Every claim has a citation."],
    )
    assert ha.acceptanceCriteria == ["Every claim has a citation."]


def test_accept_summary_at_limit() -> None:
    """Summary right at the token budget should pass, not fail at the edge."""
    # ~500 tokens using the char/4 heuristic → 2000 chars.
    at_limit = "x" * (MAX_SUMMARY_TOKENS * 4)
    HandoffArtifact.build(
        fromAgent="a",
        toAgent="b",
        taskId="t",
        context=HandoffContext(summary=at_limit),
    )


# --- Reject paths ---------------------------------------------------------- #

def test_reject_wrong_version() -> None:
    """Version mismatch surfaces as HandoffValidationError, not silent drift."""
    try:
        HandoffArtifact.build(
            version="1.9",
            fromAgent="a",
            toAgent="b",
            taskId="t",
            context=HandoffContext(summary="hi"),
        )
    except HandoffValidationError as exc:
        assert SCHEMA_VERSION in str(exc)
        return
    raise AssertionError("expected HandoffValidationError for wrong version")


def test_reject_summary_over_budget() -> None:
    """A dump-shaped summary must fail before the receiver ever sees it."""
    too_big = "x" * (MAX_SUMMARY_TOKENS * 4 + 100)
    try:
        HandoffArtifact.build(
            fromAgent="a",
            toAgent="b",
            taskId="t",
            context=HandoffContext.build(summary=too_big),
        )
    except HandoffValidationError as exc:
        assert "summary" in str(exc).lower()
        return
    raise AssertionError("expected HandoffValidationError for oversize summary")


def test_reject_raw_text_in_tool_outputs() -> None:
    """A huge string in toolOutputs is a raw dump — must be cached-by-id."""
    dump = "raw source text " * 200  # > 500-token threshold
    try:
        HandoffArtifact.build(
            fromAgent="a",
            toAgent="b",
            taskId="t",
            context=HandoffContext(summary="short"),
            artifacts=HandoffArtifacts.build(toolOutputs=[dump]),
        )
    except HandoffValidationError as exc:
        assert "searchCacheIds" in str(exc) or "cache" in str(exc).lower()
        return
    raise AssertionError("expected HandoffValidationError for raw text dump")


def test_reject_verification_without_criteria() -> None:
    """verificationRequired=true with empty criteria is nonsensical — reject."""
    try:
        HandoffArtifact.build(
            fromAgent="a",
            toAgent="b",
            taskId="t",
            context=HandoffContext(summary="short"),
            verificationRequired=True,
        )
    except HandoffValidationError as exc:
        assert "acceptanceCriteria" in str(exc) or "criteria" in str(exc).lower()
        return
    raise AssertionError(
        "expected HandoffValidationError for verificationRequired without criteria"
    )


def test_reject_empty_required_string() -> None:
    """Empty fromAgent/toAgent/taskId all fail — not just missing keys."""
    for field in ("fromAgent", "toAgent", "taskId"):
        kwargs = dict(
            fromAgent="a",
            toAgent="b",
            taskId="t",
            context=HandoffContext(summary="short"),
        )
        kwargs[field] = ""
        try:
            HandoffArtifact.build(**kwargs)
        except HandoffValidationError:
            continue
        raise AssertionError(f"expected HandoffValidationError for empty {field}")


# --- Runner ---------------------------------------------------------------- #

TESTS: List[Tuple[str, Callable[[], None]]] = [
    (name, obj)
    for name, obj in list(globals().items())
    if name.startswith("test_") and callable(obj)
]


def main() -> int:
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

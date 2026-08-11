"""Walk the Team Engine state machine through its three required paths.

Runnable two ways:
  * `python -m runtime.engine.tests.test_state_machine` (from backend/) —
    self-check with a small assertion harness, exit-code non-zero on failure
  * `pytest runtime/engine/tests/test_state_machine.py`

Fixtures are stub agent callables (no LLM anywhere): a recording producer,
and queue-driven verifier/red-teamer stubs whose scripted verdicts steer
the machine down each path.
"""

from __future__ import annotations

import sys
import traceback
from typing import Callable, List, Optional, Tuple

# Import through the package re-export so these tests also pin the
# __init__.py contract (schema-pattern: explicit re-export).
from runtime.engine import (
    RED_TEAM_FAILURE_TAGS,
    EngineEvent,
    EventType,
    InvalidArtifactError,
    InvalidVerdictError,
    MaxRetriesExceededError,
    RetryContext,
    TeamEngine,
    TeamState,
    Verdict,
)
from runtime.schema.handoff import (
    HandoffArtifact,
    HandoffContext,
)


# --- Fixtures ---------------------------------------------------------------- #

def make_artifact(task_id: str = "task-1") -> HandoffArtifact:
    """A minimal legal artifact; verification is required so the verifier
    and red-teamer actually have criteria to judge against."""
    return HandoffArtifact.build(
        fromAgent="producer",
        toAgent="verifier",
        taskId=task_id,
        context=HandoffContext(summary="Draft complete."),
        verificationRequired=True,
        acceptanceCriteria=["All claims cited."],
    )


class StubProducer:
    """Returns a fixed artifact and records every (task, retry_context) it
    is called with, so tests can assert tagged retry context arrived."""

    def __init__(self, artifact: HandoffArtifact) -> None:
        self._artifact = artifact
        self.calls: List[Tuple[str, Optional[RetryContext]]] = []

    def __call__(
        self, task: str, retry_context: Optional[RetryContext]
    ) -> HandoffArtifact:
        self.calls.append((task, retry_context))
        return self._artifact


class QueueVerdictAgent:
    """Plays back a scripted queue of Verdicts; raising if the machine calls
    it more times than scripted keeps the tests honest about call counts."""

    def __init__(self, verdicts: List[Verdict]) -> None:
        self._queue = list(verdicts)
        self.seen: List[HandoffArtifact] = []

    def __call__(self, artifact: HandoffArtifact) -> Verdict:
        self.seen.append(artifact)
        if not self._queue:
            raise AssertionError("agent called more times than scripted")
        return self._queue.pop(0)


def run_with_events(
    producer: StubProducer,
    verifier: QueueVerdictAgent,
    redteamer: QueueVerdictAgent,
    max_retries: int = 3,
) -> Tuple[TeamEngine, List[EngineEvent], Optional[HandoffArtifact], Optional[Exception]]:
    """One driver for every path: subscribes a recorder, runs, and returns
    (engine, events, result, error) so each test asserts on what it needs."""
    events: List[EngineEvent] = []
    engine = TeamEngine(subscribers=[events.append])
    result: Optional[HandoffArtifact] = None
    error: Optional[Exception] = None
    try:
        result = engine.run(
            producer, verifier, redteamer, "write the report", max_retries
        )
    except Exception as exc:  # noqa: BLE001 - returned, not swallowed
        error = exc
    return engine, events, result, error


def event_types(events: List[EngineEvent]) -> List[EventType]:
    return [e.type for e in events]


# --- (a) Happy path ------------------------------------------------------------ #

def test_happy_path() -> None:
    """producing → verifying → red_team → done, first try, no retries."""
    artifact = make_artifact()
    producer = StubProducer(artifact)
    verifier = QueueVerdictAgent([Verdict(ok=True)])
    redteamer = QueueVerdictAgent([Verdict(ok=True)])

    engine, events, result, error = run_with_events(producer, verifier, redteamer)

    assert error is None, f"unexpected error: {error}"
    assert result is artifact
    assert engine.state is TeamState.DONE
    # First (and only) producer call must carry no retry context.
    assert producer.calls == [("write the report", None)]
    # The full transition stream, in order, with no retry beats.
    assert event_types(events) == [
        EventType.STATE_ENTER,   # producing
        EventType.STATE_EXIT,
        EventType.STATE_ENTER,   # verifying
        EventType.STATE_EXIT,
        EventType.STATE_ENTER,   # red_team
        EventType.STATE_EXIT,
        EventType.STATE_ENTER,   # done
    ]
    assert EventType.RETRY_SCHEDULED not in event_types(events)
    assert EventType.HARD_FAIL not in event_types(events)


# --- (b) Retry path after red-team failure ------------------------------------- #

def test_retry_after_red_team_failure() -> None:
    """Red team fails once with a §5 tag; producer is looped back WITH the
    tag as retry context; second pass is accepted."""
    artifact = make_artifact()
    producer = StubProducer(artifact)
    verifier = QueueVerdictAgent([Verdict(ok=True), Verdict(ok=True)])
    redteamer = QueueVerdictAgent(
        [
            Verdict(ok=False, tags=["LOGIC_GAP"], reason="step 3 does not follow"),
            Verdict(ok=True),
        ]
    )

    engine, events, result, error = run_with_events(producer, verifier, redteamer)

    assert error is None, f"unexpected error: {error}"
    assert result is artifact
    assert engine.state is TeamState.DONE
    assert len(producer.calls) == 2
    # Tagged retry, not blind retry: second call carries the failure tags.
    _, retry_ctx = producer.calls[1]
    assert retry_ctx is not None
    assert retry_ctx.attempt == 1
    assert retry_ctx.tags == ("LOGIC_GAP",)
    assert "step 3" in retry_ctx.reason
    # Exactly one RETRY_SCHEDULED beat, carrying the same tags.
    retries = [e for e in events if e.type is EventType.RETRY_SCHEDULED]
    assert len(retries) == 1
    assert retries[0].tags == ("LOGIC_GAP",)
    assert retries[0].state is TeamState.RETRY
    # The machine actually visited RETRY between red_team and the re-run.
    entered = [e.state for e in events if e.type is EventType.STATE_ENTER]
    assert entered == [
        TeamState.PRODUCING,
        TeamState.VERIFYING,
        TeamState.RED_TEAM,
        TeamState.RETRY,
        TeamState.PRODUCING,
        TeamState.VERIFYING,
        TeamState.RED_TEAM,
        TeamState.DONE,
    ]


def test_verifier_failure_also_loops_back_with_reason() -> None:
    """A linear verifier failure (criteria miss) retries too, and the
    producer sees the verifier's reason — not just red-team failures."""
    artifact = make_artifact()
    producer = StubProducer(artifact)
    verifier = QueueVerdictAgent(
        [
            Verdict(ok=False, tags=["CRITERIA_MISS"], reason="claim 2 uncited"),
            Verdict(ok=True),
        ]
    )
    redteamer = QueueVerdictAgent([Verdict(ok=True)])

    _, events, result, error = run_with_events(producer, verifier, redteamer)

    assert error is None
    assert result is artifact
    _, retry_ctx = producer.calls[1]
    assert retry_ctx is not None
    assert retry_ctx.tags == ("CRITERIA_MISS",)
    assert "uncited" in retry_ctx.reason
    assert event_types(events).count(EventType.RETRY_SCHEDULED) == 1


# --- (c) Hard-fail path ----------------------------------------------------------- #

def test_hard_fail_after_max_retries() -> None:
    """Red team keeps failing; budget of 2 retries ⇒ 3 producer attempts,
    then HARD_FAIL, terminal FAILED state, typed error with forensics."""
    artifact = make_artifact()
    producer = StubProducer(artifact)
    verifier = QueueVerdictAgent([Verdict(ok=True)] * 3)
    redteamer = QueueVerdictAgent(
        [Verdict(ok=False, tags=["SECURITY_RISK"], reason="leaks secrets")] * 3
    )

    engine, events, result, error = run_with_events(
        producer, verifier, redteamer, max_retries=2
    )

    assert result is None
    assert isinstance(error, MaxRetriesExceededError), f"got {error!r}"
    assert error.attempts == 3
    assert error.last_tags == ("SECURITY_RISK",)
    assert engine.state is TeamState.FAILED
    # Budget arithmetic is exact: 1 initial attempt + 2 retries, no more.
    assert len(producer.calls) == 3
    assert event_types(events).count(EventType.RETRY_SCHEDULED) == 2
    hard_fails = [e for e in events if e.type is EventType.HARD_FAIL]
    assert len(hard_fails) == 1
    assert hard_fails[0].state is TeamState.FAILED
    assert hard_fails[0].attempt == 3
    # Every retry delivered the tags to the producer (no blind retry).
    for _, ctx in producer.calls[1:]:
        assert ctx is not None and ctx.tags == ("SECURITY_RISK",)


def test_max_retries_zero_hard_fails_immediately() -> None:
    """max_retries=0 means the first failure is already a hard fail."""
    producer = StubProducer(make_artifact())
    verifier = QueueVerdictAgent([Verdict(ok=True)])
    redteamer = QueueVerdictAgent(
        [Verdict(ok=False, tags=["HALLUCINATION_CITATION"], reason="fake source")]
    )

    engine, events, result, error = run_with_events(
        producer, verifier, redteamer, max_retries=0
    )

    assert result is None
    assert isinstance(error, MaxRetriesExceededError)
    assert len(producer.calls) == 1
    assert engine.state is TeamState.FAILED
    assert EventType.RETRY_SCHEDULED not in event_types(events)
    assert event_types(events).count(EventType.HARD_FAIL) == 1


# --- Distrust guards -------------------------------------------------------------- #

def test_red_team_failure_with_unknown_tag_is_rejected() -> None:
    """Tags outside the brief §5 vocabulary are schema-level rejection,
    not a silently mistagged retry."""
    producer = StubProducer(make_artifact())
    verifier = QueueVerdictAgent([Verdict(ok=True)])
    redteamer = QueueVerdictAgent(
        [Verdict(ok=False, tags=["VIBES"], reason="feels wrong")]
    )

    _, _, result, error = run_with_events(producer, verifier, redteamer)

    assert result is None
    assert isinstance(error, InvalidVerdictError), f"got {error!r}"
    assert "VIBES" in str(error)


def test_red_team_failure_without_tags_is_rejected() -> None:
    """A tagless red-team failure would be a blind retry — forbidden by §5."""
    producer = StubProducer(make_artifact())
    verifier = QueueVerdictAgent([Verdict(ok=True)])
    redteamer = QueueVerdictAgent([Verdict(ok=False, reason="nope")])

    _, _, result, error = run_with_events(producer, verifier, redteamer)

    assert result is None
    assert isinstance(error, InvalidVerdictError)


def test_failure_verdict_without_reason_is_rejected() -> None:
    """A failure with no reason gives the producer nothing to fix."""
    producer = StubProducer(make_artifact())
    verifier = QueueVerdictAgent([Verdict(ok=False, tags=["CRITERIA_MISS"])])
    redteamer = QueueVerdictAgent([Verdict(ok=True)])

    _, _, result, error = run_with_events(producer, verifier, redteamer)

    assert result is None
    assert isinstance(error, InvalidVerdictError)


def test_producer_must_return_handoff_artifact() -> None:
    """Agents must not trust each other: a non-artifact payload is rejected
    before the verifier ever sees it."""
    bad_producer = lambda task, ctx: "not an artifact"  # noqa: E731
    events: List[EngineEvent] = []
    engine = TeamEngine(subscribers=[events.append])
    try:
        engine.run(
            bad_producer,  # type: ignore[arg-type]
            QueueVerdictAgent([Verdict(ok=True)]),
            QueueVerdictAgent([Verdict(ok=True)]),
            "task",
        )
    except InvalidArtifactError:
        return
    raise AssertionError("expected InvalidArtifactError for non-artifact payload")


def test_event_stream_is_strictly_paired() -> None:
    """Every EXIT is immediately followed by an ENTER, the stream opens with
    ENTER PRODUCING, and the terminal state is entered but never exited —
    so orchestrator dashboards can rely on the pairing invariant."""
    producer = StubProducer(make_artifact())
    # Two verifier verdicts: the red-team failure below forces a retry, and
    # the retry round re-enters VERIFYING before red_team passes.
    verifier = QueueVerdictAgent([Verdict(ok=True), Verdict(ok=True)])
    redteamer = QueueVerdictAgent(
        [
            Verdict(ok=False, tags=["LOGIC_GAP"], reason="gap"),
            Verdict(ok=True),
        ]
    )
    _, events, _, error = run_with_events(producer, verifier, redteamer)
    assert error is None

    assert events[0].type is EventType.STATE_ENTER
    assert events[0].state is TeamState.PRODUCING
    enters = exits = 0
    for i, ev in enumerate(events):
        if ev.type is EventType.STATE_EXIT:
            exits += 1
            assert events[i + 1].type is EventType.STATE_ENTER
        elif ev.type is EventType.STATE_ENTER:
            enters += 1
    assert enters == exits + 1  # DONE is entered, never exited
    # Decision beats only ever fire inside their resolved state.
    for ev in events:
        if ev.type is EventType.RETRY_SCHEDULED:
            assert ev.state is TeamState.RETRY
        if ev.type is EventType.HARD_FAIL:
            assert ev.state is TeamState.FAILED


def test_red_team_failure_tags_constant_matches_brief() -> None:
    """Pin the §5 vocabulary so a drive-by edit shows up as a test failure."""
    assert RED_TEAM_FAILURE_TAGS == frozenset(
        {"HALLUCINATION_CITATION", "LOGIC_GAP", "SECURITY_RISK"}
    )


# --- Runner ---------------------------------------------------------------------- #

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

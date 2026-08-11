"""Team Engine state machine — deterministic orchestration, zero LLM calls.

Per docs/TEAM_ENGINE_BRIEF.md §1 Non-Negotiable #1: the engine is the
product. This machine decides who runs when; agents are replaceable,
stateless containers injected as callables. The machine owns three things
and nothing more:

  1. State transitions:  producing → verifying → red_team → retry → done
  2. Payload validation: every agent output is checked before it is acted on
     (Non-Negotiable #2 — agents must not trust each other).
  3. The retry budget:   red-team failure loops back to the producer *with
     the failure tags* (brief §5 — never a blind retry), and exhausting the
     budget is a typed hard-fail, never a silent swallow.

Orchestrator layers subscribe to the emitted EngineEvent stream for
observability; the machine itself is pure Python with no clock, no
randomness, and no I/O beyond logging.

Event-stream invariant: the stream opens with STATE_ENTER for the initial
state (there is no prior state to exit), every later transition is a
strictly paired STATE_EXIT immediately followed by STATE_ENTER, and the
terminal state (DONE or FAILED) is entered but never exited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Protocol, Tuple

from runtime.schema.handoff import HandoffArtifact

logger = logging.getLogger(__name__)


# --- Constants (single source of truth — mirror brief §5) ----------------- #

RED_TEAM_FAILURE_TAGS = frozenset(
    {"HALLUCINATION_CITATION", "LOGIC_GAP", "SECURITY_RISK"}
)
"""The only tags a Red Team verdict may carry (brief §5). Anything else is
rejected as an untrusted verdict, because a retry loop keyed on free-text
tags is an unbounded state space."""


# --- States & events ------------------------------------------------------- #

class TeamState(Enum):
    """The five states from brief §2 plus a terminal FAILED for hard-fail."""

    PRODUCING = "producing"
    VERIFYING = "verifying"
    RED_TEAM = "red_team"
    RETRY = "retry"
    DONE = "done"
    FAILED = "failed"


class EventType(Enum):
    """The named events the orchestrator layer subscribes to."""

    STATE_ENTER = "STATE_ENTER"
    STATE_EXIT = "STATE_EXIT"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    HARD_FAIL = "HARD_FAIL"


@dataclass(frozen=True)
class EngineEvent:
    """One observable beat of the machine. Frozen so subscribers cannot
    mutate history that later subscribers will read."""

    type: EventType
    state: TeamState
    attempt: int  # 1-based producer attempt this event relates to
    detail: str = ""
    tags: Tuple[str, ...] = ()


# --- Agent-facing contracts ------------------------------------------------ #

@dataclass
class Verdict:
    """What a Verifier or RedTeamer returns. `tags` is list-typed per spec;
    the machine copies it into an immutable tuple before using it."""

    ok: bool
    tags: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class RetryContext:
    """The tagged failure handed back to the producer on a retry. Frozen:
    retry context is evidence, and evidence must not be editable mid-run."""

    attempt: int  # 1 for the first retry, 2 for the second, ...
    tags: Tuple[str, ...]
    reason: str


class Producer(Protocol):
    """Builds the work-product. Receives the raw task plus None on the first
    attempt, or a RetryContext carrying the failure tags on retries."""

    def __call__(
        self, task: str, retry_context: Optional[RetryContext]
    ) -> HandoffArtifact: ...


class Verifier(Protocol):
    """Linear pass/fail against the artifact's acceptance criteria (§5)."""

    def __call__(self, artifact: HandoffArtifact) -> Verdict: ...


class RedTeamer(Protocol):
    """Adversarial pass: counterexamples, stress tests, edge cases (§5).
    A failure verdict must carry tags from RED_TEAM_FAILURE_TAGS."""

    def __call__(self, artifact: HandoffArtifact) -> Verdict: ...


# --- Typed errors ----------------------------------------------------------- #

class TeamEngineError(RuntimeError):
    """Base class so orchestrators can catch any engine-raised failure with
    one except clause without swallowing unrelated RuntimeErrors."""


class InvalidArtifactError(TeamEngineError):
    """Producer returned something that is not a HandoffArtifact."""


class InvalidVerdictError(TeamEngineError):
    """Verifier/RedTeamer returned a malformed verdict — wrong type, empty
    failure reason (blind retry), or tags outside RED_TEAM_FAILURE_TAGS."""


class AgentExecutionError(TeamEngineError):
    """An injected agent callable raised. Chained via `raise ... from` so the
    agent's own traceback is preserved under a typed wrapper."""


class MaxRetriesExceededError(TeamEngineError):
    """The retry budget was exhausted — the run hard-fails (brief §5)."""

    def __init__(
        self,
        *,
        task: str,
        attempts: int,
        last_reason: str,
        last_tags: Tuple[str, ...],
    ) -> None:
        # Carry the failure forensics on the exception so the orchestrator
        # can route to human review without re-running anything.
        self.task = task
        self.attempts = attempts
        self.last_reason = last_reason
        self.last_tags = last_tags
        super().__init__(
            f"hard fail after {attempts} attempt(s); last verdict: "
            f"{last_reason!r} (tags: {', '.join(last_tags) or 'none'})"
        )


# --- The machine ------------------------------------------------------------ #

class TeamEngine:
    """Deterministic state machine driving one producer/verifier/red-team
    triple through the brief §2 loop. Reusable: each `run()` resets state."""

    def __init__(
        self, subscribers: Optional[List[Callable[[EngineEvent], None]]] = None
    ) -> None:
        # Subscribers are copied so a caller mutating its own list mid-run
        # cannot change who observes the event stream.
        self._subscribers: List[Callable[[EngineEvent], None]] = list(
            subscribers or []
        )
        self._reset()

    # -- public API -------------------------------------------------------- #

    @property
    def state(self) -> TeamState:
        return self._state

    def subscribe(self, listener: Callable[[EngineEvent], None]) -> None:
        # Late subscription is allowed so orchestrators can attach log
        # shippers after construction but before `run()`.
        self._subscribers.append(listener)

    def run(
        self,
        producer: Producer,
        verifier: Verifier,
        redteamer: RedTeamer,
        initial_task: str,
        max_retries: int = 3,
    ) -> HandoffArtifact:
        """Drive the loop to DONE and return the accepted artifact.

        Raises MaxRetriesExceededError on hard-fail, InvalidArtifactError /
        InvalidVerdictError on untrusted agent output, AgentExecutionError if
        an agent callable itself raises.
        """
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        # Reset first so a reused engine instance can never leak retry
        # budget or artifacts from a previous run into this one.
        self._reset()
        self._max_retries = max_retries

        self._enter_initial(TeamState.PRODUCING)
        while True:
            if self._state is TeamState.PRODUCING:
                self._artifact = self._invoke_producer(producer, initial_task)
                self._transition(TeamState.VERIFYING)
            elif self._state is TeamState.VERIFYING:
                verdict = self._invoke_verifier(verifier)
                if verdict.ok:
                    self._transition(TeamState.RED_TEAM)
                else:
                    self._handle_failure(verdict)
            elif self._state is TeamState.RED_TEAM:
                verdict = self._invoke_redteamer(redteamer)
                if verdict.ok:
                    self._transition(TeamState.DONE)
                    logger.info("task accepted after %d attempt(s)", self._attempt)
                    return self._artifact  # type: ignore[return-value]
                self._handle_failure(verdict)
            else:
                # RETRY is handled inline by _handle_failure and DONE/FAILED
                # exit the loop, so reaching here means a bug in this file.
                raise TeamEngineError(f"unreachable state: {self._state}")

    # -- internals ---------------------------------------------------------- #

    def _reset(self) -> None:
        # All per-run state lives behind one method so `run()` can guarantee
        # a clean slate with a single call.
        self._state = TeamState.PRODUCING
        self._retries_used = 0
        self._max_retries = 0
        self._retry_context: Optional[RetryContext] = None
        self._artifact: Optional[HandoffArtifact] = None

    @property
    def _attempt(self) -> int:
        # Attempt numbering is derived from the retry counter (never stored
        # separately) so the two can never drift out of sync.
        return self._retries_used + 1

    def _emit(
        self, event_type: EventType, detail: str = "", tags: Tuple[str, ...] = ()
    ) -> None:
        # Events are built in one place so every beat on the bus carries a
        # consistent attempt number and state.
        event = EngineEvent(
            type=event_type,
            state=self._state,
            attempt=self._attempt,
            detail=detail,
            tags=tags,
        )
        for listener in self._subscribers:
            listener(event)

    def _enter_initial(self, state: TeamState) -> None:
        # The opening beat of a run has no prior state to exit, so it is
        # ENTER-only. Every later beat goes through _transition, which keeps
        # the EXIT/ENTER pairing invariant for the rest of the stream.
        self._state = state
        self._emit(EventType.STATE_ENTER)
        logger.debug("enter %s", state.value)

    def _transition(self, new_state: TeamState) -> None:
        # Single funnel for every post-initial transition: subscribers always
        # see a strictly paired EXIT/ENTER stream and no state changes
        # off-bus.
        self._emit(EventType.STATE_EXIT)
        logger.debug("exit %s", self._state.value)
        self._state = new_state
        self._emit(EventType.STATE_ENTER)
        logger.debug("enter %s", new_state.value)

    def _invoke_producer(
        self, producer: Producer, task: str
    ) -> HandoffArtifact:
        # Agents are untrusted containers (Non-Negotiable #2): wrap crashes
        # in a typed error and schema-check the payload before acting on it.
        try:
            artifact = producer(task, self._retry_context)
        except TeamEngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raise typed, keep chain
            raise AgentExecutionError(f"producer raised: {exc}") from exc
        if not isinstance(artifact, HandoffArtifact):
            raise InvalidArtifactError(
                f"producer returned {type(artifact).__name__}, expected "
                "HandoffArtifact — schema-level rejection, not silent failure"
            )
        return artifact

    def _invoke_verifier(self, verifier: Verifier) -> Verdict:
        # Same distrust posture as the producer; additionally a failing
        # verdict must carry a reason because blind retries are forbidden.
        verdict = self._call_verdict_agent(verifier, "verifier")
        self._require_reason(verdict, "verifier")
        return verdict

    def _invoke_redteamer(self, redteamer: RedTeamer) -> Verdict:
        # Red-team failures drive the retry loop, so their tags are
        # restricted to the brief §5 vocabulary — anything else is rejected.
        verdict = self._call_verdict_agent(redteamer, "redteamer")
        self._require_reason(verdict, "redteamer")
        if not verdict.ok:
            unknown = set(verdict.tags) - RED_TEAM_FAILURE_TAGS
            if unknown:
                raise InvalidVerdictError(
                    f"redteamer returned unknown failure tags "
                    f"{sorted(unknown)}; allowed: "
                    f"{sorted(RED_TEAM_FAILURE_TAGS)}"
                )
            if not verdict.tags:
                raise InvalidVerdictError(
                    "redteamer failure carried no tags — brief §5 requires "
                    "tagged retry, not blind retry"
                )
        return verdict

    def _call_verdict_agent(self, agent: Callable[[HandoffArtifact], Verdict], role: str) -> Verdict:
        # One call site for both verdict agents so the type check and the
        # typed-crash wrapper stay identical across roles.
        try:
            verdict = agent(self._artifact)  # type: ignore[arg-type]
        except TeamEngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raise typed, keep chain
            raise AgentExecutionError(f"{role} raised: {exc}") from exc
        if not isinstance(verdict, Verdict):
            raise InvalidVerdictError(
                f"{role} returned {type(verdict).__name__}, expected Verdict"
            )
        return verdict

    @staticmethod
    def _require_reason(verdict: Verdict, role: str) -> None:
        # A failure without a reason would send the producer back with no
        # signal — the brief calls that a chatroom, not a runtime.
        if not verdict.ok and not verdict.reason.strip():
            raise InvalidVerdictError(
                f"{role} failed the artifact but gave no reason"
            )

    def _handle_failure(self, verdict: Verdict) -> None:
        # The only place the retry budget lives: either schedule a tagged
        # retry (brief §5) or hard-fail with forensics when it is exhausted.
        if self._retries_used >= self._max_retries:
            self._transition(TeamState.FAILED)
            self._emit(
                EventType.HARD_FAIL,
                detail=verdict.reason,
                tags=tuple(verdict.tags),
            )
            logger.error(
                "hard fail: %s (tags=%s)", verdict.reason, verdict.tags
            )
            raise MaxRetriesExceededError(
                task=self._artifact.taskId if self._artifact else "<none>",
                attempts=self._attempt,
                last_reason=verdict.reason,
                last_tags=tuple(verdict.tags),
            )
        self._retries_used += 1
        self._retry_context = RetryContext(
            attempt=self._retries_used,
            tags=tuple(verdict.tags),
            reason=verdict.reason,
        )
        self._transition(TeamState.RETRY)
        self._emit(
            EventType.RETRY_SCHEDULED,
            detail=verdict.reason,
            tags=self._retry_context.tags,
        )
        logger.info(
            "retry %d/%d scheduled (tags=%s)",
            self._retries_used,
            self._max_retries,
            verdict.tags,
        )
        self._transition(TeamState.PRODUCING)


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

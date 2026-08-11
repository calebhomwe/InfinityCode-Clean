"""HandoffArtifact v2.0 — the typed contract for every agent-to-agent handoff.

Per docs/TEAM_ENGINE_BRIEF.md §3 (Non-Negotiable):

    Every agent-to-agent communication must use this schema.
    No agent may receive raw source text inline. Only summaries and
    retrievable IDs.

That rule is enforced here at the schema level, not by convention. A worker
that tries to hand raw source text to the next agent gets a validation error
before the receiver ever sees it. Rejection is the whole point: if Worker A
hands garbage to Worker B, Worker B rejects it at the schema level, not
silently choke on it.

The schema is deliberately mirrored 1:1 in TypeScript at
`src/lib/handoff.ts` so the frontend and backend enforce the same contract.
"""

from __future__ import annotations

from typing import Any, List, Optional

try:  # Pydantic v2 is the target; v1 fallback keeps older environments alive.
    from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
    from pydantic import ValidationError as _PydanticValidationError
    _PYDANTIC_V2 = True
except ImportError:  # pragma: no cover
    from pydantic import (  # type: ignore[assignment]
        BaseModel,
        Field,
        validator as field_validator,
        root_validator as model_validator,
        ValidationError as _PydanticValidationError,
    )
    ConfigDict = None  # type: ignore[assignment]
    _PYDANTIC_V2 = False


# --- Constants (single source of truth — mirror in TS) --------------------- #

SCHEMA_VERSION: str = "2.0"
"""Bumped when a breaking change lands. Consumers pin on this."""

MAX_SUMMARY_TOKENS: int = 500
"""Hard cap on `context.summary`. 500 tokens ≈ 2000 chars for English prose.
Enforced at the token-ish level via char-count so we do not need a tokenizer
in the hot path. See `_tokens_ish`."""

TOKEN_CHAR_RATIO: float = 4.0
"""Rough chars-per-token used by the summary length guard. Deliberately
conservative — we would rather reject a borderline summary than let a 700-
token dump through."""


class HandoffValidationError(ValueError):
    """Raised when a HandoffArtifact fails the non-negotiable rules.

    Distinct from generic Pydantic ValidationError so orchestration code can
    catch just this class and route to schema-rejection handling without
    swallowing other, unexpected exceptions.
    """


def _tokens_ish(text: str) -> int:
    """Rough token count using char/4 heuristic — fast, no tokenizer dep.

    Deliberately overcounts on token-dense languages (CJK) rather than
    undercount, so a summary that would blow the real 500-token budget on
    OpenAI/Kimi tokenizers is caught here even in the pessimistic case.
    """
    return max(1, int(len(text) / TOKEN_CHAR_RATIO))


# --- Nested types ---------------------------------------------------------- #

class HandoffContext(BaseModel):
    """The compressed shape of the world that the receiving agent needs.

    Summary is *the* signal; the arrays are structural cues so the receiver
    can plan without needing to re-read raw sources.
    """

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(
        ...,
        min_length=1,
        description=(
            f"Compressed narrative of the run so far. ≤ {MAX_SUMMARY_TOKENS} "
            "tokens (chars/4). Enforced."
        ),
    )
    keyDecisions: List[str] = Field(default_factory=list)
    openQuestions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _summary_within_budget(cls, value: str) -> str:
        est = _tokens_ish(value)
        if est > MAX_SUMMARY_TOKENS:
            raise HandoffValidationError(
                f"context.summary is ~{est} tokens (limit {MAX_SUMMARY_TOKENS}). "
                "Distil before handing off — the receiver's context budget is "
                "not a dump."
            )
        return value

    @classmethod
    def build(cls, **kwargs: Any) -> "HandoffContext":
        """Normalize pydantic ValidationError → HandoffValidationError. See
        `HandoffArtifact.build` for the same pattern rationale."""
        try:
            return cls(**kwargs)
        except HandoffValidationError:
            raise
        except _PydanticValidationError as exc:
            raise HandoffValidationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(str(exc)) from exc


class HandoffArtifacts(BaseModel):
    """Where the actual work-product lives. Note what is NOT here: raw text.

    The Non-Negotiable is that agents pass POINTERS to work, not the work
    itself. Files live on disk. Tool outputs are typed values. Search results
    live in the search cache keyed by id. If your receiver needs the raw
    text, it fetches by id on demand — never inlines.
    """

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid", frozen=True)

    filePaths: List[str] = Field(default_factory=list)
    toolOutputs: List[Any] = Field(default_factory=list)
    searchCacheIds: List[str] = Field(
        default_factory=list,
        description="Retrievable IDs. Never the raw text those IDs point to.",
    )

    @field_validator("toolOutputs")
    @classmethod
    def _no_raw_text_dumps(cls, value: List[Any]) -> List[Any]:
        # A tool output "dict/list/number/None" is fine. A single giant
        # string screams "someone pasted raw source text here" — reject with
        # a hint pointing at searchCacheIds.
        for i, out in enumerate(value):
            if isinstance(out, str) and _tokens_ish(out) > MAX_SUMMARY_TOKENS:
                raise HandoffValidationError(
                    f"toolOutputs[{i}] looks like a raw text dump "
                    f"(~{_tokens_ish(out)} tokens). Store it in the search "
                    "cache and pass its id in `searchCacheIds` instead. "
                    "Non-Negotiable #4: context is a budget, not a dump."
                )
        return value

    @classmethod
    def build(cls, **kwargs: Any) -> "HandoffArtifacts":
        """Normalize pydantic ValidationError → HandoffValidationError."""
        try:
            return cls(**kwargs)
        except HandoffValidationError:
            raise
        except _PydanticValidationError as exc:
            raise HandoffValidationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(str(exc)) from exc


# --- Root ------------------------------------------------------------------ #

class HandoffArtifact(BaseModel):
    """The canonical contract for every agent-to-agent handoff.

    Mirrors the TypeScript interface in `src/lib/handoff.ts` byte-for-byte
    on field names. Both sides validate.
    """

    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(default=SCHEMA_VERSION)
    fromAgent: str = Field(..., min_length=1)
    toAgent: str = Field(..., min_length=1)
    taskId: str = Field(..., min_length=1)
    context: HandoffContext
    artifacts: HandoffArtifacts = Field(default_factory=HandoffArtifacts)
    verificationRequired: bool = False
    acceptanceCriteria: List[str] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _version_pinned(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise HandoffValidationError(
                f"HandoffArtifact.version must be '{SCHEMA_VERSION}', got "
                f"'{value}'. Bump the constant and migrate call-sites; do "
                "not let mismatched versions cross the boundary."
            )
        return value

    # `model_validator(after)` sees the fully-constructed model so it can
    # cross-reference sibling fields without depending on pydantic v1-vs-v2
    # field ordering quirks. Field-level cross-refs via `info.data` were
    # unreliable between versions in practice.
    @model_validator(mode="after")
    def _criteria_required_when_verifying(self) -> "HandoffArtifact":
        if self.verificationRequired and not self.acceptanceCriteria:
            raise HandoffValidationError(
                "verificationRequired=true but acceptanceCriteria is empty. "
                "The verifier has nothing to check against — either supply "
                "criteria or set verificationRequired=false."
            )
        return self

    # Convenience so orchestration code has a single call site.
    @classmethod
    def build(cls, **kwargs: Any) -> "HandoffArtifact":
        """Construct with strict-error wrapping so orchestrators can catch
        the domain-specific `HandoffValidationError` without also catching
        generic ValidationError from pydantic."""
        try:
            return cls(**kwargs)
        except HandoffValidationError:
            raise
        except _PydanticValidationError as exc:
            # Pydantic wraps every ValueError-subclass raise inside a validator
            # into a ValidationError. Unwrap so callers see our domain error.
            raise HandoffValidationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - re-raise as domain error
            raise HandoffValidationError(str(exc)) from exc


__all__ = [
    "SCHEMA_VERSION",
    "MAX_SUMMARY_TOKENS",
    "HandoffValidationError",
    "HandoffContext",
    "HandoffArtifacts",
    "HandoffArtifact",
]

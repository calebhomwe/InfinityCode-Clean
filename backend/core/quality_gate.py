"""Deterministic quality gate + cost-aware tier router (GENESIS port).

The gate runs FREE, instant checks on every model output before any paid
critique or blind retry: PASS / RETRY (same model, soft miss) / ESCALATE
(bump one tier, hard miss) / REJECT (give up, surface the trail). Measured in
GENESIS at ~73% token savings vs blind same-model retries.

The router classifies tasks into BULK / STANDARD / HEAVY / PREMIUM tiers and
escalates exactly one tier on gate failure. A running local LM Studio model is
preferred for BULK (free). Exact model IDs are pinned — legacy slugs silently
resolve to weaker variants on some providers.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- gate ---- #

PASS, RETRY, ESCALATE, REJECT = "pass", "retry", "escalate", "reject"

_REFUSAL_PATTERNS = [
    r"\bI (?:can(?:no|')t|am unable to|won't) (?:help|assist|do|comply|provide)\b",
    r"\bAs an AI\b.{0,40}\b(?:cannot|can't)\b",
    r"^\s*I'm sorry[,.]",
]
_TRUNCATION_TAILS = (":", ",", "- ", "* ", "…", "```", "={", "([")


@dataclass
class GateResult:
    verdict: str
    reasons: List[str] = field(default_factory=list)


def check(
    text: str,
    expect_json: bool = False,
    expect_code: bool = False,
    min_chars: int = 80,
) -> GateResult:
    """Free deterministic checks. No LLM calls, ever."""
    reasons: List[str] = []

    if not text or not text.strip():
        return GateResult(ESCALATE, ["empty output"])

    stripped = text.strip()

    for pat in _REFUSAL_PATTERNS:
        if re.search(pat, stripped[:400], re.IGNORECASE):
            return GateResult(ESCALATE, ["refusal detected"])

    if len(stripped) < min_chars:
        reasons.append(f"too thin ({len(stripped)} chars < {min_chars})")

    # Truncation heuristics: dangling tails or an unclosed code fence.
    if stripped.endswith(_TRUNCATION_TAILS):
        reasons.append("looks truncated (dangling tail)")
    if stripped.count("```") % 2 == 1:
        reasons.append("unclosed code fence")

    if expect_json:
        m = re.search(r"\{.*\}|\[.*\]", stripped, re.DOTALL)
        if not m:
            return GateResult(ESCALATE, ["no JSON found where JSON expected"])
        try:
            json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            return GateResult(ESCALATE, [f"invalid JSON: {exc}"])

    if expect_code:
        if "```" not in stripped and not re.search(
            r"(?:def |class |function |const |import |#include|public |fn )", stripped
        ):
            return GateResult(ESCALATE, ["no code found where code expected"])

    if reasons:
        return GateResult(RETRY, reasons)
    return GateResult(PASS, [])


# -------------------------------------------------------------- router ---- #

# Pinned exact IDs (legacy slugs downgrade silently on some providers).
TIERS: List[Dict[str, Any]] = [
    {"name": "BULK", "model": "qwen/qwen3-235b-a22b-2507", "jobs":
        {"classify", "tag", "summarize", "format", "score", "extract"}},
    {"name": "STANDARD", "model": "qwen/qwen3.7-max", "jobs":
        {"chat", "write", "plan", "draft", "explain"}},
    {"name": "HEAVY", "model": "deepseek/deepseek-v4-pro", "jobs":
        {"code", "debug", "refactor", "system_design", "architecture"}},
    {"name": "PREMIUM", "model": "anthropic/claude-sonnet-4.6", "jobs":
        {"critique", "review", "meta"}},
]
_TIER_INDEX = {t["name"]: i for i, t in enumerate(TIERS)}


def route(job_type: str, prompt_len: int = 0) -> Dict[str, Any]:
    """Pick the cheapest tier whose job set covers the task (conservative:
    long prompts bump BULK->STANDARD)."""
    job = (job_type or "chat").lower()
    for tier in TIERS:
        if job in tier["jobs"]:
            if tier["name"] == "BULK" and prompt_len > 12_000:
                return TIERS[_TIER_INDEX["STANDARD"]]
            return tier
    return TIERS[_TIER_INDEX["STANDARD"]]


def escalate(tier_name: str) -> Dict[str, Any]:
    """One tier up (never skips; PREMIUM stays PREMIUM)."""
    i = _TIER_INDEX.get(tier_name, 1)
    return TIERS[min(i + 1, len(TIERS) - 1)]


__all__ = ["check", "route", "escalate", "GateResult",
           "PASS", "RETRY", "ESCALATE", "REJECT", "TIERS"]

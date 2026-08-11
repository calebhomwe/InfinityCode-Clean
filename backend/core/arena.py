"""VariantArena — same-prompt race between coding-agent variants.

Given one user prompt and N variant personas (DaCoder, X-Coder, ...),
fan out to all variants in parallel, meter each reply, optionally hand
the error-free replies to a judge, and return one structured result.

Pure logic only: no FastAPI, no I/O, everything dependency-injected
(chat_fn / judge_fn) so the HTTP layer and the frontend can be built
against this contract without caring which providers sit behind it.

Failure-proof by design: one variant exploding never kills the race,
a dead or lying judge never crashes the run, and run() itself never
raises once validation has passed.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

# Error strings are capped so a chatty exception can't bloat the payload.
_ERROR_CAP = 300

_DEFAULT_PERSONA = (
    "You are {name}, an elite autonomous coding agent variant competing "
    "in a same-prompt race. Answer the user's coding request directly, "
    "completely and with production-quality code. No filler, no caveats."
)


class VariantArena:
    """Runs one prompt against many variants in parallel and crowns a winner.

    chat_fn(variant, messages) -> {"text": str, "cost_usd": float}
        messages = [{"role":"system",...}, {"role":"user",...}]
    judge_fn(prompt, entries) -> {"winner": variant_id, "reason": str}
        Optional; only ever sees error-free entries.
    """

    def __init__(self, chat_fn: Callable[[Dict, List[Dict]], Dict],
                 judge_fn: Optional[Callable[[str, List[Dict]], Dict]] = None,
                 max_workers: int = 4) -> None:
        self.chat_fn = chat_fn
        self.judge_fn = judge_fn
        self.max_workers = max(1, int(max_workers))

    # --- helpers --------------------------------------------------------- #

    @staticmethod
    def _messages_for(variant: Dict[str, Any], prompt: str) -> List[Dict[str, str]]:
        """System message carries the variant persona (or a sane default),
        user message carries the race prompt — exactly two messages."""
        persona = str(variant.get("prompt") or "").strip()
        if not persona:
            persona = _DEFAULT_PERSONA.format(name=variant.get("name", "variant"))
        return [{"role": "system", "content": persona},
                {"role": "user", "content": prompt}]

    def _race_one(self, variant: Dict[str, Any], prompt: str) -> Dict[str, Any]:
        """Run a single variant. Never raises: failures are recorded."""
        entry: Dict[str, Any] = {
            "variant_id": str(variant.get("id", "")),
            "name": str(variant.get("name", "")),
            "emoji": str(variant.get("emoji", "")),
            "response": None,
            "error": None,
            "cost_usd": 0.0,
            "duration_ms": 0,
        }
        t0 = time.time()
        try:
            reply = self.chat_fn(variant, self._messages_for(variant, prompt))
            if not isinstance(reply, dict):
                reply = {"text": str(reply), "cost_usd": 0.0}
            entry["response"] = str(reply.get("text", ""))
            try:
                entry["cost_usd"] = float(reply.get("cost_usd", 0.0))
            except (TypeError, ValueError):
                entry["cost_usd"] = 0.0
        except Exception as exc:  # noqa: BLE001 - one variant dying is local
            entry["error"] = str(exc)[:_ERROR_CAP]
        entry["duration_ms"] = int((time.time() - t0) * 1000)
        return entry

    def _judge(self, prompt: str, entries: List[Dict[str, Any]]
               ) -> Dict[str, Optional[str]]:
        """Crown a winner from the error-free entries. Never raises."""
        clean = [e for e in entries if e.get("error") is None]
        if not clean:
            return {"winner": None, "reason": "no successful entries to judge"}
        if self.judge_fn is None:
            return {"winner": None, "reason": "no judge configured"}
        try:
            verdict = self.judge_fn(prompt, clean)
        except Exception as exc:  # noqa: BLE001 - a dead judge never crashes
            return {"winner": None,
                    "reason": f"judge failed: {str(exc)[:_ERROR_CAP]}"}
        if not isinstance(verdict, dict):
            return {"winner": None, "reason": "judge returned an invalid verdict"}
        winner = verdict.get("winner")
        valid_ids = {e["variant_id"] for e in clean}
        if winner not in valid_ids:
            # A judge that names a non-participant (or a crashed variant)
            # is not to be trusted: the pick is dropped, not remapped.
            return {"winner": None, "reason": "judge picked an invalid entry"}
        return {"winner": winner, "reason": str(verdict.get("reason", ""))}

    # --- the race --------------------------------------------------------- #

    def run(self, prompt: str, variants: List[Dict[str, Any]],
            max_cost_usd: float = 0.0) -> Dict[str, Any]:
        """Race all variants on one prompt. Validates inputs, then never
        raises: every provider/judge failure is captured in the result."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("arena prompt must be a non-blank string")
        if not isinstance(variants, (list, tuple)) or len(variants) < 2:
            raise ValueError("an arena needs at least 2 variants to race")

        start = time.time()
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            # map() preserves input order, so entries come back in the same
            # order the variants were supplied — regardless of finish order.
            entries = list(pool.map(
                lambda v: self._race_one(v, prompt), variants))

        total_cost_usd = float(sum(e["cost_usd"] for e in entries))
        # Metering happens after replies land: nothing is pre-blocked, the
        # guardrail only flags that the race blew through the budget.
        budget_exceeded = max_cost_usd > 0 and total_cost_usd > max_cost_usd

        verdict = self._judge(prompt, entries)
        return {
            "prompt": prompt,
            "entries": entries,
            "winner": verdict["winner"],
            "reason": verdict["reason"],
            "total_cost_usd": total_cost_usd,
            "budget_exceeded": budget_exceeded,
            "duration_ms": int((time.time() - start) * 1000),
        }


__all__ = ["VariantArena"]

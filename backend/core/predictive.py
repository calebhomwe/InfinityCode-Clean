"""Predictive cost routing for Infinity Code.

A cheap "worker" model acts as a gate in front of the expensive council
members: it predicts whether a goal needs full architectural planning or is
plain code execution, and the router picks the role accordingly. The same
philosophy powers ``output_looks_plausible`` — a zero-cost pre-filter that lets
callers skip an expensive Critic pass on obviously-broken output.

The gate is deliberately conservative: any error, malformed reply, or ambiguity
falls back to the cheap "simple" verdict (and a cost of 0.0), so a flaky gate
never silently upgrades work to a pricier model.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from backend.core.router import ModelRouter, ModelSpec
    from backend.tools.openrouter_client import OpenRouterClient, OpenRouterError
except ImportError:  # running with backend/ as the working directory
    from core.router import ModelRouter, ModelSpec  # type: ignore[no-redef]
    from tools.openrouter_client import (  # type: ignore[no-redef]
        OpenRouterClient,
        OpenRouterError,
    )

logger = logging.getLogger(__name__)

GATE_ROLE: str = "worker"
GATE_MAX_TOKENS: int = 8
MIN_PLAUSIBLE_IMAGE_BYTES: int = 1024

_GATE_PROMPT: str = (
    "Predict whether this task needs architectural planning or is simple code "
    "execution. Reply with ONLY one word: complex or simple.\n\nTask: "
)

# Precompiled word-boundary matchers for robust parsing of a noisy reply.
_COMPLEX_RE = re.compile(r"\bcomplex\b", re.IGNORECASE)
_SIMPLE_RE = re.compile(r"\bsimple\b", re.IGNORECASE)


class PredictiveRouter:
    """Gate expensive routing decisions behind a cheap complexity prediction."""

    def __init__(self, client: OpenRouterClient, router: ModelRouter) -> None:
        self.client: OpenRouterClient = client
        self.router: ModelRouter = router

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_verdict(text: str) -> str:
        """Extract 'complex' or 'simple' from a model reply.

        Biased toward the cheap/safe default: only an unambiguous "complex"
        (present, with no competing "simple") upgrades the verdict. Anything
        else — both words, neither word, or empty — returns "simple".
        """
        if not text:
            return "simple"
        has_complex: bool = _COMPLEX_RE.search(text) is not None
        has_simple: bool = _SIMPLE_RE.search(text) is not None
        if has_complex and not has_simple:
            return "complex"
        return "simple"

    def _run_gate(self, goal: str) -> Tuple[str, float]:
        """Run the cheap gate once; return (verdict, cost_aud).

        Never raises. On any error, empty goal, or ambiguous reply it returns
        the safe default ("simple", 0.0).
        """
        if not goal or not goal.strip():
            return "simple", 0.0

        try:
            spec: ModelSpec = self.router.get_spec(GATE_ROLE)
        except Exception as exc:  # noqa: BLE001 - defensive: never break routing
            logger.error("PredictiveRouter could not resolve gate spec: %s", exc)
            return "simple", 0.0

        try:
            result: Dict[str, Any] = self.client.chat(
                model_id=spec.id,
                messages=[{"role": "user", "content": _GATE_PROMPT + goal.strip()}],
                max_tokens=GATE_MAX_TOKENS,
                extra_body=None,
            )
        except OpenRouterError as exc:
            logger.warning("Complexity gate call failed: %s", exc)
            return "simple", 0.0
        except Exception as exc:  # noqa: BLE001 - any transport/API failure is non-fatal
            logger.warning("Complexity gate raised unexpectedly: %s", exc)
            return "simple", 0.0

        verdict: str = self._parse_verdict(str(result.get("text", "")))

        cost_aud: float = 0.0
        try:
            cost_aud = self.router.calculate_cost(
                spec,
                int(result.get("input_tokens", 0) or 0),
                int(result.get("output_tokens", 0) or 0),
            )
        except Exception as exc:  # noqa: BLE001 - pricing must never break routing
            logger.error("Gate cost calculation failed: %s", exc)
            cost_aud = 0.0

        return verdict, cost_aud

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def predict_complexity(self, goal: str) -> str:
        """Ask the cheap gate whether ``goal`` is 'complex' or 'simple'.

        Returns a plain string, defaulting to "simple" on any error or
        ambiguity.
        """
        verdict, _cost = self._run_gate(goal)
        return verdict

    def route_role(self, goal: str, default_role: str = "engineer") -> Dict[str, Any]:
        """Pick a council role for ``goal``, gated by predicted complexity.

        - "complex"  -> "architect".
        - "simple"   -> keep ``default_role`` when it is already cheap
          (in {"worker"}), otherwise "engineer".

        Returns ``{"role", "predicted", "cost_aud"}`` where ``cost_aud`` is the
        AUD cost of the single gate call.
        """
        predicted, cost_aud = self._run_gate(goal)

        if predicted == "complex":
            role: str = "architect"
        elif default_role in {"worker"}:
            role = default_role
        else:
            role = "engineer"

        return {"role": role, "predicted": predicted, "cost_aud": cost_aud}

    def output_looks_plausible(
        self, image_path: Optional[Path], exit_ok: bool
    ) -> bool:
        """Cheap, model-free pre-filter to run BEFORE an expensive Critic call.

        Returns True only when execution succeeded and, if an image is
        expected, that image exists and is larger than a trivial-stub size.
        This lets callers skip the Critic on obvious garbage.
        """
        if not exit_ok:
            return False
        if image_path is None:
            return True
        try:
            path = Path(image_path)
            return path.is_file() and path.stat().st_size > MIN_PLAUSIBLE_IMAGE_BYTES
        except OSError as exc:
            logger.warning("Could not stat candidate image %s: %s", image_path, exc)
            return False


__all__ = [
    "PredictiveRouter",
    "GATE_ROLE",
    "MIN_PLAUSIBLE_IMAGE_BYTES",
]

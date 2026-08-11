"""Direct MiniMax API client — no OpenRouter in the middle.

MiniMax exposes an OpenAI-compatible endpoint, so this is a thin wrapper over
the OpenAI SDK pointed at the international API. Used so `minimax/*` model ids
skip the router hop when a MiniMax key is configured.

Model IDs are namespaced `minimax/<id>` inside the app so they never collide
with the OpenRouter `minimax/...` slugs; `normalize_model()` strips the prefix
before the call.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger("infinity.minimax")

# International OpenAI-compatible endpoint (api.minimax.io). Overridable.
DEFAULT_BASE_URL = "https://api.minimax.io/v1"

_PREFIX = "minimax/"


def normalize_model(model: str) -> str:
    return model[len(_PREFIX):] if model.startswith(_PREFIX) else model


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax API refuses a call."""


class MiniMaxClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        resolved = api_key or os.environ.get("MINIMAX_API_KEY")
        if not resolved:
            raise ValueError("MINIMAX_API_KEY is not set.")
        url = base_url or os.environ.get("MINIMAX_BASE_URL") or DEFAULT_BASE_URL
        self._client = OpenAI(api_key=resolved, base_url=url, timeout=timeout)
        self.total_cost_usd: float = 0.0

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """Returns {"text", "input_tokens", "output_tokens"} — the same shape
        the rest of the app expects from OpenRouterClient.chat()."""
        mid = normalize_model(model)
        try:
            resp = self._client.chat.completions.create(
                model=mid,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 - typed error for callers
            raise MiniMaxError(f"MiniMax call failed for {mid}: {exc}") from exc

        choice = resp.choices[0].message if resp.choices else None
        text = (getattr(choice, "content", "") or "").strip()
        usage = getattr(resp, "usage", None)
        return {
            "text": text,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }

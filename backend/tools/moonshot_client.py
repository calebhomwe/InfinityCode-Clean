"""Direct Moonshot (Kimi) API client — no OpenRouter in the middle.

Moonshot's API is OpenAI-compatible, so this is a thin wrapper over the OpenAI
SDK pointed at api.moonshot.ai. Used for K3 so calls go straight to the source:
lower latency, no router markup, full 1M context.

Two Moonshot-specific rules are enforced here so callers don't have to know them:
  * K3 and the K2.6/K2.7 thinking models REQUIRE temperature=1 — any other value
    is rejected by the API, so we clamp it.
  * Model IDs are bare ("kimi-k3"), not namespaced ("moonshotai/kimi-k3").

`available()` reports whether the account can actually complete a request. A
valid key whose org has no balance still lists models but 429s on every
completion, so callers should degrade to OpenRouter rather than fail the task.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

try:
    from backend.core.session_logger import SessionLogger
except ImportError:  # running with backend/ as the working directory
    from core.session_logger import SessionLogger  # type: ignore[no-redef]

logger = logging.getLogger("infinity.moonshot")

MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"

# Models that reject any temperature other than 1.
_FIXED_TEMP_MODELS = ("kimi-k3", "kimi-k2.6", "kimi-k2.7")

# Namespaced -> bare, so callers can pass either form.
_ALIASES = {
    "moonshotai/kimi-k3": "kimi-k3",
    "moonshotai/kimi-k2.6": "kimi-k2.6",
    "moonshotai/kimi-k2.7-code": "kimi-k2.7-code",
}


class MoonshotError(RuntimeError):
    """Raised when the Moonshot API refuses a call (incl. billing suspension)."""


def normalize_model(model: str) -> str:
    return _ALIASES.get(model, model)


class MoonshotClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = MOONSHOT_BASE_URL,
        timeout: float = 180.0,
        session_logger: Optional[SessionLogger] = None,
    ) -> None:
        resolved = api_key or os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")
        if not resolved:
            raise ValueError("MOONSHOT_API_KEY is not set.")
        self._client = OpenAI(api_key=resolved, base_url=base_url, timeout=timeout)
        self.total_cost_usd: float = 0.0
        self.session_logger: Optional[SessionLogger] = session_logger

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        session_id: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Returns {"text", "input_tokens", "output_tokens"} — same shape the
        rest of the app expects from OpenRouterClient.chat()."""
        mid = normalize_model(model)
        if mid.startswith(_FIXED_TEMP_MODELS):
            temperature = 1.0
        try:
            resp = self._client.chat.completions.create(
                model=mid,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 - surface a typed error to callers
            raise MoonshotError(f"Moonshot call failed for {mid}: {exc}") from exc

        choice = resp.choices[0].message if resp.choices else None
        text = (getattr(choice, "content", "") or "").strip()
        usage = getattr(resp, "usage", None)
        result = {
            "text": text,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }
        if session_id and self.session_logger is not None:
            try:
                self.session_logger.log_turn(
                    session_id=session_id,
                    messages=list(messages),
                    output=text,
                    model=model,
                    lane=lane,
                    cost_usd=0.0,
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                    latency_ms=None,
                    metadata={"source": "moonshot_direct", "provider": "moonshot"},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Moonshot session logging failed: %s", exc)
        return result

    def available(self) -> bool:
        """True only if a real completion succeeds — a key with an unfunded org
        lists models fine but 429s on every completion."""
        try:
            self.chat("kimi-k3", [{"role": "user", "content": "hi"}], max_tokens=8)
            return True
        except MoonshotError as exc:
            logger.info("Moonshot direct unavailable: %s", str(exc)[:200])
            return False

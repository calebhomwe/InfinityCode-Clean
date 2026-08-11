"""Direct DashScope (Alibaba Qwen) API client — no OpenRouter in the middle.

DashScope's Model Studio exposes an OpenAI-compatible endpoint, so this is a
thin wrapper over the OpenAI SDK pointed at the workspace's maas URL. Used for
the verified-fast Qwen models the owner benchmarked (qwen3-coder-480b ~1.5s,
qwen3.7-max reasoning, qwen3-vl-plus vision) so those calls skip the router hop.

Model IDs are namespaced `dashscope/<id>` inside the app so they never collide
with the OpenRouter `qwen/...` slugs; `normalize_model()` strips the prefix
before the call. `available()` reports whether the account can actually
complete a request, so callers degrade to OpenRouter rather than fail a task.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger("infinity.dashscope")

# Workspace-scoped intl endpoint (ap-southeast-1). Overridable via env/config.
DEFAULT_BASE_URL = "https://ws-izjouc465l0oerxs.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"

_PREFIX = "dashscope/"


def normalize_model(model: str) -> str:
    return model[len(_PREFIX):] if model.startswith(_PREFIX) else model


class DashScopeError(RuntimeError):
    """Raised when the DashScope API refuses a call."""


class DashScopeClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        resolved = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not resolved:
            raise ValueError("DASHSCOPE_API_KEY is not set.")
        url = base_url or os.environ.get("DASHSCOPE_BASE_URL") or DEFAULT_BASE_URL
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
            raise DashScopeError(f"DashScope call failed for {mid}: {exc}") from exc

        choice = resp.choices[0].message if resp.choices else None
        text = (getattr(choice, "content", "") or "").strip()
        usage = getattr(resp, "usage", None)
        return {
            "text": text,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }

    def available(self) -> bool:
        try:
            self.chat(
                "dashscope/qwen-turbo",
                [{"role": "user", "content": "hi"}],
                max_tokens=8,
            )
            return True
        except DashScopeError as exc:
            logger.info("DashScope unavailable: %s", str(exc)[:200])
            return False

"""Shared model-id -> provider dispatch that preserves the message list.

swarm._provider_chat flattens a conversation into one user prompt and has no
local/* branch, so the long-task engine (which needs multi-turn history and
local FABLE) gets its own dispatcher. Zero coupling to AgentSwarm: pass the
already-constructed clients in, and this routes kimi/* -> Moonshot direct,
dashscope/* -> DashScope direct, local/* -> a live llama.cpp server, and
everything else -> OpenRouter (with the dashscope/ prefix stripped, since OR
cannot serve our namespaced ids).
"""
from __future__ import annotations

import logging
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from backend.core import llm_cache
    from backend.core.credits import meter as credits_meter
    from backend.core.router import MODEL_SPECS, is_dashscope, is_kimi
except ImportError:  # running with backend/ as the working directory
    from core import llm_cache  # type: ignore[no-redef]
    from core.credits import meter as credits_meter  # type: ignore[no-redef]
    from core.router import MODEL_SPECS, is_dashscope, is_kimi  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Same ports the swarm's _LLAMA_SERVERS probes for the local big brain.
_LOCAL_SERVERS = ("http://127.0.0.1:8081/v1", "http://127.0.0.1:8082/v1")


def probe_local(timeout: float = 1.0) -> Optional[str]:
    """Return the base_url of a live llama.cpp server, else None."""
    for base in _LOCAL_SERVERS:
        try:
            urllib.request.urlopen(base + "/models", timeout=timeout)
            return base
        except Exception:  # noqa: BLE001 - any failure just means "not here"
            continue
    return None


def provider_chat(model_id: str, messages: List[Dict[str, str]], max_tokens: int,
                  openrouter: Any, moonshot: Any = None,
                  dashscope: Any = None) -> Dict[str, Any]:
    """Dispatch one chat to the right backend for this model id.

    Returns {"text": str, "cost_usd": float}. Raises on provider failure;
    callers (e.g. the long-task chain walker) decide what a raise means.
    """
    # Response cache for the single-shot shape this dispatcher is used with
    # (multi-turn longtask history stays uncached — it is stateful).
    can_cache = (
        len(messages) <= 4
        and not any(m.get("role") == "assistant" for m in messages)
        and llm_cache.should_cache(messages, 0.2, max_tokens)
    )
    cached_reply = None
    if can_cache:
        cached_reply = llm_cache.lookup(
            provider="", base_url="", model=model_id, messages=messages,
            max_tokens=max_tokens, temperature=0.2,
        )
    if cached_reply is not None:
        return {"text": cached_reply["text"], "cost_usd": 0.0}
    if moonshot is not None and is_kimi(model_id):
        raw = moonshot.chat(model_id, messages, max_tokens=max_tokens)
    elif dashscope is not None and is_dashscope(model_id):
        raw = dashscope.chat(model_id, messages, max_tokens=max_tokens)
    elif model_id.startswith("local/"):
        base = probe_local()
        if base is None:
            raise RuntimeError(f"no local llama.cpp server for {model_id}")
        try:
            from backend.tools.openrouter_client import OpenRouterClient
        except ImportError:  # running with backend/ as the working directory
            from tools.openrouter_client import OpenRouterClient  # type: ignore[no-redef]
        local = OpenRouterClient(api_key="local", base_url=base)
        bare = model_id.split("/", 1)[1]
        raw = local.chat(bare, messages, max_tokens=max_tokens)
    else:
        # OpenRouter can't serve our namespaced dashscope/* ids — strip the prefix.
        or_id = model_id.split("/", 1)[1] if model_id.startswith("dashscope/") else model_id
        raw = openrouter.chat(or_id, messages, max_tokens=max_tokens)
    if can_cache and isinstance(raw, dict):
        llm_cache.store(
            provider="", base_url="", model=model_id, messages=messages,
            max_tokens=max_tokens, temperature=0.2,
            text=str(raw.get("text") or ""),
            input_tokens=int(raw.get("input_tokens", 0) or 0),
            output_tokens=int(raw.get("output_tokens", 0) or 0),
        )
    # Credit Engine: this dispatcher serves direct providers (longtask chain);
    # OpenRouter-client results are already metered inside the client.
    if isinstance(raw, dict) and not raw.get("_metered"):
        spec = MODEL_SPECS.get(model_id)
        if spec is not None:
            tin = int(raw.get("input_tokens", 0) or 0)
            tout = int(raw.get("output_tokens", 0) or 0)
            usd = (tin * float(spec.cost_in_per_million)
                   + tout * float(spec.cost_out_per_million)) / 1_000_000.0
            if raw.get("cached"):
                credits_meter(0.0, model_id, "longtask_cached", tin, tout, avoided_usd=usd)
            else:
                credits_meter(usd, model_id, "longtask_direct", tin, tout)
    return _norm(raw, model_id)


def _norm(result: Any, model_id: str = "") -> Dict[str, Any]:
    """Coerce a client reply (dict or object) to the engine's shape.

    Direct clients (DashScope/Moonshot) reply with token counts but no cost,
    so estimate it from the router's pricing table — budget guardrails need a
    number on every call, and zero would let a run blow past max_cost_aud.
    """
    if isinstance(result, dict):
        text = str(result.get("text", ""))
        cost = float(result.get("cost_usd", 0.0))
        tin = int(result.get("input_tokens", 0) or 0)
        tout = int(result.get("output_tokens", 0) or 0)
    else:
        text = str(getattr(result, "text", result))
        cost = float(getattr(result, "cost_usd", 0.0))
        tin = int(getattr(result, "input_tokens", 0) or 0)
        tout = int(getattr(result, "output_tokens", 0) or 0)
    if cost <= 0 and (tin or tout):
        spec = MODEL_SPECS.get(model_id)
        if spec is not None:
            cost = (tin * spec.cost_in_per_million +
                    tout * spec.cost_out_per_million) / 1_000_000.0
    return {"text": text, "cost_usd": cost}


__all__ = ["probe_local", "provider_chat"]

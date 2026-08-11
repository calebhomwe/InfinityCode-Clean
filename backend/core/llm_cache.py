"""Disk-backed response cache for LLM calls.

Always-on cost discipline: an exact repeat of a request costs zero tokens and
zero latency. The key covers the full request shape (provider, base_url,
model, messages, max_tokens, temperature, extra_body), so a hit is only ever
served for an identical repeat. Deterministic calls only: temperature must be
<= 0.3, image content parts are never cached, and tool-role turns bypass the
cache entirely (they are stateful by nature).

Env knobs:
  INFINITY_LLM_CACHE=0        disable the cache entirely
  INFINITY_LLM_CACHE_DIR=...  override the cache directory (tests)
  INFINITY_LLM_CACHE_TTL=...  entry TTL in seconds (default 7 days)

Writes are atomic (tmp file + os.replace) so a crash can never corrupt an
entry or the cache directory.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS: float = 7 * 24 * 3600
_MAX_ENTRY_BYTES: int = 4_000_000  # sanity cap: never cache absurdly large payloads


def _cache_root() -> Optional[Path]:
    """Return the cache dir, or None when the cache is disabled."""
    if os.environ.get("INFINITY_LLM_CACHE", "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return None
    override = os.environ.get("INFINITY_LLM_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    # repo_root/backend/data/.llm_cache
    return Path(__file__).resolve().parents[2] / "backend" / "data" / ".llm_cache"


def _ttl_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("INFINITY_LLM_CACHE_TTL", "").strip()))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _has_image_content(messages: List[Dict[str, Any]]) -> bool:
    """Vision payloads (image_url parts) are never cached — image tokens and
    the image itself can change meaning between identical-looking prompts."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
        elif isinstance(content, str) and content.startswith("data:image/"):
            return True
    return False


def should_cache(
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> bool:
    """True when this request shape is deterministic enough to cache.

    Temperature > 0.3 introduces variance; image content and tool-role turns
    are stateful; empty prompts and zero-token budgets cache nothing.
    """
    if temperature is None:
        temperature = 0.2
    try:
        if float(temperature) > 0.3:
            return False
    except (TypeError, ValueError):
        return False
    if not isinstance(messages, list) or not messages:
        return False
    if int(max_tokens or 0) <= 0:
        return False
    if _has_image_content(messages):
        return False
    for m in messages:
        if not isinstance(m, dict):
            return False
        role = m.get("role")
        if role == "tool" or m.get("tool_calls"):
            return False
    return True


def _canonical(value: Any) -> str:
    """Stable serialization: sorted keys, no whitespace, unicode preserved."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def cache_key(
    provider: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    extra_body: Optional[Dict[str, Any]] = None,
) -> str:
    """sha256 over the full request shape — any drift in the prompt, model,
    endpoint or budget produces a different key (i.e. a fresh call)."""
    raw = "|".join([
        str(provider or ""),
        str(base_url or ""),
        str(model or ""),
        _canonical(messages),
        str(int(max_tokens or 0)),
        str(temperature if temperature is not None else ""),
        _canonical(extra_body or {}),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lookup(
    provider: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the cached reply or None. A stale entry is removed on read."""
    root = _cache_root()
    if root is None:
        return None
    path = root / (cache_key(provider, base_url, model, messages,
                             max_tokens, temperature, extra_body) + ".json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ttl = _ttl_seconds() or DEFAULT_TTL_SECONDS
        if ttl > 0 and time.time() - float(data.get("cached_at", 0.0)) > ttl:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return {
            "text": str(data.get("text", "")),
            "input_tokens": int(data.get("input_tokens", 0) or 0),
            "output_tokens": int(data.get("output_tokens", 0) or 0),
            "cached": True,
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("llm_cache: dropping unreadable entry %s: %s", path, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def store(
    provider: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    text: str,
    input_tokens: int,
    output_tokens: int,
    extra_body: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a reply. Atomic write; oversize payloads are dropped."""
    root = _cache_root()
    if root is None:
        return
    payload = {
        "text": str(text or ""),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cached_at": time.time(),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > _MAX_ENTRY_BYTES:
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        key = cache_key(provider, base_url, model, messages,
                        max_tokens, temperature, extra_body)
        tmp = root / (key + ".json.tmp")
        tmp.write_bytes(body)
        tmp.replace(root / (key + ".json"))
    except OSError as exc:
        logger.warning("llm_cache: store failed: %s", exc)


def clear() -> int:
    """Delete every cache entry. Returns the number of files removed."""
    root = _cache_root()
    if root is None or not root.is_dir():
        return 0
    removed = 0
    try:
        for path in root.glob("*.json"):
            path.unlink()
            removed += 1
    except OSError as exc:
        logger.warning("llm_cache: clear failed: %s", exc)
    return removed


__all__ = [
    "should_cache",
    "cache_key",
    "lookup",
    "store",
    "clear",
    "DEFAULT_TTL_SECONDS",
]

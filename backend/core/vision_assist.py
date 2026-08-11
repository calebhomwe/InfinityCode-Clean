"""Weak-model visual augmentation layer (Qwen-MM).

Only injects when a visual need exists (an image is attached to the turn).
The Qwen-MM description is cached by sha256(image bytes + prompt). Calls run
on a dedicated 4-thread pool independent of the BFB swarm pool; when the
pool is exhausted the task is queued, never dropped. Failures are logged and
return None -- the calling model still answers, just without augmentation.

Route: OpenRouter qwen/qwen3-vl-32b-instruct (verified live 2026-08-10),
falling back to qwen/qwen3-vl-8b-instruct on HTTP errors. DashScope token-plan
has no vision model (verified 404), so DashScope is not attempted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

logger = logging.getLogger("infinity.vision_assist")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL_PRIMARY = "qwen/qwen3-vl-32b-instruct"
MODEL_FALLBACK = "qwen/qwen3-vl-8b-instruct"

# Local-first route: qwen3-vl:8b via Ollama (free, private, no key).
# Pulled 2026-08-10; used before any cloud call when the server is up.
LOCAL_OLLAMA = "http://localhost:11434/api/chat"
LOCAL_VL_MODEL = "qwen3-vl:8b"

# Dedicated pool: 4 workers, bounded queue. Never drops -- blocks until a
# worker frees up (or the per-call timeout fires in the caller).
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qwenmm")
_ENV_LOCK = threading.Lock()
_KEY_CACHE: Dict[str, str] = {}
# sha256(image bytes + prompt) -> description
_DESC_CACHE: Dict[str, str] = {}

_DESCRIBE_PROMPT = (
    "Describe this image in structured detail so a text-only coding model can "
    "work from it without seeing it: layout, key objects, colors, any text, "
    "camera/UI style, and what a developer must replicate. User context: {ctx}"
)


def _env_key(name: str) -> str:
    with _ENV_LOCK:
        if name in _KEY_CACHE:
            return _KEY_CACHE[name]
        value = ""
        env_file = os.path.expanduser(r"~\.env")
        if os.path.exists(env_file):
            with open(env_file, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(rf"^\s*{name}\s*=\s*(.*)$", line)
                    if m:
                        value = m.group(1).strip().strip('"').strip("'")
                        break
        _KEY_CACHE[name] = value
        return value


def _decode_image(image: str) -> Optional[bytes]:
    """Accepts a data URL (data:image/...;base64,...) or a local file path."""
    if image.startswith("data:image/"):
        m = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)$", image)
        if not m:
            return None
        try:
            return base64.b64decode(m.group(1))
        except Exception:  # noqa: BLE001
            return None
    try:
        with open(image, "rb") as f:
            return f.read()
    except OSError:
        return None


def _call_vl_local(image_b64: str, prompt: str) -> Optional[str]:
    """Qwen-VL via Ollama (/api/chat, legacy images field). None on any error."""
    try:
        payload = {
            "model": LOCAL_VL_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
            # qwen3 defaults to reasoning and burns the budget on thinking.
            # Both spellings are sent because Ollama ignores the top-level
            # key when `options` is present (measured 2026-08-10).
            "think": False,
            "options": {"num_predict": 1500, "think": False},
        }
        req = urllib.request.Request(
            LOCAL_OLLAMA,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msg = data["message"]
        # Some Ollama builds still emit the answer in `thinking` with an
        # empty `content`; fall back so the route never returns None on a
        # successful inference.
        content = str(msg.get("content") or msg.get("thinking") or "")
        return content if content.strip() else None
    except Exception as exc:  # noqa: BLE001 - local route is best-effort
        logger.info("local Qwen-VL unavailable (%s); cloud fallback", exc)
        return None


def _call_vl(image_b64: str, mime: str, prompt: str, key: str) -> Optional[str]:
    # Local-first (free, private); cloud is the fallback when Ollama is down.
    local = _call_vl_local(image_b64, prompt)
    if local:
        return local
    payload = {
        "model": MODEL_PRIMARY,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    last_err: Optional[str] = None
    for model in (MODEL_PRIMARY, MODEL_FALLBACK):
        payload["model"] = model
        req = urllib.request.Request(
            OPENROUTER_BASE + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(req, timeout=75) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return str(data["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code} {body[:300]}"
            logger.warning("Qwen-MM %s failed: %s", model, last_err)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            logger.warning("Qwen-MM %s error: %s", model, last_err)
    if last_err:
        logger.warning("Qwen-MM all routes failed: %s", last_err)
    return None


def describe_image(image: str, context: str = "") -> Optional[str]:
    """Describe one image (data URL or path). Returns None on failure (fail-open)."""
    raw = _decode_image(image)
    if raw is None:
        return None
    prompt = _DESCRIBE_PROMPT.format(ctx=(context or "")[:500])
    cache_key = hashlib.sha256(raw + prompt.encode("utf-8")).hexdigest()
    if cache_key in _DESC_CACHE:
        return _DESC_CACHE[cache_key]

    key = _env_key("OPENROUTER_API_KEY")
    if not key:
        logger.warning("OPENROUTER_API_KEY not set; vision augmentation skipped")
        return None

    mime = "image/png"
    if image.startswith("data:image/"):
        mime = image.split(";", 1)[0].split("data:", 1)[1]
    b64 = base64.b64encode(raw).decode("utf-8")

    future = _POOL.submit(_call_vl, b64, mime, prompt, key)
    try:
        desc = future.result(timeout=300)
    except Exception as exc:  # noqa: BLE001 - fail-open, never stall the reply
        logger.warning("Qwen-MM pool task failed: %s", exc)
        return None
    if desc:
        _DESC_CACHE[cache_key] = desc
    return desc


def augment_content_if_needed(images: List[str], text: str) -> str:
    """Return `text` plus Qwen-MM descriptions for up to 2 images.

    Only triggers when images are present; returns `text` unchanged on any
    failure (fail-open). The caller decides whether the model needs it.
    """
    if not images:
        return text
    descriptions: List[str] = []
    for image in images[:2]:
        desc = describe_image(image, text)
        if desc:
            descriptions.append(desc.strip())
    if not descriptions:
        return text
    block = "\n\n[Qwen-MM vision description of the attached image(s) - the model cannot see the image, work from this]\n"
    block += "\n\n---\n\n".join(descriptions)
    return text + block


_CRITIQUE_PROMPT = (
    "You are a brutal UI/visual reviewer. Look at this screenshot and judge it "
    "against the criteria: {criteria}. Reply with STRICT JSON only: "
    '{{"score": 0-10, "issues": ["..."], "fixes": ["..."]}}. Score harshly: '
    "generic AI look, misalignment, broken layout, bad contrast each cost points."
)


def critique_image(image: str, criteria: str) -> Optional[str]:
    """Qwen-MM critique of one image (data URL or path) against criteria.

    Returns the model's raw reply (STRICT JSON contract) or None on failure.
    Cached by sha256(image + criteria); runs on the dedicated pool.
    """
    raw = _decode_image(image)
    if raw is None:
        return None
    prompt = _CRITIQUE_PROMPT.format(criteria=(criteria or "")[:400])
    cache_key = hashlib.sha256(raw + prompt.encode("utf-8")).hexdigest()
    if cache_key in _DESC_CACHE:
        return _DESC_CACHE[cache_key]
    key = _env_key("OPENROUTER_API_KEY")
    if not key:
        logger.warning("OPENROUTER_API_KEY not set; critique skipped")
        return None
    mime = "image/png"
    if image.startswith("data:image/"):
        mime = image.split(";", 1)[0].split("data:", 1)[1]
    b64 = base64.b64encode(raw).decode("utf-8")
    future = _POOL.submit(_call_vl, b64, mime, prompt, key)
    try:
        reply = future.result(timeout=300)
    except Exception as exc:  # noqa: BLE001 - fail-open
        logger.warning("Qwen-MM critique task failed: %s", exc)
        return None
    if reply:
        _DESC_CACHE[cache_key] = reply
    return reply

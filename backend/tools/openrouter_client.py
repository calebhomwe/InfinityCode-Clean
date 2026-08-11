"""Unified OpenRouter LLM client for Infinity Code.

All LLM traffic (text, vision, image generation) goes through a single
OpenRouter endpoint with one OPENROUTER_API_KEY. Token usage and USD cost
are tracked per call and cumulatively.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, TypedDict

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover - hard dependency
    raise ImportError(
        "The 'openai' package is required for OpenRouterClient. "
        "Install it with: pip install openai"
    ) from exc

try:
    from backend.core.session_logger import SessionLogger
except ImportError:  # running with backend/ as the working directory
    from core.session_logger import SessionLogger  # type: ignore[no-redef]

try:
    from backend.core import llm_cache
    from backend.core.credits import meter as credits_meter
except ImportError:  # running with backend/ as the working directory
    from core import llm_cache  # type: ignore[no-redef]
    from core.credits import meter as credits_meter  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
# Alibaba DashScope (kept for reference — revoked, do not use).
DASHSCOPE_BASE_URL: str = "https://ws-izjouc465l0oerxs.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
# Moonshot direct (Kimi K2.6 / K2.7-code / K3). OpenAI-compatible wire.
MOONSHOT_BASE_URL: str = "https://api.moonshot.ai/v1"
# DeepSeek direct (DeepSeek-V3 / Chat / Coder / Reasoner). OpenAI-compatible.
DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
# NVIDIA NIM (build.nvidia.com). OpenAI-compatible wire, free credit quota.
# Cold starts can run 60-180s on the free tier, so it sits last in failover.
NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
DEFAULT_TIMEOUT_SECONDS: float = 120.0
MAX_RETRIES: int = 3
RETRY_BACKOFF_SECONDS: float = 1.5

# DashScope serves models under bare ids (no vendor prefix). The rest of the app
# uses OpenRouter-style "vendor/model" ids in config. When talking to DashScope
# we strip the prefix, and remap the handful whose bare name differs.
_DASHSCOPE_MODEL_ALIASES: Dict[str, str] = {
    # dashscope/* namespaced models -> bare DashScope names
    "dashscope/qwen-turbo": "qwen-turbo",
    "dashscope/qwen-plus": "qwen-plus",
    "dashscope/qwen-max": "qwen-max",
    "dashscope/qwen-coder-plus": "qwen-coder-plus",
    "dashscope/qwen-vl-plus": "qwen-vl-plus",
    "dashscope/qwen-vl-max": "qwen-vl-max",
    "dashscope/qwen3.8-max": "qwen3.8-max",
    "dashscope/qwen3.7-max": "qwen3.7-max",
    "dashscope/qwen3.7-max-2026-05-17": "qwen3.7-max",
    "dashscope/qwen3.7-flash": "qwen3.7-flash",
    "dashscope/qwen3-coder-480b-a35b-instruct": "qwen3-coder-plus",
    "dashscope/qwen3-vl-plus": "qwen3-vl-plus",
    # OpenRouter-style slugs -> DashScope equivalents
    "qwen/qwen3-coder": "qwen-coder-plus",
    "qwen/qwen3-vl-30b-a3b-thinking": "qwen-vl-plus",
    "openai/gpt-5.6-sol": "qwen-max",
    "anthropic/claude-opus-4.6": "qwen-max",
    "google/gemini-3.1-flash-lite": "qwen-turbo",
    "google/gemini-3.1-flash-image": "qwen-image-2.0-pro",
    "moonshotai/kimi-k3": "qwen-max",
    "moonshotai/kimi-k2.7-code": "qwen-coder-plus",
    "moonshotai/kimi-k2.6": "qwen-plus",
    "deepseek/deepseek-v4-flash": "qwen-turbo",
    "deepseek/deepseek-v4-pro": "qwen-max",
    # The "free" picker entry is dead on OpenRouter (all :free slugs 404);
    # land it on the zero-cost DashScope lane instead.
    "deepseek/deepseek-chat": "qwen-turbo",
    "deepseek/deepseek-chat:free": "qwen-turbo",
    "z-ai/glm-5.2": "qwen-max",
    "minimax/minimax-m3": "qwen-plus",
    # NVIDIA NIM picker entries degrade onto the zero-cost DashScope lane.
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": "qwen-max",
    "meta/llama-3.3-70b-instruct": "qwen-plus",
    "deepseek-ai/deepseek-v4-flash-0731": "qwen-turbo",
}

# NVIDIA NIM serves models under full "vendor/model" ids. Map the app's
# OpenRouter-style slugs onto the free NIM catalog; planning-class roles go to
# Nemotron Ultra 253B, worker/bulk roles to Llama 3.3 70B.
_NVIDIA_MODEL_ALIASES: Dict[str, str] = {
    "qwen/qwen3.7-max": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "openai/gpt-5.6-sol": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "anthropic/claude-opus-4.6": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "z-ai/glm-5.2": "z-ai/glm-5.2",
    "moonshotai/kimi-k3": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "moonshotai/kimi-k2.7-code": "meta/llama-3.3-70b-instruct",
    "moonshotai/kimi-k2.6": "moonshotai/kimi-k2.6",
    "qwen/qwen3-coder": "meta/llama-3.3-70b-instruct",
    "qwen/qwen3-coder-plus": "meta/llama-3.3-70b-instruct",
    "qwen/qwen3-vl-30b-a3b-thinking": "meta/llama-3.2-90b-vision-instruct",
    "google/gemini-3.1-flash-lite": "meta/llama-3.3-70b-instruct",
    "google/gemini-3.1-flash-image": "meta/llama-3.3-70b-instruct",
    "deepseek/deepseek-v4-pro": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "deepseek/deepseek-v4-flash": "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek/deepseek-v3.2": "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek/deepseek-chat": "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek/deepseek-chat:free": "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek/deepseek-coder": "deepseek-ai/deepseek-coder-6.7b-instruct",
    "deepseek/deepseek-reasoner": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "minimax/minimax-m3": "meta/llama-3.3-70b-instruct",
    # Native NIM picker ids pass through explicitly.
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "deepseek-ai/deepseek-v4-flash-0731": "deepseek-ai/deepseek-v4-flash-0731",
}

# Moonshot direct serves models under bare kimi ids. Map every model this app
# might request onto a K3 / K2.7-code / K2.6 fallback so the entire chat and
# swarm still route somewhere useful when only a Moonshot key is present.
_MOONSHOT_MODEL_ALIASES: Dict[str, str] = {
    # Kimi native — pass through
    "moonshotai/kimi-k3": "kimi-k3",
    "moonshotai/kimi-k2.7-code": "kimi-k2.7-code",
    "moonshotai/kimi-k2.7-code-highspeed": "kimi-k2.7-code-highspeed",
    "moonshotai/kimi-k2.6": "kimi-k2.6",
    # Roles from config.yaml → best available Kimi
    "qwen/qwen3.7-max":         "kimi-k3",
    "qwen/qwen3-coder":         "kimi-k2.7-code",
    "qwen/qwen3-vl-30b-a3b-thinking": "kimi-k2.6",
    "google/gemini-3.1-flash-lite":   "kimi-k2.6",
    "google/gemini-3.1-flash-image":  "kimi-k2.6",
    "openai/gpt-5.6-sol":       "kimi-k3",
    "anthropic/claude-opus-4.6": "kimi-k3",
    "z-ai/glm-5.2":             "kimi-k3",
    "deepseek/deepseek-v3.2":   "kimi-k2.7-code",
    "minimax/minimax-m3":       "kimi-k2.6",
    "qwen/qwen3-coder-plus":    "kimi-k2.7-code",
    # NVIDIA NIM picker entries land on the best Kimi when only Moonshot exists.
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": "kimi-k3",
    "meta/llama-3.3-70b-instruct": "kimi-k2.7-code",
    "deepseek-ai/deepseek-v4-flash-0731": "kimi-k2.7-code",
}

# DeepSeek direct serves models under bare deepseek ids. Map OpenRouter-style
# slugs to DeepSeek native names so a DEEPSEEK_API_KEY works immediately.
# 2026-07 API: current first-class ids are deepseek-v4-pro / deepseek-v4-flash
# (legacy "deepseek-chat" still resolves server-side). Planning/architect-class
# roles go to V4 Pro; worker/bulk roles to V4 Flash — keep Pro low-volume, the
# account balance is small.
_DEEPSEEK_MODEL_ALIASES: Dict[str, str] = {
    "deepseek/deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek/deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek/deepseek-v3.2": "deepseek-v4-flash",
    "deepseek/deepseek-chat": "deepseek-v4-flash",
    "deepseek/deepseek-chat:free": "deepseek-v4-flash",
    "deepseek/deepseek-coder": "deepseek-v4-flash",
    "deepseek/deepseek-reasoner": "deepseek-v4-pro",
    # Planning / architect / judge-class roles from config.yaml -> V4 Pro
    "qwen/qwen3.7-max": "deepseek-v4-pro",
    "openai/gpt-5.6-sol": "deepseek-v4-pro",
    "anthropic/claude-opus-4.6": "deepseek-v4-pro",
    "z-ai/glm-5.2": "deepseek-v4-pro",
    "moonshotai/kimi-k3": "deepseek-v4-pro",
    # Worker / bulk / vision-fallback roles -> V4 Flash
    "qwen/qwen3-coder": "deepseek-v4-flash",
    "qwen/qwen3-coder-plus": "deepseek-v4-flash",
    "qwen/qwen3-vl-30b-a3b-thinking": "deepseek-v4-flash",
    "google/gemini-3.1-flash-lite": "deepseek-v4-flash",
    "google/gemini-3.1-flash-image": "deepseek-v4-flash",
    "minimax/minimax-m3": "deepseek-v4-flash",
    "moonshotai/kimi-k2.7-code": "deepseek-v4-flash",
    "moonshotai/kimi-k2.6": "deepseek-v4-flash",
    # NVIDIA NIM picker entries land on V4 Pro/Flash when only DeepSeek exists.
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": "deepseek-v4-pro",
    "meta/llama-3.3-70b-instruct": "deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-flash-0731": "deepseek-v4-flash",
}


def resolve_llm_provider(
    api_key: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Pick the active LLM provider from explicit arg + environment.

    Returns (provider, api_key, base_url). Preference order:
      1. Explicit api_key arg (sniffed by prefix: sk-or- OpenRouter, sk-ws- DashScope,
         sk-d... DeepSeek).
      2. OPENROUTER_API_KEY  -> OpenRouter.
      3. MOONSHOT_API_KEY -> Moonshot direct.
      4. DEEPSEEK_API_KEY -> DeepSeek direct.
      5. DASHSCOPE_API_KEY / ALIBABA_API_KEY -> DashScope.
      6. NVIDIA_API_KEY -> NVIDIA NIM (free tier, last resort).
    A provider env override (INFINITY_LLM_PROVIDER) beats everything else.
    """
    dash_env = (
        os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("ALIBABA_API_KEY")
        or ""
    ).strip()
    or_env = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    moon_env = (os.environ.get("MOONSHOT_API_KEY") or "").strip()
    deep_env = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    nv_env = (os.environ.get("NVIDIA_API_KEY") or "").strip()
    base_override = os.environ.get("INFINITY_LLM_BASE_URL", "").strip()
    forced = os.environ.get("INFINITY_LLM_PROVIDER", "").strip().lower()

    # Sniff an explicit api_key by prefix so callers who pass a non-OpenRouter
    # key don't get silently routed to OpenRouter.
    explicit_dash: str = ""
    explicit_or: str = ""
    explicit_deep: str = ""
    explicit_nv: str = ""
    if api_key:
        key = api_key.strip()
        if key.startswith("sk-or-"):
            explicit_or = key
        elif key.startswith("sk-ws-"):
            explicit_dash = key
        elif key.startswith("sk-d"):
            explicit_deep = key
        elif key.startswith("nvapi-"):
            explicit_nv = key
        else:
            # Unknown-shape explicit key: assume OpenRouter (historical default).
            explicit_or = key

    dash_key = explicit_dash or dash_env
    or_key = explicit_or or or_env
    deep_key = explicit_deep or deep_env
    nv_key = explicit_nv or nv_env

    # Explicit override wins, so a machine that also has an OpenRouter key can
    # still be pinned to a different provider.
    if forced == "moonshot" and moon_env:
        return "moonshot", moon_env, base_override or MOONSHOT_BASE_URL
    if forced == "dashscope" and dash_key:
        return "dashscope", dash_key, base_override or DASHSCOPE_BASE_URL
    if forced == "deepseek" and deep_key:
        return "deepseek", deep_key, base_override or DEEPSEEK_BASE_URL
    if forced == "openrouter" and or_key:
        return "openrouter", or_key, base_override or OPENROUTER_BASE_URL
    if forced == "nvidia" and nv_key:
        return "nvidia", nv_key, base_override or NVIDIA_BASE_URL

    # Explicit api_key beats env.
    if explicit_dash:
        return "dashscope", explicit_dash, base_override or DASHSCOPE_BASE_URL
    if explicit_or:
        return "openrouter", explicit_or, base_override or OPENROUTER_BASE_URL
    if explicit_deep:
        return "deepseek", explicit_deep, base_override or DEEPSEEK_BASE_URL
    if explicit_nv:
        return "nvidia", explicit_nv, base_override or NVIDIA_BASE_URL

    # Env preference: DashScope (free quota) > DeepSeek > Moonshot > OpenRouter
    # > NVIDIA NIM (free but slow cold starts - last resort).
    # DashScope is cheapest (workspace free tier), then DeepSeek/Moonshot direct.
    if dash_key:
        return "dashscope", dash_key, base_override or DASHSCOPE_BASE_URL
    if deep_key:
        return "deepseek", deep_key, base_override or DEEPSEEK_BASE_URL
    if moon_env:
        return "moonshot", moon_env, base_override or MOONSHOT_BASE_URL
    if or_key:
        return "openrouter", or_key, base_override or OPENROUTER_BASE_URL
    if nv_key:
        return "nvidia", nv_key, base_override or NVIDIA_BASE_URL
    raise ValueError(
        "No LLM key found. Set OPENROUTER_API_KEY, MOONSHOT_API_KEY, or DEEPSEEK_API_KEY."
    )


def normalize_model_id(model_id: str, provider: str) -> str:
    """Map an OpenRouter-style model id to the provider's native id."""
    if provider == "moonshot":
        if model_id in _MOONSHOT_MODEL_ALIASES:
            return _MOONSHOT_MODEL_ALIASES[model_id]
        # Bare "kimi-*" id passes through; otherwise map to K3 as safe default.
        bare = model_id.split("/", 1)[1] if "/" in model_id else model_id
        return bare if bare.startswith("kimi-") else "kimi-k3"
    if provider == "deepseek":
        if model_id in _DEEPSEEK_MODEL_ALIASES:
            return _DEEPSEEK_MODEL_ALIASES[model_id]
        # Bare deepseek-* id passes through; otherwise default to chat.
        bare = model_id.split("/", 1)[1] if "/" in model_id else model_id
        return bare if bare.startswith("deepseek-") else "deepseek-chat"
    if provider == "nvidia":
        if model_id in _NVIDIA_MODEL_ALIASES:
            return _NVIDIA_MODEL_ALIASES[model_id]
        # NIM ids are already namespaced (vendor/model): pass through.
        return model_id if "/" in model_id else f"meta/{model_id}"
    if provider == "dashscope":
        if model_id in _DASHSCOPE_MODEL_ALIASES:
            return _DASHSCOPE_MODEL_ALIASES[model_id]
        return model_id.split("/", 1)[1] if "/" in model_id else model_id
    return model_id


def is_thinking_only(model_id: str) -> bool:
    """Models that require temperature=1 and a larger token floor.

    DeepSeek V4 Pro / Reasoner think before answering: with a small max_tokens
    the entire budget goes to reasoning and content comes back EMPTY (measured:
    900/900 reasoning tokens, ""). The 4000-token floor prevents that. V4
    Flash is deliberately NOT here: BFB grunt work runs with thinking disabled
    (see _create_completion) and honors small budgets instead.
    """
    return (
        model_id.endswith("kimi-k3")
        or model_id == "kimi-k3"
        or model_id.endswith("deepseek-reasoner")
        or model_id == "deepseek-reasoner"
        or model_id.endswith("deepseek-v4-pro")
    )


# --------------------------------------------------------------------------- #
# Provider failover chain (0.1.59): every quota on disk, auto-swapped.
#
# Owner directive (2026-08-07): DashScope first, then the free router, then
# OpenRouter — and every other key on the machine as backup. A chat must
# never surface "no response" while any route is alive.
# --------------------------------------------------------------------------- #

# The free catalog rotates constantly (verified 2026-08-10: every hardcoded
# :free slug below was 404 — only the meta router survives). openrouter/free
# auto-selects among the CURRENT free models, so it is the only stable slug.
FREE_ROUTER_SLUGS: Tuple[str, ...] = (
    "openrouter/free",
)

_DEAD_TTL_S = 300.0   # a route proven dead is skipped for 5 minutes
_LIVE_TTL_S = 600.0   # a route proven live is trusted for 10 minutes
_ROUTE_HEALTH: Dict[str, Tuple[str, float]] = {}  # route_id -> (state, ts)


def _read_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class Route(TypedDict):
    provider: str        # dashscope | openrouter | deepseek | moonshot | nvidia
    key: str
    base_url: str
    source: str          # where the key came from (env/home .env/infra/key file)
    free_only: bool      # free-router stage: force :free slugs


def collect_routes(
    env: Optional[Dict[str, str]] = None,
    include_files: bool = True,
) -> List[Route]:
    """Gather every LLM key on this machine into an ordered failover chain.

    Order (owner directive): DashScope (all workspace keys) -> free router ->
    OpenRouter paid -> DeepSeek direct -> Moonshot direct. INFINITY_LLM_PROVIDER
    pins that provider's routes to the front instead.
    """
    e: Dict[str, str] = dict(os.environ if env is None else env)
    if include_files:
        for p in (
            Path.home() / ".env",
            Path("D:/genesis/infra/.env"),
        ):
            for k, v in _read_env_file(p).items():
                e.setdefault(k, v)

    # OpenRouter re-enabled 2026-08-10 (owner request): the free tier is the
    # default lane. The GENESIS infra key must STILL never leak in — allow the
    # key only when it came from the process env or the owner's home .env.
    _or_from_env = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    _or_home = (_read_env_file(Path.home() / ".env").get("OPENROUTER_API_KEY") or "").strip()
    if not (_or_from_env or _or_home):
        e.pop("OPENROUTER_API_KEY", None)
        e.pop("OPENROUTER_BASE_URL", None)

    routes: List[Route] = []
    seen: set = set()

    def add(provider: str, key: str, base_url: str, source: str,
            free_only: bool = False) -> None:
        key = (key or "").strip()
        if not key:
            return
        ident = (provider, key, free_only)
        if ident in seen:
            return
        seen.add(ident)
        routes.append(Route(provider=provider, key=key, base_url=base_url,
                            source=source, free_only=free_only))

    dash_base = (e.get("DASHSCOPE_BASE_URL") or DASHSCOPE_BASE_URL).strip()
    # 1. DashScope — primary workspace key plus every spare workspace key.
    add("dashscope", e.get("DASHSCOPE_API_KEY") or e.get("ALIBABA_API_KEY") or "",
        dash_base, "env")
    for i in ("", "2", "3", "4", "5"):
        wk = e.get(f"QWEN_WS_KEY{i}", "")
        if wk:
            add("dashscope", wk,
                (e.get("QWEN_WS_BASE_INTL") or dash_base).strip(),
                f"QWEN_WS_KEY{i}")
    # 2. Free router (OpenRouter key, :free slugs only) then 3. OpenRouter paid.
    or_key = (e.get("OPENROUTER_API_KEY") or "").strip()
    if or_key:
        add("openrouter", or_key, OPENROUTER_BASE_URL, "env", free_only=True)
        add("openrouter", or_key, OPENROUTER_BASE_URL, "env")
    # 4. DeepSeek direct. 5. Moonshot direct (env + key file).
    add("deepseek", e.get("DEEPSEEK_API_KEY") or "", DEEPSEEK_BASE_URL, "env")
    add("moonshot", e.get("MOONSHOT_API_KEY") or e.get("KIMI_API_KEY") or "",
        MOONSHOT_BASE_URL, "env")
    # 6. NVIDIA NIM - free credit quota, last resort (slow cold starts).
    add("nvidia", e.get("NVIDIA_API_KEY") or "", NVIDIA_BASE_URL, "env")
    if include_files:
        moon_file = Path(__file__).resolve().parents[2] / "backend" / "moonshot.key"
        if moon_file.is_file():
            try:
                add("moonshot", moon_file.read_text(encoding="utf-8").strip(),
                    MOONSHOT_BASE_URL, "moonshot.key")
            except OSError:
                pass

    # Same-named keys can differ across stores: a dead key in the process env
    # must not shadow a live one on disk. Add differing file values as their
    # own routes so every quota on the machine is usable.
    if include_files:
        for p in (Path.home() / ".env", Path("D:/genesis/infra/.env")):
            fe = _read_env_file(p)
            src = "infra .env" if "infra" in str(p) else "home .env"
            dv = (fe.get("DEEPSEEK_API_KEY") or "").strip()
            if dv and dv != e.get("DEEPSEEK_API_KEY"):
                add("deepseek", dv, DEEPSEEK_BASE_URL, src)
            mv = (fe.get("MOONSHOT_API_KEY") or fe.get("KIMI_API_KEY") or "").strip()
            if mv and mv != e.get("MOONSHOT_API_KEY"):
                add("moonshot", mv, MOONSHOT_BASE_URL, src)
            wv = (fe.get("DASHSCOPE_API_KEY") or "").strip()
            if wv and wv != e.get("DASHSCOPE_API_KEY"):
                add("dashscope", wv,
                    (fe.get("DASHSCOPE_BASE_URL") or dash_base).strip(), src)

    forced = (e.get("INFINITY_LLM_PROVIDER") or "").strip().lower()
    if forced:
        pinned = [r for r in routes if r["provider"] == forced]
        rest = [r for r in routes if r["provider"] != forced]
        routes = pinned + rest
    return routes


def route_id(route: Route) -> str:
    return f"{route['provider']}:{route['key'][:8]}…{'free' if route['free_only'] else 'paid'}"


def route_state(route: Route) -> str:
    """known-dead within TTL -> 'dead'; known-live within TTL -> 'live'; else 'unknown'."""
    state, ts = _ROUTE_HEALTH.get(route_id(route), ("unknown", 0.0))
    ttl = _DEAD_TTL_S if state == "dead" else _LIVE_TTL_S
    if state != "unknown" and (time.time() - ts) > ttl:
        return "unknown"
    return state


def _health_file() -> Optional[str]:
    """Persist route health across restarts so a freshly rebooted backend
    does not re-walk routes proven dead minutes ago (probe: dead DashScope
    keys added ~400s of latency per cold start)."""
    override = os.environ.get("INFINITY_ROUTE_HEALTH_FILE", "").strip()
    if override:
        return override
    data_dir = os.environ.get("INFINITY_DATA_DIR", "").strip()
    if data_dir:
        return str(Path(data_dir) / "route_health.json")
    return str(Path.home() / "AppData" / "Roaming" / "com.infinitycode.app" / "route_health.json")


def _save_health() -> None:
    path = _health_file()
    if not path:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(_ROUTE_HEALTH), encoding="utf-8")
    except OSError:
        pass  # persistence is best-effort; health still works in-process


def load_health() -> None:
    """Merge persisted route health into the in-process table. Entries whose
    TTL has expired are ignored (route_state() treats them as unknown)."""
    path = _health_file()
    if not path:
        return
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(saved, dict):
        with _HEALTH_LOCK:
            for rid, (state, ts) in saved.items():
                if state in ("dead", "live") and isinstance(ts, (int, float)):
                    _ROUTE_HEALTH.setdefault(rid, (state, float(ts)))


# Route health is mutated from concurrent worker threads (the network call
# and its bookkeeping now run outside _route_lock): one module lock guards
# the dict + the persisted file.
_HEALTH_LOCK = threading.Lock()


def mark_route_dead(route: Route, reason: str) -> None:
    with _HEALTH_LOCK:
        _ROUTE_HEALTH[route_id(route)] = ("dead", time.time())
        _save_health()
    logger.warning("Provider route %s marked DEAD for %.0fs: %s",
                   route_id(route), _DEAD_TTL_S, reason[:160])


def mark_route_live(route: Route) -> None:
    with _HEALTH_LOCK:
        _ROUTE_HEALTH[route_id(route)] = ("live", time.time())
        _save_health()


_FATAL_MARKERS: Tuple[str, ...] = (
    "401", "402", "403", "404", "user not found", "invalid api key",
    "incorrect api key", "unauthorized", "access denied", "quota",
    "exhausted", "insufficient", "balance", "suspended", "not exist",
    "model not found", "rate limit", "429", "usage limit", "billing",
)


def is_upstream_provider_error(exc: BaseException) -> bool:
    """True when the aggregator relayed a failure from one of its upstream
    providers (OpenRouter metadata: 'Provider returned error'). The route
    itself is fine — a different upstream answers next try, so this is
    transient, never route-fatal. (Stress 2026-08-10: Novita
    NOT_ENOUGH_BALANCE killed the free route for 300s otherwise.)"""
    return "provider returned error" in str(exc).lower()


_PROVIDER_BALANCE_MARKERS: Tuple[str, ...] = (
    "not_enough_balance", "not enough balance", "insufficient balance",
    "insufficient_balance", "insufficient funds",
)


_CONTEXT_OVERFLOW_MARKERS: Tuple[str, ...] = (
    "context length", "context_length", "maximum context",
    "too many tokens", "input is too long", "prompt is too long",
    "reduce the length", "max_tokens", "request too large",
)


def is_context_overflow(exc: BaseException) -> bool:
    """True when the upstream rejected the REQUEST as too big for the
    model window (400-class). No retry -- on any route, with the same
    prompt -- can heal it; the caller must shrink the conversation."""
    return any(m in str(exc).lower() for m in _CONTEXT_OVERFLOW_MARKERS)


def is_route_fatal(exc: BaseException, free_only: bool = False):
    """True when the error says this ROUTE is unusable (auth/quota/model),
    as opposed to a transient transport problem worth retrying in place.

    free_only stages relay random upstream providers: an upstream balance
    failure there is transient (the meta router picks another upstream next
    try). Direct/paid stages answer for one account, so a relayed
    NOT_ENOUGH_BALANCE is account-level and no retry can heal it -- fatal,
    so the chain advances immediately instead of burning the retry budget
    + backoff on a dead route (live failover run 2026-08-11)."""
    text = str(exc).lower()
    if is_context_overflow(exc):
        # Fuzz 2026-08-11: an oversized prompt fails identically on every
        # route -- fatal everywhere so the walk fails fast with the real
        # reason instead of burning retries/backoff on a guaranteed re-fail.
        return True
    if is_upstream_provider_error(exc):
        if free_only:
            return False
        return any(m in text for m in _PROVIDER_BALANCE_MARKERS)
    return any(m in text for m in _FATAL_MARKERS)


def reset_route_health() -> int:
    """Ops helper: clear persisted route-health state so routes marked
    DEAD are re-probed immediately instead of after _DEAD_TTL_S. Returns
    the number of cleared entries. (CLI `routes-reset`, DX 2026-08-11.)"""
    with _HEALTH_LOCK:
        n = len(_ROUTE_HEALTH)
        _ROUTE_HEALTH.clear()
        _save_health()
    return n


def routes_health_report(routes: List[Route]) -> List[Dict[str, Any]]:
    return [
        {
            "provider": r["provider"],
            "source": r["source"],
            "key": r["key"][:6] + "…" + r["key"][-4:] if len(r["key"]) > 12 else "****",
            "stage": "free-router" if r["free_only"] else "paid",
            "state": route_state(r),
        }
        for r in routes
    ]

# USD cost per million tokens: model_id -> (input, output).
# Models not listed here are billed at 0.0 (e.g. subscription-covered fallbacks).
MODEL_PRICING_USD_PER_MILLION: Dict[str, Tuple[float, float]] = {
    "deepseek/deepseek-v4-flash": (0.14, 0.28),
    "deepseek/deepseek-v4-pro": (0.56, 1.68),
    "moonshotai/kimi-k3": (2.00, 8.00),
    "qwen/qwen3.7-max": (1.40, 7.50),
    "qwen/qwen3-coder": (0.70, 2.10),
    "qwen/qwen3-vl-30b-a3b-thinking": (0.15, 0.45),
    "google/gemini-3.1-flash-image": (0.0, 0.0),
    "qwen/qwen-2.5-72b-instruct": (0.40, 1.20),
    "google/gemini-3.1-flash-lite": (0.25, 1.50),
    "z-ai/glm-5.2": (0.85, 2.50),
    "moonshotai/kimi-k2.7-code": (0.72, 3.49),
    "moonshotai/kimi-k2.6": (0.66, 3.41),
    "minimax/minimax-m3": (0.30, 1.20),
    "qwen/qwen3-coder-plus": (0.65, 3.25),
    "deepseek/deepseek-v3.2": (0.21, 0.32),
    "deepseek/deepseek-chat": (0.50, 2.00),
    "deepseek/deepseek-coder": (0.50, 2.00),
    "deepseek/deepseek-reasoner": (0.55, 2.19),
    # OpenRouter free tier: the meta router and all current :free catalog
    # models cost 0 (verified against the live /models catalog 2026-08-10;
    # unknown :free slugs bill 0.0 by the default rule anyway).
    "openrouter/free": (0.0, 0.0),
    "openai/gpt-5.6-sol": (0.0, 0.0),
    "anthropic/claude-opus-4.6": (0.0, 0.0),
}


class ChatResult(TypedDict):
    """Result of a single chat/vision call."""

    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached: bool  # True when served from the disk response cache (cost 0)


class ImageResult(TypedDict):
    """Result of a single image-generation call."""

    text: str
    image_base64: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


class OpenRouterError(RuntimeError):
    """Raised when an OpenRouter call fails after all retries."""


class StreamWrapper:
    """Iterable wrapper returned by chat_stream().

    Iterating yields the same deltas the raw generator would. After the
    iteration ends, `stream_error` holds the exception that cut the stream
    short mid-answer (None when the stream finished cleanly), so callers
    can tell a truncated response apart from a complete one.
    """

    def __init__(self) -> None:
        self._gen: Iterator[Any] = iter(())
        self.stream_error: Optional[BaseException] = None

    def __iter__(self) -> Iterator[Any]:
        return self._gen


class OpenRouterClient:
    """Thin wrapper over openai.OpenAI pointed at OpenRouter.

    Provides chat, vision-chat, and image generation with retry, timeout,
    and per-call cost accounting.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        session_logger: Optional[SessionLogger] = None,
    ) -> None:
        self._timeout = timeout
        self._max_retries: int = max(1, int(max_retries))
        # Hard cap on the whole failover walk (all routes): a caller must
        # never wait longer than this for an answer, regardless of how many
        # routes are slow/dead. 0 disables the cap.
        self._walk_timeout: float = float(
            os.environ.get("INFINITY_WALK_TIMEOUT_S", "120") or 0
        )
        self._routes: List[Route] = []
        self._clients: Dict[str, Any] = {}
        if not base_url:
            self._routes = collect_routes()
        if not self._routes:
            # Legacy single-route mode: explicit base override, or a caller
            # that passed an api_key directly (Settings UI flow).
            provider, resolved_key, resolved_base = resolve_llm_provider(api_key)
            if base_url:  # explicit override always wins
                resolved_base = base_url
            self._routes = [
                Route(provider=provider, key=resolved_key,
                      base_url=resolved_base, source="legacy", free_only=False)
            ]
        load_health()
        self._route_idx = self._first_non_dead_index()
        # One client is shared by concurrent swarm workers; route-state
        # mutation (_route_idx/_client/provider) serializes on this lock.
        # The network call itself runs UNLOCKED — holding the lock across
        # the wire serialized every concurrent call (stress 2026-08-10:
        # p50 129.6s pure queueing at N=50).
        self._route_lock = threading.Lock()
        route = self._routes[self._route_idx]
        self.provider: str = route["provider"]
        self._client: Any = self._client_for(route)
        self.total_cost_usd: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.call_log: List[Dict[str, Any]] = []
        self.session_logger: Optional[SessionLogger] = session_logger

    # ------------------------------------------------------------------ #
    # Failover chain helpers
    # ------------------------------------------------------------------ #

    def _client_for(self, route: Route) -> Any:
        rid = route_id(route)
        client = self._clients.get(rid)
        if client is None:
            default_headers = {
                "HTTP-Referer": "https://infinity-code.local",
                "X-Title": "Infinity Code",
            }
            # Native Qwen hosts (DashScope) cache the prompt server-side when
            # opted in via this header; cached input tokens bill discounted.
            if route["provider"] == "dashscope":
                default_headers["x-dashscope-session-cache"] = "enable"
            client = OpenAI(
                api_key=route["key"],
                base_url=route["base_url"],
                timeout=self._timeout,
                # The walk owns retries/failover; SDK-internal retries on a
                # dead route multiplied latency by ~3x per route (probe:
                # ~400s end-to-end). One attempt per route keeps dead-route
                # walks bounded while the chain still falls through.
                max_retries=1,
                default_headers=default_headers,
            )
            self._clients[rid] = client
        return client

    def _first_non_dead_index(self) -> int:
        for i, r in enumerate(self._routes):
            if route_state(r) != "dead":
                return i
        return 0

    def _advance_route(
        self, reason: BaseException, failed_route: Optional[Route] = None
    ) -> bool:
        """Mark the current route dead and move to the next non-dead one.

        Returns False when every route is now known-dead (or there is only
        one route), meaning there is nothing left to swap to.

        failed_route: when given, a concurrent worker has already failed this
        route over if the current index moved on — do not mark the new
        current route dead for someone else's error, just retry there.
        """
        current = self._routes[self._route_idx]
        if (failed_route is not None
                and route_id(current) != route_id(failed_route)):
            return True
        mark_route_dead(current, str(reason))
        n = len(self._routes)
        for step in range(1, n):
            idx = (self._route_idx + step) % n
            if route_state(self._routes[idx]) != "dead":
                self._route_idx = idx
                route = self._routes[idx]
                self.provider = route["provider"]
                self._client = self._client_for(route)
                logger.info("Failover: swapped LLM route to %s", route_id(route))
                return True
        return False

    def _model_for(self, model_id: str, route: Route) -> str:
        """Normalize the requested model for the route's provider; the
        free-router stage forces zero-cost slugs."""
        # Every hardcoded :free slug is 404 on OpenRouter now (verified
        # 2026-08-10); the free-router stage always uses the meta router.
        if route["free_only"]:
            model_id = FREE_ROUTER_SLUGS[0]
        return normalize_model_id(model_id, route["provider"])

    @staticmethod
    def _cache_control_messages(
        messages: List[Dict[str, Any]], provider: str
    ) -> List[Dict[str, Any]]:
        """Mark the leading system message for providers with explicit
        prompt caching (OpenRouter ephemeral cache). Caching is prefix-based:
        the static system prompt must stay byte-identical turn to turn, so we
        only touch role=system and never the dynamic tail. Other providers
        cache automatically or via headers and pass through untouched."""
        if provider != "openrouter":
            return messages
        out = [dict(m) for m in messages]
        for m in out:
            if m.get("role") == "system":
                m["cache_control"] = {"type": "ephemeral"}
                break
        return out

    def health(self) -> List[Dict[str, Any]]:
        """Per-route liveness for the /api/v1/providers/health endpoint."""
        report = routes_health_report(self._routes)
        for i, entry in enumerate(report):
            entry["active"] = i == self._route_idx
        return report

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _maybe_log_turn(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        output: str,
        model: str,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        lane: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a turn to the session logger if one is configured."""
        if self.session_logger is None:
            return
        try:
            self.session_logger.log_turn(
                session_id=session_id,
                messages=list(messages),
                output=output,
                model=model,
                lane=lane,
                cost_usd=cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                metadata=metadata or {"source": "openrouter_client"},
            )
        except Exception as exc:  # noqa: BLE001 - logging must never break calls
            logger.warning("Session logging failed: %s", exc)

    def _cost_usd(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        cost_in, cost_out = MODEL_PRICING_USD_PER_MILLION.get(model_id, (0.0, 0.0))
        return (input_tokens * cost_in / 1_000_000.0) + (
            output_tokens * cost_out / 1_000_000.0
        )

    @staticmethod
    def _billing_model_id(served: str, requested: str) -> str:
        """Billing key: prefer the route-mapped model that actually served
        the call (free stage maps any request to openrouter/free -> $0).
        Native-provider ids are the same model without the vendor prefix
        and miss the pricing table, so fall back to the requested id."""
        if served and served in MODEL_PRICING_USD_PER_MILLION:
            return served
        return requested or served

    def _record(
        self, model_id: str, kind: str, input_tokens: int, output_tokens: int
    ) -> float:
        cost = self._cost_usd(model_id, input_tokens, output_tokens)
        spike = float(os.environ.get("INFINITY_COST_SPIKE_USD", "2.0") or 0)
        if spike > 0 and cost > spike:
            logger.warning(
                "COST SPIKE: %s call cost $%.4f (> $%.2f threshold). Model: %s",
                kind, cost, spike, model_id,
            )
        self.total_cost_usd += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.call_log.append(
            {
                "model": model_id,
                "kind": kind,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
            }
        )
        # Credit Engine: every metered call debits the wallet once (deduped).
        credits_meter(cost, model_id, str(kind), input_tokens, output_tokens)
        return cost

    def _record_cached(
        self, model_id: str, kind: str, input_tokens: int, output_tokens: int
    ) -> None:
        """Book a cache hit: zero cost, but totals + call_log stay honest so
        usage_report() reflects real (zero) spend on repeats."""
        self.total_cost_usd += 0.0
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.call_log.append(
            {
                "model": model_id,
                "kind": kind,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": 0.0,
                "cached": True,
            }
        )
        # Cache hits cost zero credits but count as savings in analytics.
        avoided = self._cost_usd(model_id, input_tokens, output_tokens)
        credits_meter(0.0, model_id, str(kind) + "_cached",
                      input_tokens, output_tokens, avoided_usd=avoided)

    def _create_completion(self, **kwargs: Any) -> Tuple[Any, str]:
        """Call chat.completions.create with retry + provider failover.
        Returns (response, effective_model).

        Transient transport errors retry in place with backoff; fatal
        per-route errors (auth/quota/model-missing) mark the route dead and
        swap to the next quota in the chain. Only when every route is dead
        does this raise — a chat can never fail while any quota lives.
        """
        original_messages = kwargs.get("messages")
        last_error: Optional[Exception] = None
        return self._walk_routes(kwargs, original_messages, last_error)

    def _prepare_route_kwargs(
        self, kwargs: Dict[str, Any], original_messages: Any
    ) -> Any:
        """Snapshot the current route and adapt kwargs for it. Caller must
        hold _route_lock (reads _route_idx, which failover mutates)."""
        route = self._routes[self._route_idx]
        if original_messages is not None:
            kwargs["messages"] = self._cache_control_messages(
                original_messages, route["provider"]
            )
        if "model" in kwargs:
            kwargs["model"] = self._model_for(kwargs["model"], route)
            # K3 hard-fails on any temperature != 1 (thinking-only). Force
            # it, regardless of what the caller passed, so upstream code
            # that always sends 0.7 still works.
            if is_thinking_only(kwargs["model"]):
                kwargs["temperature"] = 1
                # Thinking eats most of the token budget. Callers who ask
                # for 20 max_tokens on a chit-chat reply would get empty
                # content because reasoning consumed all of it. Enforce a
                # floor so actual answer text has room.
                if int(kwargs.get("max_tokens", 0) or 0) < 4000:
                    kwargs["max_tokens"] = 4000
            # BFB workhorse: flash thinks silently and burns the whole
            # budget (harness-measured ~35s + empty content). Non-reasoning
            # flash calls on the DeepSeek route disable thinking for ~2s
            # answers; other routes never receive the param.
            if (route["provider"] == "deepseek"
                    and str(kwargs["model"]) == "deepseek-v4-flash"
                    and "thinking" not in (kwargs.get("extra_body") or {})):
                extra = dict(kwargs.get("extra_body") or {})
                extra["thinking"] = {"type": "disabled"}
                kwargs["extra_body"] = extra
        return route

    def _walk_deadline_error(
        self, walk_started: float, last_error: Optional[Exception]
    ) -> "OpenRouterError":
        return OpenRouterError(
            f"Failover walk exceeded {self._walk_timeout:.0f}s across "
            f"{len(self._routes)} routes (last error: {last_error}). "
            f"Health: {routes_health_report(self._routes)}"
        )

    def _walk_routes(
        self,
        kwargs: Dict[str, Any],
        original_messages: Any,
        last_error: Optional[Exception],
    ) -> Tuple[Any, str]:
        """Walk the failover chain for one completion/stream call.

        Returns (response, effective_model): the route-mapped model that
        actually served the call, so billing attributes free-stage answers
        to openrouter/free ($0) even when a priced model was requested.
        The lock guards route-state mutation only; the network call runs
        unlocked so concurrent chats do not serialize (stress 2026-08-10).
        """
        _walk_started = time.monotonic()
        for _ in range(len(self._routes) + 1):
            if self._walk_timeout > 0 and (time.monotonic() - _walk_started) > self._walk_timeout:
                raise self._walk_deadline_error(_walk_started, last_error)
            with self._route_lock:
                route = self._prepare_route_kwargs(kwargs, original_messages)
                client = self._client
            fatal: Optional[Exception] = None
            for attempt in range(self._max_retries):
                if self._walk_timeout > 0 and (time.monotonic() - _walk_started) > self._walk_timeout:
                    raise self._walk_deadline_error(_walk_started, last_error)
                try:
                    response = client.chat.completions.create(**kwargs)
                    mark_route_live(route)
                    return response, str(kwargs.get("model", ""))
                except Exception as exc:  # noqa: BLE001 - classified below
                    last_error = exc
                    if is_route_fatal(exc, free_only=bool(route.get("free_only"))):
                        fatal = exc
                        break
                    wait = RETRY_BACKOFF_SECONDS * (2**attempt) * random.uniform(0.5, 1.5)
                    logger.warning(
                        "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, self._max_retries, exc, wait,
                    )
                    if attempt < self._max_retries - 1:
                        time.sleep(wait)
            # Exhausted this route (fatally or transiently): swap and continue.
            with self._route_lock:
                advanced = self._advance_route(
                    fatal or last_error or Exception("route exhausted"),
                    failed_route=route)
            if not advanced:
                break
        raise OpenRouterError(
            f"All LLM routes failed ({len(self._routes)} in chain): {last_error}. "
            f"Health: {routes_health_report(self._routes)}"
        ) from last_error

    def _open_stream(self, **kwargs: Any) -> Tuple[Any, str]:
        """Open a streaming completion with the same retry + failover policy.
        Returns (stream, effective_model)."""
        original_messages = kwargs.get("messages")
        last_error: Optional[Exception] = None
        return self._walk_routes(kwargs, original_messages, last_error)

    @staticmethod
    def _usage_tokens(response: Any) -> Tuple[int, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0
        try:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            return input_tokens, output_tokens
        except (TypeError, ValueError):
            return 0, 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def embed(
        self, texts: List[str], model_id: str = "openai/text-embedding-3-small"
    ) -> List[List[float]]:
        """Return an embedding vector per input text (empty list on failure).

        Chain: DashScope/OpenRouter -> HF Inference API -> empty.
        """
        try:
            emb_model = model_id
            if self.provider == "dashscope":
                emb_model = "text-embedding-v4"  # DashScope-native embeddings
            resp = self._client.embeddings.create(model=emb_model, input=texts)
            return [list(d.embedding) for d in resp.data]
        except Exception as exc:  # noqa: BLE001
            logger.warning("embed() primary failed for %s: %s", model_id, exc)
        # Fallback: HuggingFace Inference API (free, no extra deps).
        try:
            from backend.core.local_rag import HFEmbedder
            hf = HFEmbedder()
            return hf.embed(texts)
        except Exception:
            pass
        try:
            from core.local_rag import HFEmbedder
            hf = HFEmbedder()
            return hf.embed(texts)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("embed() HF fallback also failed: %s", exc2)
            return []

    def chat(
        self,
        model_id: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4000,
        extra_body: Optional[Dict[str, Any]] = None,
        max_continuations: int = 4,
        session_id: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> ChatResult:
        """Plain text chat completion with auto-continuation.

        If the model stops on finish_reason=="length" (truncated), the
        truncated turn is appended plus a "continue exactly" instruction, and
        the pieces are concatenated — up to max_continuations rounds. Ported
        from the proven GENESIS llm_client pattern; long generations stop
        arriving half-finished.
        """
        start = time.time()
        # Response cache: exact repeats are free. Keyed on the first route in
        # the failover chain (deterministic) + the full request shape.
        route = self._routes[self._first_non_dead_index()]
        cache_args: Dict[str, Any] = dict(
            provider=route["provider"], base_url=route["base_url"],
            model=model_id, messages=messages, max_tokens=max_tokens,
            temperature=0.2, extra_body=extra_body or {},
        )
        cached_reply: Optional[Dict[str, Any]] = None
        if llm_cache.should_cache(messages, 0.2, max_tokens):
            cached_reply = llm_cache.lookup(**cache_args)
        if cached_reply is not None:
            result = ChatResult(
                text=cached_reply["text"],
                cost_usd=0.0,
                input_tokens=cached_reply["input_tokens"],
                output_tokens=cached_reply["output_tokens"],
                cached=True,
            )
            self.last_call_cached = True
            self._record_cached(
                model_id, "chat", result["input_tokens"], result["output_tokens"])
            if session_id:
                latency = (time.time() - start) * 1000
                self._maybe_log_turn(
                    session_id=session_id,
                    messages=messages,
                    output=result["text"],
                    model=model_id,
                    cost_usd=0.0,
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                    latency_ms=latency,
                    lane=lane,
                    metadata={"source": "chat", "provider": self.provider, "cached": True},
                )
            return result
        self.last_call_cached = False
        convo = list(messages)
        full_text = ""
        total_in = 0
        total_out = 0
        total_cost = 0.0
        rounds = 0
        retried_empty = False
        while True:
            try:
                response, served_model = self._create_completion(
                    model=model_id,
                    messages=convo,
                    max_tokens=max_tokens,
                    extra_body=extra_body or {},
                )
            except OpenRouterError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise OpenRouterError(f"chat() failed for {model_id}: {exc}") from exc

            piece = ""
            finish = None
            try:
                msg = response.choices[0].message
                # Reasoning models (deepseek-v4-*) can return their answer in
                # reasoning_content with content empty — never treat that as
                # "no response".
                piece = msg.content or getattr(msg, "reasoning_content", None) or ""
                finish = getattr(response.choices[0], "finish_reason", None)
            except (AttributeError, IndexError, TypeError) as exc:
                # TypeError: upstream occasionally returns choices=None under
                # concurrent free-tier load (live repro 2026-08-11).
                logger.error("Malformed chat response from %s: %s", model_id, exc)

            full_text += piece
            i_tok, o_tok = self._usage_tokens(response)
            total_in += i_tok
            total_out += o_tok
            total_cost += self._record(self._billing_model_id(served_model, model_id), "chat", i_tok, o_tok)

            # Free-router trap (stress 2026-08-10: 30/49 empties): the meta
            # router dispatches to thinking-only models that consume a small
            # max_tokens budget entirely on reasoning and return no content.
            # One bounded retry with a real token floor recovers the answer.
            if not full_text and not retried_empty and int(max_tokens or 0) < 4000:
                retried_empty = True
                logger.info(
                    "chat(): empty reply from %s (finish=%s) — retrying once "
                    "with max_tokens=4000 (thinking-model budget trap)",
                    model_id, finish,
                )
                max_tokens = 4000
                continue
            if finish != "length" or rounds >= max_continuations or not piece:
                break
            rounds += 1
            logger.info("chat(): continuing truncated output (round %d)", rounds)
            convo = convo + [
                {"role": "assistant", "content": piece},
                {"role": "user", "content":
                    "Continue exactly from where you stopped. Do not repeat "
                    "anything already written; no preamble."},
            ]

        result = ChatResult(
            text=full_text,
            cost_usd=total_cost,
            input_tokens=total_in,
            output_tokens=total_out,
            cached=False,
        )
        if llm_cache.should_cache(messages, 0.2, max_tokens):
            llm_cache.store(
                provider=route["provider"], base_url=route["base_url"],
                model=model_id, messages=messages, max_tokens=max_tokens,
                temperature=0.2, text=result["text"],
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                extra_body=extra_body or {},
            )
        if session_id:
            latency = (time.time() - start) * 1000
            self._maybe_log_turn(
                session_id=session_id,
                messages=messages,
                output=result["text"],
                model=model_id,
                cost_usd=result["cost_usd"],
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                latency_ms=latency,
                lane=lane,
                metadata={"source": "chat", "provider": self.provider, "continuations": rounds},
            )
        return result

    def chat_tools(
        self,
        model_id: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 1500,
    ) -> Any:
        """One tool-calling turn. Returns the raw message (has .content and
        .tool_calls). Raises OpenRouterError on failure so callers can fall
        back to a plain answer."""
        try:
            response, served_model = self._create_completion(
                model=model_id,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=max_tokens,
            )
            self.last_stream_usage = None
            i, o = self._usage_tokens(response)
            self._record(self._billing_model_id(served_model, model_id), "chat", i, o)
            return response.choices[0].message
        except OpenRouterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterError(f"chat_tools() failed for {model_id}: {exc}") from exc

    def chat_stream(
        self,
        model_id: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4000,
        extra_body: Optional[Dict[str, Any]] = None,
        yield_reasoning: bool = False,
        session_id: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> StreamWrapper:
        """Stream a chat completion, yielding text deltas as they arrive.

        With yield_reasoning=True, yields ("reasoning", delta) and
        ("content", delta) tuples so callers can show thinking progress
        instead of dead air on reasoning models. Default yields plain str
        content deltas (reasoning silently skipped) for compatibility.

        Raises OpenRouterError if the stream cannot be opened (so callers can
        fall back before any tokens have been sent). Errors mid-stream stop
        the iteration cleanly and are recorded on the returned wrapper's
        `stream_error` so callers can surface the interruption instead of
        treating a truncated answer as complete.
        """
        start = time.time()
        # Response cache: a repeat of the same streamed prompt renders
        # instantly from disk instead of re-billing the provider.
        route = self._routes[self._first_non_dead_index()]
        cache_args: Dict[str, Any] = dict(
            provider=route["provider"], base_url=route["base_url"],
            model=model_id, messages=messages, max_tokens=max_tokens,
            temperature=0.2, extra_body=extra_body or {},
        )
        wrapper = StreamWrapper()
        if llm_cache.should_cache(messages, 0.2, max_tokens):
            cached_reply = llm_cache.lookup(**cache_args)
            if cached_reply is not None:
                self.last_stream_usage = {
                    "prompt_tokens": cached_reply["input_tokens"],
                    "completion_tokens": cached_reply["output_tokens"],
                    "cost": 0.0,
                }
                self.last_call_cached = True
                self._record_cached(
                    model_id, "chat", cached_reply["input_tokens"],
                    cached_reply["output_tokens"])

                def _cached_gen():
                    text = cached_reply["text"]
                    if yield_reasoning:
                        yield ("content", text)
                    else:
                        yield text
                    if session_id:
                        latency = (time.time() - start) * 1000
                        self._maybe_log_turn(
                            session_id=session_id,
                            messages=messages,
                            output=text,
                            model=model_id,
                            cost_usd=0.0,
                            input_tokens=cached_reply["input_tokens"],
                            output_tokens=cached_reply["output_tokens"],
                            latency_ms=latency,
                            lane=lane,
                            metadata={"source": "chat_stream", "provider": self.provider, "cached": True},
                        )

                wrapper._gen = _cached_gen()
                return wrapper
        self.last_call_cached = False
        stream, served_model = self._open_stream(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            extra_body=extra_body or {},
        )

        self.last_stream_usage = None
        full_text = ""
        wrapper = StreamWrapper()

        def _gen():
            nonlocal full_text
            reasoning_buf = ""
            try:
                for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        # Final chunk carries usage when extra_body includes
                        # {"usage": {"include": True}} (OpenRouter extension).
                        try:
                            self.last_stream_usage = {
                                "prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": usage.completion_tokens,
                                "cost": getattr(usage, "cost", None),
                            }
                        except AttributeError:
                            self.last_stream_usage = None
                    try:
                        d = chunk.choices[0].delta
                    except (AttributeError, IndexError, TypeError):
                        # TypeError: a chunk with choices=None (seen under
                        # concurrent free-tier load) -- skip, keep streaming.
                        continue
                    if yield_reasoning:
                        thinking = getattr(d, "reasoning", None) or getattr(
                            d, "reasoning_content", None)
                        if thinking:
                            reasoning_buf += str(thinking)
                            yield ("reasoning", thinking)
                    delta = getattr(d, "content", None)
                    if delta:
                        full_text += str(delta)
                        yield ("content", delta) if yield_reasoning else delta
                if not full_text and reasoning_buf:
                    # Reasoning-only stream: deliver the thinking rather than
                    # silence — a thin answer beats "no response".
                    full_text = reasoning_buf
                    yield ("content", reasoning_buf) if yield_reasoning else reasoning_buf
            except Exception as exc:  # noqa: BLE001 - end the stream cleanly on error
                logger.warning("Stream from %s ended early: %s", model_id, exc)
                # Record the failure so the caller can tell the answer was cut
                # short mid-stream instead of persisting it as complete.
                wrapper.stream_error = exc
            finally:
                if session_id:
                    latency = (time.time() - start) * 1000
                    usage = self.last_stream_usage or {}
                    input_tokens = int(usage.get("prompt_tokens") or 0)
                    output_tokens = int(usage.get("completion_tokens") or 0)
                    cost = usage.get("cost")
                    cost_usd = float(cost) if cost is not None else self._cost_usd(
                        model_id, input_tokens, output_tokens
                    )
                    self._maybe_log_turn(
                        session_id=session_id,
                        messages=messages,
                        output=full_text,
                        model=model_id,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=latency,
                        lane=lane,
                        metadata={"source": "chat_stream", "provider": self.provider},
                    )

        wrapper._gen = _gen()
        return wrapper

    def chat_with_vision(
        self,
        model_id: str,
        messages_with_images: List[Dict[str, Any]],
        max_tokens: int = 2000,
        extra_body: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> ChatResult:
        """Vision chat (e.g. Qwen3-VL). Messages carry image_url content parts."""
        start = time.time()
        try:
            response, served_model = self._create_completion(
                model=model_id,
                messages=messages_with_images,
                max_tokens=max_tokens,
                extra_body=extra_body or {},
            )
        except OpenRouterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterError(
                f"chat_with_vision() failed for {model_id}: {exc}"
            ) from exc

        text: str = ""
        try:
            text = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            logger.error("Malformed vision response from %s: %s", model_id, exc)

        input_tokens, output_tokens = self._usage_tokens(response)
        cost = self._record(self._billing_model_id(served_model, model_id), "vision", input_tokens, output_tokens)
        result = ChatResult(
            text=text,
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached=False,
        )
        if session_id:
            latency = (time.time() - start) * 1000
            self._maybe_log_turn(
                session_id=session_id,
                messages=messages_with_images,
                output=result["text"],
                model=model_id,
                cost_usd=result["cost_usd"],
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                latency_ms=latency,
                lane=lane,
                metadata={"source": "chat_with_vision", "provider": self.provider},
            )
        return result

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model_id: str = "google/gemini-3.1-flash-image",
        session_id: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> ImageResult:
        """Generate an image via OpenRouter's multimodal chat endpoint.

        Returns base64 image data (empty string if the model returned none).
        """
        start = time.time()
        messages = [{"role": "user", "content": prompt}]
        try:
            response, served_model = self._create_completion(
                model=model_id,
                messages=messages,
                max_tokens=1024,
                extra_body={"modalities": ["image", "text"], "size": size},
            )
        except OpenRouterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterError(
                f"generate_image() failed for {model_id}: {exc}"
            ) from exc

        text: str = ""
        image_base64: str = ""
        try:
            message = response.choices[0].message
            text = message.content or ""
            images = getattr(message, "images", None)
            if not images:
                extra = getattr(message, "model_extra", None) or {}
                images = extra.get("images") if isinstance(extra, dict) else None
            if images:
                first = images[0]
                url: str = ""
                if isinstance(first, dict):
                    url = str(first.get("image_url", {}).get("url", ""))
                else:
                    image_url_obj = getattr(first, "image_url", None)
                    url = str(getattr(image_url_obj, "url", "") or "")
                if url.startswith("data:") and "," in url:
                    image_base64 = url.split(",", 1)[1]
                else:
                    image_base64 = url
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            logger.error("Could not extract image from %s response: %s", model_id, exc)

        input_tokens, output_tokens = self._usage_tokens(response)
        cost = self._record(self._billing_model_id(served_model, model_id), "image_gen", input_tokens, output_tokens)
        result = ImageResult(
            text=text,
            image_base64=image_base64,
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if session_id:
            latency = (time.time() - start) * 1000
            self._maybe_log_turn(
                session_id=session_id,
                messages=messages,
                output=result["text"],
                model=model_id,
                cost_usd=result["cost_usd"],
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                latency_ms=latency,
                lane=lane,
                metadata={"source": "generate_image", "provider": self.provider, "size": size},
            )
        return result

    @staticmethod
    def save_image(image_base64: str, output_path: Path) -> Path:
        """Decode a base64 image string and write it to disk."""
        if not image_base64:
            raise ValueError("image_base64 is empty — nothing to save.")
        try:
            data: bytes = base64.b64decode(image_base64)
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(data)
            return output_path
        except (OSError, ValueError) as exc:
            raise OpenRouterError(f"Failed to save image to {output_path}: {exc}") from exc

    def usage_report(self) -> Dict[str, Any]:
        """Cumulative usage across the life of this client instance."""
        per_model: Dict[str, float] = {}
        try:
            for entry in self.call_log:
                model = str(entry.get("model", "unknown"))
                per_model[model] = per_model.get(model, 0.0) + float(
                    entry.get("cost_usd", 0.0)
                )
        except (TypeError, ValueError) as exc:
            logger.error("Failed to build usage breakdown: %s", exc)
        return {
            "total_cost_usd": self.total_cost_usd,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "calls": len(self.call_log),
            "cost_by_model_usd": per_model,
        }


_MAX_IMAGE_BYTES = 1_000_000  # ~1MB; beyond this we downscale before sending
_MAX_IMAGE_SIDE = 1280


def downscale_image_bytes(data: bytes, max_side: int = _MAX_IMAGE_SIDE) -> bytes:
    """Shrink an oversized image to at most max_side px (JPEG q85). Returns
    the original bytes when PIL is unavailable or the decode fails — vision
    review degrades to 'expensive', never 'broken'."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        (img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img).save(
            buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - keep the original payload as fallback
        return data


def encode_image_base64(image_path: Path) -> str:
    """Read an image file and return its base64 string (no data-URI prefix).
    Images over ~1MB are downscaled first so vision calls stay cheap."""
    try:
        data = Path(image_path).read_bytes()
        if len(data) > _MAX_IMAGE_BYTES:
            data = downscale_image_bytes(data)
        return base64.b64encode(data).decode("utf-8")
    except OSError as exc:
        raise OpenRouterError(f"Cannot read image {image_path}: {exc}") from exc


__all__ = [
    "OpenRouterClient",
    "OpenRouterError",
    "ChatResult",
    "ImageResult",
    "MODEL_PRICING_USD_PER_MILLION",
    "encode_image_base64",
]

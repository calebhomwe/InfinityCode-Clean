"""Safe economic / arbitrage engine for Infinity Code.

Read-only pricing intelligence. Pulls live per-token prices from OpenRouter's
public ``/models`` endpoint, detects a locally running LM Studio server, and
ranks the council by capability-per-dollar so the swarm can pick the cheapest
capable model for a job.

This is purely observational: it never farms accounts, never rotates keys, and
never mutates any remote state. Every network / parse step is wrapped so no
public method can raise — callers always get a safe default.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

try:
    from backend.core.router import ModelRouter, ModelSpec
    from backend.tools.openrouter_client import OpenRouterClient
except ImportError:  # running with backend/ as the working directory
    from core.router import ModelRouter, ModelSpec  # type: ignore[no-redef]
    from tools.openrouter_client import OpenRouterClient  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL: str = "https://openrouter.ai/api/v1/models"
LOCAL_ENDPOINT: str = "http://localhost:1234/v1"
LOCAL_MODELS_URL: str = "http://localhost:1234/v1/models"

PRICING_CACHE_TTL_SECONDS: float = 300.0
LIVE_FETCH_TIMEOUT_SECONDS: float = 10.0
LOCAL_DETECT_TIMEOUT_SECONDS: float = 1.5

_USER_AGENT: str = "InfinityCode/1.0 (+https://infinity-code.local)"

# Per-role capability strength (higher = stronger reasoning/coding).
ROLE_STRENGTH: Dict[str, int] = {
    "architect": 9,
    "creative": 9,
    "debugger": 9,
    "engineer": 8,
    "eye": 7,
    "worker": 6,
    "artist": 6,
}
_DEFAULT_STRENGTH: int = 5
_CAPABLE_STRENGTH_THRESHOLD: int = 7
_LOCAL_STRENGTH: int = 6
_LOCAL_CAP_PER_DOLLAR: float = 9999.0
_MIN_PRICE_FLOOR: float = 0.01
_TOKENS_PER_MILLION: float = 1_000_000.0


def _num(value: Any) -> Optional[float]:
    """Best-effort float parse; returns None on anything non-numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class EconomicsEngine:
    """Read-only pricing + local-model detection + capability/dollar ranking."""

    def __init__(self, client: OpenRouterClient, router: ModelRouter) -> None:
        self.client: OpenRouterClient = client
        self.router: ModelRouter = router
        # 300s cache for live pricing, guarded by a monotonic-ish clock with an
        # int-counter fallback so a broken time source can never wedge the cache.
        self._pricing_cache: Optional[Dict[str, Tuple[float, float]]] = None
        self._pricing_cache_at: float = 0.0
        self._pricing_calls: int = 0

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _monotonic(self) -> float:
        """Monotonic-ish clock. Falls back to the fetch counter if time fails."""
        try:
            return float(time.monotonic())
        except Exception:  # noqa: BLE001 - a clock error must never break caching
            return float(self._pricing_calls)

    @staticmethod
    def _loads_tolerant(raw: Any) -> Optional[Any]:
        """Parse JSON, tolerating truncation/garbage via a regex blob fallback."""
        if raw is None:
            return None
        text: str
        if isinstance(raw, (bytes, bytearray)):
            try:
                text = bytes(raw).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return None
        else:
            text = str(raw)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        return None

    def _http_get_json(self, url: str, timeout: float) -> Optional[Any]:
        """GET a URL and parse the body as (possibly malformed) JSON. Never raises."""
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw: bytes = response.read()
        except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
            logger.debug("HTTP GET failed for %s: %s", url, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - urllib can raise odd transport errors
            logger.debug("Unexpected HTTP error for %s: %s", url, exc)
            return None
        return self._loads_tolerant(raw)

    @staticmethod
    def _parse_pricing(payload: Any) -> Dict[str, Tuple[float, float]]:
        """Turn the /models payload into {model_id: (in_per_M, out_per_M)}."""
        result: Dict[str, Tuple[float, float]] = {}
        if not isinstance(payload, dict):
            return result
        models = payload.get("data")
        if not isinstance(models, list):
            return result
        for entry in models:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            pricing = entry.get("pricing")
            if not isinstance(pricing, dict):
                continue
            in_per_token = _num(pricing.get("prompt"))
            out_per_token = _num(pricing.get("completion"))
            if in_per_token is None and out_per_token is None:
                continue
            in_per_m = (in_per_token or 0.0) * _TOKENS_PER_MILLION
            out_per_m = (out_per_token or 0.0) * _TOKENS_PER_MILLION
            result[model_id] = (in_per_m, out_per_m)
        return result

    def _blended_price(
        self,
        model_id: str,
        spec: ModelSpec,
        live: Dict[str, Tuple[float, float]],
    ) -> float:
        """Blended $/M: mean of live in/out if known, else mean of the spec's."""
        live_price = live.get(model_id) if model_id else None
        if live_price is not None:
            try:
                in_m, out_m = live_price
                return (float(in_m) + float(out_m)) / 2.0
            except (TypeError, ValueError):
                pass
        try:
            spec_in = float(getattr(spec, "cost_in_per_million", 0.0) or 0.0)
            spec_out = float(getattr(spec, "cost_out_per_million", 0.0) or 0.0)
            return (spec_in + spec_out) / 2.0
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def fetch_live_pricing(self) -> Dict[str, Tuple[float, float]]:
        """Live per-million pricing from OpenRouter. Cached 300s; {} on failure."""
        try:
            self._pricing_calls += 1
            now: float = self._monotonic()
            if (
                self._pricing_cache is not None
                and (now - self._pricing_cache_at) < PRICING_CACHE_TTL_SECONDS
            ):
                return dict(self._pricing_cache)
            payload = self._http_get_json(
                OPENROUTER_MODELS_URL, LIVE_FETCH_TIMEOUT_SECONDS
            )
            pricing = self._parse_pricing(payload)
            if pricing:  # only cache a successful, non-empty pull
                self._pricing_cache = dict(pricing)
                self._pricing_cache_at = now
            return dict(pricing)
        except Exception as exc:  # noqa: BLE001 - public method must never raise
            logger.error("fetch_live_pricing failed: %s", exc)
            return {}

    def detect_local(self) -> Dict[str, Any]:
        """Probe LM Studio at localhost:1234 (1.5s timeout). Never raises."""
        result: Dict[str, Any] = {
            "available": False,
            "models": [],
            "endpoint": LOCAL_ENDPOINT,
        }
        try:
            payload = self._http_get_json(
                LOCAL_MODELS_URL, LOCAL_DETECT_TIMEOUT_SECONDS
            )
            if not isinstance(payload, dict):
                return result
            data = payload.get("data")
            models: List[str] = []
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict):
                        mid = entry.get("id")
                        if isinstance(mid, str) and mid:
                            models.append(mid)
                    elif isinstance(entry, str) and entry:
                        models.append(entry)
            result["models"] = models
            result["available"] = True  # server responded => reachable
            return result
        except Exception as exc:  # noqa: BLE001 - detection must never raise
            logger.debug("Local model detection failed: %s", exc)
            return result

    def market_report(self) -> Dict[str, Any]:
        """Rank council roles by capability-per-dollar. Never raises."""
        report: Dict[str, Any] = {
            "rows": [],
            "cheapest_capable": None,
            "local_available": False,
            "generated_at": "",  # caller stamps this
        }
        try:
            live = self.fetch_live_pricing()
        except Exception as exc:  # noqa: BLE001
            logger.error("market_report: live pricing lookup failed: %s", exc)
            live = {}

        rows: List[Dict[str, Any]] = []
        try:
            council = getattr(self.router, "council", {}) or {}
            for role, spec in council.items():
                try:
                    strength = int(ROLE_STRENGTH.get(role, _DEFAULT_STRENGTH))
                    model_id = str(getattr(spec, "id", "") or "")
                    blended = self._blended_price(model_id, spec, live)
                    cap = round(strength / max(blended, _MIN_PRICE_FLOOR), 2)
                    rows.append(
                        {
                            "role": role,
                            "model_id": model_id,
                            "strength": strength,
                            "blended_per_million": round(blended, 4),
                            "capability_per_dollar": cap,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - skip a bad role, keep going
                    logger.debug("market_report: skipped role %r: %s", role, exc)
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.error("market_report: council iteration failed: %s", exc)

        # Cheapest capable (strength >= 7) — computed before the local row so it
        # only ever picks a real council model, not the free local placeholder.
        cheapest_capable: Optional[str] = None
        try:
            capable = [
                r
                for r in rows
                if int(r.get("strength", 0)) >= _CAPABLE_STRENGTH_THRESHOLD
            ]
            if capable:
                best = max(
                    capable, key=lambda r: float(r.get("capability_per_dollar", 0.0))
                )
                cheapest_capable = str(best.get("model_id") or "") or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("market_report: cheapest_capable failed: %s", exc)

        try:
            rows.sort(
                key=lambda r: float(r.get("capability_per_dollar", 0.0)),
                reverse=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("market_report: sort failed: %s", exc)

        local_available: bool = False
        try:
            local = self.detect_local()
            local_available = bool(local.get("available"))
            local_models = local.get("models") or []
            if local_available and local_models:
                rows.insert(
                    0,
                    {
                        "role": "local",
                        "model_id": str(local_models[0]),
                        "strength": _LOCAL_STRENGTH,
                        "blended_per_million": 0.0,
                        "capability_per_dollar": _LOCAL_CAP_PER_DOLLAR,
                        "note": "local, electricity only",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("market_report: local row failed: %s", exc)

        report["rows"] = rows
        report["cheapest_capable"] = cheapest_capable
        report["local_available"] = local_available
        return report


__all__ = [
    "EconomicsEngine",
    "ROLE_STRENGTH",
    "OPENROUTER_MODELS_URL",
    "LOCAL_ENDPOINT",
]

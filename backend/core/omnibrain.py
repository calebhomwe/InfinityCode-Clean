"""OmniBrain client — Caleb's unified vector brain as a grounding source.

OmniBrain (https://fonedo-omnibrain.hf.space) indexes his Claude memories, the
Obsidian GameDev-Vault, every project's docs, and his agent/skill definitions —
~4k curated vectors, far broader than this app's local knowledge.db.

Why HTTP instead of loading the vectors locally: OmniBrain embeds with
nomic-embed-text-v1.5 (768-d) while this app embeds with
openai/text-embedding-3-small (1536-d). Vectors from different models are not
comparable, so the index cannot be merged into KnowledgeStore's matrix. Running
nomic locally would mean shipping torch + sentence-transformers in the
PyInstaller freeze (~2GB on an 88MB exe), which is a non-starter. So the remote
service does its own embedding and we send raw text.

That is also why this is CHEAP inside the grounding budget: no local embed call
is needed, so it runs CONCURRENTLY with client.embed() rather than after it.

Fails soft, always. A brain outage must never cost a reply.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://fonedo-omnibrain.hf.space"

# Measured 2026-07-28 over 10 known-answer questions vs 8 deliberately
# off-topic ones: real hits scored 0.692-0.814, off-topic noise 0.594-0.661.
# Anything under this floor is noise the model is better off not seeing.
MIN_SCORE = 0.675

# The chat path allows 2.5s for ALL grounding. Stay well inside it so a slow
# brain never eats the budget that local memory/knowledge also needs.
DEFAULT_TIMEOUT = 1.8

_CACHE_TTL = 300.0
_CACHE_MAX = 128


class OmniBrainClient:
    """Thin, fail-soft HTTP client for the OmniBrain search API."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        timeout: float = DEFAULT_TIMEOUT,
        min_score: float = MIN_SCORE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_score = min_score
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._healthy: Optional[bool] = None
        self._vectors: int = 0
        self._checked_at: float = 0.0

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _post(self, path: str, body: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.debug("OmniBrain %s failed: %s", path, exc)
            return None

    def _cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        with self._lock:
            hit = self._cache.get(key)
        if not hit:
            return None
        if time.time() - hit["t"] > _CACHE_TTL:
            with self._lock:
                self._cache.pop(key, None)
            return None
        return hit["v"]

    def _cache_put(self, key: str, value: List[Dict[str, Any]]) -> None:
        with self._lock:
            if len(self._cache) >= _CACHE_MAX:
                oldest = min(self._cache, key=lambda k: self._cache[k]["t"])
                self._cache.pop(oldest, None)
            self._cache[key] = {"t": time.time(), "v": value}

    # ------------------------------------------------------------------ #
    # public
    # ------------------------------------------------------------------ #
    def health(self, force: bool = False) -> Dict[str, Any]:
        """Cached health probe (60s). Never raises."""
        if not force and self._healthy is not None and time.time() - self._checked_at < 60:
            return {"ok": self._healthy, "vectors": self._vectors, "url": self.base_url}
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=self.timeout) as r:
                d = json.load(r)
            self._healthy = bool(d.get("ready"))
            self._vectors = int(d.get("vectors") or 0)
        except Exception as exc:  # noqa: BLE001 - status only, never fatal
            logger.debug("OmniBrain health failed: %s", exc)
            self._healthy = False
        self._checked_at = time.time()
        return {"ok": self._healthy, "vectors": self._vectors, "url": self.base_url}

    def search(
        self,
        query: str,
        top_k: int = 4,
        scope: str = "personal",
        project: Optional[str] = None,
        agent: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Return scored hits above the noise floor. [] on any failure."""
        query = (query or "").strip()
        if not query:
            return []
        key = f"{scope}|{project}|{agent}|{top_k}|{query}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        body: Dict[str, Any] = {"query": query[:2000], "k": top_k, "scope": scope}
        if project:
            body["project"] = project
        if agent:
            body["agent"] = agent

        data = self._post("/search", body, timeout or self.timeout)
        if not data or "results" not in data:
            return []
        hits = [h for h in data["results"] if float(h.get("score") or 0) >= self.min_score]
        self._cache_put(key, hits)
        return hits

    def as_prompt_block(self, hits: List[Dict[str, Any]], start_index: int = 1) -> str:
        """Render hits as a citable block for the system prompt."""
        if not hits:
            return ""
        blocks = []
        for i, h in enumerate(hits, start=start_index):
            src = str(h.get("source") or "")
            short = src.rsplit("/", 1)[-1] or src
            blocks.append(f"[{i}] (omnibrain: {short})\n{str(h.get('text') or '')[:1200]}")
        return "\n\n".join(blocks)

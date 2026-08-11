"""Self-testing / gap analysis — free, via a local LM Studio model.

The loop: sample chunks from the knowledge base -> have the LOCAL model write a
factual question+answer grounded in each chunk -> ask the same model the
question WITHOUT the chunk -> grade the ungrounded reply deterministically
(salient-term overlap, no LLM judge cost). Low scores are recorded as "gaps":
facts the base model does NOT know and must retrieve, never free-recall.

This is the anti-hallucination flywheel: gaps prove where retrieval is
mandatory, and the results are visible in Settings so the user can watch the
model learn its own blind spots. Zero cloud spend — if LM Studio isn't
running, the run reports that instead of falling back to paid models.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LOCAL_BASE = "http://127.0.0.1:1234/v1"  # literal IPv4: skips the ::1 timeout hop
GAP_THRESHOLD = 0.4

# local_model() is called from hot endpoints (/system/info); with LM Studio
# down the probe eats the full connect timeout, so cache the answer briefly.
_LOCAL_MODEL_CACHE: Tuple[float, Optional[str]] = (0.0, None)
_LOCAL_MODEL_TTL = 60.0

# Only ever pick a small, VRAM-resident model. Never models[0]: a self-test
# must not JIT-load a 21GB model just because it happens to be listed first.
_PREFERENCE: Tuple[str, ...] = (
    "fable-fast",
    "qwen/qwen3.5-9b",
    "qwen/qwen3.5-4b",
    "qwen/qwen3.5-2b",
)


def local_model() -> Optional[str]:
    """A small preferred LM Studio model id, or None if none is available.
    Cached for 60s so UI polls don't block on the connect timeout."""
    global _LOCAL_MODEL_CACHE
    ts, cached = _LOCAL_MODEL_CACHE
    if time.time() - ts < _LOCAL_MODEL_TTL:
        return cached
    result: Optional[str] = None
    try:
        with urllib.request.urlopen(LOCAL_BASE + "/models", timeout=1.5) as r:
            data = json.load(r)
        models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        result = next((p for p in _PREFERENCE if p in models), None)
    except Exception:  # noqa: BLE001 - simply not running
        result = None
    _LOCAL_MODEL_CACHE = (time.time(), result)
    return result


def _local_chat(model: str, messages: List[Dict[str, str]], max_tokens: int = 500) -> str:
    body = json.dumps(
        {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    ).encode()
    req = urllib.request.Request(
        LOCAL_BASE + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return str(data["choices"][0]["message"]["content"] or "")


def _salient_terms(text: str, cap: int = 8) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{4,}", text.lower())
    stop = {"which", "there", "their", "about", "these", "those", "where", "should",
            "would", "could", "because", "using", "based", "answer"}
    seen: List[str] = []
    for w in words:
        if w not in stop and w not in seen:
            seen.append(w)
        if len(seen) >= cap:
            break
    return seen


def _grade(expected: str, got: str) -> float:
    terms = _salient_terms(expected)
    if not terms:
        return 1.0
    hits = sum(1 for t in terms if t in got.lower())
    return round(hits / len(terms), 3)


class SelfTester:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path), timeout=10.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        c.row_factory = sqlite3.Row
        return c

    def _ensure(self) -> None:
        with self._connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS gaps ("
                "id TEXT PRIMARY KEY, chunk_id TEXT, relpath TEXT, question TEXT,"
                "expected TEXT, got TEXT, score REAL, created_at REAL)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_gaps_score ON gaps(score)")

    def run(self, knowledge: Any, n: int = 6) -> Dict[str, Any]:
        """One self-test pass. Free (local model only)."""
        model = local_model()
        if model is None:
            return {"status": "no_local_model",
                    "detail": "LM Studio is not serving on :1234 — start it to self-test for free."}
        chunks = knowledge.random_chunks(n)
        if not chunks:
            return {"status": "no_knowledge", "detail": "Knowledge base is empty — add sources and reindex."}
        tested = 0
        gaps_found = 0
        results: List[Dict[str, Any]] = []
        for ch in chunks:
            try:
                qa_raw = _local_chat(model, [
                    {"role": "system", "content":
                        "You write ONE factual quiz question answerable ONLY from the given text. "
                        "Reply as JSON: {\"question\": str, \"answer\": str}. The answer must be short and factual."},
                    {"role": "user", "content": ch["text"][:2800]},
                ], 300)
                m = re.search(r"\{.*\}", qa_raw, re.DOTALL)
                if not m:
                    continue
                qa = json.loads(m.group(0))
                question, expected = str(qa.get("question", "")), str(qa.get("answer", ""))
                if not question or not expected:
                    continue
                got = _local_chat(model, [
                    {"role": "system", "content":
                        "Answer from your own knowledge only. If you do not know, say exactly: I don't know."},
                    {"role": "user", "content": question},
                ], 300)
                score = _grade(expected, got)
                tested += 1
                is_gap = score < GAP_THRESHOLD
                if is_gap:
                    gaps_found += 1
                    with self._connect() as c:
                        c.execute(
                            "INSERT INTO gaps (id, chunk_id, relpath, question, expected, got, score, created_at)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (str(uuid.uuid4()), ch["id"], ch.get("relpath", ""),
                             question[:500], expected[:500], got[:500], score, time.time()),
                        )
                results.append({"question": question[:120], "score": score, "gap": is_gap})
            except Exception as exc:  # noqa: BLE001 - keep testing the rest
                logger.warning("selftest item failed: %s", exc)
        return {"status": "ok", "model": model, "tested": tested,
                "gaps_found": gaps_found, "results": results}

    def list_gaps(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT id, relpath, question, expected, score, created_at"
                " FROM gaps ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def gap_count(self) -> int:
        with self._connect() as c:
            return int(c.execute("SELECT COUNT(*) FROM gaps").fetchone()[0])


__all__ = ["SelfTester", "local_model"]

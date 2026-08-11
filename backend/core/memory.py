"""Semantic memory: automatic cross-chat recall.

Stores compact "memories" (a user turn + a snippet of the reply) with an
embedding, and retrieves the most relevant ones for a new message so the
assistant remembers past conversations without the user re-explaining. Kept
deliberately dependency-free: embeddings are packed as float32 bytes in SQLite
and cosine similarity is computed in pure Python (fast enough for a few
thousand memories).
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
import uuid
from array import array
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("infinity.memory")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at REAL NOT NULL
);
"""

MAX_MEMORIES = 800  # oldest evicted beyond this


def _pack(vec: List[float]) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def _cosine(a: array, b: array) -> float:
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        try:
            with self._connection() as c:
                c.execute(_SCHEMA)
        except sqlite3.Error as exc:
            logger.error("Could not init memory DB: %s", exc)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back through SQLite, then always release the handle."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add(self, text: str, embedding: List[float], chat_id: Optional[str] = None) -> Optional[str]:
        if not text.strip() or not embedding:
            return None
        new_id = str(uuid.uuid4())
        try:
            with self._connection() as c:
                c.execute(
                    "INSERT INTO memories (id, chat_id, text, embedding, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (new_id, chat_id, text[:1500], _pack(embedding), time.time()),
                )
                # Evict oldest beyond the cap.
                c.execute(
                    "DELETE FROM memories WHERE id IN ("
                    "SELECT id FROM memories ORDER BY created_at DESC "
                    "LIMIT -1 OFFSET ?)",
                    (MAX_MEMORIES,),
                )
            return new_id
        except sqlite3.Error as exc:
            logger.warning("memory add failed: %s", exc)
            return None

    def search(self, query_embedding: List[float], top_k: int = 3, min_score: float = 0.25) -> List[Dict[str, Any]]:
        if not query_embedding:
            return []
        q = array("f", query_embedding)
        rows: List[Tuple[float, sqlite3.Row]] = []
        try:
            with self._connection() as c:
                for row in c.execute("SELECT id, text, embedding, created_at FROM memories"):
                    score = _cosine(q, _unpack(row["embedding"]))
                    if score >= min_score:
                        rows.append((score, row))
        except sqlite3.Error as exc:
            logger.warning("memory search failed: %s", exc)
            return []
        rows.sort(key=lambda t: t[0], reverse=True)
        return [
            {"id": r["id"], "text": r["text"], "score": round(s, 3)}
            for s, r in rows[:top_k]
        ]

    def list_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            with self._connection() as c:
                rows = c.execute(
                    "SELECT id, text, created_at FROM memories "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def count(self) -> int:
        try:
            with self._connection() as c:
                return int(c.execute("SELECT count(*) FROM memories").fetchone()[0])
        except sqlite3.Error:
            return 0

    def delete(self, memory_id: str) -> None:
        try:
            with self._connection() as c:
                c.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        except sqlite3.Error:
            pass

    def clear(self) -> None:
        try:
            with self._connection() as c:
                c.execute("DELETE FROM memories")
        except sqlite3.Error:
            pass


__all__ = ["MemoryStore"]

"""Personal knowledge base (RAG) for Infinity Code.

Ingests the user's markdown corpora (Obsidian vaults, the LLM WIKI, stray
Downloads notes) into knowledge.db: sources -> files -> ~800-token chunks with
OpenRouter embeddings. Search is a numpy matrix dot-product over pre-normalized
rows (cosine == dot), cached in memory and invalidated on writes — sub-ms for
thousands of chunks, no native extensions to freeze.

The Downloads "collector" copies recent stray .md/.txt notes into an organized
Inbox folder inside the first vault source (originals are left in place), so
notes dumped in Downloads end up organized and indexed automatically.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
import uuid
from array import array
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

CHUNK_CHARS = 3200      # ~800 tokens
CHUNK_OVERLAP = 320
EMBED_BATCH = 32
SKIP_DIRS = {".obsidian", ".git", "node_modules", ".trash"}


def _pack(vec: List[float]) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def chunk_text(text: str) -> List[str]:
    """Paragraph-aware chunking to ~CHUNK_CHARS with overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [text]
    paras = text.split("\n\n")
    chunks: List[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 > CHUNK_CHARS and cur:
            chunks.append(cur.strip())
            cur = cur[-CHUNK_OVERLAP:] + "\n\n" + p  # carry overlap
        else:
            cur = (cur + "\n\n" + p) if cur else p
        # A single monster paragraph: hard-split it.
        while len(cur) > CHUNK_CHARS * 1.5:
            chunks.append(cur[:CHUNK_CHARS].strip())
            cur = cur[CHUNK_CHARS - CHUNK_OVERLAP:]
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


class KnowledgeStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._matrix: Optional[np.ndarray] = None       # (N,dim) L2-normalized
        self._chunk_index: List[Dict[str, Any]] = []    # aligned metadata
        self._lock = threading.Lock()                   # guards matrix cache
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
                "CREATE TABLE IF NOT EXISTS sources ("
                "id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,"
                "enabled INTEGER NOT NULL DEFAULT 1, added_at REAL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS files ("
                "id TEXT PRIMARY KEY, source_id TEXT NOT NULL, relpath TEXT NOT NULL,"
                "mtime REAL NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,"
                "UNIQUE(source_id, relpath))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                "id TEXT PRIMARY KEY, file_id TEXT NOT NULL, source_id TEXT NOT NULL,"
                "ord INTEGER NOT NULL, text TEXT NOT NULL, embedding BLOB NOT NULL)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_files_source ON files(source_id)")

    # ---------------- sources ---------------- #

    def add_source(self, path: str, kind: str = "vault") -> Dict[str, Any]:
        p = Path(path)
        sid = str(uuid.uuid4())
        with self._connect() as c:
            try:
                c.execute(
                    "INSERT INTO sources (id, path, kind, enabled, added_at) VALUES (?,?,?,1,?)",
                    (sid, str(p), kind, time.time()),
                )
            except sqlite3.IntegrityError:
                row = c.execute("SELECT id FROM sources WHERE path=?", (str(p),)).fetchone()
                sid = row["id"]
        return {"id": sid, "path": str(p), "kind": kind}

    def remove_source(self, source_id: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
            c.execute("DELETE FROM files WHERE source_id=?", (source_id,))
            c.execute("DELETE FROM sources WHERE id=?", (source_id,))
        self._invalidate()

    def toggle_source(self, source_id: str, enabled: bool) -> None:
        with self._connect() as c:
            c.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if enabled else 0, source_id))
        self._invalidate()

    def list_sources(self) -> List[Dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT s.id, s.path, s.kind, s.enabled,"
                " (SELECT COUNT(*) FROM files f WHERE f.source_id=s.id) AS file_count,"
                " (SELECT COUNT(*) FROM chunks k WHERE k.source_id=s.id) AS chunk_count"
                " FROM sources s ORDER BY s.added_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> Dict[str, int]:
        with self._connect() as c:
            files = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"files": files, "chunks": chunks}

    # ---------------- downloads collector ---------------- #

    def collect_downloads(
        self, downloads: Path, inbox: Path, max_age_days: int = 14
    ) -> List[str]:
        """Copy recent stray .md/.txt notes from Downloads into an organized
        vault Inbox (Inbox/YYYY-MM/name). Originals stay put. Returns copies."""
        copied: List[str] = []
        if not downloads.is_dir():
            return copied
        cutoff = time.time() - max_age_days * 86400
        for f in list(downloads.glob("*.md")) + list(downloads.glob("*.txt")):
            try:
                if f.stat().st_mtime < cutoff or f.stat().st_size > 512_000:
                    continue
                stamp = time.strftime("%Y-%m", time.localtime(f.stat().st_mtime))
                dest_dir = inbox / stamp
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f.name
                if dest.exists() and dest.stat().st_mtime >= f.stat().st_mtime:
                    continue  # already collected this version
                shutil.copy2(f, dest)
                copied.append(str(dest))
            except OSError as exc:
                logger.warning("collect: skip %s (%s)", f, exc)
        return copied

    # ---------------- indexing ---------------- #

    def reindex(self, embed: Callable[[List[str]], List[List[float]]]) -> Dict[str, Any]:
        """Sync all enabled sources: (re)embed changed files, drop deleted ones."""
        stats = {"files_indexed": 0, "files_removed": 0, "chunks_embedded": 0, "errors": 0}
        with self._connect() as c:
            # "downloads" sources are collect-only (their notes are copied into a
            # vault Inbox and indexed there) — never indexed as a folder directly.
            sources = c.execute(
                "SELECT * FROM sources WHERE enabled=1 AND kind != 'downloads'"
            ).fetchall()
        for src in sources:
            root = Path(src["path"])
            if not root.exists():
                continue
            seen: set = set()
            md_files = (
                [p for p in root.rglob("*.md") if not any(d in p.parts for d in SKIP_DIRS)]
                + [p for p in root.rglob("*.txt") if not any(d in p.parts for d in SKIP_DIRS)]
                if root.is_dir()
                else [root]
            )
            for f in md_files:
                rel = str(f.relative_to(root)) if root.is_dir() else f.name
                seen.add(rel)
                try:
                    mtime = f.stat().st_mtime
                    with self._connect() as c:
                        row = c.execute(
                            "SELECT id, mtime FROM files WHERE source_id=? AND relpath=?",
                            (src["id"], rel),
                        ).fetchone()
                    if row and abs(row["mtime"] - mtime) < 1:
                        continue  # unchanged
                    text = f.read_text(encoding="utf-8", errors="replace")
                    pieces = chunk_text(text)
                    if not pieces:
                        continue
                    vectors: List[List[float]] = []
                    for i in range(0, len(pieces), EMBED_BATCH):
                        vectors.extend(embed(pieces[i : i + EMBED_BATCH]))
                    fid = row["id"] if row else str(uuid.uuid4())
                    with self._connect() as c:
                        c.execute("DELETE FROM chunks WHERE file_id=?", (fid,))
                        c.execute(
                            "INSERT OR REPLACE INTO files (id, source_id, relpath, mtime, chunk_count)"
                            " VALUES (?,?,?,?,?)",
                            (fid, src["id"], rel, mtime, len(pieces)),
                        )
                        for i, (piece, vec) in enumerate(zip(pieces, vectors)):
                            c.execute(
                                "INSERT INTO chunks (id, file_id, source_id, ord, text, embedding)"
                                " VALUES (?,?,?,?,?,?)",
                                (str(uuid.uuid4()), fid, src["id"], i, piece[:6000], _pack(vec)),
                            )
                    stats["files_indexed"] += 1
                    stats["chunks_embedded"] += len(pieces)
                except Exception as exc:  # noqa: BLE001 - one bad file never kills the pass
                    logger.warning("reindex: %s failed: %s", f, exc)
                    stats["errors"] += 1
            # Drop DB records for files deleted from disk.
            with self._connect() as c:
                for row in c.execute(
                    "SELECT id, relpath FROM files WHERE source_id=?", (src["id"],)
                ).fetchall():
                    if row["relpath"] not in seen:
                        c.execute("DELETE FROM chunks WHERE file_id=?", (row["id"],))
                        c.execute("DELETE FROM files WHERE id=?", (row["id"],))
                        stats["files_removed"] += 1
        self._invalidate()
        return stats

    # ---------------- search ---------------- #

    def _invalidate(self) -> None:
        with self._lock:
            self._matrix = None
            self._chunk_index = []

    def _load_matrix(self) -> None:
        with self._connect() as c:
            rows = c.execute(
                "SELECT k.id, k.text, k.embedding, f.relpath, s.path AS source_path"
                " FROM chunks k JOIN files f ON f.id=k.file_id"
                " JOIN sources s ON s.id=k.source_id WHERE s.enabled=1"
            ).fetchall()
        if not rows:
            self._matrix = np.zeros((0, 1), dtype=np.float32)
            self._chunk_index = []
            return
        vecs = np.stack([_unpack(r["embedding"]) for r in rows])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = (vecs / norms).astype(np.float32)
        self._chunk_index = [
            {"id": r["id"], "text": r["text"], "relpath": r["relpath"],
             "source_path": r["source_path"]}
            for r in rows
        ]

    def search(
        self, query_vec: List[float], top_k: int = 4, min_score: float = 0.30
    ) -> List[Dict[str, Any]]:
        q = np.asarray(query_vec, dtype=np.float32)
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        # Lock the whole matrix read so a concurrent reindex/invalidate can't
        # swap the matrix out from under the matmul + index lookups.
        with self._lock:
            if self._matrix is None:
                self._load_matrix()
            if self._matrix is None or self._matrix.shape[0] == 0:
                return []
            if self._matrix.shape[1] != q.shape[0]:
                # Dimension mismatch (embedding model changed) — fail soft, don't crash.
                logger.warning("knowledge search dim mismatch: matrix %s vs query %s",
                               self._matrix.shape[1], q.shape[0])
                return []
            scores = self._matrix @ (q / qn)
            chunk_index = self._chunk_index
        order = np.argsort(scores)[::-1][: top_k * 3]  # over-fetch, then dedup
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for i in order:
            s = float(scores[i])
            if s < min_score:
                continue
            item = dict(chunk_index[int(i)])
            key = item["text"][:200].strip()  # collapse duplicate content across vaults
            if key in seen:
                continue
            seen.add(key)
            item["score"] = round(s, 4)
            out.append(item)
            if len(out) >= top_k:
                break
        return out

    def random_chunks(self, n: int = 8) -> List[Dict[str, Any]]:
        """Sample chunks for the self-test engine."""
        with self._connect() as c:
            rows = c.execute(
                "SELECT k.id, k.text, f.relpath FROM chunks k"
                " JOIN files f ON f.id=k.file_id ORDER BY RANDOM() LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]


__all__ = ["KnowledgeStore", "chunk_text"]

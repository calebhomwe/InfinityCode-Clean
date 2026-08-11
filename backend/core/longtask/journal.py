"""SQLite journal for Long Tasks — the substrate for resume (P2)."""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from array import array
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS longtasks(
  id TEXT PRIMARY KEY, goal TEXT NOT NULL, repo_path TEXT NOT NULL,
  branch TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
  autonomy TEXT NOT NULL DEFAULT 'ask', model_builder TEXT, model_reviewer TEXT,
  budget_json TEXT DEFAULT '{}', cost_aud REAL DEFAULT 0, steps INTEGER DEFAULT 0,
  result TEXT DEFAULT '', created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS longtask_steps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, seq INTEGER NOT NULL,
  kind TEXT NOT NULL, tool TEXT DEFAULT '', args_json TEXT DEFAULT '{}',
  result_json TEXT DEFAULT '{}', model TEXT DEFAULT '', cost_aud REAL DEFAULT 0,
  duration_ms INTEGER DEFAULT 0, ts REAL);
CREATE INDEX IF NOT EXISTS idx_steps_task ON longtask_steps(task_id, seq);
CREATE TABLE IF NOT EXISTS longtask_artifacts(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
  path TEXT DEFAULT '', body TEXT DEFAULT '', gates_json TEXT DEFAULT '{}',
  review_json TEXT DEFAULT '{}', accepted INTEGER, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON longtask_artifacts(task_id);
CREATE TABLE IF NOT EXISTS longtask_feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL,
  signal TEXT NOT NULL, note TEXT DEFAULT '', created_at REAL);
CREATE TABLE IF NOT EXISTS references_locked(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'style',
  artifact_id TEXT DEFAULT '', prompt TEXT DEFAULT '',
  dna_json TEXT DEFAULT '{}', embedding BLOB, created_at REAL);
CREATE TABLE IF NOT EXISTS longtask_lessons(
  id TEXT PRIMARY KEY, task_id TEXT, kind TEXT DEFAULT 'general',
  lesson TEXT NOT NULL, embedding BLOB, uses INTEGER DEFAULT 0,
  distilled INTEGER DEFAULT 0, created_at REAL);
"""


def _cosine(a: array, b: array) -> float:
    if len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class LongTaskJournal:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = self._open_or_recover(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    @staticmethod
    def _open_or_recover(db_path: Path) -> sqlite3.Connection:
        """Open the journal DB; a corrupt file (state-fuzz 2026-08-11:
        garbage bytes raised DatabaseError at backend startup) is
        quarantined and rebuilt rather than crashing the app."""
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1")
            return conn
        except sqlite3.Error:
            conn.close()
            quarantine = db_path.with_name(
                f"{db_path.name}.corrupt-{int(time.time())}")
            try:
                db_path.replace(quarantine)
            except OSError:
                try:
                    db_path.unlink()
                except OSError:
                    pass
            return sqlite3.connect(str(db_path), check_same_thread=False)

    def create_task(self, goal: str, repo_path: str, model_builder: str = "",
                    model_reviewer: str = "", budget: Optional[Dict] = None,
                    autonomy: str = "ask") -> str:
        tid = uuid.uuid4().hex[:12]
        now = time.time()
        self.conn.execute(
            "INSERT INTO longtasks(id,goal,repo_path,status,autonomy,model_builder,"
            "model_reviewer,budget_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, goal, repo_path, "running", autonomy, model_builder,
             model_reviewer, json.dumps(budget or {}), now, now))
        self.conn.commit()
        return tid

    def append_step(self, task_id: str, seq: int, kind: str, tool: str = "",
                    args: Optional[Dict] = None, result: Any = None,
                    model: str = "", cost_aud: float = 0.0,
                    duration_ms: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO longtask_steps(task_id,seq,kind,tool,args_json,result_json,"
            "model,cost_aud,duration_ms,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task_id, seq, kind, tool, json.dumps(args or {}),
             json.dumps(result, default=str), model, cost_aud, duration_ms,
             time.time()))
        self.conn.execute(
            "UPDATE longtasks SET steps=?, cost_aud=cost_aud+?, updated_at=? WHERE id=?",
            (seq, cost_aud, time.time(), task_id))
        self.conn.commit()

    def set_status(self, task_id: str, status: str, result: str = "",
                   cost_aud: Optional[float] = None) -> None:
        if cost_aud is None:
            self.conn.execute(
                "UPDATE longtasks SET status=?, result=?, updated_at=? WHERE id=?",
                (status, result, time.time(), task_id))
        else:
            self.conn.execute(
                "UPDATE longtasks SET status=?, result=?, cost_aud=?, updated_at=? "
                "WHERE id=?",
                (status, result, float(cost_aud), time.time(), task_id))
        self.conn.commit()

    # --- self-teaching: lessons ------------------------------------------ #

    # --- quest artifacts ----------------------------------------------- #

    def add_artifact(self, task_id: str, kind: str, path: str, body: str,
                     gates: Optional[Dict] = None) -> str:
        aid = uuid.uuid4().hex[:12]
        self.conn.execute(
            "INSERT INTO longtask_artifacts(id,task_id,kind,path,body,"
            "gates_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (aid, task_id, kind, path, body[:8000],
             json.dumps(gates or {}), time.time()))
        self.conn.commit()
        return aid

    def artifacts_for(self, task_id: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM longtask_artifacts WHERE task_id=? "
            "ORDER BY created_at", (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_artifact(self, artifact_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM longtask_artifacts WHERE id=?",
            (artifact_id,)).fetchone()
        return dict(row) if row else None

    # --- feedback + reference lock (like -> DNA -> future prompts) --- #

    def add_feedback(self, artifact_id: str, signal: str,
                     note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO longtask_feedback(artifact_id,signal,note,created_at)"
            " VALUES(?,?,?,?)",
            (artifact_id, signal, note[:1000], time.time()))
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def feedback_for(self, artifact_id: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM longtask_feedback WHERE artifact_id=? "
            "ORDER BY created_at", (artifact_id,)).fetchall()
        return [dict(r) for r in rows]

    def lock_reference(self, kind: str, artifact_id: str, prompt: str,
                       dna: Dict, embedding: Optional[List[float]]) -> str:
        rid = uuid.uuid4().hex[:12]
        blob = array("f", embedding).tobytes() if embedding else b""
        self.conn.execute(
            "INSERT INTO references_locked(id,kind,artifact_id,prompt,"
            "dna_json,embedding,created_at) VALUES(?,?,?,?,?,?,?)",
            (rid, kind, artifact_id, prompt[:2000], json.dumps(dna or {}),
             blob, time.time()))
        self.conn.commit()
        return rid

    def locked_references(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT id, kind, artifact_id, prompt, dna_json, created_at "
            "FROM references_locked ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def search_locked_references(self, query_embedding: List[float],
                                 top_k: int = 2,
                                 min_score: float = 0.20) -> List[Dict]:
        """Cosine-recall locked style DNA (same pattern as lessons)."""
        if not query_embedding:
            return []
        q = array("f", query_embedding)
        scored: List[tuple] = []
        for r in self.conn.execute(
                "SELECT id, dna_json, embedding FROM references_locked"):
            blob = r["embedding"] or b""
            if not blob:
                continue
            e = array("f")
            e.frombytes(blob)
            if len(e) != len(q):
                continue
            score = _cosine(q, e)
            if score >= min_score:
                scored.append((score, r))
        scored.sort(key=lambda t2: t2[0], reverse=True)
        out = []
        for s, r in scored[:top_k]:
            try:
                dna = json.loads(r["dna_json"] or "{}")
            except (ValueError, TypeError):
                dna = {}
            out.append({"id": r["id"], "dna": dna, "score": round(s, 3)})
        return out

    def add_lesson(self, task_id: str, kind: str, lesson: str,
                   embedding: Optional[List[float]] = None) -> str:
        lid = uuid.uuid4().hex[:12]
        blob = array("f", embedding).tobytes() if embedding else b""
        self.conn.execute(
            "INSERT INTO longtask_lessons(id,task_id,kind,lesson,embedding,"
            "uses,distilled,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (lid, task_id, kind, lesson[:500], blob, 0, 0, time.time()))
        self.conn.commit()
        return lid

    def lessons(self, kind: Optional[str] = None,
                undistilled_only: bool = False) -> List[Dict]:
        sql = "SELECT * FROM longtask_lessons"
        conds, params = [], []
        if kind:
            conds.append("kind=?")
            params.append(kind)
        if undistilled_only:
            conds.append("distilled=0")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        rows = self.conn.execute(sql + " ORDER BY created_at DESC",
                                 params).fetchall()
        return [dict(r) for r in rows]

    def search_lessons(self, query_embedding: List[float], top_k: int = 3,
                       min_score: float = 0.25) -> List[Dict]:
        """Cosine-recall over stored lesson embeddings (memory.py pattern)."""
        if not query_embedding:
            return []
        q = array("f", query_embedding)
        scored: List[tuple] = []
        for r in self.conn.execute(
                "SELECT id, lesson, kind, embedding, uses FROM longtask_lessons"):
            blob = r["embedding"] or b""
            if not blob:
                continue
            e = array("f")
            e.frombytes(blob)
            if len(e) != len(q):
                continue
            score = _cosine(q, e)
            if score >= min_score:
                scored.append((score, r))
        scored.sort(key=lambda t2: t2[0], reverse=True)
        return [{"id": r["id"], "lesson": r["lesson"], "kind": r["kind"],
                 "score": round(s, 3)} for s, r in scored[:top_k]]

    def bump_lesson_use(self, lesson_id: str) -> None:
        self.conn.execute(
            "UPDATE longtask_lessons SET uses=uses+1 WHERE id=?", (lesson_id,))
        self.conn.commit()

    def mark_lessons_distilled(self, lesson_ids: List[str]) -> None:
        for lid in lesson_ids:
            self.conn.execute(
                "UPDATE longtask_lessons SET distilled=1 WHERE id=?", (lid,))
        self.conn.commit()


    def get_task(self, task_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT * FROM longtasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def steps_for(self, task_id: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM longtask_steps WHERE task_id=? ORDER BY seq",
            (task_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_tasks(self, limit: int = 50) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM longtasks ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

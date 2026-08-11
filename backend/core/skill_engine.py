"""Skill execution and evolution for Infinity Code.

Skills live as JSON files in the skills/ directory; their track record
(success/fail counts, success rate) lives in the SQLite `skills` table so the
swarm can prefer skills that have actually worked before.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR: Path = Path(__file__).resolve().parents[1] / "skills"
_DEFAULT_DB_PATH: Path = Path(__file__).resolve().parents[1] / "skills.db"

_SKILLS_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    source_url TEXT,
    source_type TEXT,
    skill_json TEXT NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP,
    evolved_from TEXT
);
"""


class SkillEngineError(RuntimeError):
    """Raised when the skill library cannot be read or updated."""


class SkillEngine:
    """Loads, executes, and score-keeps skills from the skill library."""

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.skills_dir: Path = Path(skills_dir) if skills_dir else _DEFAULT_SKILLS_DIR
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SkillEngineError(f"Cannot create skill storage paths: {exc}") from exc
        self._ensure_table()

    # ------------------------------------------------------------------ #
    # Storage helpers
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.db_path), timeout=10.0)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as exc:
            raise SkillEngineError(
                f"Cannot open skills database {self.db_path}: {exc}"
            ) from exc

    def _ensure_table(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(_SKILLS_TABLE_SQL)
        except sqlite3.Error as exc:
            raise SkillEngineError(f"Cannot create skills table: {exc}") from exc

    def _load_skill_file(self, skill_name: str) -> Optional[Dict[str, Any]]:
        skill_path: Path = self.skills_dir / f"{skill_name}.json"
        if not skill_path.is_file():
            return None
        try:
            # utf-8-sig: editors (Notepad) save with a BOM, which must not
            # make a skill unreadable.
            loaded = json.loads(skill_path.read_text(encoding="utf-8-sig"))
            return loaded if isinstance(loaded, dict) else None
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Skill file %s is unreadable: %s", skill_path, exc)
            return None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def list_skills(self) -> List[str]:
        """Names of every skill JSON in the library."""
        try:
            return sorted(path.stem for path in self.skills_dir.glob("*.json"))
        except OSError as exc:
            logger.error("Could not list skills directory: %s", exc)
            return []

    def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Load one skill definition, or None if missing/corrupt."""
        return self._load_skill_file(skill_name)

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Rank skills by keyword overlap with the query.

        Returns up to `top_k` hits, each a compact dict with `name`,
        `topic`, `description`, and the top steps' instructions. Designed
        for chat-turn injection so the model sees relevant skills without
        being asked. Fast and dependency-free; swap the scoring later for
        embeddings if precision needs to improve.
        """
        q_raw: str = str(query or "").strip().lower()
        if not q_raw:
            return []
        # Score by count of query tokens found in each skill's searchable text.
        # Short tokens (< 3 chars) are noise (a, of, to) so we drop them.
        tokens: List[str] = [
            tok for tok in "".join(
                c if c.isalnum() else " " for c in q_raw
            ).split()
            if len(tok) >= 3
        ]
        if not tokens:
            return []
        scored: List[Tuple[float, str, Dict[str, Any]]] = []
        for name in self.list_skills():
            skill = self._load_skill_file(name)
            if skill is None:
                continue
            title = str(skill.get("title") or skill.get("name") or name)
            topic = str(skill.get("topic") or "")
            desc = str(skill.get("description") or "")
            hay = f"{title} {topic} {desc} {name}".lower()
            score = float(sum(1 for tok in tokens if tok in hay))
            # Title matches count double — a skill titled "React refactor"
            # for a query "refactor react" should outrank one that only
            # mentions those words deep in its description.
            title_low = title.lower()
            score += float(sum(1 for tok in tokens if tok in title_low))
            if score <= 0:
                continue
            scored.append((score, name, skill))
        try:
            limit = int(top_k)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            return []
        scored.sort(key=lambda row: row[0], reverse=True)
        hits: List[Dict[str, Any]] = []
        for score, name, skill in scored[:limit]:
            steps_raw = skill.get("steps") or []
            step_hints: List[str] = []
            if isinstance(steps_raw, list):
                for step in steps_raw[:3]:
                    if isinstance(step, dict):
                        text = str(step.get("instruction") or "").strip()
                    else:
                        text = str(step).strip()
                    if text:
                        step_hints.append(text[:160])
            hits.append(
                {
                    "name": name,
                    "title": str(skill.get("title") or skill.get("name") or name),
                    "topic": str(skill.get("topic") or ""),
                    "description": str(skill.get("description") or "")[:280],
                    "step_hints": step_hints,
                    "score": score,
                }
            )
        return hits

    def always_on(self) -> List[Dict[str, Any]]:
        """Every skill flagged "always": true, in search-hit shape.

        These load every session regardless of the query - the owner's pinned
        plugin set (qwencloud-*, autonomous-coder-loop).
        """
        out: List[Dict[str, Any]] = []
        for name in self.list_skills():
            skill = self._load_skill_file(name)
            if not isinstance(skill, dict) or not skill.get("always"):
                continue
            steps_raw = skill.get("steps") or []
            step_hints: List[str] = []
            if isinstance(steps_raw, list):
                for step in steps_raw[:3]:
                    if isinstance(step, dict):
                        text = str(step.get("instruction") or "").strip()
                    else:
                        text = str(step).strip()
                    if text:
                        step_hints.append(text[:160])
            out.append({
                "name": name,
                "title": str(skill.get("title") or skill.get("name") or name),
                "topic": str(skill.get("topic") or ""),
                "description": str(skill.get("description") or "")[:280],
                "step_hints": step_hints,
                "score": 0.0,
            })
        return out

    def execute(
        self, skill_name: str, context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """Walk a skill's steps and report the outcome.

        Step execution is currently a structured walk-through (log each
        instruction, count them); wiring steps to real tool invocations is the
        swarm's job and happens above this layer.
        """
        context = context or {}
        skill: Optional[Dict[str, Any]] = self._load_skill_file(skill_name)
        if skill is None:
            return False, f"Skill not found or unreadable: {skill_name}"

        steps_raw: Any = skill.get("steps", [])
        if not isinstance(steps_raw, list) or not steps_raw:
            return False, f"Skill {skill_name!r} has no steps to execute."

        executed: int = 0
        try:
            for step in steps_raw:
                if isinstance(step, dict):
                    instruction: str = str(step.get("instruction", "")).strip()
                else:
                    instruction = str(step).strip()
                if not instruction:
                    continue
                executed += 1
                logger.info(
                    "[skill:%s] step %d: %s (context keys: %s)",
                    skill_name,
                    executed,
                    instruction[:120],
                    sorted(context.keys()),
                )
        except (TypeError, AttributeError) as exc:
            return False, f"Skill {skill_name!r} has malformed steps: {exc}"

        if executed == 0:
            return False, f"Skill {skill_name!r} contained only empty steps."
        return True, f"Executed {executed} steps for skill {skill_name!r}."

    def record_result(self, skill_name: str, success: bool) -> None:
        """Update the skill's success/fail counts and success rate in SQLite.

        Inserts the row on first use (pulling metadata from the JSON file if
        available) so results are never dropped.
        """
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT id, success_count, fail_count FROM skills WHERE name = ?",
                    (skill_name,),
                ).fetchone()

                if row is None:
                    skill: Dict[str, Any] = self._load_skill_file(skill_name) or {}
                    connection.execute(
                        """
                        INSERT INTO skills
                            (id, name, topic, source_url, source_type, skill_json,
                             verified, success_count, fail_count, success_rate)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0.0)
                        """,
                        (
                            str(uuid.uuid4()),
                            skill_name,
                            str(skill.get("topic", "unknown")),
                            skill.get("source_url"),
                            skill.get("source_type"),
                            json.dumps(skill, ensure_ascii=False),
                            bool(skill.get("verified", False)),
                        ),
                    )
                    success_count, fail_count = 0, 0
                else:
                    success_count = int(row["success_count"] or 0)
                    fail_count = int(row["fail_count"] or 0)

                if success:
                    success_count += 1
                else:
                    fail_count += 1
                total: int = success_count + fail_count
                success_rate: float = success_count / total if total > 0 else 0.0

                connection.execute(
                    """
                    UPDATE skills
                    SET success_count = ?, fail_count = ?, success_rate = ?,
                        last_used = ?
                    WHERE name = ?
                    """,
                    (
                        success_count,
                        fail_count,
                        round(success_rate, 4),
                        datetime.now(timezone.utc).isoformat(),
                        skill_name,
                    ),
                )
            logger.info(
                "Recorded %s for skill %r (rate now %.0f%%)",
                "success" if success else "failure",
                skill_name,
                success_rate * 100,
            )
        except sqlite3.Error as exc:
            raise SkillEngineError(
                f"Could not record result for skill {skill_name!r}: {exc}"
            ) from exc


__all__ = ["SkillEngine", "SkillEngineError"]

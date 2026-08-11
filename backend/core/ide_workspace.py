"""Durable, revisioned storage for the browser IDE's virtual projects.

The browser keeps a localStorage cache for offline startup, while this store is
the authority shared across devices.  A whole project is replaced in one SQLite
transaction so clients never observe a partially-saved file tree.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional


class IDEWorkspaceError(ValueError):
    """The requested virtual workspace operation is invalid."""


class RevisionConflict(IDEWorkspaceError):
    """A client attempted to save an out-of-date project revision."""

    def __init__(self, current_revision: int) -> None:
        self.current_revision = int(current_revision)
        super().__init__(f"Workspace changed; current revision is {self.current_revision}.")


class IDEWorkspaceStore:
    """SQLite-backed storage for small browser-IDE projects."""

    _PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    _WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

    def __init__(
        self,
        db_path: Path,
        *,
        max_files: int = 256,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_files = int(max_files)
        self.max_file_bytes = int(max_file_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ide_projects (
                    project TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ide_files (
                    project TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (project, path),
                    FOREIGN KEY (project) REFERENCES ide_projects(project)
                        ON DELETE CASCADE
                );
                """
            )

    @classmethod
    def _validate_project(cls, project: str) -> str:
        value = str(project or "").strip()
        if not cls._PROJECT_RE.fullmatch(value):
            raise IDEWorkspaceError(
                "Project must be 1-64 characters using letters, numbers, dot, dash, or underscore."
            )
        return value

    @classmethod
    def _validate_path(cls, path: str) -> str:
        raw = str(path or "")
        if not raw or "\x00" in raw or cls._WINDOWS_DRIVE_RE.match(raw):
            raise IDEWorkspaceError("File path must be a safe relative path.")
        normalized = raw.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
            raise IDEWorkspaceError("File path must stay inside the virtual project.")
        if len(normalized) > 240 or ":" in normalized:
            raise IDEWorkspaceError("File path is too long or contains an invalid character.")
        return pure.as_posix()

    def _validate_files(self, files: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(files, Mapping):
            raise IDEWorkspaceError("Files must be an object mapping paths to text.")
        if len(files) > self.max_files:
            raise IDEWorkspaceError(f"Workspace exceeds the {self.max_files} file limit.")

        validated: dict[str, str] = {}
        total_bytes = 0
        for raw_path, raw_content in files.items():
            path = self._validate_path(raw_path)
            if not isinstance(raw_content, str):
                raise IDEWorkspaceError(f"File content must be text: {path}")
            size = len(raw_content.encode("utf-8"))
            if size > self.max_file_bytes:
                raise IDEWorkspaceError(
                    f"File size exceeds the {self.max_file_bytes} byte limit: {path}"
                )
            total_bytes += size
            if total_bytes > self.max_total_bytes:
                raise IDEWorkspaceError(
                    f"Workspace exceeds the {self.max_total_bytes} byte project size limit."
                )
            validated[path] = raw_content
        return validated

    def get(self, project: str) -> dict[str, Any]:
        project = self._validate_project(project)
        with self._connect() as conn:
            meta = conn.execute(
                "SELECT revision, updated_at FROM ide_projects WHERE project = ?",
                (project,),
            ).fetchone()
            if meta is None:
                return {"project": project, "files": {}, "revision": 0, "updated_at": 0.0}
            rows = conn.execute(
                "SELECT path, content FROM ide_files WHERE project = ? ORDER BY path",
                (project,),
            ).fetchall()
        return {
            "project": project,
            "files": {str(row["path"]): str(row["content"]) for row in rows},
            "revision": int(meta["revision"]),
            "updated_at": float(meta["updated_at"]),
        }

    def save(
        self,
        project: str,
        files: Mapping[str, Any],
        *,
        base_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        project = self._validate_project(project)
        validated = self._validate_files(files)
        if base_revision is not None and (
            isinstance(base_revision, bool) or int(base_revision) < 0
        ):
            raise IDEWorkspaceError("Base revision must be a non-negative integer.")

        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision FROM ide_projects WHERE project = ?", (project,)
            ).fetchone()
            current_revision = int(row["revision"]) if row is not None else 0
            if base_revision is not None and int(base_revision) != current_revision:
                raise RevisionConflict(current_revision)

            revision = current_revision + 1
            conn.execute(
                """
                INSERT INTO ide_projects(project, revision, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(project) DO UPDATE SET
                    revision = excluded.revision,
                    updated_at = excluded.updated_at
                """,
                (project, revision, now),
            )
            conn.execute("DELETE FROM ide_files WHERE project = ?", (project,))
            conn.executemany(
                "INSERT INTO ide_files(project, path, content, updated_at) VALUES (?, ?, ?, ?)",
                [(project, path, content, now) for path, content in validated.items()],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {
            "project": project,
            "files": validated,
            "revision": revision,
            "updated_at": now,
        }


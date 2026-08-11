"""Scheduled tasks — set-and-forget runs (Kimi-style), stdlib only.

A schedule = a prompt + a cadence (15m / 1h / 6h / 1d / daily). A background
asyncio loop wakes each minute, runs anything due through an injected `run_fn`
(which does the chat + persists a result chat), and reschedules it. Runs are
FORCED read-only (allow_actions=False) — a scheduled task never fires paid or
side-effectful actions unattended.

No APScheduler; just a table + a minute tick.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# cadence token -> seconds
_UNIT = {"m": 60, "h": 3600, "d": 86400}


def cadence_seconds(cadence: str) -> Optional[int]:
    c = (cadence or "").strip().lower()
    if c in ("daily", "day"):
        return 86400
    if c in ("hourly",):
        return 3600
    if len(c) >= 2 and c[-1] in _UNIT and c[:-1].isdigit():
        n = int(c[:-1])
        if n > 0:
            return n * _UNIT[c[-1]]
    return None


class Scheduler:
    def __init__(
        self,
        db_path: Path,
        run_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.run_fn = run_fn
        self._task: Optional[asyncio.Task] = None
        self._stop = False
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
                "CREATE TABLE IF NOT EXISTS schedules ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,"
                "cadence TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'chat',"
                "enabled INTEGER NOT NULL DEFAULT 1,"
                "next_run REAL NOT NULL, last_run REAL, last_status TEXT,"
                "created_at REAL NOT NULL)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_sched_next ON schedules(next_run)")

    # ---------------- CRUD ---------------- #

    def create(self, title: str, prompt: str, cadence: str, mode: str = "chat") -> Dict[str, Any]:
        secs = cadence_seconds(cadence)
        if secs is None:
            raise ValueError(f"bad cadence: {cadence!r} (use 15m/1h/6h/1d/daily)")
        sid = str(uuid.uuid4())
        now = time.time()
        with self._connect() as c:
            c.execute(
                "INSERT INTO schedules (id, title, prompt, cadence, mode, enabled,"
                " next_run, created_at) VALUES (?,?,?,?,?,1,?,?)",
                (sid, title[:200], prompt, cadence, mode, now + secs, now),
            )
        return self.get(sid)  # type: ignore[return-value]

    def get(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._connect() as c:
            row = c.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def list(self) -> List[Dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM schedules ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete(self, sid: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM schedules WHERE id=?", (sid,))

    def toggle(self, sid: str, enabled: bool) -> None:
        with self._connect() as c:
            c.execute("UPDATE schedules SET enabled=? WHERE id=?", (1 if enabled else 0, sid))

    def _mark_ran(self, sid: str, cadence: str, status: str) -> None:
        secs = cadence_seconds(cadence) or 3600
        now = time.time()
        with self._connect() as c:
            c.execute(
                "UPDATE schedules SET last_run=?, last_status=?, next_run=? WHERE id=?",
                (now, status[:120], now + secs, sid),
            )

    def due(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = now if now is not None else time.time()
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def run_now(self, sid: str) -> Dict[str, Any]:
        sched = self.get(sid)
        if sched is None:
            raise KeyError(sid)
        self._run_one(sched)
        return self.get(sid)  # type: ignore[return-value]

    # ---------------- runner ---------------- #

    def _run_one(self, sched: Dict[str, Any]) -> None:
        status = "ok"
        try:
            if self.run_fn is not None:
                # allow_actions is FORCED false at the runner — never trust stored config.
                self.run_fn({"title": sched["title"], "prompt": sched["prompt"],
                             "mode": sched.get("mode", "chat"), "allow_actions": False})
        except Exception as exc:  # noqa: BLE001
            status = f"error: {exc}"[:120]
            logger.warning("scheduled task %s failed: %s", sched["id"], exc)
        self._mark_ran(sched["id"], sched["cadence"], status)

    async def _loop(self) -> None:
        logger.info("Scheduler started (minute tick).")
        while not self._stop:
            try:
                for sched in self.due():
                    # Off the event loop — run_fn does blocking IO/LLM calls.
                    await asyncio.to_thread(self._run_one, sched)
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler tick error: %s", exc)
            await asyncio.sleep(60)

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Scheduler.start() called outside a running event loop.")
            return
        self._task = loop.create_task(self._loop())

    def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            self._task = None


__all__ = ["Scheduler", "cadence_seconds"]

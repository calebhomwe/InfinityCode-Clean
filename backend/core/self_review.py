"""Proposal-only self-review for Infinity Code.

Reads the mission/attempt/red-team/tournament SQLite database (and, optionally,
a separate skills database) and derives concrete *improvement proposals* for the
user. It is strictly read-only: it never writes to any database and never
modifies code — it only observes what has happened and suggests what a human
might change.

Every table is optional. Older or partially-migrated databases may be missing
the ``missions.params_json`` column, whole tables, or the skills DB entirely.
Every query is guarded so a missing table simply drops that one metric instead
of raising. No public method ever raises.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Status vocabularies. Missions/attempts written by different agents over time
# use slightly different words for the same terminal state, so we bucket
# generously and treat everything else (queued/running/unknown) as unconcluded.
SUCCESS_STATUSES: Tuple[str, ...] = ("completed", "passed", "succeeded", "done")
FAILURE_STATUSES: Tuple[str, ...] = ("failed", "failure", "error", "aborted")

# Proposal thresholds.
OVERALL_FAILURE_THRESHOLD: float = 0.4
MODE_FAILURE_THRESHOLD: float = 0.5
MODE_MIN_MISSIONS: int = 2
HIGH_COST_THRESHOLD_AUD: float = 0.5
REDTEAM_PASS_THRESHOLD: float = 0.6
SKILL_SUCCESS_THRESHOLD: float = 0.5

# Fallback extractor for a mission's mode when params_json is not valid JSON.
_MODE_RE: "re.Pattern[str]" = re.compile(r'"mode"\s*:\s*"([^"]+)"')
_DEFAULT_MODE: str = "auto"


class SelfReview:
    """Analyzes the run history and surfaces improvement proposals only."""

    def __init__(self, db_path: Path, skills_db_path: Optional[Path] = None) -> None:
        self.db_path: Path = Path(db_path)
        self.skills_db_path: Optional[Path] = (
            Path(skills_db_path) if skills_db_path is not None else None
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(self) -> Dict[str, Any]:
        """Return a metrics snapshot of the run history.

        Keys map to a metric or ``None`` when the underlying table (or column)
        is absent. ``generated_at`` is intentionally left blank for the caller
        to stamp, keeping this method deterministic and side-effect free.
        """
        metrics: Dict[str, Any] = {
            "generated_at": "",
            "missions": None,
            "modes": None,
            "avg_cost_per_mission_aud": None,
            "attempts": None,
            "redteam": None,
            "tournament": None,
            "skills": None,
        }

        connection: Optional[sqlite3.Connection] = self._open(self.db_path)
        if connection is not None:
            try:
                metrics["missions"] = self._mission_metrics(connection)
                metrics["modes"] = self._mode_metrics(connection)
                metrics["avg_cost_per_mission_aud"] = self._avg_cost(connection)
                metrics["attempts"] = self._attempt_metrics(connection)
                metrics["redteam"] = self._redteam_metrics(connection)
                metrics["tournament"] = self._tournament_metrics(connection)
            except Exception as exc:  # noqa: BLE001 - analyze must never raise
                logger.error("analyze() failed mid-scan: %s", exc)
            finally:
                self._close(connection)

        metrics["skills"] = self._skill_metrics()
        return metrics

    def proposals(self) -> List[Dict[str, Any]]:
        """Derive concrete improvement proposals from :meth:`analyze`.

        Each proposal is ``{area, finding, proposal, severity}`` with severity in
        ``{"low", "med", "high"}``. With no missions on record, returns a single
        low-severity "no data yet" nudge instead.
        """
        try:
            metrics: Dict[str, Any] = self.analyze()
        except Exception as exc:  # noqa: BLE001 - proposals must never raise
            logger.error("proposals(): analyze() raised unexpectedly: %s", exc)
            metrics = {}

        missions: Any = metrics.get("missions") if isinstance(metrics, dict) else None
        total_missions: int = 0
        if isinstance(missions, dict):
            try:
                total_missions = int(missions.get("total") or 0)
            except (TypeError, ValueError):
                total_missions = 0

        if total_missions <= 0:
            return [
                {
                    "area": "data",
                    "finding": "No missions yet",
                    "proposal": "Run a few missions so I can analyze what to improve",
                    "severity": "low",
                }
            ]

        items: List[Dict[str, Any]] = []
        self._propose_overall_failure(missions, items)
        self._propose_mode_failures(metrics.get("modes"), items)
        self._propose_high_cost(metrics.get("avg_cost_per_mission_aud"), items)
        self._propose_redteam(metrics.get("redteam"), items)
        self._propose_skills(metrics.get("skills"), items)
        return items

    # ------------------------------------------------------------------ #
    # Proposal rules
    # ------------------------------------------------------------------ #

    @staticmethod
    def _propose_overall_failure(
        missions: Any, items: List[Dict[str, Any]]
    ) -> None:
        if not isinstance(missions, dict):
            return
        rate: Any = missions.get("failure_rate")
        if isinstance(rate, (int, float)) and rate > OVERALL_FAILURE_THRESHOLD:
            concluded: Any = missions.get("concluded", 0)
            items.append(
                {
                    "area": "reliability",
                    "finding": (
                        f"Overall mission failure rate is {float(rate):.0%} "
                        f"across {concluded} concluded missions"
                    ),
                    "proposal": (
                        "Review the Engineer prompt and raise the default effort "
                        "level so more missions pass on the first loop"
                    ),
                    "severity": "high",
                }
            )

    @staticmethod
    def _propose_mode_failures(modes: Any, items: List[Dict[str, Any]]) -> None:
        if not isinstance(modes, dict):
            return
        for mode, stats in sorted(modes.items()):
            if not isinstance(stats, dict):
                continue
            rate: Any = stats.get("failure_rate")
            total: Any = stats.get("total", 0)
            if not (isinstance(rate, (int, float)) and rate > MODE_FAILURE_THRESHOLD):
                continue
            if not (isinstance(total, int) and total >= MODE_MIN_MISSIONS):
                continue
            failed: Any = stats.get("failed", 0)
            concluded: int = int(stats.get("completed", 0)) + int(
                stats.get("failed", 0)
            )
            items.append(
                {
                    "area": "mode",
                    "finding": (
                        f"Mode '{mode}' fails {float(rate):.0%} of the time "
                        f"({failed}/{concluded} concluded)"
                    ),
                    "proposal": (
                        f"Add mode-specific handling for '{mode}' missions — inspect "
                        "their failure evidence and tune the prompt or tools for it"
                    ),
                    "severity": "med",
                }
            )

    @staticmethod
    def _propose_high_cost(avg_cost: Any, items: List[Dict[str, Any]]) -> None:
        if isinstance(avg_cost, (int, float)) and avg_cost > HIGH_COST_THRESHOLD_AUD:
            items.append(
                {
                    "area": "cost",
                    "finding": f"Average spend is ${float(avg_cost):.2f} AUD per active mission",
                    "proposal": (
                        "Turn on predictive routing and default to fast mode so "
                        "cheaper council members handle routine work"
                    ),
                    "severity": "low",
                }
            )

    @staticmethod
    def _propose_redteam(redteam: Any, items: List[Dict[str, Any]]) -> None:
        if not isinstance(redteam, dict):
            return
        pass_rate: Any = redteam.get("pass_rate")
        total: Any = redteam.get("total", 0)
        if not (isinstance(total, int) and total > 0):
            return
        if isinstance(pass_rate, (int, float)) and pass_rate < REDTEAM_PASS_THRESHOLD:
            items.append(
                {
                    "area": "security",
                    "finding": (
                        f"Red-team attacks pass only {float(pass_rate):.0%} of the "
                        f"time ({redteam.get('passed', 0)}/{total})"
                    ),
                    "proposal": (
                        "Run more hardening iterations per mission and feed failed "
                        "attack vectors back to the Engineer"
                    ),
                    "severity": "med",
                }
            )

    @staticmethod
    def _propose_skills(skills: Any, items: List[Dict[str, Any]]) -> None:
        if not isinstance(skills, dict):
            return
        underperforming: Any = skills.get("underperforming")
        if not isinstance(underperforming, list):
            return
        for entry in underperforming:
            if not isinstance(entry, dict):
                continue
            name: str = str(entry.get("name") or "").strip()
            if not name:
                continue
            try:
                rate: float = float(entry.get("success_rate") or 0.0)
            except (TypeError, ValueError):
                rate = 0.0
            items.append(
                {
                    "area": "skills",
                    "finding": f"Skill '{name}' has a {rate:.0%} success rate",
                    "proposal": (
                        f"Revise or retire the '{name}' skill — review its recent "
                        "failures and update its steps"
                    ),
                    "severity": "med",
                }
            )

    # ------------------------------------------------------------------ #
    # Metric collectors (each returns None when its table is absent)
    # ------------------------------------------------------------------ #

    def _mission_metrics(
        self, connection: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        if not self._table_exists(connection, "missions"):
            return None
        try:
            cursor = connection.execute(
                "SELECT status, COUNT(*) FROM missions GROUP BY status"
            )
            by_status: Dict[str, int] = {}
            for row in cursor.fetchall():
                status: str = str(row[0] or "unknown").strip().lower()
                by_status[status] = by_status.get(status, 0) + int(row[1] or 0)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("mission metrics query failed: %s", exc)
            return None

        total: int = sum(by_status.values())
        completed: int = sum(by_status.get(s, 0) for s in SUCCESS_STATUSES)
        failed: int = sum(by_status.get(s, 0) for s in FAILURE_STATUSES)
        concluded: int = completed + failed
        failure_rate: Optional[float] = (
            round(failed / concluded, 4) if concluded > 0 else None
        )
        return {
            "total": total,
            "by_status": by_status,
            "completed": completed,
            "failed": failed,
            "concluded": concluded,
            "failure_rate": failure_rate,
        }

    def _mode_metrics(
        self, connection: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        if not self._table_exists(connection, "missions"):
            return None
        try:
            cursor = connection.execute("SELECT status, params_json FROM missions")
            rows: List[Tuple[Any, ...]] = cursor.fetchall()
        except sqlite3.Error as exc:
            # e.g. pre-migration DBs without a params_json column.
            logger.info("mode metrics unavailable (no params_json?): %s", exc)
            return None

        modes: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            status: str = str(row[0] or "").strip().lower()
            mode: str = self._extract_mode(row[1] if len(row) > 1 else None)
            bucket: Dict[str, Any] = modes.setdefault(
                mode, {"total": 0, "completed": 0, "failed": 0}
            )
            bucket["total"] += 1
            if status in SUCCESS_STATUSES:
                bucket["completed"] += 1
            elif status in FAILURE_STATUSES:
                bucket["failed"] += 1

        if not modes:
            return None
        for bucket in modes.values():
            concluded: int = int(bucket["completed"]) + int(bucket["failed"])
            bucket["failure_rate"] = (
                round(bucket["failed"] / concluded, 4) if concluded > 0 else None
            )
        return modes

    def _avg_cost(self, connection: sqlite3.Connection) -> Optional[float]:
        if not self._table_exists(connection, "missions"):
            return None
        try:
            cursor = connection.execute(
                "SELECT AVG(total_cost_aud) FROM missions WHERE total_cost_aud > 0"
            )
            row: Optional[Tuple[Any, ...]] = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            return round(float(row[0]), 4)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("avg cost query failed: %s", exc)
            return None

    def _attempt_metrics(
        self, connection: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        if not self._table_exists(connection, "attempts"):
            return None
        try:
            cursor = connection.execute(
                "SELECT status, COUNT(*) FROM attempts GROUP BY status"
            )
            by_status: Dict[str, int] = {}
            for row in cursor.fetchall():
                status: str = str(row[0] or "unknown").strip().lower()
                by_status[status] = by_status.get(status, 0) + int(row[1] or 0)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("attempt metrics query failed: %s", exc)
            return None

        total: int = sum(by_status.values())
        failed: int = sum(by_status.get(s, 0) for s in FAILURE_STATUSES)
        failure_rate: Optional[float] = round(failed / total, 4) if total > 0 else None
        return {
            "total": total,
            "failed": failed,
            "failure_rate": failure_rate,
            "by_status": by_status,
        }

    def _redteam_metrics(
        self, connection: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        if not self._table_exists(connection, "redteam_attacks"):
            return None
        try:
            cursor = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN passed THEN 1 ELSE 0 END) "
                "FROM redteam_attacks"
            )
            row: Optional[Tuple[Any, ...]] = cursor.fetchone()
            total: int = int(row[0] or 0) if row is not None else 0
            passed: int = int(row[1] or 0) if row is not None else 0
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("redteam metrics query failed: %s", exc)
            return None

        pass_rate: Optional[float] = round(passed / total, 4) if total > 0 else None
        return {"total": total, "passed": passed, "pass_rate": pass_rate}

    def _tournament_metrics(
        self, connection: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        if not self._table_exists(connection, "tournament_candidates"):
            return None
        try:
            avg_cursor = connection.execute(
                "SELECT AVG(score) FROM tournament_candidates WHERE selected"
            )
            avg_row: Optional[Tuple[Any, ...]] = avg_cursor.fetchone()
            avg_score: Any = avg_row[0] if avg_row is not None else None

            count_cursor = connection.execute(
                "SELECT COUNT(*) FROM tournament_candidates WHERE selected"
            )
            count_row: Optional[Tuple[Any, ...]] = count_cursor.fetchone()
            winners: int = int(count_row[0] or 0) if count_row is not None else 0
        except (sqlite3.Error, TypeError, ValueError) as exc:
            logger.warning("tournament metrics query failed: %s", exc)
            return None

        return {
            "avg_winner_score": (
                round(float(avg_score), 3) if avg_score is not None else None
            ),
            "winners": winners,
        }

    def _skill_metrics(self) -> Optional[Dict[str, Any]]:
        if self.skills_db_path is None:
            return None
        connection: Optional[sqlite3.Connection] = self._open(self.skills_db_path)
        if connection is None:
            return None
        try:
            if not self._table_exists(connection, "skills"):
                return None
            rows: List[Tuple[Any, ...]] = self._query_underperforming_skills(connection)
        except sqlite3.Error as exc:
            logger.warning("skill metrics query failed: %s", exc)
            return None
        finally:
            self._close(connection)

        underperforming: List[Dict[str, Any]] = []
        for row in rows:
            name: str = str(row[0] or "").strip()
            if not name:
                continue
            try:
                rate: float = round(float(row[1] or 0.0), 4)
            except (TypeError, ValueError):
                rate = 0.0
            underperforming.append({"name": name, "success_rate": rate})
        return {"underperforming": underperforming, "checked": True}

    @staticmethod
    def _query_underperforming_skills(
        connection: sqlite3.Connection,
    ) -> List[Tuple[Any, ...]]:
        """Skills below the success threshold, ignoring never-run skills.

        Falls back to a bare threshold query if the usage-count columns are
        absent from this particular skills schema.
        """
        try:
            cursor = connection.execute(
                "SELECT name, success_rate FROM skills "
                "WHERE success_rate < ? "
                "AND (COALESCE(success_count, 0) + COALESCE(fail_count, 0)) > 0 "
                "ORDER BY success_rate ASC",
                (SKILL_SUCCESS_THRESHOLD,),
            )
            return cursor.fetchall()
        except sqlite3.Error:
            cursor = connection.execute(
                "SELECT name, success_rate FROM skills "
                "WHERE success_rate < ? ORDER BY success_rate ASC",
                (SKILL_SUCCESS_THRESHOLD,),
            )
            return cursor.fetchall()

    # ------------------------------------------------------------------ #
    # Low-level helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_mode(raw: Any) -> str:
        """Best-effort ``mode`` from a params_json blob, tolerating bad JSON."""
        if not raw:
            return _DEFAULT_MODE
        text: str = raw if isinstance(raw, str) else str(raw)
        try:
            data: Any = json.loads(text)
            if isinstance(data, dict):
                mode: Any = data.get("mode")
                if mode:
                    return str(mode).strip().lower() or _DEFAULT_MODE
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        match = _MODE_RE.search(text)
        if match:
            return match.group(1).strip().lower() or _DEFAULT_MODE
        return _DEFAULT_MODE

    @staticmethod
    def _open(path: Path) -> Optional[sqlite3.Connection]:
        """Open ``path`` read-only if possible; never create or lock a new DB."""
        try:
            resolved: Path = Path(path)
            if not resolved.exists():
                return None
        except (OSError, ValueError) as exc:
            logger.warning("SelfReview could not stat DB %s: %s", path, exc)
            return None

        try:
            uri: str = f"{resolved.as_uri()}?mode=ro"
            return sqlite3.connect(uri, uri=True, timeout=5.0)
        except (sqlite3.Error, ValueError):
            pass
        try:
            return sqlite3.connect(str(resolved), timeout=5.0)
        except sqlite3.Error as exc:
            logger.warning("SelfReview could not open DB %s: %s", path, exc)
            return None

    @staticmethod
    def _close(connection: sqlite3.Connection) -> None:
        try:
            connection.close()
        except sqlite3.Error:
            pass

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        try:
            cursor = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error:
            return False


__all__ = ["SelfReview"]

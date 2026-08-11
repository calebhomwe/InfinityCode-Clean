"""Predictive next-mission suggestions for Infinity Code.

Reads the mission history out of SQLite and turns it into a small, ranked set
of "what should I build next?" ideas. When the database is empty (a brand-new
install) it hands back a varied set of starter ideas; once there is real
history it asks the cheap "worker" council model to predict the most likely
next missions given the user's recent titles and their distribution of modes.

This module is deliberately *safe*: it only ever suggests. It never spends,
never launches a mission, and never mutates the database. Every network / DB /
model call is wrapped so that no public method can raise — on any failure the
engine degrades to sensible starter suggestions or an empty summary rather than
propagating an exception into the caller's request handler.

Timestamps are intentionally left blank ("") in the returned dicts so the
caller can stamp them; nothing here depends on wall-clock time or randomness.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from backend.core.router import ModelRouter, ModelSpec
    from backend.tools.openrouter_client import OpenRouterClient, OpenRouterError
except ImportError:  # running with backend/ as the working directory
    from core.router import ModelRouter, ModelSpec  # type: ignore[no-redef]
    from tools.openrouter_client import (  # type: ignore[no-redef]
        OpenRouterClient,
        OpenRouterError,
    )

logger = logging.getLogger(__name__)

SUGGEST_ROLE: str = "worker"
SUGGEST_MAX_TOKENS: int = 900
BUSY_TIMEOUT_MS: int = 10_000
RECENT_TITLE_LIMIT: int = 8

DEFAULT_MODE: str = "auto"
VALID_MODES: tuple[str, ...] = ("auto", "code", "image", "3d")
COMPLETED_STATUS: str = "completed"
FAILED_STATUS: str = "failed"

STARTER_REASON: str = "Starter idea"

# A varied pool of first-run ideas: a code utility, an image, a small game,
# and a 3D asset — so a fresh install always sees breadth, not one flavour.
_STARTER_SUGGESTIONS: List[Dict[str, str]] = [
    {
        "title": "CSV to JSON converter",
        "goal": (
            "Write a small Python command-line tool that reads a CSV file and "
            "writes an equivalent JSON array, with a --pretty flag."
        ),
        "mode": "code",
        "reason": STARTER_REASON,
    },
    {
        "title": "Minimalist mountain landscape poster",
        "goal": (
            "Generate a clean, flat-style poster of a mountain range at sunrise "
            "with a limited three-colour palette."
        ),
        "mode": "image",
        "reason": STARTER_REASON,
    },
    {
        "title": "Terminal Snake game",
        "goal": (
            "Build a playable Snake game that runs in the terminal using Python "
            "and the curses library, with score tracking."
        ),
        "mode": "code",
        "reason": STARTER_REASON,
    },
    {
        "title": "Low-poly treasure chest",
        "goal": (
            "Model a stylised low-poly wooden treasure chest with metal banding, "
            "suitable for a game engine."
        ),
        "mode": "3d",
        "reason": STARTER_REASON,
    },
    {
        "title": "Markdown to HTML renderer",
        "goal": (
            "Write a self-contained Python function that converts a subset of "
            "Markdown (headings, lists, bold, links) into HTML."
        ),
        "mode": "code",
        "reason": STARTER_REASON,
    },
    {
        "title": "Retro synthwave album cover",
        "goal": (
            "Generate an 80s synthwave album cover with a neon grid horizon and "
            "a chrome sun."
        ),
        "mode": "image",
        "reason": STARTER_REASON,
    },
]

# First bracket-balanced-ish JSON array in a possibly noisy reply.
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


class SuggestionEngine:
    """Turn mission history into safe, predictive next-mission suggestions."""

    def __init__(
        self, db_path: Path, client: OpenRouterClient, router: ModelRouter
    ) -> None:
        self.db_path: Path = Path(db_path)
        self.client: OpenRouterClient = client
        self.router: ModelRouter = router

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _empty_summary() -> Dict[str, Any]:
        """The zero-state summary used for a missing table / empty / broken DB."""
        return {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "success_rate": 0.0,
            "mode_distribution": {},
            "recent_titles": [],
        }

    @staticmethod
    def _row_get(row: sqlite3.Row, key: str, default: Any) -> Any:
        """Fetch a column from a Row by name, tolerating a missing column."""
        try:
            keys = row.keys()
        except Exception:  # noqa: BLE001 - defensive against odd row objects
            return default
        if key not in keys:
            return default
        value = row[key]
        return default if value is None else value

    @staticmethod
    def _extract_mode(params_json_value: Any) -> str:
        """Parse ``params_json`` and return its ``mode`` (default 'auto').

        Tolerates NULL, non-string values, and malformed JSON — any of which
        collapse to the default mode rather than raising.
        """
        if not params_json_value or not isinstance(params_json_value, str):
            return DEFAULT_MODE
        try:
            parsed: Any = json.loads(params_json_value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return DEFAULT_MODE
        if not isinstance(parsed, dict):
            return DEFAULT_MODE
        mode: Any = parsed.get("mode", DEFAULT_MODE)
        if not isinstance(mode, str) or not mode.strip():
            return DEFAULT_MODE
        return mode.strip().lower()

    @classmethod
    def _normalize_mode(cls, mode: Any) -> str:
        """Coerce an arbitrary value to one of ``VALID_MODES`` (default 'auto')."""
        if isinstance(mode, str):
            candidate = mode.strip().lower()
            if candidate in VALID_MODES:
                return candidate
        return DEFAULT_MODE

    @classmethod
    def _coerce_suggestion(cls, item: Any) -> Optional[Dict[str, str]]:
        """Validate one model-produced item into a clean suggestion dict.

        Returns ``None`` for anything that is not a usable object (which the
        caller simply skips).
        """
        if not isinstance(item, dict):
            return None
        title: Any = item.get("title", "")
        goal: Any = item.get("goal", "")
        reason: Any = item.get("reason", "")
        title_s: str = str(title).strip() if title is not None else ""
        goal_s: str = str(goal).strip() if goal is not None else ""
        if not title_s and not goal_s:
            return None
        reason_s: str = str(reason).strip() if reason is not None else ""
        return {
            "title": title_s or goal_s[:60],
            "goal": goal_s or title_s,
            "mode": cls._normalize_mode(item.get("mode")),
            "reason": reason_s or "Predicted from your recent missions",
        }

    @classmethod
    def _extract_json_array(cls, text: str) -> Optional[List[Any]]:
        """Best-effort extraction of a JSON array from a noisy model reply.

        Tries a direct parse first, then a regex-scoped substring (to survive
        markdown fences / surrounding prose). Returns ``None`` if nothing
        parses into a list.
        """
        if not text or not text.strip():
            return None
        stripped: str = text.strip()
        # Strip a leading/trailing markdown code fence if present.
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            newline = stripped.find("\n")
            if newline != -1:
                # Drop a leading language hint like ``json`` on the first line.
                first_line = stripped[:newline].strip().lower()
                if first_line in {"json", ""}:
                    stripped = stripped[newline + 1 :]
        for candidate in (stripped, text):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        match = _JSON_ARRAY_RE.search(text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        return None

    def _starter_suggestions(self, n: int) -> List[Dict[str, Any]]:
        """Return exactly ``n`` starter ideas, cycling the pool if needed."""
        if n <= 0:
            return []
        pool = _STARTER_SUGGESTIONS
        out: List[Dict[str, Any]] = []
        for i in range(n):
            base = pool[i % len(pool)]
            out.append(dict(base))
        return out

    def _build_prompt(self, summary: Dict[str, Any], n: int) -> str:
        """Compose the worker prompt from the compact history summary."""
        recent = summary.get("recent_titles", []) or []
        modes = summary.get("mode_distribution", {}) or {}
        recent_block = "\n".join(f"- {t}" for t in recent) or "- (none)"
        try:
            modes_block = json.dumps(modes, ensure_ascii=False)
        except (TypeError, ValueError):
            modes_block = "{}"
        return (
            "You predict a creative maker's next projects from their history.\n"
            f"Recent mission titles (newest first):\n{recent_block}\n\n"
            f"Mode usage counts: {modes_block}\n\n"
            f"Predict the {n} most likely NEXT missions this person will want.\n"
            "Return ONLY a JSON array (no prose, no markdown fences) of exactly "
            f"{n} objects. Each object must have these string keys: "
            '"title", "goal", "mode", "reason". '
            '"mode" must be one of: auto, code, image, 3d. '
            '"goal" is a concrete one-sentence build brief. '
            '"reason" briefly explains why it follows from the history.'
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze_history(self) -> Dict[str, Any]:
        """Summarise the mission history in ``db_path``.

        Computes total / completed / failed counts, a success rate, the
        distribution of mission modes, and the most recent titles. Tolerates a
        missing table, an empty database, or an unreadable file by returning a
        zero-valued summary. Never raises.
        """
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            except sqlite3.Error as exc:
                logger.debug("Could not set busy_timeout: %s", exc)
            cursor = conn.execute(
                "SELECT title, status, created_at, params_json FROM missions"
            )
            rows: List[sqlite3.Row] = cursor.fetchall()
        except sqlite3.Error as exc:
            # Missing table, locked/corrupt DB, or bad path — all non-fatal.
            logger.info("analyze_history: could not read missions (%s)", exc)
            return self._empty_summary()
        except Exception as exc:  # noqa: BLE001 - never let history reading raise
            logger.warning("analyze_history: unexpected failure (%s)", exc)
            return self._empty_summary()
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        try:
            total: int = len(rows)
            if total == 0:
                return self._empty_summary()

            completed = 0
            failed = 0
            mode_distribution: Dict[str, int] = {}
            for row in rows:
                status = str(self._row_get(row, "status", "") or "").strip().lower()
                if status == COMPLETED_STATUS:
                    completed += 1
                elif status == FAILED_STATUS:
                    failed += 1
                mode = self._extract_mode(self._row_get(row, "params_json", None))
                mode_distribution[mode] = mode_distribution.get(mode, 0) + 1

            success_rate: float = round(completed / total, 4) if total else 0.0

            # Sort by created_at DESC; ISO-8601 strings sort lexicographically,
            # and a blank/missing timestamp sinks to the bottom.
            def _created_key(r: sqlite3.Row) -> str:
                return str(self._row_get(r, "created_at", "") or "")

            ordered = sorted(rows, key=_created_key, reverse=True)
            recent_titles: List[str] = []
            for row in ordered:
                title = str(self._row_get(row, "title", "") or "").strip()
                if title:
                    recent_titles.append(title)
                if len(recent_titles) >= RECENT_TITLE_LIMIT:
                    break

            return {
                "total": total,
                "completed": completed,
                "failed": failed,
                "success_rate": success_rate,
                "mode_distribution": mode_distribution,
                "recent_titles": recent_titles,
            }
        except Exception as exc:  # noqa: BLE001 - computation must never raise
            logger.warning("analyze_history: summarisation failed (%s)", exc)
            return self._empty_summary()

    def suggest(self, n: int = 3) -> List[Dict[str, Any]]:
        """Return up to ``n`` predicted next-mission suggestions.

        With no history, returns varied starter ideas. Otherwise asks the cheap
        worker council model to predict likely next missions from the history
        summary, tolerating markdown-fenced or malformed JSON via regex
        extraction and falling back to starter ideas on any failure. Each item
        is ``{"title", "goal", "mode", "reason"}``. Never raises.
        """
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 3
        if n <= 0:
            return []

        summary: Dict[str, Any] = self.analyze_history()
        total: int = int(summary.get("total", 0) or 0)
        if total == 0:
            return self._starter_suggestions(n)

        try:
            spec: ModelSpec = self.router.get_spec(SUGGEST_ROLE)
        except Exception as exc:  # noqa: BLE001 - resolving a spec must not break us
            logger.warning("suggest: could not resolve worker spec (%s)", exc)
            return self._starter_suggestions(n)

        prompt: str = self._build_prompt(summary, n)
        try:
            result: Dict[str, Any] = self.client.chat(
                model_id=spec.id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=SUGGEST_MAX_TOKENS,
                extra_body=None,
            )
        except OpenRouterError as exc:
            logger.warning("suggest: worker call failed (%s)", exc)
            return self._starter_suggestions(n)
        except Exception as exc:  # noqa: BLE001 - any transport/API failure is non-fatal
            logger.warning("suggest: worker call raised unexpectedly (%s)", exc)
            return self._starter_suggestions(n)

        array: Optional[List[Any]] = self._extract_json_array(
            str(result.get("text", ""))
        )
        if not array:
            return self._starter_suggestions(n)

        suggestions: List[Dict[str, Any]] = []
        for item in array:
            coerced = self._coerce_suggestion(item)
            if coerced is not None:
                suggestions.append(coerced)
            if len(suggestions) >= n:
                break

        if not suggestions:
            return self._starter_suggestions(n)
        return suggestions[:n]


__all__ = [
    "SuggestionEngine",
    "SUGGEST_ROLE",
    "VALID_MODES",
    "DEFAULT_MODE",
]

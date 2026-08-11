"""Session logging for the custom model strategy.

Every model turn is persisted as JSONL under DATA_DIR/sessions/ in a
training-ready schema (OpenAI chat format messages + metadata). User signals
(accepted/edited/rejected/reverted) are recorded separately so the dataset can
be filtered before fine-tuning.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("infinity.session")

MAX_TURNS_PER_FILE: int = 10_000

# User feedback signals. Keep values stable; they are written to disk.
SIGNAL_ACCEPTED: str = "accepted"
SIGNAL_EDITED: str = "edited"
SIGNAL_REJECTED: str = "rejected"
SIGNAL_REVERTED: str = "reverted"
VALID_SIGNALS: frozenset[str] = frozenset(
    {SIGNAL_ACCEPTED, SIGNAL_EDITED, SIGNAL_REJECTED, SIGNAL_REVERTED}
)


class SessionLogger:
    """Append-only, thread-safe logger of model turns for training data."""

    def __init__(self, data_dir: Path) -> None:
        self.base_dir: Path = Path(data_dir).resolve() / "sessions"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock: threading.Lock = threading.Lock()

    def _session_path(self, session_id: str) -> Path:
        # Sanitize session id so it is safe as a filename.
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self.base_dir / f"{safe or 'unknown'}.jsonl"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def log_turn(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        output: str,
        model: str,
        lane: Optional[str] = None,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Log one model turn. Returns the turn_id."""
        turn_id: str = str(uuid.uuid4())
        record: Dict[str, Any] = {
            "turn_id": turn_id,
            "session_id": session_id,
            "timestamp": self._now_iso(),
            "model": model,
            "lane": lane or "unknown",
            "messages": list(messages),
            "output": output,
            "cost_usd": round(float(cost_usd), 8),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "latency_ms": round(float(latency_ms), 2) if latency_ms is not None else None,
            "metadata": dict(metadata) if metadata else {},
            "signal": None,
        }
        path = self._session_path(session_id)
        try:
            with self._lock:
                # Simple rotation: if a session file grows too large, start a
                # numbered archive so reads stay fast.
                self._rotate_if_needed(path)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("Could not write session log to %s: %s", path, exc)
        return turn_id

    def _rotate_if_needed(self, path: Path) -> None:
        """If a session file is at cap, rename it to .1, .2, etc."""
        if not path.is_file():
            return
        try:
            count = sum(1 for _ in path.open(encoding="utf-8"))
        except OSError:
            return
        if count < MAX_TURNS_PER_FILE:
            return
        # Find next free archive slot.
        slot = 1
        while True:
            archive = path.with_suffix(f".jsonl.{slot}")
            if not archive.exists():
                break
            slot += 1
        try:
            path.rename(archive)
        except OSError as exc:
            logger.warning("Session log rotation failed: %s", exc)

    def signal(self, session_id: str, turn_id: str, signal_value: str) -> bool:
        """Record a user outcome signal for an existing turn.

        Returns True if the turn was found and updated.
        """
        signal_value = str(signal_value).strip().lower()
        if signal_value not in VALID_SIGNALS:
            logger.warning("Ignoring invalid session signal %r", signal_value)
            return False
        path = self._session_path(session_id)
        if not path.is_file():
            return False
        try:
            with self._lock:
                lines: List[str] = []
                found = False
                with path.open("r", encoding="utf-8") as handle:
                    for raw in handle:
                        if not raw.strip():
                            lines.append(raw)
                            continue
                        try:
                            record = json.loads(raw)
                        except json.JSONDecodeError:
                            lines.append(raw)
                            continue
                        if record.get("turn_id") == turn_id:
                            record["signal"] = signal_value
                            record["signalled_at"] = self._now_iso()
                            found = True
                        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
                if not found:
                    return False
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as handle:
                    handle.writelines(lines)
                tmp.replace(path)
            return True
        except OSError as exc:
            logger.error("Could not update session signal in %s: %s", path, exc)
            return False

    def read_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Return all turns for a session, newest first."""
        path = self._session_path(session_id)
        records: List[Dict[str, Any]] = []
        if not path.is_file():
            return records
        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        records.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.error("Could not read session log %s: %s", path, exc)
        return list(reversed(records))

    def stats(self) -> Dict[str, Any]:
        """Aggregate stats across all session files."""
        total_turns = 0
        signalled: Dict[str, int] = {s: 0 for s in VALID_SIGNALS}
        cost_usd = 0.0
        sessions: set[str] = set()
        try:
            for path in self.base_dir.glob("*.jsonl"):
                with path.open("r", encoding="utf-8") as handle:
                    for raw in handle:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            record = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        total_turns += 1
                        sessions.add(record.get("session_id", path.stem))
                        cost_usd += float(record.get("cost_usd") or 0.0)
                        sig = record.get("signal")
                        if sig in VALID_SIGNALS:
                            signalled[sig] += 1
        except OSError as exc:
            logger.error("Could not compute session stats: %s", exc)
        return {
            "sessions": len(sessions),
            "turns": total_turns,
            "cost_usd": round(cost_usd, 6),
            "signals": signalled,
        }

    def training_jsonl(self, output_path: Path, require_signal: Optional[str] = None) -> int:
        """Export accepted (or matching-signal) turns to a training JSONL file.

        Each line is {"messages": [...]} in OpenAI chat format.
        Returns the number of examples written.
        """
        require_signal = require_signal or SIGNAL_ACCEPTED
        written = 0
        try:
            with Path(output_path).open("w", encoding="utf-8") as out:
                for path in self.base_dir.glob("*.jsonl"):
                    with path.open("r", encoding="utf-8") as handle:
                        for raw in handle:
                            raw = raw.strip()
                            if not raw:
                                continue
                            try:
                                record = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if record.get("signal") != require_signal:
                                continue
                            messages = list(record.get("messages", []))
                            messages.append({"role": "assistant", "content": record.get("output", "")})
                            out.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
                            written += 1
        except OSError as exc:
            logger.error("Could not write training export %s: %s", output_path, exc)
        return written


__all__ = [
    "SessionLogger",
    "SIGNAL_ACCEPTED",
    "SIGNAL_EDITED",
    "SIGNAL_REJECTED",
    "SIGNAL_REVERTED",
    "VALID_SIGNALS",
]

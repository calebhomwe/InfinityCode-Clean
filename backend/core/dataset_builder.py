"""Dataset builder for the custom model strategy.

Turns session JSONL logs into filtered, training-ready JSONL datasets for the
coder (Qwen3-Coder-Next) and vision (Qwen-VL) fine-tunes. Includes accepted
outputs and recovery traces where the model broke and was later fixed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from backend.core.session_logger import (
        SessionLogger,
        SIGNAL_ACCEPTED,
        SIGNAL_EDITED,
        SIGNAL_REJECTED,
        SIGNAL_REVERTED,
        VALID_SIGNALS,
    )
except ImportError:  # running with backend/ as the working directory
    from core.session_logger import (  # type: ignore[no-redef]
        SessionLogger,
        SIGNAL_ACCEPTED,
        SIGNAL_EDITED,
        SIGNAL_REJECTED,
        SIGNAL_REVERTED,
        VALID_SIGNALS,
    )

logger = logging.getLogger("infinity.dataset")

# Positive signals we always train on.
POSITIVE_SIGNALS: Set[str] = {SIGNAL_ACCEPTED, SIGNAL_EDITED}
# Negative signals that make a session a "recovery" session when mixed with positives.
NEGATIVE_SIGNALS: Set[str] = {SIGNAL_REJECTED, SIGNAL_REVERTED}


def _message_has_image(message: Dict[str, Any]) -> bool:
    """Detect vision content parts in an OpenAI-style message."""
    content = message.get("content")
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            return True
    return False


def _is_vision_record(record: Dict[str, Any]) -> bool:
    """Heuristic: coder vs vision split based on message content + metadata."""
    messages = record.get("messages") or []
    if any(_message_has_image(m) for m in messages):
        return True
    source = (record.get("metadata") or {}).get("source")
    if source in {"generate_image", "chat_with_vision", "render_feedback"}:
        return True
    tools = record.get("tools") or []
    if any(t.get("name") in {"see_image", "render_feedback"} for t in tools):
        return True
    return False


def _clean_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty keys so the training file stays compact."""
    return {k: v for k, v in message.items() if v not in (None, "", [], {})}


class DatasetBuilder:
    """Build coder + vision training sets from the session logger."""

    def __init__(
        self,
        session_logger: SessionLogger,
        output_dir: Optional[Path] = None,
    ) -> None:
        self.session_logger = session_logger
        self.output_dir = (
            Path(output_dir).resolve()
            if output_dir
            else session_logger.base_dir.parent / "datasets"
        )

    def _read_records(self) -> List[Dict[str, Any]]:
        """Read every turn from every session file."""
        records: List[Dict[str, Any]] = []
        base = self.session_logger.base_dir
        if not base.is_dir():
            return records
        for path in sorted(base.glob("*.jsonl")):
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
                logger.warning("Could not read session file %s: %s", path, exc)
        return records

    @staticmethod
    def _recovery_session_ids(records: List[Dict[str, Any]]) -> Set[str]:
        """Sessions that contain both a negative signal and a positive signal."""
        by_session: Dict[str, Set[str]] = {}
        for rec in records:
            sid = rec.get("session_id") or rec.get("turn_id")
            if not sid:
                continue
            sig = rec.get("signal")
            if sig in VALID_SIGNALS:
                by_session.setdefault(sid, set()).add(sig)
        recovery: Set[str] = set()
        for sid, signals in by_session.items():
            if signals & NEGATIVE_SIGNALS and signals & POSITIVE_SIGNALS:
                recovery.add(sid)
        return recovery

    def build(
        self,
        name: Optional[str] = None,
        min_positive_per_split: int = 1,
    ) -> Dict[str, Any]:
        """Export training-ready datasets and return a summary.

        Args:
            name: optional run name; defaults to a timestamped folder.
            min_positive_per_split: if a split has fewer positive examples than
                this, no file is written for that split.

        Returns:
            Dict with run name, paths, counts, and recovery stats.
        """
        run_name = name or datetime.now(timezone.utc).strftime("dataset_%Y%m%d_%H%M%S")
        run_dir = self.output_dir / run_name
        run_dir.mkdir(parents=True, exist_ok=True)

        records = self._read_records()
        recovery_ids = self._recovery_session_ids(records)

        coder_examples: List[Dict[str, Any]] = []
        vision_examples: List[Dict[str, Any]] = []
        recovery_count = 0
        skipped = 0

        for rec in records:
            signal = rec.get("signal")
            if signal not in POSITIVE_SIGNALS:
                skipped += 1
                continue

            messages = [_clean_message(m) for m in (rec.get("messages") or [])]
            messages.append(
                _clean_message({"role": "assistant", "content": rec.get("output", "")})
            )
            example: Dict[str, Any] = {"messages": messages}
            sid = rec.get("session_id")
            if sid and sid in recovery_ids:
                example["recovery"] = True
                recovery_count += 1

            if _is_vision_record(rec):
                vision_examples.append(example)
            else:
                coder_examples.append(example)

        coder_path = run_dir / "coder.jsonl"
        vision_path = run_dir / "vision.jsonl"
        stats: Dict[str, Any] = {
            "run_name": run_name,
            "run_dir": str(run_dir),
            "total_turns": len(records),
            "positive_turns": len(records) - skipped,
            "recovery_examples": recovery_count,
            "coder_count": len(coder_examples),
            "vision_count": len(vision_examples),
            "coder_path": None,
            "vision_path": None,
        }

        if len(coder_examples) >= min_positive_per_split:
            stats["coder_path"] = str(coder_path)
            with coder_path.open("w", encoding="utf-8") as handle:
                for ex in coder_examples:
                    handle.write(json.dumps(ex, ensure_ascii=False) + "\n")

        if len(vision_examples) >= min_positive_per_split:
            stats["vision_path"] = str(vision_path)
            with vision_path.open("w", encoding="utf-8") as handle:
                for ex in vision_examples:
                    handle.write(json.dumps(ex, ensure_ascii=False) + "\n")

        stats_path = run_dir / "stats.json"
        try:
            stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write dataset stats: %s", exc)

        logger.info(
            "Dataset build complete: coder=%d, vision=%d, recovery=%d",
            len(coder_examples),
            len(vision_examples),
            recovery_count,
        )
        return stats

    def list_runs(self) -> List[Dict[str, Any]]:
        """Return metadata for every dataset run on disk."""
        runs: List[Dict[str, Any]] = []
        if not self.output_dir.is_dir():
            return runs
        for run_dir in sorted(self.output_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            stats_path = run_dir / "stats.json"
            stats: Dict[str, Any] = {"run_name": run_dir.name, "run_dir": str(run_dir)}
            if stats_path.is_file():
                try:
                    stats = json.loads(stats_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            runs.append(stats)
        return runs


__all__ = ["DatasetBuilder", "POSITIVE_SIGNALS", "NEGATIVE_SIGNALS"]

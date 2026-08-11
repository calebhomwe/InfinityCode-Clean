"""Seed synthetic accepted sessions from the eval task bank.

This is a demo/training-pipeline kick-starter: it turns the existing eval tasks
into accepted session logs so the dataset builder has something to work with
before real user feedback accumulates. Replace these with real sessions as soon
as possible.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

try:
    from backend.core.session_logger import SessionLogger, SIGNAL_ACCEPTED
except ImportError:
    from core.session_logger import SessionLogger, SIGNAL_ACCEPTED  # type: ignore


def _build_output(task: dict) -> str:
    """Build a plausible assistant output from expected_contains."""
    contains = task.get("expected_contains") or []
    if task.get("expected_exact"):
        return task["expected_exact"]
    if len(contains) == 1:
        return contains[0]
    # Build a tiny fenced snippet for coding tasks.
    body = "\n".join(contains)
    return f"```python\n{body}\n```"


def main() -> int:
    data_dir = ROOT / "backend"
    logger = SessionLogger(data_dir)
    eval_path = data_dir / "data" / "eval_tasks.jsonl"
    if not eval_path.is_file():
        print(f"Eval tasks not found: {eval_path}")
        return 1

    created = 0
    with eval_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            task = json.loads(line)
            session_id = f"demo-{task['id']}"
            messages = [{"role": "user", "content": task["prompt"]}]
            output = _build_output(task)
            turn_id = logger.log_turn(
                session_id=session_id,
                messages=messages,
                output=output,
                model="synthetic-demo",
                lane=task.get("lane", "cheap"),
                cost_usd=0.0,
                input_tokens=len(task["prompt"].split()),
                output_tokens=len(output.split()),
                latency_ms=0.0,
                metadata={"source": "demo_seed", "task_id": task["id"]},
            )
            logger.signal(session_id, turn_id, SIGNAL_ACCEPTED)
            created += 1

    print(f"Seeded {created} accepted demo sessions into {logger.base_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

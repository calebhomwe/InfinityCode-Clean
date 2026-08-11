"""Unit tests for the plan-anchored long-horizon protocol."""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.plan_anchor import PlanAnchor
except ImportError:
    from core.plan_anchor import PlanAnchor  # type: ignore


def test_plan_anchor_creates_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        anchor = PlanAnchor(goal="build a thing", work_dir=Path(tmp))
        assert anchor.path.is_file()
        assert "build a thing" in anchor.path.read_text(encoding="utf-8")


def test_add_subtask_and_mark_done() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        anchor = PlanAnchor(goal="g", work_dir=Path(tmp))
        anchor.add_subtask("step one")
        anchor.add_subtask("step two")
        anchor.mark_done("step one")
        summary = anchor.summary()
        assert summary["total"] == 2
        assert summary["done"] == 1
        assert summary["remaining"] == 1


def test_update_from_response() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        anchor = PlanAnchor(goal="g", work_dir=Path(tmp))
        anchor.add_subtask("old task")
        response = (
            "Some output\n\n```plan\n- [x] research\n- [ ] implement\n```"
        )
        anchor.update_from_response(response)
        assert len(anchor.subtasks) == 2
        titles = [st["title"] for st in anchor.subtasks]
        assert "research" in titles and "implement" in titles
        assert anchor.subtasks[0]["done"]
        assert not anchor.subtasks[1]["done"]


def test_to_prompt_format() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        anchor = PlanAnchor(goal="test goal", work_dir=Path(tmp))
        anchor.add_subtask("do x")
        prompt = anchor.to_prompt()
        assert "# Plan" in prompt
        assert "Goal: test goal" in prompt
        assert "[ ] do x" in prompt
        assert "```plan" in prompt


def test_load_existing_plan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        anchor = PlanAnchor(goal="g", work_dir=Path(tmp))
        anchor.add_subtask("persisted")
        anchor.mark_done("persisted")

        anchor2 = PlanAnchor(goal="g", work_dir=Path(tmp))
        assert any(st["title"] == "persisted" and st["done"] for st in anchor2.subtasks)


TESTS: List[Tuple[str, Callable[[], None]]] = [
    (name, obj)
    for name, obj in list(globals().items())
    if name.startswith("test_") and callable(obj)
]


def main() -> int:
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""Regression tests for the benchmark suite: profiles, execution scoring,
verbosity caps, and the no-LLM-judge guarantee."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.eval_harness import EvalHarness, EvalTask
except ImportError:
    from core.eval_harness import EvalHarness, EvalTask  # type: ignore

PROFILES = [
    "livebench", "arc_agi2", "gpqa", "simpleqa", "swebench", "hle",
    "mmmu", "tau_bench", "lmarena", "seal", "scicode", "hhem",
]


def _harness() -> EvalHarness:
    return EvalHarness(None, None, Path.cwd())


def test_all_twelve_profiles_load() -> None:
    harness = _harness()
    for profile in PROFILES:
        tasks = harness.load_tasks(profile=profile)
        assert len(tasks) >= 4, f"profile {profile} has {len(tasks)} tasks, want >= 4"
    aggregate = harness.load_tasks(profile="benchmarks")
    assert len(aggregate) == sum(len(harness.load_tasks(profile=p)) for p in PROFILES)


def test_core_profile_unaffected() -> None:
    tasks = _harness().load_tasks(profile="core")
    assert len(tasks) == 20, f"core suite should stay at 20 tasks, got {len(tasks)}"


def test_execution_scoring_pass_and_fail() -> None:
    harness = _harness()
    task = EvalTask(
        id="x", name="x", lane="cheap", prompt="p",
        check_run=True, expected_output="42",
    )
    passed, score, reason = harness._score(task, "print(6 * 7)")
    assert passed and score == 1.0, reason
    passed, score, reason = harness._score(task, "print(41)")
    assert not passed and "mismatch" in reason
    passed, score, reason = harness._score(task, "raise ValueError('boom')")
    assert not passed and "run failed" in reason


def test_execution_scoring_strips_fences() -> None:
    harness = _harness()
    task = EvalTask(
        id="x", name="x", lane="cheap", prompt="p",
        check_run=True, expected_output="7",
    )
    fenced = "Here you go:\n```python\nprint(3 + 4)\n```\nDone!"
    passed, score, reason = harness._score(task, fenced)
    assert passed and score == 1.0, reason


def test_verbosity_cap_is_objective() -> None:
    harness = _harness()
    task = EvalTask(
        id="x", name="x", lane="cheap", prompt="p",
        expected_contains=["dict"], max_output_chars=20,
    )
    passed, _, _ = harness._score(task, "dict")
    assert passed
    passed, score, reason = harness._score(task, "dict " + "filler " * 20)
    assert not passed and "verbosity" in reason


def test_benchmark_tasks_have_capability_labels() -> None:
    harness = _harness()
    tasks = harness.load_tasks(profile="benchmarks")
    for task in tasks:
        capability = harness._capability_for(task)
        assert capability, f"task {task.id} has no capability label"


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

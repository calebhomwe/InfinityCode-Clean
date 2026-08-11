"""Unit tests for the eval harness (no live LLM calls)."""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# Make imports work when running this script directly.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Import through the package re-export where possible.
try:
    from backend.core.eval_harness import EvalHarness, EvalTask
except ImportError:
    from core.eval_harness import EvalHarness, EvalTask  # type: ignore


class StubClient:
    """Returns scripted responses for eval task ids."""

    def __init__(self, responses: Dict[str, str]) -> None:
        self.responses = responses
        self.calls: List[Tuple[str, List[Dict[str, str]]]] = []

    def chat(
        self,
        model_id: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.calls.append((model_id, messages))
        # Derive a stable response from the prompt content.
        prompt = messages[-1]["content"] if messages else ""
        for key, text in self.responses.items():
            if key in prompt:
                return {"text": text, "cost_usd": 0.001, "input_tokens": 10, "output_tokens": 10}
        return {"text": "default response", "cost_usd": 0.001, "input_tokens": 10, "output_tokens": 10}


def test_load_tasks() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({
            "id": "t1", "name": "Test 1", "lane": "cheap", "prompt": "say hi",
            "expected_contains": ["hi"], "max_tokens": 100, "weight": 2.0,
        }) + "\n")
        tmp.write(json.dumps({
            "id": "t2", "name": "Test 2", "lane": "smart", "prompt": "solve",
            "expected_exact": "42", "max_tokens": 50,
        }) + "\n")
        path = Path(tmp.name)
    try:
        harness = EvalHarness(None, None, Path.cwd())
        tasks = harness.load_tasks(path)
        assert len(tasks) == 2
        assert tasks[0].id == "t1"
        assert tasks[0].weight == 2.0
        assert tasks[1].expected_exact == "42"
    finally:
        path.unlink()


def test_load_tasks_filters_by_profile() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({
            "id": "spatial", "name": "spatial", "lane": "cheap", "prompt": "p",
            "metadata": {"profiles": ["spatial_3d"], "capability": "spatial_reasoning"},
        }) + "\n")
        tmp.write(json.dumps({
            "id": "agentic", "name": "agentic", "lane": "cheap", "prompt": "p",
            "metadata": {"profiles": "agentic", "capability": "agentic"},
        }) + "\n")
        path = Path(tmp.name)
    try:
        harness = EvalHarness(None, None, Path.cwd())
        tasks = harness.load_tasks(path, profile="spatial_3d")
        assert [task.id for task in tasks] == ["spatial"]
    finally:
        path.unlink()


def test_score_contains() -> None:
    harness = EvalHarness(None, None, Path.cwd())
    task = EvalTask(
        id="x", name="x", lane="cheap", prompt="p",
        expected_contains=["alpha", "beta"],
    )
    passed, score, reason = harness._score(task, "alpha beta gamma")
    assert passed and score == 1.0, reason

    passed, score, reason = harness._score(task, "alpha gamma")
    assert not passed and score == 0.0
    assert "beta" in reason


def test_score_exact() -> None:
    harness = EvalHarness(None, None, Path.cwd())
    task = EvalTask(
        id="x", name="x", lane="cheap", prompt="p",
        expected_exact="hello",
    )
    assert harness._score(task, "hello") == (True, 1.0, "exact match")
    assert harness._score(task, "  hello  ") == (True, 1.0, "exact match")
    assert harness._score(task, "world") == (False, 0.0, "exact mismatch")


def test_score_syntax() -> None:
    harness = EvalHarness(None, None, Path.cwd())
    task = EvalTask(
        id="x", name="x", lane="cheap", prompt="p", check_syntax=True,
    )
    assert harness._score(task, "def f():\n    return 1")[0]
    assert not harness._score(task, "def f(\n")[0]


def test_run_report_format() -> None:
    responses = {
        "hello": "def hello():\n    return 'Hello, world!'",
        "fib": "def fib(n):\n    return n if n < 2 else fib(n-1)+fib(n-2)",
        "reverses": "items[::-1]",
    }
    client = StubClient(responses)
    harness = EvalHarness(client, None, Path.cwd())
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({
            "id": "t1", "name": "hello", "lane": "cheap",
            "prompt": "Write a function hello", "expected_contains": ["def hello"],
            "check_syntax": True, "max_tokens": 200,
        }) + "\n")
        tmp.write(json.dumps({
            "id": "t2", "name": "fib", "lane": "cheap",
            "prompt": "Write a function fib", "expected_contains": ["def fib"],
            "check_syntax": True, "max_tokens": 200,
        }) + "\n")
        tmp.write(json.dumps({
            "id": "t3", "name": "reverse", "lane": "cheap",
            "prompt": "Write an expression that reverses a list items", "expected_contains": ["items[::-1]"],
            "max_tokens": 100,
        }) + "\n")
        path = Path(tmp.name)
    try:
        report = harness.run(lane="cheap", tasks_path=path)
        assert "run_id" in report
        assert report["lane"] == "cheap"
        assert report["tasks_total"] == 3
        assert report["tasks_passed"] == 3
        assert report["aggregate_score"] == 1.0
        assert report["summary"]["pass_rate"] == 1.0
        assert len(report["results"]) == 3
        for r in report["results"]:
            assert "latency_ms" in r
            assert "cost_usd" in r
            assert "capability" in r
    finally:
        path.unlink()


def test_run_with_failure() -> None:
    client = StubClient({})  # all default responses fail the checks
    harness = EvalHarness(client, None, Path.cwd())
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({
            "id": "t1", "name": "exact", "lane": "cheap",
            "prompt": "say 42", "expected_exact": "42", "max_tokens": 50,
        }) + "\n")
        path = Path(tmp.name)
    try:
        report = harness.run(lane="cheap", tasks_path=path)
        assert report["tasks_passed"] == 0
        assert report["aggregate_score"] == 0.0
    finally:
        path.unlink()


def test_run_groups_failures_by_capability() -> None:
    client = StubClient({"write function": "def ready():\n    return True"})
    harness = EvalHarness(client, None, Path.cwd())
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({
            "id": "code-ok", "name": "write function", "lane": "cheap",
            "prompt": "write function", "expected_contains": ["def ready"],
            "metadata": {"capability": "code"},
        }) + "\n")
        tmp.write(json.dumps({
            "id": "browser-fail", "name": "browser task", "lane": "cheap",
            "prompt": "browser task", "expected_exact": "done",
            "metadata": {"capability": "browser"},
        }) + "\n")
        path = Path(tmp.name)
    try:
        report = harness.run(lane="cheap", tasks_path=path)
        assert report["capabilities"]["code"]["score"] == 1.0
        assert report["capabilities"]["browser"]["score"] == 0.0
        assert report["summary"]["weakest_capability"] == "browser"
        assert report["weaknesses"][0]["failed_tasks"][0]["id"] == "browser-fail"
    finally:
        path.unlink()


def test_local_eval_never_calls_cloud_client() -> None:
    class LocalHarness(EvalHarness):
        def _chat_local(
            self, model_id: str, _messages: List[Dict[str, str]], _max_tokens: int
        ) -> str:
            assert model_id == "local/fable-fast"
            return "hello"

    class CloudMustNotRun:
        def chat(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
            raise AssertionError("local evaluation must not call a cloud client")

    task = EvalTask(
        id="local", name="local", lane="fable_fast", prompt="p", expected_exact="hello"
    )
    result = LocalHarness(CloudMustNotRun(), None, Path.cwd())._run_task(
        task, "local/fable-fast"
    )
    assert result.passed
    assert result.cost_usd == 0.0


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

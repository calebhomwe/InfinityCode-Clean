"""Unit tests for the monthly retrain loop."""

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
    from backend.core.retrain_loop import RetrainLoop
except ImportError:
    from core.retrain_loop import RetrainLoop  # type: ignore


class FakeDatasetBuilder:
    def __init__(self, stats: Dict[str, Any]) -> None:
        self.stats = stats

    def build(self, name: str | None = None) -> Dict[str, Any]:
        return dict(self.stats)

    def list_runs(self) -> List[Dict[str, Any]]:
        return []


class FakeFineTuner:
    def __init__(self, configured: bool = True) -> None:
        self._configured = configured
        self.jobs: List[Dict[str, Any]] = []

    def configured(self) -> bool:
        return self._configured

    def upload_and_tune(self, **kwargs: Any) -> Dict[str, Any]:
        self.jobs.append(kwargs)
        return {"ok": True, "provider": "together", "job_id": "job_1", "status": "pending"}

    def status(self, job_id: str) -> Dict[str, Any]:
        return {"id": job_id, "status": "completed", "output_name": "custom-model-v2"}


class FakeLaneRouter:
    def __init__(self) -> None:
        self.custom_model: str | None = None

    def set_custom_lane(self, model_id: str) -> None:
        self.custom_model = model_id


def _make_loop(tmp: str, positive: int = 10, configured: bool = True) -> RetrainLoop:
    data_dir = Path(tmp)
    dataset_builder = FakeDatasetBuilder(
        {
            "coder_count": positive,
            "vision_count": 0,
            "coder_path": str(data_dir / "coder.jsonl"),
            "vision_path": None,
        }
    )
    # Create the coder file so start_tune can stat it.
    (data_dir / "coder.jsonl").write_text('{"messages": []}\n', encoding="utf-8")
    lane_router = FakeLaneRouter()

    def eval_factory() -> Any:
        class Harness:
            def run(self, **kwargs: Any) -> Dict[str, Any]:
                return {"aggregate_score": 0.95, "summary": {"score": 0.95}}
        return Harness()

    loop = RetrainLoop(
        dataset_builder=dataset_builder,
        lane_router=lane_router,
        eval_harness_factory=eval_factory,
        data_dir=data_dir,
        min_examples=5,
        provider="together",
        base_model="base-model",
    )
    loop.fine_tuner = FakeFineTuner(configured)
    return loop


def test_skips_when_not_enough_examples() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        loop = _make_loop(tmp, positive=2)
        loop.min_examples = 5
        result = loop.run()
        assert not result["ok"]
        assert result["status"] == "skipped"


def test_starts_tune_when_enough_examples() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        loop = _make_loop(tmp, positive=10)
        result = loop.run()
        assert result["ok"]
        assert result["status"] == "started"
        assert result["job"]["job_id"] == "job_1"


def test_poll_ships_better_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        loop = _make_loop(tmp, positive=10)
        loop.run()
        result = loop.poll()
        assert result["ok"]
        assert result["status"] == "completed"
        assert result["shipped"]
        assert result["model_id"] == "custom-model-v2"
        assert loop.state.best_model == "custom-model-v2"
        assert loop.lane_router.custom_model == "custom-model-v2"


def test_poll_does_not_ship_worse_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        loop = _make_loop(tmp, positive=10)
        loop.state.best_score = 0.99
        loop.run()
        result = loop.poll()
        assert result["ok"]
        assert not result["shipped"]


def test_status_returns_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        loop = _make_loop(tmp, positive=10)
        status = loop.status()
        assert status["configured"]
        assert status["provider"] == "together"


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

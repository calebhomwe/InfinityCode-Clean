"""Proof that active drills enter context and archived drills do not."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.benchmark_curriculum import BenchmarkCurriculum
from core.loop_engine import LoopEngine


class Client:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    def chat(self, _model: str, messages: list[dict[str, str]], *_args: Any, **_kwargs: Any) -> dict:
        self.messages.append(messages)
        return {"text": "A concise response with a verification step.", "cost_usd": 0.0}


def test_loop_retrieves_active_drill_and_excludes_archived_drill() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        curriculum = BenchmarkCurriculum(root)
        report = {"run_id": "eval-first", "results": [{
            "task_id": "spatial-1", "name": "ray cast", "capability": "spatial_reasoning",
            "passed": False, "score": 0.0, "reason": "missing: mathutils.Vector",
        }]}
        curriculum.record("cheap", report)

        client = Client()
        events = list(LoopEngine(client, data_dir=root).run("Design a 3D raycast helper", job_type="chat", max_iter=1))
        assert "cheap:spatial-1" in client.messages[0][0]["content"]
        assert events[-1]["drills_applied"] == ["cheap:spatial-1"]

        curriculum.record("cheap", {"run_id": "eval-pass", "results": [{
            "task_id": "spatial-1", "name": "ray cast", "capability": "spatial_reasoning",
            "passed": True, "score": 1.0,
        }]})
        client = Client()
        events = list(LoopEngine(client, data_dir=root).run("Design a 3D raycast helper", job_type="chat", max_iter=1))
        assert "cheap:spatial-1" not in client.messages[0][0]["content"]
        assert events[-1]["drills_applied"] == []


if __name__ == "__main__":
    test_loop_retrieves_active_drill_and_excludes_archived_drill()
    print("1/1 passed")

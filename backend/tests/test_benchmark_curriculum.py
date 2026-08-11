"""Offline tests for benchmark-driven learning drills."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.benchmark_curriculum import BenchmarkCurriculum


def test_failure_creates_drill_then_passing_resolves_it() -> None:
    with tempfile.TemporaryDirectory() as directory:
        curriculum = BenchmarkCurriculum(Path(directory))
        failed = curriculum.record("cheap", {"results": [{
            "task_id": "spatial-1", "name": "ray cast", "capability": "spatial_reasoning",
            "passed": False, "score": 0.0, "reason": "missing: mathutils.Vector",
        }]})
        assert len(failed) == 1
        assert failed[0]["status"] == "queued"
        assert "ray casts" in failed[0]["query"]
        topics = curriculum.queued_topics()
        assert topics[0]["key"] == "benchmark-spatial_reasoning"
        context, retrieved = curriculum.context_for("Build a Blender scene with a raycast")
        assert retrieved[0]["id"] == "cheap:spatial-1"
        assert "cheap:spatial-1" in context
        curriculum.record_application(["cheap:spatial-1"], "mission-42", "completed")
        assert curriculum.status()[0]["applications"][0]["mission_id"] == "mission-42"

        curriculum.record("cheap", {"results": [{
            "task_id": "spatial-1", "name": "ray cast", "capability": "spatial_reasoning",
            "passed": True, "score": 1.0,
        }]})
        assert curriculum.queued_topics() == []
        archived = curriculum.status()[0]
        assert archived["status"] == "archived"
        assert archived["resolution"]["task_id"] == "spatial-1"
        assert curriculum.context_for("Blender raycast")[0] == ""


if __name__ == "__main__":
    test_failure_creates_drill_then_passing_resolves_it()
    print("1/1 passed")

"""Offline checks for the Blender benchmark grader."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.benchmark_curriculum import BenchmarkCurriculum
from backend.core.blender_benchmark import _record_vehicle_learning, dense_vehicle_spec, local_9b_vehicle_prompt, random_object_spec, validate_scene, vehicle_spec
from backend.core.scene_graph import SceneGraph, SceneNode


def test_random_spec_is_repeatable_and_diverse() -> None:
    spec = random_object_spec(12)
    assert spec == random_object_spec(12)
    assert len(spec) == 5
    assert len({item["name"] for item in spec}) == 5
    assert len({tuple(item["location"]) for item in spec}) == 5


def test_validate_scene_checks_all_requested_facts() -> None:
    spec = random_object_spec(12, count=3)
    nodes = [
        SceneNode(
            name=item["name"], type="MESH", location=list(item["location"]),
            material=item["material"],
            custom_props={"infinity_benchmark_id": "random-scene-12", "infinity_primitive": item["primitive"]},
        )
        for item in spec
    ] + [
        SceneNode(name="Bench_Ground", type="MESH"),
        SceneNode(name="Bench_Camera", type="CAMERA"),
        SceneNode(name="Bench_KeyLight", type="LIGHT"),
    ]
    result = validate_scene(SceneGraph(nodes), spec, "random-scene-12")
    assert result["passed"]
    nodes[0].location[0] += 1
    assert not validate_scene(SceneGraph(nodes), spec, "random-scene-12")["passed"]


def test_local_9b_prompt_is_a_strict_reproducible_brief() -> None:
    blend_path = Path("C:/temp/vehicle.blend")
    prompt = local_9b_vehicle_prompt(vehicle_spec(), blend_path, "proof-1")
    assert "Return only a complete Blender Python" in prompt
    assert "Vehicle_Wheel_04" in prompt
    assert "infinity_benchmark_id" in prompt
    assert "vehicle.blend" in prompt


def test_dense_vehicle_has_a_real_detail_budget() -> None:
    spec = dense_vehicle_spec()
    assert len(spec) == 20
    assert sum(item["name"].startswith("Vehicle_Hub") for item in spec) == 4
    assert {"Vehicle_Headlight_L", "Vehicle_Headlight_R", "Vehicle_Spoiler"}.issubset({item["name"] for item in spec})


def test_vehicle_failure_becomes_a_retrievable_drill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reports = Path(tmp) / "eval_reports"
        queued = _record_vehicle_learning(reports, "vehicle-tier-2", {
            "failed_checks": [{"label": "wheel symmetry", "passed": False}],
        })
        assert len(queued) == 1
        context, drills = BenchmarkCurriculum(Path(tmp)).context_for("Build a Blender vehicle with spatial transforms")
        assert drills[0]["capability"] == "spatial_reasoning"
        assert "wheel symmetry" in context


if __name__ == "__main__":
    test_random_spec_is_repeatable_and_diverse()
    test_validate_scene_checks_all_requested_facts()
    test_local_9b_prompt_is_a_strict_reproducible_brief()
    test_dense_vehicle_has_a_real_detail_budget()
    test_vehicle_failure_becomes_a_retrievable_drill()
    print("5/5 passed")

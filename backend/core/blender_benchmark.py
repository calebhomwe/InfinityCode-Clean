"""Deterministic, evidence-first Blender benchmark for the agent harness.

This is deliberately not a screenshot-only test.  It creates a seeded scene,
captures Blender's own scene graph, and checks every requested object against
its expected primitive, transform, material, camera, saved .blend and render.
The same runner can later grade a model-authored bpy script against a task spec.
"""

from __future__ import annotations

import json
import os
import random
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from backend.core.art_bridge import BlenderBridge, BlenderBridgeError, find_blender_executable
    from backend.core.benchmark_curriculum import BenchmarkCurriculum
    from backend.core.scene_graph import SceneGraph
except ImportError:
    from core.art_bridge import BlenderBridge, BlenderBridgeError, find_blender_executable  # type: ignore
    from core.benchmark_curriculum import BenchmarkCurriculum  # type: ignore
    from core.scene_graph import SceneGraph  # type: ignore


_PRIMITIVES: Dict[str, str] = {
    "cube": "primitive_cube_add",
    "uv_sphere": "primitive_uv_sphere_add",
    "cylinder": "primitive_cylinder_add",
    "cone": "primitive_cone_add",
    "torus": "primitive_torus_add",
}
_POSITIONS: Tuple[Tuple[float, float, float], ...] = (
    (-3.0, -2.0, 1.0), (-3.0, 2.0, 1.0), (0.0, 0.0, 1.0),
    (3.0, -2.0, 1.0), (3.0, 2.0, 1.0), (0.0, 3.5, 1.0),
)
_COLORS: Tuple[Tuple[float, float, float, float], ...] = (
    (0.85, 0.12, 0.08, 1.0), (0.08, 0.36, 0.9, 1.0),
    (0.12, 0.72, 0.28, 1.0), (0.92, 0.48, 0.06, 1.0),
    (0.62, 0.16, 0.82, 1.0),
)


def random_object_spec(seed: int, count: int = 5) -> List[Dict[str, Any]]:
    """Return a repeatable, diverse object prompt suitable for an agent run."""
    if count < 3 or count > len(_POSITIONS):
        raise ValueError("count must be between 3 and 6")
    rng = random.Random(seed)
    kinds = list(_PRIMITIVES)
    rng.shuffle(kinds)
    positions = list(_POSITIONS)
    rng.shuffle(positions)
    result: List[Dict[str, Any]] = []
    for index in range(count):
        kind = kinds[index % len(kinds)]
        result.append({
            "name": f"Bench_{index + 1:02d}_{kind}",
            "primitive": kind,
            "location": list(positions[index]),
            "material": f"BenchMat_{index + 1:02d}",
            "color": list(_COLORS[index]),
        })
    return result


def benchmark_prompt(spec: List[Dict[str, Any]]) -> str:
    lines = [
        "In Blender, create exactly these labelled objects on a ground plane.",
        "Use the requested primitive, location, and material name for each.",
        "Add a camera and a light, save the .blend, then render a PNG.",
    ]
    for item in spec:
        lines.append(
            f"- {item['name']}: {item['primitive']} at {item['location']}, material {item['material']}"
        )
    return "\n".join(lines)


def vehicle_spec() -> List[Dict[str, Any]]:
    """A harder structured-asset brief with symmetry and hierarchy facts."""
    wheel_positions = ((-2.25, -1.4, 0.58), (-2.25, 1.4, 0.58), (2.25, -1.4, 0.58), (2.25, 1.4, 0.58))
    wheels = [
        {
            "name": f"Vehicle_Wheel_{index + 1:02d}", "primitive": "cylinder",
            "location": list(location), "scale": [0.58, 0.58, 0.34],
            "rotation": [1.570796, 0.0, 0.0], "material": "Rubber",
            "color": [0.025, 0.03, 0.04, 1.0], "parent": "Vehicle_Body",
        }
        for index, location in enumerate(wheel_positions)
    ]
    return [
        {
            "name": "Vehicle_Body", "primitive": "cube", "location": [0.0, 0.0, 1.35],
            "scale": [3.0, 1.35, 0.55], "material": "VehiclePaint",
            "color": [0.08, 0.33, 0.88, 1.0],
        },
        {
            "name": "Vehicle_Cabin", "primitive": "cube", "location": [0.35, 0.0, 2.28],
            "scale": [1.35, 1.12, 0.48], "material": "WindowGlass",
            "color": [0.08, 0.55, 0.8, 1.0], "parent": "Vehicle_Body",
        },
        *wheels,
    ]


def dense_vehicle_spec() -> List[Dict[str, Any]]:
    """Tier three: a denser game-car asset with 20 separately checked parts."""
    spec = vehicle_spec()
    hub_positions = ((-2.25, -1.76, 0.58), (-2.25, 1.76, 0.58), (2.25, -1.76, 0.58), (2.25, 1.76, 0.58))
    for index, location in enumerate(hub_positions, start=1):
        spec.append({
            "name": f"Vehicle_Hub_{index:02d}", "primitive": "cylinder", "location": list(location),
            "scale": [0.25, 0.25, 0.08], "rotation": [1.570796, 0.0, 0.0],
            "material": "HubMetal", "color": [0.52, 0.56, 0.62, 1.0],
            "parent": f"Vehicle_Wheel_{index:02d}",
        })
    spec.extend([
        {"name": "Vehicle_Headlight_L", "primitive": "cube", "location": [3.12, -0.76, 1.42], "scale": [0.12, 0.3, 0.16], "material": "Headlight", "color": [1.0, 0.84, 0.38, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_Headlight_R", "primitive": "cube", "location": [3.12, 0.76, 1.42], "scale": [0.12, 0.3, 0.16], "material": "Headlight", "color": [1.0, 0.84, 0.38, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_TailLight_L", "primitive": "cube", "location": [-3.12, -0.76, 1.42], "scale": [0.12, 0.3, 0.16], "material": "TailLight", "color": [0.94, 0.04, 0.03, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_TailLight_R", "primitive": "cube", "location": [-3.12, 0.76, 1.42], "scale": [0.12, 0.3, 0.16], "material": "TailLight", "color": [0.94, 0.04, 0.03, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_FrontBumper", "primitive": "cube", "location": [3.18, 0.0, 0.88], "scale": [0.15, 1.4, 0.19], "material": "Trim", "color": [0.035, 0.04, 0.05, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_RearBumper", "primitive": "cube", "location": [-3.18, 0.0, 0.88], "scale": [0.15, 1.4, 0.19], "material": "Trim", "color": [0.035, 0.04, 0.05, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_Windshield", "primitive": "cube", "location": [1.15, 0.0, 2.3], "scale": [0.06, 1.13, 0.36], "material": "WindowGlass", "color": [0.08, 0.55, 0.8, 1.0], "parent": "Vehicle_Cabin"},
        {"name": "Vehicle_Grille", "primitive": "cube", "location": [3.16, 0.0, 1.1], "scale": [0.08, 0.65, 0.13], "material": "Trim", "color": [0.035, 0.04, 0.05, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_Exhaust", "primitive": "cylinder", "location": [-3.18, -0.66, 0.72], "scale": [0.12, 0.12, 0.28], "rotation": [1.570796, 0.0, 0.0], "material": "HubMetal", "color": [0.52, 0.56, 0.62, 1.0], "parent": "Vehicle_Body"},
        {"name": "Vehicle_Spoiler", "primitive": "cube", "location": [-2.0, 0.0, 2.35], "scale": [0.16, 1.25, 0.08], "material": "Trim", "color": [0.035, 0.04, 0.05, 1.0], "parent": "Vehicle_Body"},
    ])
    return spec


def vehicle_prompt(spec: List[Dict[str, Any]]) -> str:
    return (
        "Build a low-poly game-ready car. The four wheels must be symmetric around "
        "the body and parented to it; preserve every requested transform.\n\n"
        + benchmark_prompt(spec)
    )


def dense_vehicle_prompt(spec: List[Dict[str, Any]]) -> str:
    return (
        "Build a dense, game-ready low-poly car with readable silhouette, lights, "
        "bumpers, wheel hubs and a spoiler. All attached details must retain their "
        "world transforms after parenting.\n\n" + vehicle_prompt(spec)
    )


def local_9b_vehicle_prompt(spec: List[Dict[str, Any]], blend_path: Path, benchmark_id: str) -> str:
    """Strict prompt for an agent-authored tier-two scene, not the reference."""
    tags = "\n".join(
        f'- After creating {item["name"]}, set obj["infinity_primitive"] = {item["primitive"]!r} and obj["infinity_benchmark_id"] = {benchmark_id!r}.'
        for item in spec
    )
    return f'''\
Return only a complete Blender Python (bpy) script. Do not explain it and do not use Markdown fences.

{vehicle_prompt(spec)}

Strict grading requirements:
- Delete the default scene before building.
- Set exact object names, world locations, scales, rotations, materials, and required parents from the brief.
- Parent the cabin and all four wheels to Vehicle_Body while retaining their requested world transforms.
{tags}
- Add Bench_Ground, one camera, and at least one light.
- Save the scene exactly to {str(blend_path)!r}.
The renderer will save the PNG after your script. Your script must be self-contained.
'''


def _ask_local_9b(prompt: str) -> str:
    """Call only the owner's fable-fast server; never fail over to a cloud model."""
    endpoint = os.environ.get("INFINITY_FABLE_FAST_ENDPOINT", "http://127.0.0.1:1234/v1").rstrip("/")
    request = urllib.request.Request(
        f"{endpoint}/chat/completions",
        data=json.dumps({
            "model": os.environ.get("INFINITY_FABLE_FAST_MODEL", "fable-fast"),
            "temperature": 0.15,
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": "You are a precise Blender Python engineer. Return executable bpy code only."},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310 - fixed owner-controlled localhost endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise RuntimeError(
            "local/fable-fast is unavailable at " + endpoint + "; start the 9B local server before running this benchmark."
        ) from exc
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("local/fable-fast returned an invalid chat-completions response") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("local/fable-fast returned no Blender script")
    return content.strip()


def _record_vehicle_learning(output_dir: Path, suite: str, validation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn deterministic Blender misses into scoped persistent drills."""
    failures = list(validation.get("failed_checks") or [])
    groups: Dict[str, List[str]] = {"blender": [], "spatial_reasoning": []}
    for item in failures:
        label = str(item.get("label") or "scene requirement")
        target = "spatial_reasoning" if any(word in label.lower() for word in ("location", "scale", "rotation", "parent", "symmetry", "wheel")) else "blender"
        groups[target].append(label)
    report = {
        "run_id": f"{suite}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "results": [
            {
                "task_id": f"{suite}-{capability}",
                "name": f"{suite} {capability.replace('_', ' ')}",
                "capability": capability,
                "passed": not reasons,
                "score": 1.0 if not reasons else 0.0,
                "reason": "; ".join(reasons[:6]) or "all checks passed",
            }
            for capability, reasons in groups.items()
        ],
    }
    return BenchmarkCurriculum(Path(output_dir).parent).record("fable_fast", report)


def reference_script(spec: List[Dict[str, Any]], blend_path: Path, benchmark_id: str) -> str:
    """Build a trusted reference scene for integration and regression testing."""
    serialized = json.dumps(spec)
    return f'''\
import bpy
import json
from mathutils import Vector

SPEC = json.loads({serialized!r})
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

def material(name, color):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = color
        principled.inputs["Roughness"].default_value = 0.36
    return mat

for item in SPEC:
    getattr(bpy.ops.mesh, {{
        "cube": "primitive_cube_add",
        "uv_sphere": "primitive_uv_sphere_add",
        "cylinder": "primitive_cylinder_add",
        "cone": "primitive_cone_add",
        "torus": "primitive_torus_add",
    }}[item["primitive"]])(location=item["location"])
    obj = bpy.context.active_object
    obj.name = item["name"]
    obj["infinity_benchmark_id"] = {benchmark_id!r}
    obj["infinity_primitive"] = item["primitive"]
    obj.scale = item.get("scale", (1.0, 1.0, 1.0))
    obj.rotation_euler = item.get("rotation", (0.0, 0.0, 0.0))
    obj.data.materials.append(material(item["material"], item["color"]))

for item in SPEC:
    # Reapply transforms by stable name before introducing hierarchy.  Blender
    # operators can leave the final active object stale in headless mode.
    obj = bpy.data.objects[item["name"]]
    obj.location = item["location"]
    obj.scale = item.get("scale", (1.0, 1.0, 1.0))
    obj.rotation_euler = item.get("rotation", (0.0, 0.0, 0.0))

# Evaluate every world matrix before preserving it through parent assignment.
bpy.context.view_layer.update()

for item in SPEC:
    parent_name = item.get("parent")
    if parent_name:
        obj = bpy.data.objects[item["name"]]
        world_matrix = obj.matrix_world.copy()
        obj.parent = bpy.data.objects[parent_name]
        obj.matrix_world = world_matrix

bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
ground = bpy.context.active_object
ground.name = "Bench_Ground"
ground.data.materials.append(material("BenchGround", (0.06, 0.07, 0.09, 1.0)))

bpy.ops.object.light_add(type="AREA", location=(2, -4, 7))
light = bpy.context.active_object
light.name = "Bench_KeyLight"
light.data.energy = 1200
light.data.shape = "DISK"
light.data.size = 5

bpy.ops.object.camera_add(location=(10, -12, 9))
camera = bpy.context.active_object
camera.name = "Bench_Camera"
camera.rotation_euler = ((Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler())
bpy.context.scene.camera = camera

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 640
scene.render.resolution_y = 480
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.world.color = (0.025, 0.03, 0.05)
# Force Blender to evaluate the final child transform before the scene is
# saved, rendered, or inspected by a following probe script.
bpy.context.view_layer.update()
bpy.ops.wm.save_as_mainfile(filepath={str(blend_path)!r})
'''


def validate_scene(graph: SceneGraph, spec: List[Dict[str, Any]], benchmark_id: str) -> Dict[str, Any]:
    """Grade the Blender-owned scene graph against the requested object facts."""
    nodes = {node.name: node for node in graph.nodes}
    checks: List[Dict[str, Any]] = []

    def check(label: str, passed: bool, detail: str) -> None:
        checks.append({"label": label, "passed": passed, "detail": detail})

    for item in spec:
        node = nodes.get(item["name"])
        check(f"{item['name']} exists", node is not None, "named object present")
        if node is None:
            continue
        props = node.custom_props
        check(f"{item['name']} primitive", props.get("infinity_primitive") == item["primitive"], str(props.get("infinity_primitive")))
        check(f"{item['name']} benchmark", props.get("infinity_benchmark_id") == benchmark_id, str(props.get("infinity_benchmark_id")))
        observed_location = node.world_location if node.world_location is not None else node.location
        close = all(abs(float(a) - float(b)) < 0.02 for a, b in zip(observed_location, item["location"]))
        check(f"{item['name']} location", close, f"observed {observed_location}")
        check(f"{item['name']} material", node.material == item["material"], str(node.material))
        if "scale" in item:
            observed_scale = node.world_scale if node.world_scale is not None else node.scale
            close_scale = all(abs(float(a) - float(b)) < 0.02 for a, b in zip(observed_scale, item["scale"]))
            check(f"{item['name']} scale", close_scale, f"observed {observed_scale}")
        if "rotation" in item:
            observed_rotation = node.world_rotation if node.world_rotation is not None else node.rotation
            close_rotation = all(abs(float(a) - float(b)) < 0.02 for a, b in zip(observed_rotation, item["rotation"]))
            check(f"{item['name']} rotation", close_rotation, f"observed {observed_rotation}")
        if "parent" in item:
            check(f"{item['name']} parent", node.parent == item["parent"], str(node.parent))

    check("ground plane", nodes.get("Bench_Ground") is not None, "ground exists")
    check("camera", any(node.type == "CAMERA" for node in graph.nodes), "camera exists")
    check("light", any(node.type == "LIGHT" for node in graph.nodes), "light exists")
    failures = [check for check in checks if not check["passed"]]
    return {
        "passed": not failures,
        "checks": checks,
        "failed_checks": failures,
        "scene_object_count": len(graph.nodes),
    }


def validate_vehicle(graph: SceneGraph, spec: List[Dict[str, Any]], benchmark_id: str) -> Dict[str, Any]:
    """Add vehicle-level relational grading on top of object-level facts."""
    result = validate_scene(graph, spec, benchmark_id)
    nodes = {node.name: node for node in graph.nodes}
    checks = result["checks"]

    def check(label: str, passed: bool, detail: str) -> None:
        checks.append({"label": label, "passed": passed, "detail": detail})

    wheels = [nodes.get(f"Vehicle_Wheel_{index:02d}") for index in range(1, 5)]
    wheel_nodes = [wheel for wheel in wheels if wheel is not None]
    check("four wheels", len(wheel_nodes) == 4, f"found {len(wheel_nodes)}")
    if len(wheel_nodes) == 4:
        positions = [wheel.world_location if wheel.world_location is not None else wheel.location for wheel in wheel_nodes]
        symmetric = all(abs(position[2] - 0.58) < 0.02 for position in positions)
        symmetric = symmetric and {round(abs(position[0]), 2) for position in positions} == {2.25}
        symmetric = symmetric and {round(abs(position[1]), 2) for position in positions} == {1.4}
        check("wheel symmetry", symmetric, f"world positions {positions}")
        check("wheel hierarchy", all(wheel.parent == "Vehicle_Body" for wheel in wheel_nodes), "all wheels parented to body")
    failures = [item for item in checks if not item["passed"]]
    result["passed"] = not failures
    result["failed_checks"] = failures
    return result


def run_random_scene_proof(output_dir: Path, seed: int = 20260802, count: int = 5) -> Dict[str, Any]:
    """Run a real Blender scene proof and retain all inspectable artifacts."""
    spec = random_object_spec(seed, count)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_dir) / f"blender_random_scene_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    benchmark_id = f"random-scene-{seed}"
    blend_path = run_dir / "random_scene.blend"
    render_path = run_dir / "random_scene.png"
    script_path = run_dir / "reference_scene.py"
    script = reference_script(spec, blend_path, benchmark_id)
    script_path.write_text(script, encoding="utf-8")
    bridge = BlenderBridge(find_blender_executable())
    try:
        bridge.run_script(script, render_path)
        graph = bridge.capture_scene(script)
    except BlenderBridgeError as exc:
        return {
            "ok": False, "passed": False, "seed": seed, "spec": spec,
            "prompt": benchmark_prompt(spec), "error": str(exc),
        }
    validation = validate_scene(graph, spec, benchmark_id)
    report = {
        "ok": bool(validation["passed"] and render_path.is_file() and blend_path.is_file()),
        "seed": seed,
        "prompt": benchmark_prompt(spec),
        "spec": spec,
        "blender_executable": bridge.blender_executable,
        "render_path": str(render_path),
        "blend_path": str(blend_path),
        "script_path": str(script_path),
        "scene_graph": graph.to_dict(),
        **validation,
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_vehicle_proof(output_dir: Path) -> Dict[str, Any]:
    """Run the tier-two car benchmark and retain its render, scene and report."""
    spec = vehicle_spec()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_dir) / f"blender_vehicle_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    benchmark_id = "vehicle-tier-2"
    blend_path = run_dir / "vehicle.blend"
    render_path = run_dir / "vehicle.png"
    script_path = run_dir / "reference_vehicle.py"
    script = reference_script(spec, blend_path, benchmark_id)
    script_path.write_text(script, encoding="utf-8")
    bridge = BlenderBridge(find_blender_executable())
    try:
        bridge.run_script(script, render_path)
        graph = bridge.capture_scene(script)
    except BlenderBridgeError as exc:
        return {"ok": False, "passed": False, "suite": "vehicle-tier-2", "spec": spec, "prompt": vehicle_prompt(spec), "error": str(exc)}
    validation = validate_vehicle(graph, spec, benchmark_id)
    report = {
        "ok": bool(validation["passed"] and render_path.is_file() and blend_path.is_file()),
        "suite": "vehicle-tier-2", "prompt": vehicle_prompt(spec), "spec": spec,
        "blender_executable": bridge.blender_executable, "render_path": str(render_path),
        "blend_path": str(blend_path), "script_path": str(script_path),
        "scene_graph": graph.to_dict(), **validation,
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_local_9b_vehicle_proof(output_dir: Path) -> Dict[str, Any]:
    """Have local 9B author the car, then grade it with the same hard oracle."""
    spec = vehicle_spec()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_dir) / f"blender_vehicle_local_9b_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    benchmark_id = "vehicle-tier-2-local-9b"
    blend_path = run_dir / "vehicle.blend"
    render_path = run_dir / "vehicle.png"
    script_path = run_dir / "agent_vehicle.py"
    prompt = local_9b_vehicle_prompt(spec, blend_path, benchmark_id)
    drill_context, applied_drills = BenchmarkCurriculum(Path(output_dir).parent).context_for(prompt)
    prompt += drill_context
    try:
        script = _ask_local_9b(prompt)
    except RuntimeError as exc:
        return {"ok": False, "passed": False, "suite": "vehicle-tier-2", "model": "local/fable-fast", "prompt": prompt, "error": str(exc)}
    script_path.write_text(script, encoding="utf-8")
    bridge = BlenderBridge(find_blender_executable())
    try:
        bridge.run_script(script, render_path)
        graph = bridge.capture_scene(script)
    except BlenderBridgeError as exc:
        return {"ok": False, "passed": False, "suite": "vehicle-tier-2", "model": "local/fable-fast", "prompt": prompt, "script_path": str(script_path), "error": str(exc)}
    validation = validate_vehicle(graph, spec, benchmark_id)
    queued_drills = _record_vehicle_learning(output_dir, "vehicle-tier-2", validation)
    report = {
        "ok": bool(validation["passed"] and render_path.is_file() and blend_path.is_file()),
        "suite": "vehicle-tier-2", "model": "local/fable-fast", "prompt": prompt,
        "spec": spec, "blender_executable": bridge.blender_executable,
        "render_path": str(render_path), "blend_path": str(blend_path),
        "script_path": str(script_path), "scene_graph": graph.to_dict(),
        "drills_applied": applied_drills, "queued_drills": queued_drills, **validation,
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_dense_vehicle_proof(output_dir: Path) -> Dict[str, Any]:
    """Run tier three's denser vehicle reference proof and archive artifacts."""
    spec = dense_vehicle_spec()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_dir) / f"blender_vehicle_dense_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    benchmark_id = "vehicle-tier-3-dense"
    blend_path = run_dir / "vehicle_dense.blend"
    render_path = run_dir / "vehicle_dense.png"
    script_path = run_dir / "reference_vehicle_dense.py"
    script = reference_script(spec, blend_path, benchmark_id)
    script_path.write_text(script, encoding="utf-8")
    bridge = BlenderBridge(find_blender_executable())
    try:
        bridge.run_script(script, render_path)
        graph = bridge.capture_scene(script)
    except BlenderBridgeError as exc:
        return {"ok": False, "passed": False, "suite": "vehicle-tier-3-dense", "spec": spec, "prompt": dense_vehicle_prompt(spec), "error": str(exc)}
    validation = validate_vehicle(graph, spec, benchmark_id)
    detail_names = [item["name"] for item in spec if item["name"].startswith(("Vehicle_Hub", "Vehicle_Headlight", "Vehicle_TailLight", "Vehicle_FrontBumper", "Vehicle_RearBumper", "Vehicle_Windshield", "Vehicle_Grille", "Vehicle_Exhaust", "Vehicle_Spoiler"))]
    actual_names = {node.name for node in graph.nodes}
    detail_check = {"label": "dense detail pack", "passed": all(name in actual_names for name in detail_names), "detail": f"{len(detail_names)} detail objects required"}
    validation["checks"].append(detail_check)
    validation["failed_checks"] = [item for item in validation["checks"] if not item["passed"]]
    validation["passed"] = not validation["failed_checks"]
    report = {
        "ok": bool(validation["passed"] and render_path.is_file() and blend_path.is_file()),
        "suite": "vehicle-tier-3-dense", "prompt": dense_vehicle_prompt(spec), "spec": spec,
        "blender_executable": bridge.blender_executable, "render_path": str(render_path),
        "blend_path": str(blend_path), "script_path": str(script_path), "scene_graph": graph.to_dict(), **validation,
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


__all__ = ["benchmark_prompt", "dense_vehicle_spec", "local_9b_vehicle_prompt", "random_object_spec", "run_dense_vehicle_proof", "run_local_9b_vehicle_proof", "run_random_scene_proof", "run_vehicle_proof", "validate_scene", "validate_vehicle", "vehicle_spec"]

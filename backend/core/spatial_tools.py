"""Spatial geometry sidecar for Blender.

Callable tools that run small headless Blender scripts so the model can defer
3D math to the renderer instead of computing it in-head.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("infinity.spatial")


def _run_bpy_snippet(snippet: str, blender_executable: str = "blender") -> Any:
    """Execute a bpy snippet and return the JSON printed by the script."""
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / "snippet.py"
        wrapped = f"""\
import json
import bpy

{snippet}
"""
        script_path.write_text(wrapped, encoding="utf-8")
        command = [blender_executable, "--background", "--python", str(script_path)]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError as exc:
            return {"error": f"Blender not found: {blender_executable}"}
        except subprocess.TimeoutExpired:
            return {"error": "Blender snippet timed out"}

        stdout = result.stdout or ""
        start = stdout.find("INFINITY_RESULT_START")
        end = stdout.find("INFINITY_RESULT_END")
        if start == -1 or end == -1:
            logger.error("Spatial tool output missing markers. stdout:\n%s", stdout[-2000:])
            logger.error("stderr:\n%s", (result.stderr or "")[-2000:])
            return {"error": "Blender output missing result markers"}
        json_text = stdout[start + len("INFINITY_RESULT_START"):end].strip()
        try:
            return json.loads(json_text)
        except json.JSONDecodeError as exc:
            return {"error": f"JSON decode failed: {exc}", "raw": json_text}


def _result_wrapper(value: Any) -> str:
    return (
        "import json\n"
        "print('INFINITY_RESULT_START')\n"
        f"print(json.dumps({value}, ensure_ascii=False))\n"
        "print('INFINITY_RESULT_END')"
    )


def raycast(
    origin: List[float],
    direction: List[float],
    mesh_name: str,
    blender_executable: str = "blender",
) -> Dict[str, Any]:
    """Raycast against a named mesh and return hit location/normal."""
    snippet = f"""\
import bpy
import mathutils

obj = bpy.data.objects.get({mesh_name!r})
if obj is None or obj.type != 'MESH':
    print('INFINITY_RESULT_START')
    print(json.dumps({{"error": "mesh not found"}}, ensure_ascii=False))
    print('INFINITY_RESULT_END')
else:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ray_origin = mathutils.Vector(({origin[0]}, {origin[1]}, {origin[2]}))
    ray_dir = mathutils.Vector(({direction[0]}, {direction[1]}, {direction[2]})).normalized()
    result, location, normal, index = obj.ray_cast(ray_origin, ray_dir)
    print('INFINITY_RESULT_START')
    print(json.dumps({{
        "hit": result,
        "location": list(location) if result else None,
        "normal": list(normal) if result else None,
        "face_index": index if result else None,
    }}, ensure_ascii=False))
    print('INFINITY_RESULT_END')
"""
    return _run_bpy_snippet(snippet, blender_executable)


def measure_distance(
    a: List[float],
    b: List[float],
    blender_executable: str = "blender",
) -> Dict[str, Any]:
    """Return Euclidean distance between two 3D points."""
    snippet = f"""\
import mathutils
import json
p1 = mathutils.Vector(({a[0]}, {a[1]}, {a[2]}))
p2 = mathutils.Vector(({b[0]}, {b[1]}, {b[2]}))
print('INFINITY_RESULT_START')
print(json.dumps({{"distance": (p1 - p2).length}}, ensure_ascii=False))
print('INFINITY_RESULT_END')
"""
    return _run_bpy_snippet(snippet, blender_executable)


def camera_frame(
    camera_name: str,
    target: List[float],
    blender_executable: str = "blender",
) -> Dict[str, Any]:
    """Point a named camera at a target location and return its new rotation."""
    snippet = f"""\
import bpy
import mathutils

cam = bpy.data.objects.get({camera_name!r})
if cam is None or cam.type != 'CAMERA':
    print('INFINITY_RESULT_START')
    print(json.dumps({{"error": "camera not found"}}, ensure_ascii=False))
    print('INFINITY_RESULT_END')
else:
    target = mathutils.Vector(({target[0]}, {target[1]}, {target[2]}))
    direction = target - cam.location
    rot_quat = direction.to_track_quat('-Z', 'Y')
    cam.rotation_euler = rot_quat.to_euler()
    print('INFINITY_RESULT_START')
    print(json.dumps({{"rotation_euler": list(cam.rotation_euler)}}, ensure_ascii=False))
    print('INFINITY_RESULT_END')
"""
    return _run_bpy_snippet(snippet, blender_executable)


def collision_check(
    object_a: str,
    object_b: str,
    blender_executable: str = "blender",
) -> Dict[str, Any]:
    """Check if two named meshes' bounding boxes intersect."""
    snippet = f"""\
import bpy

a = bpy.data.objects.get({object_a!r})
b = bpy.data.objects.get({object_b!r})
if a is None or b is None:
    print('INFINITY_RESULT_START')
    print(json.dumps({{"error": "one or both objects not found"}}, ensure_ascii=False))
    print('INFINITY_RESULT_END')
else:
    bbox_a = [a.matrix_world @ v.co for v in a.bound_box]
    bbox_b = [b.matrix_world @ v.co for v in b.bound_box]
    min_a = [min(v[i] for v in bbox_a) for i in range(3)]
    max_a = [max(v[i] for v in bbox_a) for i in range(3)]
    min_b = [min(v[i] for v in bbox_b) for i in range(3)]
    max_b = [max(v[i] for v in bbox_b) for i in range(3)]
    intersects = all(min_a[i] <= max_b[i] and min_b[i] <= max_a[i] for i in range(3))
    print('INFINITY_RESULT_START')
    print(json.dumps({{"intersects": intersects}}, ensure_ascii=False))
    print('INFINITY_RESULT_END')
"""
    return _run_bpy_snippet(snippet, blender_executable)


__all__ = ["raycast", "measure_distance", "camera_frame", "collision_check"]

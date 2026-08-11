"""Scene-graph-as-text pipeline for Blender operations.

Captures Blender scenes as structured scene graphs so edits can be diffed and
used as fine-tuning data. The graph is intentionally simple: objects, their
transforms, constraints, and materials.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from backend.core.exec_utils import resolve_python
except ImportError:
    from core.exec_utils import resolve_python  # type: ignore

logger = logging.getLogger("infinity.scene_graph")


@dataclass
class SceneNode:
    name: str
    type: str  # MESH, LIGHT, CAMERA, EMPTY, etc.
    location: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    material: Optional[str] = None
    parent: Optional[str] = None
    visible: bool = True
    dimensions: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    custom_props: Dict[str, Any] = field(default_factory=dict)
    world_location: Optional[List[float]] = None
    world_scale: Optional[List[float]] = None
    world_rotation: Optional[List[float]] = None


@dataclass
class SceneGraph:
    nodes: List[SceneNode] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"nodes": [asdict(n) for n in self.nodes]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SceneGraph":
        return cls(nodes=[SceneNode(**n) for n in data.get("nodes", [])])

    @classmethod
    def from_json(cls, text: str) -> "SceneGraph":
        return cls.from_dict(json.loads(text))

    def diff(self, other: "SceneGraph") -> Dict[str, Any]:
        """Return a human-friendly diff between two scene graphs."""
        before = {n.name: asdict(n) for n in self.nodes}
        after = {n.name: asdict(n) for n in other.nodes}
        added = [name for name in after if name not in before]
        removed = [name for name in before if name not in after]
        changed: Dict[str, Dict[str, Any]] = {}
        for name in after:
            if name in before and before[name] != after[name]:
                changed[name] = {
                    "before": before[name],
                    "after": after[name],
                }
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
        }


_BPY_PROBE = """\
import json
import sys

import bpy

nodes = []
for obj in bpy.context.scene.objects:
    nodes.append({
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "material": obj.active_material.name if obj.active_material else None,
        "parent": obj.parent.name if obj.parent else None,
        "visible": obj.visible_get(),
        "dimensions": list(obj.dimensions),
        "world_location": list(obj.matrix_world.translation),
        "world_scale": list(obj.matrix_world.to_scale()),
        "world_rotation": list(obj.matrix_world.to_euler()),
        "custom_props": {
            key: value for key, value in obj.items()
            if isinstance(value, (str, int, float, bool))
        },
    })

print("SCENE_GRAPH_START")
print(json.dumps({"nodes": nodes}, ensure_ascii=False))
print("SCENE_GRAPH_END")
"""


def capture_scene_graph(blend_or_script: Path, blender_executable: str = "blender") -> SceneGraph:
    """Run Blender headless and capture the scene graph of a .blend file or script."""
    blend_or_script = Path(blend_or_script).resolve()
    if not blend_or_script.is_file():
        raise FileNotFoundError(blend_or_script)

    with tempfile.TemporaryDirectory() as tmpdir:
        probe_path = Path(tmpdir) / "probe.py"
        probe_path.write_text(_BPY_PROBE, encoding="utf-8")
        command: List[str] = [
            blender_executable,
            "--background",
            str(blend_or_script),
            "--python",
            str(probe_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Blender not found: {blender_executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Blender scene-graph capture timed out.") from exc

        stdout = result.stdout or ""
        start = stdout.find("SCENE_GRAPH_START")
        end = stdout.find("SCENE_GRAPH_END")
        if start == -1 or end == -1:
            logger.error("Could not capture scene graph. stdout:\n%s", stdout[-2000:])
            logger.error("stderr:\n%s", (result.stderr or "")[-2000:])
            raise RuntimeError("Scene graph capture failed.")
        json_text = stdout[start + len("SCENE_GRAPH_START"):end].strip()
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Scene graph JSON decode failed: {exc}") from exc
        return SceneGraph.from_dict(data)


def scene_graph_from_blender_script(
    script_text: str,
    output_image: Optional[Path] = None,
    blender_executable: str = "blender",
) -> SceneGraph:
    """Run a Blender script and capture the resulting scene graph.

    If output_image is provided, the script is extended to render at the end
    (mirroring BlenderBridge behaviour).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        blend_path = Path(tmpdir) / "scene.blend"
        script_path = Path(tmpdir) / "user_script.py"
        script_path.write_text(script_text, encoding="utf-8")

        # Build a wrapper that opens a blank file, runs the user script, saves.
        wrapper = f"""\
import bpy
bpy.ops.wm.read_factory_settings()
exec(open(r"{script_path}").read())
bpy.ops.wm.save_as_mainfile(filepath=r"{blend_path}")
"""
        wrapper_path = Path(tmpdir) / "wrapper.py"
        wrapper_path.write_text(wrapper, encoding="utf-8")

        command = [blender_executable, "--background", "--python", str(wrapper_path)]
        try:
            subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Blender not found: {blender_executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Blender script execution timed out.") from exc

        if not blend_path.is_file():
            raise RuntimeError("Blender did not produce a .blend file for scene capture.")
        return capture_scene_graph(blend_path, blender_executable)

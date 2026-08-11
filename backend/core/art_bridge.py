"""Blender headless bridge for Infinity Code.

Runs an arbitrary bpy script under `blender --background --python` and
guarantees the render lands at the requested output path. Evidence-first: the
call only succeeds if the output image actually exists afterwards.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from backend.core.scene_graph import SceneGraph
except ImportError:
    from core.scene_graph import SceneGraph  # type: ignore

DEFAULT_TIMEOUT_SECONDS: int = 120


def find_blender_executable(preferred: Optional[str] = None) -> str:
    """Return a usable Blender executable when one is installed locally.

    Most Windows Blender installs do not add ``blender.exe`` to PATH.  The
    harness should still find the owner's local installation without requiring
    a manual environment-variable setup.
    """
    requested = preferred or os.environ.get("BLENDER_PATH")
    if requested:
        return requested
    on_path = shutil.which("blender") or shutil.which("blender.exe")
    if on_path:
        return on_path
    program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    root = Path(program_files) / "Blender Foundation"
    if root.is_dir():
        installs = sorted(root.glob("Blender*/blender.exe"), reverse=True)
        if installs:
            return str(installs[0])
    return "blender"

_FORMAT_BY_SUFFIX: Dict[str, str] = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".bmp": "BMP",
    ".exr": "OPEN_EXR",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}

_SCRIPT_HEADER_TEMPLATE: str = '''\
import os

import bpy

_INFINITY_OUTPUT = r"{output_path}"
os.makedirs(os.path.dirname(_INFINITY_OUTPUT) or ".", exist_ok=True)
bpy.context.scene.render.filepath = _INFINITY_OUTPUT
bpy.context.scene.render.image_settings.file_format = "{file_format}"

# --- user script begins ---
'''

_SCRIPT_FOOTER: str = '''

# --- user script ends ---
# The render must exist on disk; if the user script did not render, do it now.
if not os.path.exists(_INFINITY_OUTPUT):
    bpy.context.scene.render.filepath = _INFINITY_OUTPUT
    bpy.ops.render.render(write_still=True)
'''


class BlenderBridgeError(RuntimeError):
    """Raised when a headless Blender run fails or produces no output image."""


class BlenderBridge:
    """Executes bpy scripts in headless Blender and returns rendered images."""

    def __init__(
        self,
        blender_executable: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.blender_executable: str = (
            find_blender_executable(blender_executable)
        )
        self.timeout_seconds: int = int(timeout_seconds)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _file_format_for(output_image: Path) -> str:
        return _FORMAT_BY_SUFFIX.get(output_image.suffix.lower(), "PNG")

    def _compose_script(self, python_script: str, output_image: Path) -> str:
        # Use .replace() instead of .format() so curly braces in the user's
        # bpy script cannot crash string formatting.
        header: str = (
            _SCRIPT_HEADER_TEMPLATE.replace("{output_path}", str(output_image))
            .replace("{file_format}", self._file_format_for(output_image))
        )
        return header + python_script + _SCRIPT_FOOTER

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run_script(self, python_script: str, output_image: Path) -> Path:
        """Run a bpy script headless; return the rendered image path.

        Raises BlenderBridgeError if Blender fails, times out, or the output
        image does not exist afterwards.
        """
        if not python_script or not python_script.strip():
            raise BlenderBridgeError("run_script() requires a non-empty bpy script.")

        output_image = Path(output_image).resolve()
        try:
            output_image.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BlenderBridgeError(
                f"Cannot create output directory {output_image.parent}: {exc}"
            ) from exc

        script_text: str = self._compose_script(python_script, output_image)
        script_path: Optional[Path] = None
        try:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".py",
                    prefix="infinity_blender_",
                    delete=False,
                    encoding="utf-8",
                ) as handle:
                    handle.write(script_text)
                    script_path = Path(handle.name)
            except OSError as exc:
                raise BlenderBridgeError(
                    f"Cannot write temporary Blender script: {exc}"
                ) from exc

            command: List[str] = [
                self.blender_executable,
                "--background",
                "--python",
                str(script_path),
            ]
            logger.info("Running headless Blender: %s", " ".join(command))
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise BlenderBridgeError(
                    f"Blender executable not found: {self.blender_executable!r}. "
                    "Install Blender or set the BLENDER_PATH environment variable."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise BlenderBridgeError(
                    f"Blender timed out after {self.timeout_seconds}s."
                ) from exc
            except OSError as exc:
                raise BlenderBridgeError(f"Blender failed to launch: {exc}") from exc

            if result.returncode != 0:
                stderr_tail: str = (result.stderr or "")[-800:]
                stdout_tail: str = (result.stdout or "")[-400:]
                raise BlenderBridgeError(
                    f"Blender exited with code {result.returncode}.\n"
                    f"stderr: {stderr_tail}\nstdout: {stdout_tail}"
                )

            # Blender frequently returns zero even if a Python script raised.
            # Treat its traceback as a hard harness failure so agents receive
            # actionable evidence instead of a misleading missing-render error.
            combined_output = f"{result.stdout or ''}\n{result.stderr or ''}"
            if "Traceback (most recent call last):" in combined_output:
                raise BlenderBridgeError(
                    "Blender Python script failed.\n"
                    f"output: {combined_output[-1600:]}"
                )

            if not output_image.is_file():
                stdout_tail = (result.stdout or "")[-800:]
                raise BlenderBridgeError(
                    f"Blender finished but no image at {output_image}.\n"
                    f"stdout: {stdout_tail}"
                )

            logger.info("Blender render complete: %s", output_image)
            return output_image
        finally:
            if script_path is not None:
                with contextlib.suppress(OSError):
                    script_path.unlink()

    _SCENE_PROBE: str = """\
import json
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

    def capture_scene(self, python_script: str) -> SceneGraph:
        """Run a bpy script and return the resulting scene graph.

        The script is executed in the same headless Blender process as
        run_script(), but no render is required. Useful for logging
        scene-graph diffs for fine-tuning.
        """
        if not python_script or not python_script.strip():
            raise BlenderBridgeError("capture_scene() requires a non-empty bpy script.")
        script_path: Optional[Path] = None
        probe_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", prefix="infinity_blender_user_",
                delete=False, encoding="utf-8",
            ) as handle:
                handle.write(python_script)
                script_path = Path(handle.name)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", prefix="infinity_blender_probe_",
                delete=False, encoding="utf-8",
            ) as handle:
                handle.write(self._SCENE_PROBE)
                probe_path = Path(handle.name)

            command: List[str] = [
                self.blender_executable,
                "--background",
                "--python",
                str(script_path),
                "--python",
                str(probe_path),
            ]
            logger.info("Capturing Blender scene graph: %s", " ".join(command))
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if result.returncode != 0:
                stderr_tail: str = (result.stderr or "")[-800:]
                raise BlenderBridgeError(
                    f"Blender exited with code {result.returncode}.\nstderr: {stderr_tail}"
                )
            combined_output = f"{result.stdout or ''}\n{result.stderr or ''}"
            if "Traceback (most recent call last):" in combined_output:
                raise BlenderBridgeError(
                    "Blender Python script failed.\n"
                    f"output: {combined_output[-1600:]}"
                )
            stdout: str = result.stdout or ""
            start = stdout.find("SCENE_GRAPH_START")
            end = stdout.find("SCENE_GRAPH_END")
            if start == -1 or end == -1:
                raise BlenderBridgeError(
                    f"Could not extract scene graph from Blender output.\nstdout: {stdout[-800:]}"
                )
            json_text = stdout[start + len("SCENE_GRAPH_START"):end].strip()
            data = json.loads(json_text)
            return SceneGraph.from_dict(data)
        finally:
            for p in (script_path, probe_path):
                if p is not None:
                    with contextlib.suppress(OSError):
                        p.unlink()


__all__ = ["BlenderBridge", "BlenderBridgeError", "find_blender_executable"]

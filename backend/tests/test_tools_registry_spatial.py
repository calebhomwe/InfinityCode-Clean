"""Unit tests for the new spatial + render tool registrations."""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.tools_registry import ToolRegistry, TOOL_SCHEMAS, TOOL_NAMES
except ImportError:
    from core.tools_registry import ToolRegistry, TOOL_SCHEMAS, TOOL_NAMES  # type: ignore


def test_spatial_tools_are_registered() -> None:
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    for name in ("spatial_raycast", "spatial_measure", "spatial_camera_frame", "spatial_collision"):
        assert name in names


def test_render_feedback_is_registered() -> None:
    assert "render_feedback" in TOOL_NAMES


def test_spatial_measure_returns_json() -> None:
    reg = ToolRegistry()
    result = reg.dispatch(
        "spatial_measure",
        {"a": [0.0, 0.0, 0.0], "b": [1.0, 0.0, 0.0]},
    )
    data = json.loads(result)
    # Without Blender installed the sidecar returns an error dict.
    if "error" in data:
        assert "Blender" in data["error"] or "timed out" in data["error"]
    else:
        assert round(data["distance"], 4) == 1.0


def test_render_feedback_without_verifier() -> None:
    reg = ToolRegistry()
    result = reg.dispatch(
        "render_feedback",
        {"script": "import bpy", "intent": "test"},
    )
    assert "no verifier" in result.lower()


class FakeVerifier:
    def render_feedback(self, script: str, intent: str, output_path: Any = None) -> Any:
        class Result:
            ok = True
            stage = "vision"
            reason = "matches intent"
            output_path = Path("/tmp/render.png")
        return Result()


def test_render_feedback_with_verifier() -> None:
    reg = ToolRegistry(verifier=FakeVerifier())
    result = reg.dispatch(
        "render_feedback",
        {"script": "import bpy", "intent": "test"},
    )
    data = json.loads(result)
    assert data["ok"]
    assert data["stage"] == "vision"


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

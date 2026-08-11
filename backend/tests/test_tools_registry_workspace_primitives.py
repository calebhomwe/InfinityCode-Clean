"""Focused checks for the code-harness workspace and app-launch tools."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.tools_registry import (  # noqa: E402
    ACTION_TOOL_NAMES,
    ASSISTANT_TOOL_NAMES,
    TOOL_NAMES,
    ToolRegistry,
    risk_of,
)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "src" / "components").mkdir(parents=True)
    (root / "src" / "components" / "App.tsx").write_text(
        "export function App() {\n  return 'Infinity';\n}\n", encoding="utf-8"
    )
    (root / "src" / "main.ts").write_text("const title = 'infinity';\n", encoding="utf-8")
    (root / "README.md").write_text("# Infinity Code\n", encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist" / "generated.tsx").write_text("Infinity\n", encoding="utf-8")
    return root


def test_code_harness_tools_are_advertised_and_classified() -> None:
    expected = {
        "find_workspace_files",
        "search_workspace_text",
        "read_workspace_files",
        "launch_app",
    }
    assert expected <= set(TOOL_NAMES)
    assert expected <= set(ASSISTANT_TOOL_NAMES)
    assert "launch_app" in ACTION_TOOL_NAMES
    assert risk_of("launch_app") == "mcp"
    assert risk_of("find_workspace_files") == "fs"
    assert risk_of("search_workspace_text") == "fs"
    assert risk_of("read_workspace_files") == "fs"


def test_workspace_glob_is_bounded_and_rejects_escape(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    registry = ToolRegistry(workspace=root)

    result = registry.dispatch("find_workspace_files", {"pattern": "**/*.tsx"})
    assert "src/components/App.tsx" in result
    assert "dist/generated.tsx" not in result
    assert "../" not in result

    escaped = registry.dispatch("find_workspace_files", {"pattern": "../*.txt"})
    assert "relative to the selected workspace" in escaped


def test_workspace_text_search_is_literal_scoped_and_concise(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    registry = ToolRegistry(workspace=root)

    result = registry.dispatch(
        "search_workspace_text",
        {"query": "INFINITY", "include": "src/**/*", "case_sensitive": False},
    )
    assert "src/components/App.tsx:2:" in result
    assert "src/main.ts:1:" in result
    assert "dist/" not in result

    exact = registry.dispatch(
        "search_workspace_text",
        {"query": "INFINITY", "include": "src/**/*", "case_sensitive": True},
    )
    assert exact == "(no text matches)"


def test_batch_read_stays_in_workspace_and_limits_output(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("do not expose", encoding="utf-8")
    (root / "large.txt").write_text("x" * 9000, encoding="utf-8")
    registry = ToolRegistry(workspace=root)

    result = registry.dispatch(
        "read_workspace_files",
        {"file_paths": ["README.md", "../secret.txt", "large.txt"]},
    )
    assert "# Infinity Code" in result
    assert "--- ../secret.txt ---\nInvalid workspace path." in result
    assert "do not expose" not in result
    assert "...(file truncated)" in result
    assert len(result) < 9000


def test_launch_app_requires_actions_and_never_uses_a_shell(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    executable = tmp_path / ("viewer.exe" if os.name == "nt" else "viewer")
    executable.write_bytes(b"not executed because Popen is mocked")
    if os.name != "nt":
        executable.chmod(0o755)
    registry = ToolRegistry(workspace=root)

    gated = registry.dispatch("launch_app", {"app": str(executable)})
    assert "Turn on 'Allow actions'" in gated

    process = Mock(pid=4321)
    with patch("core.tools_registry.subprocess.Popen", return_value=process) as popen:
        result = registry.dispatch(
            "launch_app",
            {"app": str(executable), "args": ["file with spaces.blend", "--background"]},
            allow_actions=True,
        )

    assert result == f"Launched {executable.name} (PID 4321)."
    command = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert command == [str(executable.resolve()), "file with spaces.blend", "--background"]
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(root.resolve())


def test_launch_app_rejects_command_interpreters(tmp_path: Path) -> None:
    registry = ToolRegistry()
    blocked = tmp_path / ("cmd.exe" if os.name == "nt" else "bash")
    blocked.write_bytes(b"")
    if os.name != "nt":
        blocked.chmod(0o755)

    with patch.object(ToolRegistry, "_find_app_executable", return_value=blocked):
        with patch("core.tools_registry.subprocess.Popen") as popen:
            result = registry.dispatch("launch_app", {"app": str(blocked)}, allow_actions=True)
    assert "command interpreters are not applications" in result
    popen.assert_not_called()


def test_find_app_executable_resolves_conventional_blender_install(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    blender = tmp_path / "Blender Foundation" / "Blender 5.2" / "blender.exe"
    blender.parent.mkdir(parents=True)
    blender.write_bytes(b"MZ")

    with patch.dict(os.environ, {"ProgramFiles": str(tmp_path)}), patch(
        "core.tools_registry.shutil.which", return_value=None
    ):
        found = ToolRegistry._find_app_executable("blender")

    assert found == blender

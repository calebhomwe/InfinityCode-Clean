"""kernel_skills tests — the tools-as-kernel-functions bridge.

Covers the four pieces of the pi / Prime-Agent bridge:
1. discover_tools() returns the registry's flat schemas (incl. the safe set).
2. generate_preamble() emits valid Python defining _ToolCall + wrappers, and
   only for the curated safe subset.
3. install() into a real KernelSession makes wrappers raise _ToolCall,
   surfaced as KernelResult.tool_call via execute().
4. dispatch_tool() round-trips: a stub registry records the call, and the
   output is pushed back as _last_tool_result readable by a later execute().
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core import kernel_skills  # noqa: E402
from backend.core.kernel import KernelSession  # noqa: E402


@pytest.fixture()
def kernel(tmp_path):
    k = KernelSession(workdir=tmp_path, timeout=10, result_cap=4000)
    yield k
    k.close()


def test_discover_tools_returns_flat_schemas():
    """The registry's function schemas come back as name/description/parameters."""
    tools = kernel_skills.discover_tools()
    assert tools, "registry must expose at least one tool"
    by_name = {t["name"]: t for t in tools}
    # Every curated safe tool must be discoverable.
    assert kernel_skills.SAFE_TOOL_NAMES <= set(by_name)
    for name in ("read_file", "calculator", "fetch_url", "run_workspace_shell"):
        assert set(by_name[name]) == {"name", "description", "parameters"}


def test_generate_preamble_is_valid_python():
    """The preamble compiles and defines _ToolCall + wrappers for the safe set."""
    source = kernel_skills.generate_preamble(kernel_skills.discover_tools())
    compile(source, "<kernel_skills preamble>", "exec")
    assert "class _ToolCall(Exception):" in source
    assert "self.name = name" in source
    assert "def calculator(**kwargs):" in source
    assert "raise _ToolCall('calculator', kwargs)" in source
    # The tool description lands in the docstring.
    assert "Evaluate a single arithmetic expression exactly" in source
    # Unsafe / heavy tools are never generated.
    for excluded in ("run_python", "generate_image", "text_to_speech", "launch_app", "spatial_raycast"):
        assert excluded not in source


def test_install_surfaces_tool_call_via_execute(kernel):
    """install() defines the wrappers; calling one surfaces KernelResult.tool_call."""
    installed = kernel_skills.install(kernel)
    assert set(installed) == set(kernel_skills.SAFE_TOOL_NAMES)
    res = kernel.execute("calculator(expression='6*7')")
    assert res.tool_call == {"name": "calculator", "args": {"expression": "6*7"}}


def test_install_named_subset(kernel):
    """A names list narrows the install; unsafe names are never installed."""
    installed = kernel_skills.install(kernel, names=["calculator", "fetch_url"])
    assert installed == ["calculator", "fetch_url"]
    assert kernel_skills.install(kernel, names=["run_python"]) == []


class _StubRegistry:
    """Records dispatch calls; returns a canned string (no real execution)."""

    def __init__(self, canned: str = "STUB OUTPUT") -> None:
        self.canned = canned
        self.calls = []

    def dispatch(self, name, arguments, allow_actions=False):
        self.calls.append((name, arguments, allow_actions))
        return self.canned


def test_dispatch_tool_round_trip(kernel):
    """dispatch_tool routes the call through the registry and pushes the
    answer back into the kernel as _last_tool_result."""
    kernel_skills.install(kernel, names=["calculator"])
    res = kernel.execute("calculator(expression='6*7')")
    assert res.tool_call is not None

    registry = _StubRegistry(canned="6*7 = 42")
    output = kernel_skills.dispatch_tool(kernel, registry, res)
    assert output == registry.canned
    # The registry saw the exact call, with actions still gated off.
    assert registry.calls == [("calculator", {"expression": "6*7"}, False)]

    # Later cells can read the tool result from kernel memory.
    later = kernel.execute("_last_tool_result")
    assert later.ok
    assert registry.canned in later.result


def test_dispatch_tool_requires_tool_call(kernel):
    """A result without tool_call is a programming error, not a dispatch."""
    res = kernel.execute("2 + 2")
    with pytest.raises(kernel_skills.KernelSkillsError):
        kernel_skills.dispatch_tool(kernel, _StubRegistry(), res)

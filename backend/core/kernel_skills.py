"""kernel_skills — the pi / Prime-Agent "tools as kernel functions" bridge.

Existing tool capabilities (the registry's JSON tool schemas) become callable
functions inside the persistent KernelSession. A generated preamble defines
``_ToolCall`` plus one keyword-only wrapper per tool; a wrapper simply raises
``_ToolCall(name, kwargs)``. The kernel worker catches that exception and
surfaces it as ``KernelResult.tool_call``; the host loop then dispatches the
call to ``ToolRegistry.dispatch`` and pushes the output back into the kernel
as ``_last_tool_result`` so later cells can read it.

Only a curated safe subset of tools is bridged by default — read-only,
bounded, or sandboxed capabilities. Side-effectful / paid / heavy tools
(generate_image, text_to_speech, launch_app, run_python, spatial_*,
render_feedback) are never generated into the kernel namespace.

The kernel module is imported lazily inside functions on purpose: this module
must stay importable in contexts where the kernel is unavailable (e.g. tool
discovery / schema generation for the model prompt).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "SAFE_TOOL_NAMES",
    "EXCLUDED_TOOL_NAMES",
    "KernelSkillsError",
    "discover_tools",
    "generate_preamble",
    "install",
    "dispatch_tool",
]

#: Tools bridged into the kernel by default: read-only, bounded, or sandboxed.
SAFE_TOOL_NAMES: frozenset[str] = frozenset({
    "read_file",
    "list_dir",
    "write_file",
    "calculator",
    "list_skills",
    "read_skill",
    "read_workspace_file",
    "list_workspace_dir",
    "find_workspace_files",
    "search_workspace_text",
    "apply_workspace_edit",
    "run_workspace_shell",
    "fetch_url",
    "web_search",
    "research",
    "critique",
    "review_screen",
    "see_image",
})

#: Side-effectful / paid / heavy tools excluded by default. Informational —
#: generate_preamble only ever emits names in SAFE_TOOL_NAMES, so anything
#: else (including read_workspace_files, which is not curated) is skipped.
EXCLUDED_TOOL_NAMES: frozenset[str] = frozenset({
    "generate_image",
    "text_to_speech",
    "launch_app",
    "run_python",
    "render_feedback",
    "spatial_raycast",
    "spatial_measure",
    "spatial_camera_frame",
    "spatial_collision",
})


class KernelSkillsError(RuntimeError):
    """Raised when the tool bridge cannot be installed or dispatched."""


def _load_registry() -> Any:
    """Import the tools registry, tolerating either package-root layout."""
    try:
        from backend.core import tools_registry
    except ImportError:  # running with backend/ as the working directory
        from core import tools_registry  # type: ignore[no-redef]
    return tools_registry


def discover_tools() -> List[Dict[str, Any]]:
    """Return the registry's tool schemas as flat {name, description, parameters}.

    Merges the chat toolbelt (``TOOL_SCHEMAS``) with the assistant extras
    (``ASSISTANT_TOOL_SCHEMAS``) and unwraps the OpenAI function-calling
    envelope into the plain form the preamble generator needs. Duplicate
    names keep the first (chat) definition.
    """
    registry = _load_registry()
    seen: set = set()
    tools: List[Dict[str, Any]] = []
    for schema in list(registry.TOOL_SCHEMAS) + list(registry.ASSISTANT_TOOL_SCHEMAS):
        fn = schema.get("function") or {}
        name = fn.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


def _docstring(description: str, fallback: str) -> str:
    """A triple-quoted docstring literal that is safe for any description."""
    doc = description.strip() or fallback
    doc = doc.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'    """{doc}"""'


def generate_preamble(tools: List[Dict[str, Any]]) -> str:
    """Python source that turns tools into kernel callables.

    Defines ``_ToolCall`` — an Exception carrying ``.name`` and ``.args`` —
    plus one keyword-only wrapper per tool::

        def <name>(**kwargs):
            \"\"\"<tool description>\"\"\"
            raise _ToolCall(<name>, kwargs)

    Only tools in SAFE_TOOL_NAMES are generated; everything else is skipped,
    so side-effectful / paid / heavy tools can never be called from inside
    the kernel.
    """
    lines: List[str] = [
        "class _ToolCall(Exception):",
        '    """Raised by a tool wrapper to ask the host to dispatch the tool."""',
        "    def __init__(self, name, kwargs):",
        "        super().__init__(name, kwargs)",
        "        self.name = name",
        "        self.kwargs = kwargs",
        "",
        "",
        "# --- tool wrappers: raise the call to the host for dispatch ---",
        "",
    ]
    available = {t["name"]: t for t in tools}
    for name in sorted(SAFE_TOOL_NAMES & set(available)):
        desc = str(available[name].get("description") or "")
        lines.append(f"def {name}(**kwargs):")
        lines.append(_docstring(desc, f"Call the '{name}' tool."))
        lines.append(f"    raise _ToolCall({name!r}, kwargs)")
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def install(kernel: Any, names: Optional[Iterable[str]] = None) -> List[str]:
    """Install tool wrappers into a live kernel session (one execute call).

    ``names=None`` installs the curated safe subset; a names list narrows the
    selection further. Tools outside SAFE_TOOL_NAMES are never installed.
    Returns the names actually installed.
    """
    tools = discover_tools()
    available = {t["name"]: t for t in tools}
    requested = SAFE_TOOL_NAMES if names is None else set(names) & SAFE_TOOL_NAMES
    selected = [available[n] for n in sorted(requested) if n in available]
    result = kernel.execute(generate_preamble(selected))
    if not result.ok:
        raise KernelSkillsError(f"preamble install failed: {result.error}")
    return [t["name"] for t in selected]


def dispatch_tool(kernel: Any, registry: Any, result: Any) -> str:
    """Dispatch one kernel-raised tool call and push the answer back in.

    Expects a KernelResult whose ``tool_call`` is set ({"name": "..." ,
    "args": {...}}). Calls ``registry.dispatch(name, args,
    allow_actions=False)`` — action tools stay gated — then
    ``kernel.set_tool_result(output)`` so later cells can read
    ``_last_tool_result``, and returns the output string for the caller.
    """
    call = result.tool_call
    if not call:
        raise KernelSkillsError("result has no tool_call to dispatch")
    output = registry.dispatch(call["name"], call.get("args") or {}, allow_actions=False)
    kernel.set_tool_result(output)
    return output

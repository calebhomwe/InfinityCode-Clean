"""Chat tool-use layer: the registry, JSON schemas, and safe dispatch.

Gives the chat model a real toolbelt (run Python, do math, fetch a URL, search
the live web, and read the user's own skill vault). Every tool is sandbox-safe:
no filesystem writes outside a temp dir, no shell, bounded time + output. The
model calls these via OpenRouter function-calling; `dispatch` runs one call and
returns a short string the model reads back.
"""

from __future__ import annotations

import ast
import base64
import ipaddress
import json
import logging
import os
import re
import operator
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse


def url_is_blocked(url: str) -> Optional[str]:
    """SSRF guard: reject non-http(s) URLs and any host that resolves to a
    private / loopback / link-local / reserved address, so a prompt-injected
    page can't pivot into the user's LAN or a cloud metadata endpoint."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "only http(s) URLs are allowed"
    host = parsed.hostname
    if not host:
        return "no host in URL"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        return f"DNS error: {exc}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return f"blocked internal address ({ip})"
    return None


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target against the SSRF guard. A public URL
    that 302s to 169.254.169.254 (or any internal host) is refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        blocked = url_is_blocked(newurl)
        if blocked:
            raise urllib.error.HTTPError(
                newurl, code, f"redirect blocked: {blocked}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_GUARDED_OPENER = urllib.request.build_opener(_GuardedRedirectHandler)


from .exec_utils import resolve_python

try:
    from backend.core import spatial_tools
    from backend.core.sandbox import run_sandboxed
    from backend.core.verifier import Verifier
except ImportError:  # running with backend/ as the working directory
    from core import spatial_tools  # type: ignore[no-redef]
    from core.sandbox import run_sandboxed  # type: ignore[no-redef]
    from core.verifier import Verifier  # type: ignore[no-redef]

logger = logging.getLogger("infinity.tools")

_TOOL_OUTPUT_MAX = 6000
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def sanitize_tool_output(value: Any) -> str:
    """Validate/coerce ANY tool result before the LLM sees it.

    Never raises: non-strings are JSON-serialized (or repr-ed as a last
    resort), control characters other than newline/tab are stripped, and
    the result is bounded so one chatty tool cannot blow the context.
    """
    try:
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001
                value = repr(value)
        value = _CTRL_RE.sub("", value)
        if len(value) > _TOOL_OUTPUT_MAX:
            value = value[:_TOOL_OUTPUT_MAX] + "\n[truncated]"
        return value
    except Exception as exc:  # noqa: BLE001 - validator must be total
        return f"(tool output unavailable: {exc})"

# Vision + strong-text models the tools lean on (valid OpenRouter IDs).
VISION_MODEL: str = "qwen/qwen3-vl-30b-a3b-thinking"
RESEARCH_MODEL: str = "perplexity/sonar"
SYNTH_MODEL: str = "qwen/qwen3.7-max"

# OpenAI/OpenRouter function-calling schemas advertised to the model.
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute a short Python 3 script in a sandbox and return its "
                "stdout/stderr. Use for calculations, data work, quick checks. "
                "Print results; return values are not captured."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to run."}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a single arithmetic expression exactly. Safe, no code execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. (1234*7)/3 + 2**10",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a public URL and return its readable text (truncated). Use for docs/pages the user names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL to fetch."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live web for current facts and return a cited summary. Use when knowledge may be stale.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_screen",
            "description": (
                "Take a screenshot of the user's primary display and visually "
                "review it against a goal. Returns what is on screen plus a "
                "critique. Use when the user asks you to look at their screen, "
                "check a design, or spot a UI problem."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "What to look for / evaluate (e.g. 'is the layout balanced?').",
                    }
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "see_image",
            "description": "Look at an image (local path or http URL) and answer a question about it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Local file path or http(s) image URL."},
                    "question": {"type": "string", "description": "What to determine about the image."},
                },
                "required": ["source", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "critique",
            "description": (
                "Act as a rigorous critic: score a piece of work (text, code, or "
                "a plan) from 0-10 against a goal and list concrete strengths and "
                "fixes. Use for 'review this', 'is this good', 'grade my ...'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "work": {"type": "string", "description": "The work to critique."},
                    "goal": {"type": "string", "description": "What good looks like / the intent."},
                },
                "required": ["work"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research",
            "description": (
                "Run a live-web research pass on a question and return a cited, "
                "structured briefing (key findings + sources). Use for 'research', "
                "'find out about', 'compare', 'what's the state of'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The research question."}
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List the names of skills saved in the user's skill vault.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": "Read one saved skill (its steps) from the vault by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact skill name."}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spatial_raycast",
            "description": (
                "Raycast against a named mesh in the Blender scene and return the "
                "hit location, normal, and face index. Use for precise 3D picking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Ray origin as [x, y, z].",
                    },
                    "direction": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Ray direction as [x, y, z].",
                    },
                    "mesh_name": {
                        "type": "string",
                        "description": "Name of the mesh object to raycast against.",
                    },
                },
                "required": ["origin", "direction", "mesh_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spatial_measure",
            "description": "Measure the Euclidean distance between two 3D points.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "First point [x, y, z].",
                    },
                    "b": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Second point [x, y, z].",
                    },
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spatial_camera_frame",
            "description": (
                "Point a named Blender camera at a target location. Returns the new "
                "Euler rotation. Use instead of guessing camera angles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_name": {
                        "type": "string",
                        "description": "Name of the camera object.",
                    },
                    "target": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "Target point [x, y, z] to look at.",
                    },
                },
                "required": ["camera_name", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spatial_collision",
            "description": (
                "Check whether two named Blender mesh objects' bounding boxes "
                "intersect. Use for placement validation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "object_a": {"type": "string"},
                    "object_b": {"type": "string"},
                },
                "required": ["object_a", "object_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_feedback",
            "description": (
                "Render a Blender Python script and ask a vision model to critique "
                "the render against the original intent. Returns the critique and "
                "whether the render matches the intent. Costs a small vision call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "Blender Python script to render.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "What the render should show.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional path for the rendered image.",
                    },
                },
                "required": ["script", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": (
                "Read a text file from the active workspace/project. "
                "Use workspace-relative paths (e.g. 'src/App.tsx'). "
                "Requires a workspace to be selected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_dir",
            "description": (
                "List files and folders in the active workspace/project. "
                "Use workspace-relative paths; omit or use '.' for the root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Workspace-relative directory path (default: root).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_workspace_edit",
            "description": (
                "Apply a precise search/replace edit to a file in the active workspace. "
                "The search block must match exactly once. If the file does not exist "
                "and search is empty, creates the file. Always confirm with the user first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Exact text to replace. Empty for new files.",
                    },
                    "replace": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["file_path", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_workspace_shell",
            "description": (
                "Run a shell command in the active workspace directory. "
                "Use for builds, tests, git, package managers. "
                "Always confirm with the user first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
]

# Focused code-harness primitives inspired by modern coding agents.  These
# schemas are shared by normal chat and Executive Assistant mode so both can
# inspect the selected project before proposing or applying an edit.  All
# workspace paths are resolved again by the handlers below; model-provided
# globs never grant access outside the selected workspace.
_CODE_HARNESS_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_workspace_files",
            "description": (
                "Find files in the active workspace using a relative glob "
                "such as 'src/**/*.tsx'. Results are bounded and stay inside "
                "the selected workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Workspace-relative glob pattern.",
                    },
                    "include_directories": {
                        "type": "boolean",
                        "description": "Include matching directories as well as files.",
                        "default": False,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace_text",
            "description": (
                "Search text files in the active workspace for a literal string "
                "and return bounded path:line matches. Use an optional relative "
                "glob to narrow the search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal text to find.",
                    },
                    "include": {
                        "type": "string",
                        "description": "Workspace-relative file glob (default: '**/*').",
                        "default": "**/*",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Use case-sensitive matching.",
                        "default": False,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_files",
            "description": (
                "Read several text files from the active workspace in one call. "
                "Paths must be workspace-relative; per-file and total output are bounded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 20,
                        "description": "Workspace-relative file paths.",
                    }
                },
                "required": ["file_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": (
                "Launch an installed desktop application by executable path or "
                "command name. Arguments are passed directly without a shell. "
                "This is a real-world action and requires actions to be enabled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Executable path or installed command name.",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 32,
                        "description": "Optional argument list; never interpreted by a shell.",
                    },
                },
                "required": ["app"],
            },
        },
    },
]

TOOL_SCHEMAS.extend(_CODE_HARNESS_TOOL_SCHEMAS)

TOOL_NAMES: List[str] = [t["function"]["name"] for t in TOOL_SCHEMAS]

# Extra tools the Executive Assistant gets on top of the chat toolbelt. The
# read-only ones (read_file, list_dir) run freely; the side-effectful ones
# (write_file, generate_image, text_to_speech) only run when allow_actions is on.
ASSISTANT_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the user's machine and return its contents (truncated).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute file path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the files and folders in a directory on the user's machine.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute directory path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file in the assistant's output folder. Requires actions enabled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "File name (no path); saved to the output folder."},
                    "content": {"type": "string", "description": "Text to write."},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text prompt (FAL FLUX). Paid; requires actions enabled.",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string", "description": "Image description."}},
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "text_to_speech",
            "description": "Turn text into spoken audio (ElevenLabs). Paid; requires actions enabled.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to speak."}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": (
                "Read a text file from the active workspace/project. "
                "Use workspace-relative paths (e.g. 'src/App.tsx'). "
                "Requires a workspace to be selected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_dir",
            "description": (
                "List files and folders in the active workspace/project. "
                "Use workspace-relative paths; omit or use '.' for the root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Workspace-relative directory path (default: root).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_workspace_edit",
            "description": (
                "Apply a precise search/replace edit to a file in the active workspace. "
                "The search block must match exactly once. If the file does not exist "
                "and search is empty, creates the file. Always confirm with the user first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Exact text to replace. Empty for new files.",
                    },
                    "replace": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["file_path", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_workspace_shell",
            "description": (
                "Run a shell command in the active workspace directory. "
                "Use for builds, tests, git, package managers. "
                "Always confirm with the user first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
]
ASSISTANT_TOOL_SCHEMAS.extend(_CODE_HARNESS_TOOL_SCHEMAS)
ASSISTANT_TOOL_NAMES: List[str] = [t["function"]["name"] for t in ASSISTANT_TOOL_SCHEMAS]
# Side-effectful/paid tools gated behind allow_actions.
ACTION_TOOL_NAMES: List[str] = [
    "write_file",
    "generate_image",
    "text_to_speech",
    "apply_workspace_edit",
    "run_workspace_shell",
    "launch_app",
]

# Per-invocation risk classes, used for graded per-action approval.
#   paid   -> always prompt (spends real money); show est. cost
#   write  -> always prompt (mutates disk); show filename + content
#   screen -> captures the display; prompt once per session
#   fs     -> reads arbitrary filesystem paths; prompt once per session
#   safe   -> read-only / sandboxed; auto-runs, never prompts
# MCP tools (mcp__*) are classified "mcp" -> always prompt (unknown side
# effects). The arbitrary-execution tools (run_python, run_workspace_shell)
# share that class so model-generated code never runs without a per-call
# approval, even though run_python itself executes inside the sandbox.
TOOL_RISK: Dict[str, str] = {
    "fetch_url": "web",
    "web_search": "web",
    "research": "web",
    "generate_image": "paid",
    "text_to_speech": "paid",
    "render_feedback": "paid",
    "write_file": "write",
    "apply_workspace_edit": "write",
    "run_python": "mcp",
    "run_workspace_shell": "mcp",
    "launch_app": "mcp",
    "review_screen": "screen",
    "see_image": "fs",
    "read_file": "fs",
    "list_dir": "fs",
    "read_workspace_file": "fs",
    "read_workspace_files": "fs",
    "list_workspace_dir": "fs",
    "find_workspace_files": "fs",
    "search_workspace_text": "fs",
}
# Classes that require some form of user approval before running.
#
# Default is MONEY ONLY. This is a single-user app on the owner's own machine,
# and gating write/fs/mcp made multi-hour autonomous runs unusable: every file
# write and every engine (Blender/Unreal) call blocked on a modal for up to
# 180s, then got skipped. Spending real money still asks, always.
#
# Tighten it back per-session with INFINITY_GATED_RISKS, e.g.
#   set INFINITY_GATED_RISKS=paid,write,mcp
# or open it completely with INFINITY_GATED_RISKS=none.
_DEFAULT_GATED = "paid"
_gated_env = (os.environ.get("INFINITY_GATED_RISKS") or _DEFAULT_GATED).strip().lower()
GATED_RISKS = (
    frozenset()
    if _gated_env in ("none", "off", "")
    else frozenset(r.strip() for r in _gated_env.split(",") if r.strip())
)
APPROVAL_MODE_GATES: Dict[str, frozenset[str]] = {
    "ask": frozenset({"paid", "write", "mcp", "screen", "fs", "web"}),
    "smart": frozenset({"paid", "write", "mcp", "screen", "fs"}),
    "full": frozenset(),
    "custom": GATED_RISKS,
}
# Classes gated only on their first use per stream (then "remember" applies).
ONCE_PER_SESSION_RISKS = frozenset({"screen", "fs"})


def risk_of(name: str) -> str:
    """Classify a tool invocation into a risk bucket (see TOOL_RISK)."""
    if name.startswith("mcp__"):
        return "mcp"
    return TOOL_RISK.get(name, "safe")


def gates_for_approval_mode(mode: str) -> frozenset[str]:
    """Resolve the per-chat approval posture without weakening Custom rules."""
    return APPROVAL_MODE_GATES.get(mode, APPROVAL_MODE_GATES["smart"])

# --- calculator: AST-walked, no eval ---------------------------------------- #
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        if isinstance(node.op, ast.Pow):  # guard against huge-int DoS
            left = _safe_eval(node.left)
            right = _safe_eval(node.right)
            if abs(right) > 1000 or (abs(left) > 1_000_000 and abs(right) > 100):
                raise ValueError("exponent too large")
            return _BIN_OPS[type(node.op)](left, right)
        return _BIN_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")


class _TextExtractor(HTMLParser):
    """Strip tags + script/style to recover readable page text."""

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            text = data.strip()
            if text:
                self.parts.append(text)


class ToolRegistry:
    """Runs one tool call. Holds refs to the OpenRouter client + skill engine."""

    def __init__(
        self,
        client: Any = None,
        skill_engine: Any = None,
        providers: Any = None,
        output_dir: Optional[Path] = None,
        verifier: Optional[Verifier] = None,
        workspace: Optional[Path] = None,
    ) -> None:
        self.client = client
        self.skill_engine = skill_engine
        self.providers = providers
        self.verifier = verifier
        self.workspace: Optional[Path] = Path(workspace) if workspace else None
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parent.parent / "assistant"
        # Main.py always passes its own DATA_DIR-based path; this fallback only
        # exists for standalone imports/tests and mirrors the backend default.
        # Generated media goes to a dedicated subdir that the backend serves at
        # /media (write_file output stays in output_dir and is NOT served).
        self.media_dir = self.output_dir / "media"
        self._allow_paid = False

    def dispatch(
        self, name: str, arguments: Dict[str, Any], allow_actions: bool = False
    ) -> str:
        # Gate side-effectful/paid tools unless the user enabled actions.
        if name in ACTION_TOOL_NAMES and not allow_actions:
            return f"'{name}' is a real-world action. Turn on 'Allow actions' to run it."
        # Paid handlers consult this instead of a hard-coded True; reaching dispatch
        # for a paid tool means the master switch is on and (in the assistant loop)
        # the per-action approval already passed.
        self._allow_paid = bool(allow_actions)
        handler: Optional[Callable[[Dict[str, Any]], str]] = {
            "run_python": self._run_python,
            "calculator": self._calculator,
            "fetch_url": self._fetch_url,
            "web_search": self._web_search,
            "review_screen": self._review_screen,
            "see_image": self._see_image,
            "critique": self._critique,
            "research": self._research,
            "list_skills": self._list_skills,
            "read_skill": self._read_skill,
            "read_file": self._read_file,
            "list_dir": self._list_dir,
            "write_file": self._write_file,
            "generate_image": self._generate_image,
            "text_to_speech": self._text_to_speech,
            "spatial_raycast": self._spatial_raycast,
            "spatial_measure": self._spatial_measure,
            "spatial_camera_frame": self._spatial_camera_frame,
            "spatial_collision": self._spatial_collision,
            "render_feedback": self._render_feedback,
            "read_workspace_file": self._read_workspace_file,
            "read_workspace_files": self._read_workspace_files,
            "list_workspace_dir": self._list_workspace_dir,
            "find_workspace_files": self._find_workspace_files,
            "search_workspace_text": self._search_workspace_text,
            "apply_workspace_edit": self._apply_workspace_edit,
            "run_workspace_shell": self._run_workspace_shell,
            "launch_app": self._launch_app,
        }.get(name)
        if handler is None:
            return f"Error: unknown tool {name!r}."
        try:
            return handler(arguments or {})
        except Exception as exc:  # noqa: BLE001 - report to the model, never crash
            logger.warning("Tool %s failed: %s", name, exc)
            return f"Error running {name}: {exc}"

    # --- assistant file/system + creative handlers ------------------------- #
    def _read_file(self, args: Dict[str, Any]) -> str:
        path = Path(str(args.get("path") or ""))
        if not path.is_file():
            return f"No such file: {path}"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Could not read {path}: {exc}"
        return text[:6000] + ("\n…(truncated)" if len(text) > 6000 else "")

    def _list_dir(self, args: Dict[str, Any]) -> str:
        path = Path(str(args.get("path") or ""))
        if not path.is_dir():
            return f"No such directory: {path}"
        try:
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        except OSError as exc:
            return f"Could not list {path}: {exc}"
        return "\n".join(entries[:200]) or "(empty directory)"

    def _write_file(self, args: Dict[str, Any]) -> str:
        name = str(args.get("filename") or "").strip() or "output.txt"
        name = Path(name).name  # strip any path components — output folder only
        content = str(args.get("content") or "")
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            dest = self.output_dir / name
            dest.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"Could not write file: {exc}"
        return f"Wrote {len(content)} chars → {dest}"

    def _resolve_workspace(self, rel_path: str) -> Optional[Path]:
        if self.workspace is None:
            return None
        try:
            target = (self.workspace / rel_path).resolve()
            target.relative_to(self.workspace.resolve())
            return target
        except (ValueError, OSError):
            return None

    @staticmethod
    def _validate_workspace_glob(pattern: str) -> Optional[str]:
        """Return a user-facing error for unsafe/invalid relative globs."""
        if not pattern:
            return "Error: no glob pattern provided."
        if len(pattern) > 300 or "\x00" in pattern:
            return "Invalid workspace glob: pattern is too long or malformed."
        path_pattern = Path(pattern)
        if path_pattern.is_absolute() or any(part == ".." for part in path_pattern.parts):
            return "Invalid workspace glob: use a path relative to the selected workspace."
        return None

    def _workspace_glob(
        self, pattern: str, *, max_results: int = 2500
    ) -> tuple[List[tuple[Path, Path]], Optional[str], bool]:
        """Resolve a bounded set of glob matches that cannot escape workspace."""
        if self.workspace is None:
            return [], "No workspace selected. Choose a project folder first.", False
        error = self._validate_workspace_glob(pattern)
        if error:
            return [], error, False

        root = self.workspace.resolve()
        ignored = {
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            "coverage",
            "Library",
            "Temp",
            "Binaries",
            "Intermediate",
            "DerivedDataCache",
        }
        matches: List[tuple[Path, Path]] = []
        truncated = False
        try:
            for candidate in root.glob(pattern):
                try:
                    original_rel = candidate.relative_to(root)
                except ValueError:
                    continue
                # Reuse the normal workspace boundary check for every match.
                safe_target = self._resolve_workspace(str(original_rel))
                if safe_target is None:
                    continue
                try:
                    safe_rel = safe_target.relative_to(root)
                except ValueError:
                    continue
                if any(part in ignored for part in safe_rel.parts):
                    continue
                matches.append((safe_target, safe_rel))
                if len(matches) >= max_results:
                    truncated = True
                    break
        except (OSError, RuntimeError, ValueError) as exc:
            return [], f"Could not search workspace: {exc}", False
        return matches, None, truncated

    def _read_workspace_file(self, args: Dict[str, Any]) -> str:
        if self.workspace is None:
            return "No workspace selected. Choose a project folder first."
        rel_path = str(args.get("file_path") or "").strip()
        target = self._resolve_workspace(rel_path)
        if target is None:
            return f"Invalid workspace path: {rel_path}"
        if not target.is_file():
            return f"No such file in workspace: {rel_path}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Could not read {rel_path}: {exc}"
        return text[:6000] + ("\n…(truncated)" if len(text) > 6000 else "")

    def _read_workspace_files(self, args: Dict[str, Any]) -> str:
        if self.workspace is None:
            return "No workspace selected. Choose a project folder first."
        raw_paths = args.get("file_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            return "Error: file_paths must be a non-empty array."
        if len(raw_paths) > 20:
            return "Error: read_workspace_files accepts at most 20 paths per call."

        per_file_limit = 4000
        total_limit = 16000
        chunks: List[str] = []
        used = 0
        for raw_path in raw_paths:
            rel_path = str(raw_path or "").strip()
            target = self._resolve_workspace(rel_path)
            if target is None:
                section = f"--- {rel_path or '(empty path)'} ---\nInvalid workspace path."
            elif not target.is_file():
                section = f"--- {rel_path} ---\nNo such file in workspace."
            else:
                try:
                    with target.open("r", encoding="utf-8", errors="replace") as handle:
                        text = handle.read(per_file_limit + 1)
                except OSError as exc:
                    section = f"--- {rel_path} ---\nCould not read file: {exc}"
                else:
                    was_truncated = len(text) > per_file_limit
                    text = text[:per_file_limit]
                    section = f"--- {rel_path} ---\n{text}"
                    if was_truncated:
                        section += "\n...(file truncated)"

            remaining = total_limit - used
            if remaining <= 0:
                chunks.append("...(batch output truncated)")
                break
            if len(section) > remaining:
                chunks.append(section[:remaining] + "\n...(batch output truncated)")
                break
            chunks.append(section)
            used += len(section) + 2
        return "\n\n".join(chunks)

    def _find_workspace_files(self, args: Dict[str, Any]) -> str:
        pattern = str(args.get("pattern") or "").strip()
        include_directories = bool(args.get("include_directories", False))
        matches, error, scan_truncated = self._workspace_glob(pattern, max_results=1000)
        if error:
            return error

        results: List[str] = []
        for target, rel_path in matches:
            if target.is_dir():
                if include_directories:
                    results.append(rel_path.as_posix() + "/")
            elif target.is_file():
                results.append(rel_path.as_posix())
            if len(results) >= 300:
                scan_truncated = True
                break
        if not results:
            return "(no matching workspace files)"
        suffix = "\n...(results truncated)" if scan_truncated else ""
        return "\n".join(sorted(set(results))) + suffix

    def _search_workspace_text(self, args: Dict[str, Any]) -> str:
        query = str(args.get("query") or "")
        if not query:
            return "Error: no search query provided."
        if len(query) > 1000 or "\x00" in query:
            return "Error: search query is too long or malformed."
        include = str(args.get("include") or "**/*").strip() or "**/*"
        case_sensitive = bool(args.get("case_sensitive", False))
        matches, error, scan_truncated = self._workspace_glob(include, max_results=2500)
        if error:
            return error

        needle = query if case_sensitive else query.casefold()
        results: List[str] = []
        skipped_large = 0
        for target, rel_path in matches:
            if not target.is_file():
                continue
            try:
                if target.stat().st_size > 1_500_000:
                    skipped_large += 1
                    continue
                data = target.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            text = data.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                excerpt = line.strip()
                if len(excerpt) > 300:
                    excerpt = excerpt[:300] + "..."
                results.append(f"{rel_path.as_posix()}:{line_number}: {excerpt}")
                if len(results) >= 100:
                    scan_truncated = True
                    break
            if len(results) >= 100:
                break
        if not results:
            note = " (large files skipped)" if skipped_large else ""
            return f"(no text matches{note})"
        notes: List[str] = []
        if scan_truncated:
            notes.append("results truncated")
        if skipped_large:
            notes.append(f"{skipped_large} large file(s) skipped")
        suffix = f"\n...({'; '.join(notes)})" if notes else ""
        return "\n".join(results) + suffix

    def _list_workspace_dir(self, args: Dict[str, Any]) -> str:
        if self.workspace is None:
            return "No workspace selected. Choose a project folder first."
        rel_path = str(args.get("dir_path") or ".").strip() or "."
        target = self._resolve_workspace(rel_path)
        if target is None:
            return f"Invalid workspace path: {rel_path}"
        if not target.is_dir():
            return f"No such directory in workspace: {rel_path}"
        try:
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        except OSError as exc:
            return f"Could not list {rel_path}: {exc}"
        return "\n".join(entries[:200]) or "(empty directory)"

    def _apply_workspace_edit(self, args: Dict[str, Any]) -> str:
        if self.workspace is None:
            return "No workspace selected. Choose a project folder first."
        rel_path = str(args.get("file_path") or "").strip()
        search = str(args.get("search") or "")
        replace = str(args.get("replace"))
        target = self._resolve_workspace(rel_path)
        if target is None:
            return f"Invalid workspace path: {rel_path}"
        original = ""
        if target.is_file():
            try:
                original = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return f"Could not read {rel_path}: {exc}"
        if search:
            if search not in original:
                return f"Search block not found in {rel_path}. The file may have changed."
            patched = original.replace(search, replace, 1)
        else:
            patched = replace
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(patched, encoding="utf-8")
        except OSError as exc:
            return f"Could not write {rel_path}: {exc}"
        return f"Applied edit to {rel_path} ({len(patched)} chars)."

    def _run_workspace_shell(self, args: Dict[str, Any]) -> str:
        if self.workspace is None:
            return "No workspace selected. Choose a project folder first."
        command = str(args.get("command") or "").strip()
        if not command:
            return "Error: no command provided."
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "Command timed out after 60s."
        except Exception as exc:  # noqa: BLE001
            return f"Error running command: {exc}"
        chunks = [f"exit code: {result.returncode}"]
        if result.stdout:
            chunks.append("stdout:\n" + result.stdout[:4000])
        if result.stderr:
            chunks.append("stderr:\n" + result.stderr[:2000])
        return "\n".join(chunks)

    @staticmethod
    def _find_app_executable(app: str) -> Optional[Path]:
        expanded = os.path.expandvars(os.path.expanduser(app))
        path_like = Path(expanded)
        if path_like.is_absolute() or any(sep in expanded for sep in ("/", "\\")):
            try:
                candidate = path_like.resolve()
            except OSError:
                return None
            return candidate if candidate.is_file() else None

        found = shutil.which(expanded)
        if found:
            return Path(found)

        # GUI apps on Windows are often registered without being added to PATH.
        # Query App Paths directly; never interpolate the name into a shell call.
        if os.name == "nt":
            try:
                import winreg  # type: ignore[import-not-found]

                exe_name = expanded if expanded.lower().endswith(".exe") else f"{expanded}.exe"
                key_path = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, key_path) as key:
                            registered, _ = winreg.QueryValueEx(key, None)
                    except OSError:
                        continue
                    candidate = Path(str(registered)).expanduser()
                    if candidate.is_file():
                        return candidate
            except (ImportError, OSError):
                pass
            # Creative/code tools often do not add themselves to PATH or App
            # Paths. Probe only their conventional, bounded install locations
            # so natural requests like "open Blender" work without exposing a
            # general filesystem search.
            program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
            aliases: Dict[str, List[Path]] = {
                "blender": list((program_files / "Blender Foundation").glob("Blender */blender.exe")),
                "unrealeditor": list((program_files / "Epic Games").glob("UE_*/Engine/Binaries/Win64/UnrealEditor.exe")),
                "unreal": list((program_files / "Epic Games").glob("UE_*/Engine/Binaries/Win64/UnrealEditor.exe")),
                "unity": list((program_files / "Unity/Hub/Editor").glob("*/Editor/Unity.exe")),
                "code": [local_app_data / "Programs/Microsoft VS Code/Code.exe"],
                "vscode": [local_app_data / "Programs/Microsoft VS Code/Code.exe"],
                "chrome": [program_files / "Google/Chrome/Application/chrome.exe"],
            }
            alias = Path(expanded).stem.casefold().replace(" ", "")
            candidates = [candidate for candidate in aliases.get(alias, []) if candidate.is_file()]
            if candidates:
                return sorted(candidates, reverse=True)[0]
        return None

    def _launch_app(self, args: Dict[str, Any]) -> str:
        app = str(args.get("app") or "").strip()
        if not app or "\x00" in app:
            return "Error: no valid application path or name provided."
        raw_args = args.get("args", [])
        if raw_args is None:
            raw_args = []
        if not isinstance(raw_args, list):
            return "Error: args must be an array of strings."
        if len(raw_args) > 32:
            return "Error: launch_app accepts at most 32 arguments."
        app_args = [str(value) for value in raw_args]
        if any("\x00" in value or len(value) > 4000 for value in app_args):
            return "Error: one or more application arguments are malformed."

        executable = self._find_app_executable(app)
        if executable is None:
            return f"Application not found: {app}"
        blocked_launchers = {
            "cmd",
            "cmd.exe",
            "powershell",
            "powershell.exe",
            "pwsh",
            "pwsh.exe",
            "sh",
            "bash",
            "zsh",
            "wscript.exe",
            "cscript.exe",
            "mshta.exe",
            "rundll32.exe",
        }
        if executable.name.casefold() in blocked_launchers:
            return "Error: command interpreters are not applications; use the approved shell tool instead."
        if os.name == "nt" and executable.suffix.casefold() not in {".exe", ".com"}:
            return "Error: launch_app only starts executable applications on Windows."
        if os.name != "nt" and not os.access(executable, os.X_OK):
            return f"Application is not executable: {executable}"

        command = [str(executable), *app_args]
        popen_kwargs: Dict[str, Any] = {
            "shell": False,
            "cwd": str(self.workspace.resolve()) if self.workspace else str(executable.parent),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **popen_kwargs)  # noqa: S603 - approved, no shell
        except OSError as exc:
            return f"Could not launch {executable.name}: {exc}"
        return f"Launched {executable.name} (PID {process.pid})."

    def _generate_image(self, args: Dict[str, Any]) -> str:
        if self.providers is None:
            return "Image generation unavailable (no provider manager)."
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "Error: no image prompt."
        # allow_actions already enforced in dispatch(); real paid gate is here too.
        return self.providers.generate_image(
            prompt, self.media_dir, allow_paid=getattr(self, "_allow_paid", False)
        )

    def _text_to_speech(self, args: Dict[str, Any]) -> str:
        if self.providers is None:
            return "Text-to-speech unavailable (no provider manager)."
        text = str(args.get("text") or "").strip()
        if not text:
            return "Error: no text."
        return self.providers.text_to_speech(
            text, self.media_dir, allow_paid=getattr(self, "_allow_paid", False)
        )

    # --- handlers ---------------------------------------------------------- #
    def _run_python(self, args: Dict[str, Any]) -> str:
        code = str(args.get("code") or "").strip()
        if not code:
            return "Error: no code provided."
        python_exe = resolve_python()
        if python_exe is None:
            return "Error: no Python interpreter available to run code."
        with tempfile.TemporaryDirectory(prefix="infinity-tool-") as tmp:
            path = Path(tmp) / "snippet.py"
            path.write_text(code, encoding="utf-8")
            # Same sandbox as the swarm (see core/sandbox.py): scrubbed env
            # with no API keys, isolated interpreter flags, bounded runtime
            # and output. Approval gating (TOOL_RISK) still applies upstream.
            result = run_sandboxed(python_exe, path, Path(tmp), timeout=20)
        out = (result.get("stdout") or "").strip()
        err = (result.get("stderr") or "").strip()
        rc = result.get("returncode")
        chunks: List[str] = [f"exit code: {rc}"]
        if out:
            chunks.append("stdout:\n" + out[:2500])
        if err:
            chunks.append("stderr:\n" + err[:1500])
        if not out and not err:
            chunks.append("(no output — did you print()?)")
        return "\n".join(chunks)

    def _calculator(self, args: Dict[str, Any]) -> str:
        expr = str(args.get("expression") or "").strip()
        if not expr:
            return "Error: no expression."
        tree = ast.parse(expr, mode="eval")
        value = _safe_eval(tree.body)
        return f"{expr} = {value}"

    def _fetch_url(self, args: Dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return "Error: url must start with http:// or https://."
        blocked = url_is_blocked(url)
        if blocked:
            return f"Error: refused to fetch ({blocked})."
        req = urllib.request.Request(
            url, headers={"User-Agent": "InfinityCode/1.0 (+chat tool)"}
        )
        with _GUARDED_OPENER.open(req, timeout=10) as resp:  # noqa: S310 - guarded opener
            raw = resp.read(600_000)
            ctype = resp.headers.get("Content-Type", "")
        body = raw.decode("utf-8", errors="replace")
        if "html" in ctype or body.lstrip().startswith("<"):
            parser = _TextExtractor()
            parser.feed(body)
            body = " ".join(parser.parts)
        body = " ".join(body.split())
        return body[:4000] or "(page had no readable text)"

    def _web_search(self, args: Dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "Error: no query."
        if self.client is None:
            return "Error: web search unavailable (no API client)."
        # Perplexity Sonar via OpenRouter returns cited, live-web answers.
        result = self.client.chat(
            "perplexity/sonar",
            [
                {
                    "role": "system",
                    "content": "Answer with current facts and cite sources inline.",
                },
                {"role": "user", "content": query},
            ],
            max_tokens=700,
        )
        # chat() returns a ChatResult TypedDict (dict access, not attribute).
        text = result["text"] if isinstance(result, dict) else getattr(result, "text", "")
        return (text or "").strip()[:3500] or "(no results)"

    # --- vision + research handlers --------------------------------------- #
    def _vision_ask(self, data_url: str, prompt: str) -> str:
        if self.client is None:
            return "Error: vision unavailable (no API client)."
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        result = self.client.chat_with_vision(
            model_id=VISION_MODEL, messages_with_images=messages, max_tokens=1200
        )
        text = result["text"] if isinstance(result, dict) else getattr(result, "text", "")
        return (text or "").strip()[:3500] or "(vision model returned nothing)"

    def _review_screen(self, args: Dict[str, Any]) -> str:
        goal = str(args.get("goal") or "Describe what is on screen.").strip()
        try:
            from PIL import ImageGrab  # noqa: PLC0415 - optional, Windows/mac only
        except ImportError:
            return "Error: screen capture unavailable (Pillow not installed)."
        try:
            image = ImageGrab.grab()  # primary display
        except Exception as exc:  # noqa: BLE001
            return f"Error: could not capture the screen: {exc}"
        with tempfile.TemporaryDirectory(prefix="infinity-shot-") as tmp:
            shot = Path(tmp) / "screen.png"
            # Downscale wide screens so the vision payload stays small.
            if image.width > 1600:
                ratio = 1600 / image.width
                image = image.resize((1600, int(image.height * ratio)))
            image.save(shot, "PNG")
            data = base64.b64encode(shot.read_bytes()).decode("ascii")
        prompt = (
            "You are reviewing a screenshot of the user's screen. "
            f"Goal: {goal}\n\nDescribe what is shown, then give a short, specific "
            "critique against the goal (what works, what to fix)."
        )
        return self._vision_ask(f"data:image/png;base64,{data}", prompt)

    def _see_image(self, args: Dict[str, Any]) -> str:
        source = str(args.get("source") or "").strip()
        question = str(args.get("question") or "Describe this image.").strip()
        if not source:
            return "Error: no image source."
        # Always send bytes as base64: providers often can't fetch remote URLs
        # (blocked user-agents), so we download here and inline the image.
        if source.startswith(("http://", "https://")):
            blocked = url_is_blocked(source)
            if blocked:
                return f"Error: refused to fetch image ({blocked})."
            try:
                req = urllib.request.Request(
                    source, headers={"User-Agent": "Mozilla/5.0 (InfinityCode)"}
                )
                with _GUARDED_OPENER.open(req, timeout=15) as resp:  # noqa: S310 - guarded opener
                    raw = resp.read(8_000_000)
                    ctype = resp.headers.get("Content-Type", "image/png")
            except Exception as exc:  # noqa: BLE001
                return f"Error: could not download image: {exc}"
            mime = ctype.split(";")[0].strip() or "image/png"
            if not mime.startswith("image/"):
                mime = "image/png"
            data = base64.b64encode(raw).decode("ascii")
            data_url = f"data:{mime};base64,{data}"
        else:
            path = Path(source)
            if not path.is_file():
                return f"Error: no such image file: {source}"
            suffix = path.suffix.lower().lstrip(".") or "png"
            mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            data_url = f"data:image/{mime};base64,{data}"
        return self._vision_ask(data_url, question)

    def _critique(self, args: Dict[str, Any]) -> str:
        work = str(args.get("work") or "").strip()
        goal = str(args.get("goal") or "general quality").strip()
        if not work:
            return "Error: nothing to critique."
        if self.client is None:
            return "Error: critique unavailable (no API client)."
        result = self.client.chat(
            SYNTH_MODEL,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a rigorous but fair critic. Score the work 0-10 "
                        "against the goal. Reply as: Score: N/10, then '+ Strengths' "
                        "(2-3 bullets) and '- Fixes' (2-3 concrete, actionable bullets)."
                    ),
                },
                {"role": "user", "content": f"Goal: {goal}\n\nWork:\n{work[:6000]}"},
            ],
            max_tokens=800,
        )
        text = result["text"] if isinstance(result, dict) else getattr(result, "text", "")
        return (text or "").strip()[:3000] or "(no critique)"

    def _research(self, args: Dict[str, Any]) -> str:
        question = str(args.get("question") or "").strip()
        if not question:
            return "Error: no research question."
        if self.client is None:
            return "Error: research unavailable (no API client)."
        result = self.client.chat(
            RESEARCH_MODEL,
            [
                {
                    "role": "system",
                    "content": (
                        "You are a research analyst. Answer with a structured "
                        "briefing: 3-6 key findings as bullets, each with an inline "
                        "citation, then a one-line bottom line. Use only current, "
                        "verifiable facts."
                    ),
                },
                {"role": "user", "content": question},
            ],
            max_tokens=1100,
        )
        text = result["text"] if isinstance(result, dict) else getattr(result, "text", "")
        return (text or "").strip()[:4000] or "(no findings)"

    def _list_skills(self, _args: Dict[str, Any]) -> str:
        if self.skill_engine is None:
            return "The skill vault is empty."
        names = list(self.skill_engine.list_skills())
        return ("Saved skills: " + ", ".join(names)) if names else "The skill vault is empty."

    def _read_skill(self, args: Dict[str, Any]) -> str:
        if self.skill_engine is None:
            return "Error: skill vault unavailable."
        name = str(args.get("name") or "").strip()
        skill = self.skill_engine.get_skill(name)
        if not skill:
            return f"No skill named {name!r}."
        return json.dumps(skill, ensure_ascii=False)[:3500]

    # --- spatial + render tools (Blender sidecar) ------------------------- #
    @staticmethod
    def _as_float_list(value: Any, name: str) -> Optional[List[float]]:
        try:
            seq = list(value)
            if len(seq) < 3:
                return None
            return [float(x) for x in seq[:3]]
        except (TypeError, ValueError):
            return None

    def _spatial_raycast(self, args: Dict[str, Any]) -> str:
        origin = self._as_float_list(args.get("origin"), "origin")
        direction = self._as_float_list(args.get("direction"), "direction")
        mesh = str(args.get("mesh_name") or "").strip()
        if origin is None or direction is None or not mesh:
            return "Error: origin, direction, and mesh_name are required."
        result = spatial_tools.raycast(origin, direction, mesh)
        return json.dumps(result, ensure_ascii=False)

    def _spatial_measure(self, args: Dict[str, Any]) -> str:
        a = self._as_float_list(args.get("a"), "a")
        b = self._as_float_list(args.get("b"), "b")
        if a is None or b is None:
            return "Error: a and b must be [x, y, z] arrays."
        result = spatial_tools.measure_distance(a, b)
        return json.dumps(result, ensure_ascii=False)

    def _spatial_camera_frame(self, args: Dict[str, Any]) -> str:
        camera = str(args.get("camera_name") or "").strip()
        target = self._as_float_list(args.get("target"), "target")
        if not camera or target is None:
            return "Error: camera_name and target are required."
        result = spatial_tools.camera_frame(camera, target)
        return json.dumps(result, ensure_ascii=False)

    def _spatial_collision(self, args: Dict[str, Any]) -> str:
        a = str(args.get("object_a") or "").strip()
        b = str(args.get("object_b") or "").strip()
        if not a or not b:
            return "Error: object_a and object_b are required."
        result = spatial_tools.collision_check(a, b)
        return json.dumps(result, ensure_ascii=False)

    def _render_feedback(self, args: Dict[str, Any]) -> str:
        if self.verifier is None:
            return "Error: render feedback unavailable (no verifier configured)."
        script = str(args.get("script") or "").strip()
        intent = str(args.get("intent") or "").strip()
        if not script or not intent:
            return "Error: script and intent are required."
        out = args.get("output_path")
        out_path = Path(str(out)) if out else None
        result = self.verifier.render_feedback(script, intent, out_path)
        return json.dumps(
            {
                "ok": result.ok,
                "stage": result.stage,
                "reason": result.reason,
                "output_path": str(result.output_path) if result.output_path else None,
            },
            ensure_ascii=False,
        )


__all__ = [
    "ToolRegistry",
    "TOOL_SCHEMAS",
    "TOOL_NAMES",
    "ASSISTANT_TOOL_SCHEMAS",
    "ASSISTANT_TOOL_NAMES",
    "ACTION_TOOL_NAMES",
    "GATED_RISKS",
    "gates_for_approval_mode",
    "risk_of",
]

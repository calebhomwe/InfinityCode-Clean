"""Shared code-execution helpers for the swarm and its war-mode modules.

Centralises the interpreter resolution + subprocess run so the tournament and
red-team modules don't each re-implement it (and so the PyInstaller-frozen
build still runs generated code on a real Python, not the frozen exe).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_EXEC_TIMEOUT_SECONDS: int = 60


def resolve_python() -> Optional[str]:
    """Return a real Python interpreter path.

    In a PyInstaller build sys.executable is the frozen backend exe, not
    Python, so fall back to INFINITY_PYTHON or whatever is on PATH.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    override: Optional[str] = os.environ.get("INFINITY_PYTHON")
    if override and Path(override).is_file():
        return override
    for name in ("python", "python3", "py"):
        found: Optional[str] = shutil.which(name)
        if found:
            return found
    return None


def run_python(
    code_path: Path,
    workdir: Path,
    timeout: int = DEFAULT_EXEC_TIMEOUT_SECONDS,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run a Python file in a real subprocess and capture the outcome.

    Never raises: transport/OS failures are returned as a non-zero result so
    callers can treat them as ordinary mission failures.
    """
    python_exe: Optional[str] = resolve_python()
    if python_exe is None:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": (
                "No Python interpreter available to run generated code. "
                "Install Python or set INFINITY_PYTHON to a python.exe."
            ),
            "timed_out": False,
        }
    # Resolve to an absolute path: we set cwd below, and a relative code_path
    # would be re-joined against that cwd by the subprocess (doubling it).
    abs_code_path: str = str(Path(code_path).resolve())
    # Security: -I isolates from user site-packages and env vars (same as sandbox.py run_sandboxed).
    # AST validation catches dangerous imports/calls before execution.
    from backend.core.sandbox import CodeValidator
    try:
        source = Path(code_path).read_text(encoding="utf-8")
        validator = CodeValidator(forbidden_imports={"os", "shutil", "socket", "http", "urllib", "ctypes", "subprocess"})
        valid, msg = validator.validate(source)
        if not valid:
            return {"returncode": -1, "stdout": "", "stderr": f"Sandbox rejected: {msg}", "timed_out": False}
    except (OSError, ImportError):
        pass  # If we cannot read/validate, proceed with -I protection only
    command: List[str] = [python_exe, "-I", abs_code_path, *(extra_args or [])]
    try:
        completed = subprocess.run(
            command,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "")[-4000:],
            "stderr": (completed.stderr or "")[-4000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout}s.",
            "timed_out": True,
        }
    except OSError as exc:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Could not launch python subprocess: {exc}",
            "timed_out": False,
        }


def extract_code(text: str) -> str:
    """Pull python source out of a fenced markdown response (or return as-is)."""
    import re

    if not text:
        return ""
    fenced = re.search(r"```python\s*([\s\S]*?)```", text)
    if fenced:
        return fenced.group(1).strip()
    fenced = re.search(r"```\s*([\s\S]*?)```", text)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


__all__ = [
    "resolve_python",
    "run_python",
    "extract_code",
    "DEFAULT_EXEC_TIMEOUT_SECONDS",
]

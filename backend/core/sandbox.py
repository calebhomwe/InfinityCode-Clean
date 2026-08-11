"""Sandboxed code execution for Infinity Code self-improvement.

AST validation + strict subprocess execution with resource limits.
Used by AutoFix and Evolve to run generated Python safely.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeExecutionRequest:
    code: str
    timeout_seconds: int = 15
    max_memory_mb: int = 512
    cwd: Optional[Path] = None
    env: Optional[dict] = None


@dataclass
class CodeExecutionResult:
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    output_file_path: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class CodeValidator:
    """AST-level security and syntax checks."""

    def __init__(self, forbidden_imports: Optional[set] = None) -> None:
        self.forbidden: set = set(forbidden_imports) if forbidden_imports else set()

    def validate(self, code: str) -> tuple[bool, str]:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return False, f"Syntax Error: {exc}"

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in self.forbidden:
                        return False, f"Security Violation: Import '{alias.name}' is forbidden."
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                if mod in self.forbidden:
                    return False, f"Security Violation: Import from '{node.module}' is forbidden."
            elif isinstance(node, (ast.Call,)):
                # Heuristic: flag dangerous built-ins explicitly
                if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec", "compile"):
                    return False, f"Security Violation: Call to '{node.func.id}' is forbidden."

        return True, "Valid"


class SandboxedExecutor:
    """Run Python in an isolated subprocess with timeout and env limits."""

    def __init__(self, workspace_dir: Path, validator: Optional[CodeValidator] = None) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.validator = validator or CodeValidator()

    def execute(self, request: CodeExecutionRequest) -> CodeExecutionResult:
        # --- Step A: AST validation ---
        is_valid, msg = self.validator.validate(request.code)
        if not is_valid:
            return CodeExecutionResult(success=False, stderr=msg)

        # --- Step B: Write to isolated temp file ---
        with tempfile.NamedTemporaryFile(
            dir=self.workspace_dir, suffix=".py", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(request.code)
            script_path = f.name

        # --- Step C: Build safe environment ---
        safe_env = {
            "PYTHONUNBUFFERED": "1",
            "OMP_NUM_THREADS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if request.env:
            safe_env.update(request.env)

        cwd = request.cwd or self.workspace_dir

        try:
            result = subprocess.run(
                ["python3", script_path],
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                cwd=cwd,
                env=safe_env,
            )
        except subprocess.TimeoutExpired:
            return CodeExecutionResult(
                success=False,
                stderr=f"Execution exceeded {request.timeout_seconds}s timeout.",
            )
        except FileNotFoundError:
            # Fallback: try "python" on Windows where python3 may not exist.
            try:
                result = subprocess.run(
                    ["python", script_path],
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_seconds,
                    cwd=cwd,
                    env=safe_env,
                )
            except FileNotFoundError:
                return CodeExecutionResult(
                    success=False,
                    stderr="Python interpreter not found (tried 'python3' and 'python').",
                )
        finally:
            try:
                os.remove(script_path)
            except OSError:
                pass

        # --- Step D: Hunt for expected 3D / data outputs ---
        output_file = None
        for ext in (".step", ".stl", ".obj", ".json", ".txt", ".png"):
            potential = self.workspace_dir / f"output{ext}"
            if potential.exists() and potential.stat().st_size > 0:
                output_file = str(potential)
                break

        success = result.returncode == 0
        return CodeExecutionResult(
            success=success,
            stdout=result.stdout,
            stderr=result.stderr,
            output_file_path=output_file,
            metadata={"returncode": result.returncode},
        )


def run_sandboxed(
    python_exe: str,
    script: Path,
    workdir: Path,
    timeout: int,
    output_cap: int = 4000,
) -> dict:
    """Run an untrusted script. Returns the swarm's expected dict shape.

    Compatibility API for core/swarm.py (_execute_code). Deliberately runs the
    CALLER'S interpreter (`python_exe`), not whatever `python` is on PATH: on
    this box PATH python is a Swift-toolchain 3.10 with no numpy/matplotlib,
    which silently failed every mission that renders an image.

    `-I` isolates env/user-site; `-S` is NOT used - it would skip site-packages
    entirely and break numpy/matplotlib imports (measured 2026-07-31).
    """
    env = {
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
    }
    # OS-level hardening (Stage 2.1): restricted token + low integrity + job
    # object. The workdir is labeled low-integrity so generated code can write
    # its outputs there but nowhere else in the user profile.
    try:
        from backend.core.os_sandbox import (can_harden, hardened_launch,
                                             hardened_wait, mark_low_integrity)
    except ImportError:  # pragma: no cover - flat-import fallback
        try:
            from core.os_sandbox import (can_harden, hardened_launch,
                                         hardened_wait, mark_low_integrity)
        except ImportError:
            can_harden = None  # type: ignore
    if can_harden and can_harden():
        try:
            mark_low_integrity(str(workdir))
            hp, job, out_r, err_r = hardened_launch(
                [python_exe, "-I", str(script)], str(workdir), env)
            res = hardened_wait(hp, job, out_r, err_r, timeout, output_cap)
            logger.info("Hardened sandbox executed %s (rc=%s).",
                        script.name, res["returncode"])
            return res
        except Exception as exc:  # noqa: BLE001 - fall back, never fail
            logger.warning("Hardened launch failed (%s); using plain sandbox.",
                           exc)
    try:
        result = subprocess.run(
            [python_exe, "-I", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(workdir),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout}s.",
            "timed_out": True,
            "sandboxed": True,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Could not launch sandboxed subprocess: {exc}",
            "timed_out": False,
            "sandboxed": False,
        }
    return {
        "returncode": result.returncode,
        "stdout": (result.stdout or "")[-output_cap:],
        "stderr": (result.stderr or "")[-output_cap:],
        "timed_out": False,
        "sandboxed": True,
    }


__all__ = [
    "CodeExecutionRequest",
    "CodeExecutionResult",
    "CodeValidator",
    "SandboxedExecutor",
    "run_sandboxed",
]

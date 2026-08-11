"""LSP-style diagnostics collection for Infinity Code.

Gives the model the same truth the editor shows: ruff for Python files,
TypeScript syntactic diagnostics for TS/JS files (via node + typescript).
Results are cached per (path, mtime) so repeated calls are free.

Usage:
    from backend.core.diagnostics import collect_file_diagnostics, diagnostics_block
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger("infinity.diagnostics")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODE = "node"
TS_HELPER = os.path.join(REPO_ROOT, "Tools", "ts_diag.js")

# path -> (mtime, diagnostics list)
_CACHE: Dict[str, tuple] = {}
_MAX_ENTRIES = 60


def _cached(path: str, mtime: float) -> Optional[List[Dict[str, Any]]]:
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    return None


def _store(path: str, mtime: float, diags: List[Dict[str, Any]]) -> None:
    _CACHE[path] = (mtime, diags)
    if len(_CACHE) > 256:
        oldest = sorted(_CACHE, key=lambda p: _CACHE[p][0])[: len(_CACHE) - 256]
        for p in oldest:
            _CACHE.pop(p, None)


def _ruff_check(path: str) -> List[Dict[str, Any]]:
    """ruff JSON output -> normalized diagnostics."""
    try:
        proc = subprocess.run(
            ["ruff", "check", "--output-format", "json", path],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("ruff unavailable for %s: %s", path, exc)
        return []
    if proc.returncode not in (0, 1):  # 1 = findings, anything else = tool error
        return []
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out: List[Dict[str, Any]] = []
    for it in items[:_MAX_ENTRIES]:
        code = str(it.get("code", "E"))
        loc = it.get("location", {}) or {}
        sev = "error" if code[:1] in ("E", "F") else ("warning" if code[:1] == "W" else "info")
        out.append(
            {
                "line": int(loc.get("row", 0) or 0),
                "column": int(loc.get("column", 0) or 0),
                "severity": sev,
                "code": code,
                "message": str(it.get("message", "")),
            }
        )
    return out


def _py_syntax_check(path: str) -> List[Dict[str, Any]]:
    """Fallback: python -m py_compile catches syntax errors without ruff."""
    try:
        proc = subprocess.run(
            ["python", "-m", "py_compile", path],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("py_compile unavailable for %s: %s", path, exc)
        return []
    if proc.returncode == 0:
        return []
    line = col = 0
    for ln in proc.stderr.splitlines():
        m = __import__("re").search(r"line (\d+)", ln)
        if m:
            line = int(m.group(1))
        break
    return [{"line": line, "column": col, "severity": "error", "code": "PY-SYNTAX",
             "message": (proc.stderr.strip().splitlines() or ["syntax error"])[-1][:300]}]


def _ts_check(path: str) -> List[Dict[str, Any]]:
    """TypeScript syntactic diagnostics via node + typescript (no project build)."""
    if not os.path.exists(TS_HELPER):
        return []
    try:
        proc = subprocess.run(
            [NODE, TS_HELPER, path],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("node unavailable for %s: %s", path, exc)
        return []
    if proc.returncode != 0:
        return []
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [
        {"line": int(it.get("line", 0) or 0), "column": int(it.get("column", 0) or 0),
         "severity": "error", "code": "TS-SYNTAX", "message": str(it.get("message", ""))[:300]}
        for it in items[:_MAX_ENTRIES]
    ]


def collect_file_diagnostics(path: str) -> List[Dict[str, Any]]:
    """Return normalized diagnostics for one file, cached by mtime."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _cached(path, mtime)
    if cached is not None:
        return cached
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        diags = _ruff_check(path) or _py_syntax_check(path)
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        diags = _ts_check(path)
    else:
        diags = []
    _store(path, mtime, diags)
    return diags


def diagnostics_block(path: str) -> str:
    """Compact text block for system-prompt injection; empty string if clean."""
    diags = collect_file_diagnostics(path)
    if not diags:
        return ""
    lines = [f"--- Editor diagnostics for {os.path.basename(path)} ---"]
    for d in diags[:30]:
        loc = f"{d['line']}:{d['column']}" if d.get("line") else "?"
        lines.append(f"{path}:{loc} {d['severity']} {d.get('code', '')}: {d['message']}")
    if len(diags) > 30:
        lines.append(f"... and {len(diags) - 30} more")
    lines.append("Fix the errors the user's request touches; mention the rest briefly.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        block = diagnostics_block(p)
        print(f"=== {p} ===")
        print(block if block else "(clean)")

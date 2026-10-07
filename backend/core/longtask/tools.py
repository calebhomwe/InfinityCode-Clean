"""Long Task toolset — all file access jailed to the target repo.

P1 scope: cwd-jailed subprocess for run_command with hard timeout and output
truncation. Deeper isolation (AppContainer/WSL2 per roadmap) is P5; the Ask-mode
approval gate (P3) bounds shell access until then.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

_run_sandboxed = None
try:
    from core.sandbox import run_sandboxed as _run_sandboxed
except ImportError:
    try:
        from backend.core.sandbox import run_sandboxed as _run_sandboxed
    except ImportError:
        pass

MAX_READ_BYTES = 200_000
MAX_CMD_OUTPUT = 64_000
MAX_WEB_CHARS = 20_000
WEB_FETCH_TIMEOUT_S = 15
WEB_FETCH_READ_CAP = 4 * 1024 * 1024


class ToolError(Exception):
    pass


# Windows-absolute spellings: drive letter + separator (C:/x, C:\x), a bare
# drive (D:), or a UNC prefix (\\server\share, //server/share). Deliberately
# narrower than PureWindowsPath(p).drive, which is truthy for ANY string whose
# second character is ":" and so rejected ordinary POSIX names like "a:b.txt".
_WIN_ABS = re.compile(r"^(?:[A-Za-z]:(?:[\\/]|$)|\\\\|//)")


class PathJail:
    """Confines every file operation to one repo subtree (symlink-safe)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ToolError(f"repo root does not exist: {root}")

    def resolve(self, p: str) -> Path:
        # Drive-qualified / UNC paths are absolute on Windows (and checked
        # against the root below) but merely relative on POSIX, where they
        # would silently create a "C:" folder inside the repo.
        if os.name != "nt" and _WIN_ABS.match(str(p)):
            raise ToolError(f"path escapes repo: {p}")
        cand = Path(p)
        cand = cand if cand.is_absolute() else self.root / cand
        cand = cand.resolve()
        try:
            cand.relative_to(self.root)
        except ValueError:
            raise ToolError(f"path escapes repo: {p}")
        return cand


# --- read-only ---------------------------------------------------------- #


def t_read_file(jail: PathJail, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
    f = jail.resolve(path)
    if not f.is_file():
        raise ToolError(f"no such file: {path}")
    return f.read_bytes()[:max_bytes].decode("utf-8", errors="replace")


def t_list_dir(jail: PathJail, path: str = ".") -> List[Dict[str, Any]]:
    d = jail.resolve(path)
    if not d.is_dir():
        raise ToolError(f"no such dir: {path}")
    out = [
        {"name": e.name, "type": "dir" if e.is_dir() else "file"}
        for e in sorted(d.iterdir())
    ]
    return out[:500]


def t_grep(jail: PathJail, pattern: str, path: str = ".",
           max_results: int = 50) -> List[str]:
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"bad regex: {exc}")
    hits: List[str] = []
    base = jail.resolve(path)
    files = [base] if base.is_file() else [
        f for f in base.rglob("*") if f.is_file()
    ]
    for f in files:
        if len(hits) >= max_results:
            break
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{f.relative_to(jail.root).as_posix()}:{i}: {line[:200]}")
                if len(hits) >= max_results:
                    break
    return hits


def t_glob(jail: PathJail, pattern: str) -> List[str]:
    return sorted(
        p.relative_to(jail.root).as_posix() for p in jail.root.glob(pattern)
    )[:500]


# --- mutating ----------------------------------------------------------- #


def t_write_file(jail: PathJail, path: str, content: str) -> str:
    f = jail.resolve(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"


def t_edit_file(jail: PathJail, path: str, old: str, new: str,
                replace_all: bool = False) -> str:
    f = jail.resolve(path)
    if not f.is_file():
        raise ToolError(f"no such file: {path}")
    if not old:
        raise ToolError("old text must not be empty")
    text = f.read_text(encoding="utf-8")
    n = text.count(old)
    if n == 0:
        raise ToolError("old text not found")
    if n > 1 and not replace_all:
        raise ToolError(f"old text matches {n} places; add context or replace_all")
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    f.write_text(updated, encoding="utf-8")
    return f"{n if replace_all else 1} replacement(s) in {path}"


# Safety gate (fuzz 2026-08-11): the autonomous builder reads arbitrary
# repo files into its context, so a hostile file can lure it into emitting
# destructive shell. These patterns are refused in EVERY autonomy mode.
_DESTRUCTIVE_PATTERNS: Tuple[str, ...] = (
    r"rm\s+(-[a-z]*[rf][a-z]*\s+)+(/|~|\$home|\*|c:)",  # rm -rf /, ~, *, C:
    r"rm\s+(-[a-z]*[rf][a-z]*\s+)+(--\s+)?(/|~|\$home|\*|[a-z]:|\.\.?(\s|$|;|&|\|))",  # rm -rf -- / (terminator evasion)
    r"rm\s+(-[a-z]*[rf][a-z]*\s+)+\S*(/|\\)(\s|$)",   # rm -rf <dir>/
    r"rm\s+(-[a-z]*[rf][a-z]*\s+)+\.\.?(\s|$|;|&|\|)",   # rm -rf . / .. (cwd wipe)
    r"\bmkfs\b", r"\bdiskpart\b", r"\bfdisk\b",
    r"\bformat\s+[a-z]:", r"\bdd\s+if=",
    r"\b(del|erase)\s+/[a-z]*s",                          # del /s
    r"\b(rmdir|rd)\s+/[a-z]*s",                           # rmdir /s
    r"remove-item\s+.*-recurse",  # any Remove-Item -Recurse (any arg order)
    r">\s*/dev/sd", r"shutdown\s", r"\breboot\b", r"\bpoweroff\b",
    r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b",  # curl | sh
    r":\(\)\s*\{",                                       # fork bomb
    r"\breg\s+delete\b", r"\biex\b\s*\(", r"\biex\b.*invoke-expression",
    r"git\s+clean\s+-\S*f",   # git clean -fdx (untracked wipe)
    r"\bclear-disk\b",        # PowerShell disk wipe
)
_DESTRUCTIVE_RE = re.compile("|".join(_DESTRUCTIVE_PATTERNS), re.I)

# Quote chars that can wrap a dangerous target; stripped before matching so
# `rm -rf "$HOME"` can't dodge the `$home` alternation.
_QUOTE_CHARS = "\"'`"


def _tokenize_destructive(command: str) -> bool:
    """Token-level check for rm/del/erase/rmdir/rd/git-clean.

    Catches GNU-getopt permutations (``rm /home -rf``,
    ``--recursive --force``), chained dots (``rm -rf ../..``) and
    flag-at-end variants (``del /f /s C:\\Windows``) that the
    regexes miss because they match by position.
    """
    tokens = command.replace("\\", "/").split()
    if not tokens:
        return False
    base = tokens[0].lower()

    # Windows cmd flags use "/" (e.g. /s /q); POSIX tools use "-".
    if base in ("del", "erase", "rmdir", "rd"):
        flag_toks = [t.lower() for t in tokens[1:] if t.startswith(("/", "-"))]
        return any("s" in f for f in flag_toks)

    flag_toks = [t.lower() for t in tokens[1:] if t.startswith("-")]
    targets = [t.lower() for t in tokens[1:] if not t.startswith("-")]

    def dangerous(t: str) -> bool:
        if t in (".", ".."):
            return True
        if t.startswith(("../", "././", "/", "~")):
            return True
        if "$home" in t or t.startswith("*"):
            return True
        return bool(re.match(r"^[a-z]:", t))

    if base == "rm":
        has_r = any("r" in f.replace("-", "") for f in flag_toks)
        has_f = any("f" in f.replace("-", "") for f in flag_toks)
        if has_r and has_f:
            return True
        return (has_r or has_f) and any(dangerous(t) for t in targets)
    if base == "git" and len(tokens) > 1 and tokens[1].lower() == "clean":
        return any(f in ("-f", "--force") or (f.startswith("-") and "f" in f) for f in flag_toks)
    return False


def command_is_destructive(command: str) -> bool:
    """Conservative heuristic: block obvious irreversible/destructive shell.
    False positives degrade to a builder error; a false negative can brick
    the machine, so the list errs wide."""
    if not command:
        return False
    normalized = command.replace("\\", "/")
    normalized = "".join(ch for ch in normalized if ch not in _QUOTE_CHARS)
    return bool(_DESTRUCTIVE_RE.search(normalized)) or _tokenize_destructive(normalized)


def t_run_command(jail: PathJail, command: str, timeout: int = 120) -> Dict[str, Any]:
    """Execute a command, jailed to the repo root.

    Python scripts are routed through the hardened sandbox (restricted
    token, low integrity, job object) when available. Destructive commands
    are refused outright (safety gate, all autonomy modes).
    """
    if command_is_destructive(command):
        raise ToolError(
            "blocked by safety gate: command matches a destructive pattern; "
            "choose a non-destructive approach")
    stripped = command.strip()
    is_py = (
        stripped.startswith("python ") or stripped.startswith("python3 ")
        or stripped.startswith("py ")
    )
    if is_py and _run_sandboxed is not None:
        parts = stripped.split(None, 2)
        # Only route script files through sandbox, not -c/-m flags.
        if len(parts) >= 2 and not parts[1].startswith("-"):
            try:
                sp = jail.resolve(parts[1])
                res = _run_sandboxed(sys.executable, sp, jail.root,
                                     timeout=timeout)
                return {
                    "exit_code": res.get("returncode", -1),
                    "stdout": res.get("stdout", "")[-MAX_CMD_OUTPUT:],
                    "stderr": res.get("stderr", "")[-MAX_CMD_OUTPUT:],
                    "hardened": res.get("hardened", False),
                }
            except Exception:  # noqa: BLE001
                pass  # fall back to plain subprocess
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(jail.root),
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-MAX_CMD_OUTPUT:],
            "stderr": proc.stderr[-MAX_CMD_OUTPUT:],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "",
                "stderr": f"timeout after {timeout}s"}
    except OSError as exc:
        return {"exit_code": -1, "stdout": "", "stderr": str(exc)}

# --- web (Part-3 gap closure: research tasks need outbound reads) -------- #


def _host_is_private(host: str) -> bool:
    """Resolve and classify every address a hostname maps to. Raises ToolError
    on DNS failure (a fetch to nowhere is a fetch error, not a crash)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve host: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_reserved
                or ip.is_link_local):
            return True
    return False


def t_web_fetch(jail: PathJail, url: str,
                max_chars: int = MAX_WEB_CHARS) -> Dict[str, Any]:
    """GET a public web page and return its body as text.

    http/https only; private, loopback, reserved and link-local targets are
    refused (SSRF) unless INFINITY_WEB_FETCH_ALLOW_LOCAL=1. Output is capped
    so one fetch can never balloon the builder context (history compaction
    still applies on top).
    """
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("web_fetch only supports http(s) URLs")
    if (os.environ.get("INFINITY_WEB_FETCH_ALLOW_LOCAL") != "1"
            and _host_is_private(parsed.hostname)):
        raise ToolError(
            "web_fetch refuses private/loopback targets "
            "(set INFINITY_WEB_FETCH_ALLOW_LOCAL=1 to override)")
    req = urllib.request.Request(
        str(url), headers={"User-Agent": "infinity-code/0.1 (longtask)"})
    try:
        with urllib.request.urlopen(req, timeout=WEB_FETCH_TIMEOUT_S) as resp:
            body = resp.read(WEB_FETCH_READ_CAP).decode("utf-8",
                                                        errors="replace")
            final_url = resp.geturl()
            status = getattr(resp, "status", 200)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - network errors are tool errors
        raise ToolError(f"web_fetch failed: {exc}") from exc
    return {"url": final_url, "status": status,
            "content": body[:max_chars],
            "truncated": len(body) > max_chars}

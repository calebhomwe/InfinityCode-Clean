"""Rubric judge on a forked sandbox (P9) — quest closure without self-report.

The worker says "done"; the judge decides whether the final environment state
actually earns it. Protocol (GRM): (1) read the forked output, (2) generate a
rubric, (3) score against it, (4) emit a structured scorepad. Hidden checks are
deterministic and never shown to the worker — a failed hidden check forces a
fail regardless of what the model says.

The judge runs against a COPY of the worker sandbox (fork_sandbox) so judging
never mutates the worker's state.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Submission cap: the judge gets at most this many chances to send the
# builder back before the run hard-fails with the last scorepad.
JUDGE_CAP = 3

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
              "dist", "build", ".next", "target", ".pytest_cache"}
_MAX_TREE = 150
_MAX_SNIPPET = 800

_JUDGE_PROMPT = (
    "You are a strict judge for a coding task. Decide whether the work in the "
    "repo meets the goal. Using the file tree and excerpts, reply with ONLY "
    'JSON: {"rubric": ["criterion", "..."], "scores": {"criterion": 0.0-1.0}, '
    '"total": 0.0-1.0, "pass": true/false, "issues": ["...", "..."]}. '
    "Be demanding: pass only when the goal is genuinely met, not stubbed."
)

# Deterministic markers that indicate the work was faked, not implemented.
_FAKE_MARKERS = ("TODO", "FIXME", "NotImplementedError", "pass  # stub")


def fork_sandbox(src_dir) -> Path:
    """Copy the worker sandbox into a judge-owned temp dir (never in place)."""
    src = Path(src_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"sandbox not found: {src_dir}")
    dest = Path(tempfile.mkdtemp(prefix="judge_fork_")) / src.name
    shutil.copytree(str(src), str(dest),
                    ignore=shutil.ignore_patterns(*_SKIP_DIRS))
    return dest


def _summarize_fork(fork: Path) -> str:
    """File tree + key-file excerpts for the judge to read."""
    tree: List[str] = []
    snippets: Dict[str, str] = {}
    for p in sorted(fork.rglob("*")):
        if len(tree) >= _MAX_TREE:
            break
        rel = p.relative_to(fork)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.is_dir():
            continue
        rel_s = str(rel).replace("\\", "/")
        tree.append(rel_s)
        if p.suffix in (".py", ".ts", ".tsx", ".js", ".md") and len(snippets) < 6:
            try:
                snippets[rel_s] = p.read_text(
                    encoding="utf-8", errors="replace")[:_MAX_SNIPPET]
            except OSError:
                pass
    return ("FILE TREE:\n" + "\n".join(tree)
            + "\n\nEXCERPTS:\n"
            + "\n\n".join(f"### {k}\n{v}" for k, v in snippets.items()))


def run_hidden_checks(fork: Path,
                      checks: Optional[List[Callable[[Path], Dict[str, Any]]]] = None
                      ) -> List[Dict[str, Any]]:
    """Deterministic checks the worker never sees. Each returns
    {name, pass, detail}. A failure forces the whole scorepad to fail."""
    results: List[Dict[str, Any]] = []
    default_checks = checks if checks is not None else [
        _check_has_files, _check_no_fake_markers,
    ]
    for fn in default_checks:
        try:
            results.append(fn(fork))
        except Exception as exc:  # noqa: BLE001 - a broken check is a fail
            results.append({"name": getattr(fn, "__name__", "check"),
                            "pass": False, "detail": f"check error: {str(exc)[:120]}"})
    return results


def _check_has_files(fork: Path) -> Dict[str, Any]:
    files = [p for p in fork.rglob("*") if p.is_file()
             and not any(part in _SKIP_DIRS for part in p.relative_to(fork).parts)]
    ok = len(files) > 0
    return {"name": "has_files", "pass": ok,
            "detail": f"{len(files)} files present"}


def _check_no_fake_markers(fork: Path) -> Dict[str, Any]:
    hits: List[str] = []
    for p in fork.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".ts", ".tsx", ".js"):
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(fork).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for marker in _FAKE_MARKERS:
            if marker in text:
                hits.append(f"{p.name}:{marker}")
    ok = len(hits) == 0
    return {"name": "no_fake_markers", "pass": ok,
            "detail": "; ".join(hits[:5]) if hits else "clean"}


def run_rubric_judge(fork_path, goal: str, judge_chat_fn,
                     hidden_checks: Optional[List[Callable]] = None) -> Dict[str, Any]:
    """GRM rubric protocol -> scorepad dict. Hidden checks can only lower the
    verdict, never raise it."""
    ctx = _summarize_fork(Path(fork_path))
    prompt = (_JUDGE_PROMPT + "\n\nGOAL: " + goal + "\n\n" + ctx)
    reply = judge_chat_fn([{"role": "user", "content": prompt}], max_tokens=1000)
    text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
    try:
        scorepad = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception:  # noqa: BLE001 - unreadable verdict = conservative fail
        scorepad = {"rubric": [], "scores": {}, "total": 0.0, "pass": False,
                    "issues": ["judge returned an unreadable verdict"]}
    # Normalize the model's own fields defensively.
    try:
        scorepad["total"] = round(float(scorepad.get("total", 0.0)), 3)
    except (TypeError, ValueError):
        scorepad["total"] = 0.0
    scorepad["pass"] = bool(scorepad.get("pass", False))
    scorepad["issues"] = [str(i)[:200] for i in (scorepad.get("issues") or [])][:6]

    checks = run_hidden_checks(Path(fork_path), hidden_checks)
    scorepad["hidden_checks"] = checks
    if any(not c["pass"] for c in checks):
        scorepad["pass"] = False
        scorepad["hidden_fail"] = True
        scorepad.setdefault("issues", []).append("a hidden verification check failed")
    return scorepad


__all__ = ["fork_sandbox", "run_hidden_checks", "run_rubric_judge", "JUDGE_CAP"]

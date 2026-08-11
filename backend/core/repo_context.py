"""Persistent project context for Infinity Code X.

Builds a cheap, deterministic repository map (paths, sizes, and an AST
symbol outline for Python/JS/TS sources) with a JSON cache keyed on file
mtimes, plus a keyword search over paths and symbols. No embeddings and
no paid calls — credit-free by construction. The map feeds the council
chat so repo-level questions can be answered without shipping the whole
repository to a model (credit protection rule).
"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("infinity.repo_context")

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
    "build", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next",
    "target", ".qoder", "eyes", "eval_reports",
}
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
TEXT_SUFFIXES = CODE_SUFFIXES | {".md", ".json", ".yaml", ".yml", ".toml", ".css", ".html"}
DEFAULT_MAX_FILES = 800
DEFAULT_MAX_FILE_BYTES = 200_000
DEFAULT_MAX_RESULTS = 20
CACHE_NAME = "repo_map.json"

_JS_SYMBOL_RE = re.compile(
    r"(?:^|\s)(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)"
)


@dataclass
class RepoEntry:
    """One file in the repository map."""

    path: str
    size: int
    symbols: List[str] = field(default_factory=list)


def _python_symbols(text: str) -> List[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(f"def {node.name}")
        elif isinstance(node, ast.ClassDef):
            names.append(f"class {node.name}")
    return names[:40]


def _js_symbols(text: str) -> List[str]:
    return [f"sym {m.group(1)}" for m in _JS_SYMBOL_RE.finditer(text)][:40]


def _symbols_for(path: Path, text: str) -> List[str]:
    if path.suffix == ".py":
        return _python_symbols(text)
    if path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return _js_symbols(text)
    return []


def build_repo_map(
    root: Path,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> Dict[str, Any]:
    """Walk ``root`` and produce the map dict (no cache read/write here)."""
    root = Path(root).resolve()
    entries: List[RepoEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if p.suffix not in TEXT_SUFFIXES:
                continue
            symbols: List[str] = []
            if p.suffix in CODE_SUFFIXES and size <= max_file_bytes:
                try:
                    symbols = _symbols_for(p, io.open(p, encoding="utf-8", errors="ignore").read())
                except OSError:
                    symbols = []
            entries.append(RepoEntry(path=str(p.relative_to(root)).replace("\\", "/"), size=size, symbols=symbols))
            if len(entries) >= max_files:
                break
        if len(entries) >= max_files:
            break
    entries.sort(key=lambda e: e.path)
    return {
        "root": str(root),
        "built_at": time.time(),
        "file_count": len(entries),
        "files": [
            {"path": e.path, "size": e.size, "symbols": e.symbols}
            for e in entries
        ],
    }


def _cache_path(data_dir: Path) -> Path:
    return Path(data_dir) / CACHE_NAME


def _cache_valid(cache: Dict[str, Any], root: Path) -> bool:
    if cache.get("root") != str(Path(root).resolve()):
        return False
    built_at = float(cache.get("built_at") or 0.0)
    if built_at <= 0.0:
        return False
    # Cheap freshness check: did any top-level source change after build?
    for p in Path(root).iterdir():
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            try:
                if p.stat().st_mtime > built_at:
                    return False
            except OSError:
                continue
    return True


def get_repo_map(
    root: Path,
    data_dir: Path,
    force: bool = False,
) -> Dict[str, Any]:
    """Return the cached map, rebuilding it when stale or forced."""
    cache_file = _cache_path(data_dir)
    if not force and cache_file.is_file():
        try:
            cached = json.loads(io.open(cache_file, encoding="utf-8").read())
            if isinstance(cached, dict) and _cache_valid(cached, root):
                cached["cached"] = True
                return cached
        except (OSError, ValueError):
            pass
    repo_map = build_repo_map(root)
    repo_map["cached"] = False
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        io.open(cache_file, "w", encoding="utf-8", newline="").write(
            json.dumps({k: v for k, v in repo_map.items() if k != "cached"})
        )
    except OSError as exc:
        logger.warning("repo map cache write failed: %s", exc)
    return repo_map


def search_repo_map(repo_map: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
    """Keyword search over paths and symbols (case-insensitive)."""
    needles = [q.lower() for q in query.split() if q.strip()]
    if not needles:
        return []
    hits: List[Dict[str, Any]] = []
    for item in repo_map.get("files", []):
        path = str(item.get("path", "")).lower()
        blob = " ".join([path] + [s.lower() for s in item.get("symbols", [])])
        score = sum(1 for n in needles if n in blob)
        if score:
            hits.append({**item, "score": score})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    return hits[:DEFAULT_MAX_RESULTS]

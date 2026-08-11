"""Immutable safety rules for Infinity Code self-improvement.

The Constitution is loaded once at boot and never mutated at runtime.
Any auto-fix or evolution step must check against these rules before
writing files, running code, or spending money.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AbstractSet, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Default immutable constants
# --------------------------------------------------------------------------- #

_DEFAULT_ALLOWED_PATHS: List[str] = [
    "backend/core/",
    "backend/core/",
    "skills/",
    "backend/data/eval_tasks.jsonl",
    "backend/data/agents.json",
    "backend/data/seed_skills/",
]

_DEFAULT_FORBIDDEN_PATHS: List[str] = [
    "config.yaml",
    "settings.json",
    "providers.json",
    ".env",
    "backend/config.yaml",
    "backend/main.py",  # never self-modify the entry point
]

_DEFAULT_FORBIDDEN_IMPORTS: List[str] = [
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "requests",
    "urllib",
]

# Imports that are explicitly allowed even if they match forbidden patterns.
_ALLOWED_IMPORTS: Set[str] = {
    "math",
    "json",
    "random",
    "datetime",
    "typing",
    "pathlib",
    "dataclasses",
    "enum",
    "re",
    "string",
    "itertools",
    "collections",
    "functools",
    "decimal",
    " fractions",
    "statistics",
    "hashlib",
    "base64",
    "uuid",
    "inspect",
    "textwrap",
    "copy",
    "numbers",
    "abc",
    "types",
}


class Constitution:
    """Immutable guard rails for the self-improvement loop."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        _cfg = cfg or {}
        self.allowed_auto_paths: List[str] = list(
            _cfg.get("allowed_paths", _DEFAULT_ALLOWED_PATHS)
        )
        self.forbidden_paths: List[str] = list(
            _cfg.get("forbidden_paths", _DEFAULT_FORBIDDEN_PATHS)
        )
        self.forbidden_imports: AbstractSet[str] = set(
            _cfg.get("forbidden_imports", _DEFAULT_FORBIDDEN_IMPORTS)
        )
        self.max_daily_patches: int = int(_cfg.get("max_daily_patches", 5))
        self.max_daily_cost_aud: float = float(_cfg.get("max_daily_cost_aud", 2.0))
        self.auto_approve: bool = bool(_cfg.get("auto_approve", False))
        self.sandbox_timeout_seconds: int = int(_cfg.get("sandbox", {}).get("timeout_seconds", 15))
        self.sandbox_max_memory_mb: int = int(_cfg.get("sandbox", {}).get("max_memory_mb", 512))

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #

    def path_is_allowed(self, rel_path: str) -> bool:
        """Return True if *rel_path* may be touched by auto-fix."""
        rel = Path(rel_path).as_posix().replace("\\", "/")
        # Explicit forbidden list wins.
        for bad in self.forbidden_paths:
            if rel.endswith(bad) or rel == bad:
                logger.warning("Constitution: path %s is forbidden by rule %s", rel, bad)
                return False
        # Must be under an allowed prefix.
        for ok in self.allowed_auto_paths:
            if rel.startswith(ok):
                return True
        logger.warning("Constitution: path %s not in allowed prefixes", rel)
        return False

    def import_is_allowed(self, name: str) -> bool:
        """Return True if *name* (top-level module) is safe to import."""
        top = name.split(".")[0]
        if top in _ALLOWED_IMPORTS:
            return True
        if top in self.forbidden_imports:
            return False
        return True

    def daily_budget_exceeded(self, spent_today_aud: float) -> bool:
        return spent_today_aud >= self.max_daily_cost_aud

    def daily_patch_limit_exceeded(self, count_today: int) -> bool:
        return count_today >= self.max_daily_patches


__all__ = ["Constitution"]

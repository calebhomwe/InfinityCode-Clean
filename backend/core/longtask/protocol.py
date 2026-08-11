"""Parse builder output into tool actions.

Protocol: one or more JSON objects with {"action": str, "args": {...}},
optionally inside ```action fences. Anything else -> ProtocolError (the engine
re-asks once, then fails the step).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

KNOWN = {"read_file", "list_dir", "grep", "glob", "write_file", "edit_file",
         "run_command", "update_plan", "finish"}


class ProtocolError(Exception):
    pass


_FENCE = re.compile(r"```(?:action|json)?\s*(.*?)```", re.S)
_DECODER = json.JSONDecoder()
# Fuzz 2026-08-11: Python's C scanner recurses per nesting level, so deeply
# nested malformed JSON ('{"a":' * 5000) raises RecursionError. Skip any
# candidate starting inside a nesting run deeper than this; real action
# payloads nest 2-3 levels.
_MAX_NESTING = 64


def _bounded_nesting(text: str, limit: int) -> List[Tuple[int, int]]:
    """(open_idx, depth) per '{' outside strings, capped at `limit + 1`."""
    spans: List[Tuple[int, int]] = []
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
            spans.append((i, depth))
        elif ch == "}":
            depth = max(depth - 1, 0)
    return spans


def _balanced_objects(text: str) -> List[str]:
    """Extract brace-balanced JSON candidates (nesting + strings safe)."""
    out: List[str] = []
    starts = {i for i, d in _bounded_nesting(text, _MAX_NESTING)
              if d <= _MAX_NESTING}
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        if i not in starts:
            i += 1
            continue
        try:
            obj, end = _DECODER.raw_decode(text[i:])
        except (json.JSONDecodeError, RecursionError, ValueError):
            # RecursionError/ValueError: pathological nesting the pre-scan
            # admitted -- treat as unparseable, never crash the caller.
            i += 1
            continue
        if isinstance(obj, dict):
            out.append(text[i:i + end])
        i += max(end, 1)
    return out


def _to_actions(candidates: List[str]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action") in KNOWN:
            actions.append({"action": obj["action"],
                            "args": obj.get("args") or {}})
    return actions


def parse_actions(text: str) -> List[Dict[str, Any]]:
    """Never raises anything but ProtocolError -- builder output is hostile
    input (fuzz 2026-08-11)."""
    try:
        fenced = [m.group(1) for m in _FENCE.finditer(text or "")]
        if fenced:
            candidates = [c for body in fenced for c in _balanced_objects(body)]
            actions = _to_actions(candidates)
            if actions:
                return actions
        actions = _to_actions(_balanced_objects(text or ""))
    except (RecursionError, MemoryError, ValueError) as exc:
        raise ProtocolError(f"unparseable action block: {exc}") from exc
    if not actions:
        raise ProtocolError("no valid action block found")
    return actions

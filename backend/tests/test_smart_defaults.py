"""Unit tests for the smart defaults engine."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.smart_defaults import SmartDefaults
except ImportError:
    from core.smart_defaults import SmartDefaults  # type: ignore


def test_classify_image_goal() -> None:
    result = SmartDefaults.classify_goal("draw a picture of a cat")
    assert result["mode"] == "image"


def test_classify_3d_goal() -> None:
    result = SmartDefaults.classify_goal("make a Blender scene with a cube and camera")
    assert result["mode"] == "3d"


def test_classify_code_goal() -> None:
    result = SmartDefaults.classify_goal("write a python function to sort a list")
    assert result["mode"] == "code"
    assert result["lane"] == "smart"


def test_classify_hard_goal() -> None:
    result = SmartDefaults.classify_goal("design a distributed system architecture for a chat app")
    assert result["effort"] == "high"
    assert result["lane"] == "smart"


def test_suggest_tools_for_coding() -> None:
    tools = SmartDefaults.suggest_tools("debug this python script")
    assert "run_python" in tools


def test_suggest_web_search_for_current_facts() -> None:
    assert SmartDefaults.suggest_web_search("what is the latest news today")
    assert not SmartDefaults.suggest_web_search("explain recursion")


def test_trim_history_drops_old_messages() -> None:
    messages: List[Dict[str, Any]] = [{"role": "system", "content": "sys"}]
    messages += [{"role": "user", "content": f"msg {i}"} for i in range(40)]
    trimmed, was_trimmed = SmartDefaults.trim_history(messages, max_messages=10)
    assert was_trimmed
    assert len(trimmed) <= 10
    assert trimmed[0]["role"] == "system"


def test_trim_history_keeps_short_history() -> None:
    messages = [{"role": "user", "content": "hi"}]
    trimmed, was_trimmed = SmartDefaults.trim_history(messages)
    assert not was_trimmed
    assert trimmed == messages


TESTS: List[Tuple[str, Callable[[], None]]] = [
    (name, obj)
    for name, obj in list(globals().items())
    if name.startswith("test_") and callable(obj)
]


def main() -> int:
    failures = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

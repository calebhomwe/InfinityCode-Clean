"""Unit coverage for the bounded compose-your-crew mission knobs."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.swarm import AgentSwarm
except ImportError:
    from core.swarm import AgentSwarm  # type: ignore


def test_crew_params_default_to_safe_core_swarm() -> None:
    params = AgentSwarm._parse_params("{}")
    assert params["agents"] == []
    assert params["combine_with_default_swarm"] is True


def test_crew_params_are_bounded_and_preserved() -> None:
    params = AgentSwarm._parse_params(
        json.dumps(
            {
                "agents": ["architect", "tester", "designer", "fourth"],
                "combine_with_default_swarm": False,
            }
        )
    )
    assert params["agents"] == ["architect", "tester", "designer"]
    assert params["combine_with_default_swarm"] is False


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

"""Guard the credit-saving routing: cheap flash models code, Qwen 3.8 advises.

The owner's directive: "3.7 and deepseek flash do the coding, qwen 3.8 only
sees and advises, the goal is to save credits." This test pins that split so a
future council edit can't silently put the expensive model back on the builder.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.router import COUNCIL, ModelRouter, build_council
except ImportError:
    from core.router import COUNCIL, ModelRouter, build_council  # type: ignore

CHEAP_CODERS = {
    "deepseek/deepseek-v4-flash",
    "dashscope/qwen3.7-flash",
    "dashscope/qwen3-coder-480b-a35b-instruct",
    "qwen/qwen3-coder",
}
ADVISOR = "dashscope/qwen3.8-max"


def test_builder_is_cheap_coder() -> None:
    builder = COUNCIL["longtask_builder"]
    assert builder.id in CHEAP_CODERS, f"builder primary {builder.id!r} is not a cheap coder"
    # The primary must stay on a cheap flash-tier coder.
    assert builder.id == "dashscope/qwen3.7-flash"


def test_advisor_never_codes() -> None:
    router = ModelRouter(build_council())
    builder_chain = [spec.id for spec in router.chain_for("longtask_builder")]
    assert ADVISOR not in builder_chain, (
        f"{ADVISOR} must only advise; found it in the builder coding chain {builder_chain}"
    )


def test_reviewer_is_the_advisor() -> None:
    reviewer = COUNCIL["longtask_reviewer"]
    assert reviewer.id == ADVISOR, f"reviewer primary {reviewer.id!r} should be {ADVISOR}"


def test_council_validates() -> None:
    router = ModelRouter(build_council())
    problems = router.validate()["problems"]
    assert not problems, f"council has pricing problems: {problems}"


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

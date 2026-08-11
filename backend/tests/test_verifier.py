"""Unit tests for the deterministic verifier stage."""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.verifier import Verifier, VerificationResult
except ImportError:
    from core.verifier import Verifier, VerificationResult  # type: ignore


def test_python_syntax_ok() -> None:
    v = Verifier()
    ok, reason = v.python_syntax("def f():\n    return 1")
    assert ok and reason == "syntax ok"


def test_python_syntax_bad() -> None:
    v = Verifier()
    ok, reason = v.python_syntax("def f(\n")
    assert not ok
    assert "syntax error" in reason


def test_python_lint_skipped_without_linter() -> None:
    v = Verifier()
    ok, reason = v.python_lint("x = 1")
    # The app is valid both with a minimal runtime (no linter installed) and
    # with Ruff available in the developer venv.  The old assertion only
    # accepted the former, making this test depend on the machine image.
    assert ok and ("skipped" in reason or "passed" in reason)


def test_run_python() -> None:
    v = Verifier()
    ok, stdout, stderr, reason = v.run_python("print('hello')")
    assert ok
    assert "hello" in stdout
    assert reason == ""


def test_verify_code_full_pass() -> None:
    v = Verifier()
    code = "def add(a, b):\n    return a + b\n\nprint(add(2, 3))"
    result = v.verify_code(code, run=True, lint=False)
    assert result.ok
    assert result.stage == "tests"
    assert "5" in result.stdout


def test_verify_code_syntax_fail() -> None:
    v = Verifier()
    result = v.verify_code("def broken(")
    assert not result.ok
    assert result.stage == "syntax"


class FakeVisionClient:
    """Minimal stand-in for OpenRouterClient.chat_with_vision."""

    def __init__(self, text: str) -> None:
        self.text = text

    def chat_with_vision(self, **kwargs: Any) -> Dict[str, Any]:
        return {"text": self.text}


def test_render_feedback_matches_intent() -> None:
    v = Verifier(vision_client=FakeVisionClient("matches intent"))
    # Blender may be unavailable (render failure) or installed (the fake
    # critic advances this to the vision stage).
    result = v.render_feedback("print('not a real bpy script')", "render a cube")
    assert isinstance(result, VerificationResult)
    assert result.stage in {"syntax", "render", "vision"}


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

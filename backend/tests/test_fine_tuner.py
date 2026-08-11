"""Unit tests for the hosted fine-tuner clients."""

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
    from backend.core.fine_tuner import FineTuner
except ImportError:
    from core.fine_tuner import FineTuner  # type: ignore


def test_unconfigured_returns_error() -> None:
    tuner = FineTuner(provider="together", api_key="")
    assert not tuner.configured()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write('{"messages": []}\n')
        path = Path(tmp.name)
    try:
        result = tuner.upload_and_tune(path, "model")
        assert not result["ok"]
        assert "API key" in result["error"]
    finally:
        path.unlink()


def test_missing_file_error() -> None:
    tuner = FineTuner(provider="together", api_key="fake-key")
    assert tuner.configured()
    result = tuner.upload_and_tune(Path("/tmp/does_not_exist.jsonl"), "model")
    assert not result["ok"]
    assert "File not found" in result["error"]


def test_together_happy_path(monkeypatch: Any = None) -> None:
    """Simulate a Together upload + fine-tune flow by monkey-patching HTTP."""
    tuner = FineTuner(provider="together", api_key="fake-key")
    calls: List[Dict[str, Any]] = []

    def fake_http(method: str, url: str, headers: Any = None, body: bytes = None) -> Dict[str, Any]:
        calls.append({"method": method, "url": url})
        if "files" in url:
            return {"id": "file_123"}
        if "fine-tunes" in url:
            return {"id": "job_456", "status": "pending"}
        return {}

    tuner._together_upload = lambda path: fake_http("POST", "https://api.together.xyz/v1/files")  # type: ignore
    tuner._together_create_job = (  # type: ignore
        lambda file_id, model, suffix, epochs: fake_http(
            "POST", "https://api.together.xyz/v1/fine-tunes"
        )
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write('{"messages": []}\n')
        path = Path(tmp.name)
    try:
        result = tuner.upload_and_tune(path, "model")
        assert result["ok"]
        assert result["job_id"] == "job_456"
    finally:
        path.unlink()


def test_public_config_hides_key() -> None:
    tuner = FineTuner(provider="together", api_key="super-secret")
    cfg = tuner.public_config()
    assert cfg["configured"]
    assert cfg["provider"] == "together"
    assert "super-secret" not in str(cfg)


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

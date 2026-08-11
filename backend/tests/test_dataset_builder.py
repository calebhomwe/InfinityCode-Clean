"""Unit tests for the dataset builder."""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from backend.core.dataset_builder import DatasetBuilder
    from backend.core.session_logger import SessionLogger
except ImportError:
    from core.dataset_builder import DatasetBuilder  # type: ignore
    from core.session_logger import SessionLogger  # type: ignore


def _make_record(
    session_id: str,
    output: str,
    signal: str = "accepted",
    messages: List[Dict[str, Any]] | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "turn_id": f"{session_id}-turn",
        "session_id": session_id,
        "timestamp": "2026-01-01T00:00:00Z",
        "model": "test-model",
        "lane": "cheap",
        "messages": messages or [{"role": "user", "content": "hello"}],
        "output": output,
        "cost_usd": 0.001,
        "input_tokens": 10,
        "output_tokens": 5,
        "latency_ms": 100.0,
        "metadata": metadata or {},
        "signal": signal,
    }


def test_build_filters_by_signal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        logger = SessionLogger(data_dir)
        with (logger.base_dir / "s1.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_make_record("s1", "accepted output")) + "\n")
            fh.write(json.dumps(_make_record("s1", "rejected output", signal="rejected")) + "\n")

        builder = DatasetBuilder(logger, data_dir / "datasets")
        stats = builder.build(name="run1")
        assert stats["coder_count"] == 1
        assert stats["vision_count"] == 0
        coder_path = Path(stats["coder_path"])
        assert coder_path.is_file()
        examples = [json.loads(line) for line in coder_path.read_text(encoding="utf-8").splitlines()]
        assert len(examples) == 1
        assert examples[0]["messages"][-1]["content"] == "accepted output"


def test_recovery_session_flagged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        logger = SessionLogger(data_dir)
        with (logger.base_dir / "s1.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_make_record("s1", "bad", signal="rejected")) + "\n")
            fh.write(json.dumps(_make_record("s1", "good", signal="accepted")) + "\n")

        builder = DatasetBuilder(logger, data_dir / "datasets")
        stats = builder.build(name="run1")
        assert stats["recovery_examples"] == 1


def test_vision_split_by_image() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        logger = SessionLogger(data_dir)
        messages = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]},
        ]
        with (logger.base_dir / "s1.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_make_record("s1", "vision output", messages=messages)) + "\n")

        builder = DatasetBuilder(logger, data_dir / "datasets")
        stats = builder.build(name="run1")
        assert stats["vision_count"] == 1
        assert stats["coder_count"] == 0
        assert Path(stats["vision_path"]).is_file()


def test_list_runs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        logger = SessionLogger(data_dir)
        with (logger.base_dir / "s1.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_make_record("s1", "out")) + "\n")
        builder = DatasetBuilder(logger, data_dir / "datasets")
        builder.build(name="run_a")
        runs = builder.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_name"] == "run_a"


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

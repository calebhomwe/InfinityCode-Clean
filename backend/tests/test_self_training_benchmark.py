"""Ensure benchmark drills use the free local coach, not paid research models."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.self_training import SelfTrainer


class Knowledge:
    def add_source(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def reindex(self, _embed: Any) -> dict:
        return {}


class Client:
    def __init__(self) -> None:
        self.models: list[str] = []

    def chat(self, model: str, _messages: list[dict], **_kwargs: Any) -> dict:
        self.models.append(model)
        return {"text": "## Drill\n- derive axes\n- verify with a ray cast"}


def test_benchmark_topic_uses_local_coach_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "benchmark_curriculum.json").write_text(
            '{"drills":{"cheap:spatial":{"status":"queued","capability":"spatial_reasoning","label":"Spatial reasoning","query":"vectors","lens":"verify"}}}',
            encoding="utf-8",
        )
        client = Client()
        trainer = SelfTrainer(Knowledge(), client, root, topics=[], benchmark_model="local/fable-fast")
        result = trainer.run_cycle(["benchmark-spatial-reasoning"])
        assert len(result["learned"]) == 1
        assert client.models == ["local/fable-fast", "local/fable-fast"]
        assert trainer.status()["benchmark_model"] == "local/fable-fast"


if __name__ == "__main__":
    test_benchmark_topic_uses_local_coach_only()
    print("1/1 passed")

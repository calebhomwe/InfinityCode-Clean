"""Offline checks for safe Hugging Face integration boundaries."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.huggingface_data import HuggingFaceData


def test_status_never_exposes_token_and_exports_review_candidate() -> None:
    previous = os.environ.get("HF_TOKEN")
    os.environ["HF_TOKEN"] = "secret-not-to-be-returned"
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = HuggingFaceData(Path(directory), ["owner/game-data"])
            status = source.status()
            assert status["configured"] is True
            assert "secret-not-to-be-returned" not in str(status)
            exported = source.export_candidates({
                "dataset": "owner/game-data", "config": "default", "split": "train", "rows": [{"row": {"x": 1}}],
            })
            assert exported["review_required"] is True
            assert Path(exported["path"]).is_file()
    finally:
        if previous is None:
            os.environ.pop("HF_TOKEN", None)
        else:
            os.environ["HF_TOKEN"] = previous


if __name__ == "__main__":
    test_status_never_exposes_token_and_exports_review_candidate()
    print("1/1 passed")

"""Token-aware, allow-listed Hugging Face Dataset Viewer integration.

Imports are candidates only: nothing from the Hub is injected into prompts or
fine-tuning automatically. This keeps third-party dataset text from becoming
unreviewed instruction data while still making a user's HF account useful.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


VIEWER_URL = "https://datasets-server.huggingface.co"
DEFAULT_DATASETS = ("LDJnr/GameCodeInstruct", "Unity-Technologies/ml-agents-datasets")


class HuggingFaceData:
    def __init__(self, data_dir: Path, allowed_datasets: Optional[List[str]] = None) -> None:
        self.data_dir = Path(data_dir)
        self.allowed_datasets = tuple(allowed_datasets or DEFAULT_DATASETS)

    @staticmethod
    def _token() -> str:
        # No token is logged or written by this class. HF_TOKEN is compatible
        # with the official HF CLI and the desktop app's environment.
        return (os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or "").strip()

    def status(self) -> Dict[str, Any]:
        return {
            "configured": bool(self._token()),
            "datasets": list(self.allowed_datasets),
            "mode": "read-only candidate import; explicit review required before training",
        }

    def _request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        headers = {"Accept": "application/json"}
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{VIEWER_URL}{path}?{query}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310: fixed HF Dataset Viewer host
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PermissionError("Hugging Face authorization is required for this dataset.") from exc
            raise RuntimeError(f"Hugging Face Dataset Viewer returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Hugging Face Dataset Viewer unavailable: {exc}") from exc
        return body if isinstance(body, dict) else {}

    def preview(self, dataset: str, config: Optional[str] = None, split: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        if dataset not in self.allowed_datasets:
            raise ValueError("Dataset is not in the local Hugging Face allow-list.")
        splits_data = self._request("/splits", {"dataset": dataset})
        splits = splits_data.get("splits") or []
        if not splits:
            raise RuntimeError("Dataset exposes no readable splits.")
        selected = next(
            (item for item in splits if (not config or item.get("config") == config) and (not split or item.get("split") == split)),
            splits[0],
        )
        selected_config = str(selected.get("config") or "")
        selected_split = str(selected.get("split") or "")
        if not selected_config or not selected_split:
            raise RuntimeError("Dataset Viewer did not return config/split metadata.")
        rows = self._request(
            "/first-rows",
            {"dataset": dataset, "config": selected_config, "split": selected_split},
        )
        return {
            "dataset": dataset,
            "config": selected_config,
            "split": selected_split,
            "rows": list(rows.get("rows") or [])[:max(1, min(int(limit), 100))],
        }

    def export_candidates(self, preview: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a reviewed-sample candidate file; never starts training."""
        dataset = str(preview.get("dataset") or "unknown").replace("/", "__")
        output_dir = self.data_dir / "datasets" / "hf_candidates"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{dataset}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "source": "huggingface_dataset_viewer",
            "review_required": True,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            **preview,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "rows": len(payload.get("rows") or []), "review_required": True}


__all__ = ["HuggingFaceData", "DEFAULT_DATASETS"]
